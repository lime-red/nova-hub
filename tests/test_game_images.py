"""Golden images: the archive a hub takes in, the check against its manifest,
installing from one, the recipe's cleaning of a donor's league, fetching with a
login, and the admin API."""
import io
import json
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import Base, GameImage, League, LeagueGame
from backend.services import golden
from backend.services.game_images import (
    ImageError,
    check_image,
    extract_archive,
    import_archive,
    install_image,
    pack_image,
    waiting_archives,
    write_manifest,
)
from backend.services.image_fetch import FetchError, config_problems, fetch
from backend.services.league_games import games_config


def _image(root: Path, version="t1", game="B") -> Path:
    folder = root / f"{golden.RECIPES[game].prefix if game in 'BF' else 'ib'}-{version}"
    (folder / "GAME").mkdir(parents=True)
    (folder / "DATA" / "REPORTS").mkdir(parents=True)
    (folder / "INBOUND").mkdir()
    (folder / "BRE.EXE").write_bytes(b"MZ" + bytes(range(64)))
    (folder / "GAME" / "NEWS.DAT").write_bytes(b"news")
    write_manifest(folder, game=game, version=version, recipe=1,
                   source={"name": "bre_sj1.zip", "sha256": "0" * 64})
    return folder


def _tar(folder: Path, out: Path, skip=(), extra=None) -> Path:
    """An archive of folder as it stands, whatever its manifest says."""
    with tarfile.open(out, "w:gz") as tar:
        for p in [folder, *sorted(folder.rglob("*"))]:
            rel = p.relative_to(folder.parent).as_posix()
            if any(rel.endswith(s) for s in skip):
                continue
            tar.add(p, arcname=rel, recursive=False)
        for info, data in extra or ():
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return out


@pytest.fixture
def hub(tmp_path, db_session):
    config = {"server": {"data_dir": str(tmp_path / "data")}, "games": {}}
    return config, db_session


def test_an_image_round_trips_and_a_second_import_is_the_same_image(hub, tmp_path):
    config, db = hub
    archive = pack_image(_image(tmp_path / "build"), tmp_path)
    assert archive.name == "bre-t1.tar.gz" and archive.stat().st_mode & 0o077 == 0

    image, created = import_archive(db, config, archive, "upload")
    assert created and image.dirname == "bre-t1" and image.game_type == "B" and image.version == "t1"
    folder = games_config(config).images / "bre-t1"
    assert (folder / "DATA" / "REPORTS").is_dir(), "empty folders survive"
    assert check_image(folder) == []

    again, created = import_archive(db, config, archive, "upload")
    assert again.id == image.id and not created
    assert [p.name for p in games_config(config).images.iterdir()] == ["bre-t1"], "nothing left over"


@pytest.mark.parametrize("spoil, says", [
    ("tamper", "GAME/NEWS.DAT does not match the manifest"),
    ("no manifest", "no readable manifest.json"),
    ("no empty folder", "DATA/REPORTS/ is missing"),
    ("added file", "EXTRA.TXT is not in the manifest"),
])
def test_an_archive_that_does_not_match_its_manifest_is_refused(hub, tmp_path, spoil, says):
    config, db = hub
    folder = _image(tmp_path / "build")
    skip = ()
    if spoil == "tamper":
        (folder / "GAME" / "NEWS.DAT").write_bytes(b"NEWS")
    elif spoil == "no manifest":
        skip = ("manifest.json",)
    elif spoil == "no empty folder":
        skip = ("DATA/REPORTS",)
    else:
        (folder / "EXTRA.TXT").write_text("x")
    archive = _tar(folder, tmp_path / "bre-t1.tar.gz", skip=skip)

    with pytest.raises(ImageError, match=says):
        import_archive(db, config, archive, "upload")
    assert db.query(GameImage).count() == 0
    assert list(games_config(config).images.iterdir()) == [], "a refusal leaves nothing behind"


def _info(name, kind=tarfile.REGTYPE, size=0, link=""):
    info = tarfile.TarInfo(name)
    info.type, info.size, info.linkname = kind, size, link
    return info


@pytest.mark.parametrize("extra, says", [
    ((_info("bre-t1/../../evil", size=1), b"x"), "not a path inside the image"),
    ((_info("bre-t1/GAME/link", tarfile.SYMTYPE, link="/etc/passwd"), None), "not a file or a folder"),
    ((_info("other/file", size=1), b"x"), "holds one folder, not 2"),
])
def test_an_archive_that_reaches_outside_its_folder_is_refused(hub, tmp_path, extra, says):
    config, db = hub
    archive = _tar(_image(tmp_path / "build"), tmp_path / "bre-t1.tar.gz", extra=[extra])
    with pytest.raises(ImageError, match=says):
        import_archive(db, config, archive, "upload")
    assert not (tmp_path / "evil").exists()


def test_the_folder_must_be_named_for_the_manifests_game_and_version(hub, tmp_path):
    config, db = hub
    folder = _image(tmp_path / "build")
    folder = folder.rename(folder.with_name("bre-t2"))
    with pytest.raises(ImageError, match="its manifest says bre-t1"):
        import_archive(db, config, _tar(folder, tmp_path / "x.tar.gz"), "upload")


def test_a_different_image_under_a_taken_name_is_refused(hub, tmp_path):
    config, db = hub
    import_archive(db, config, pack_image(_image(tmp_path / "a"), tmp_path / "a"), "upload")
    other = _image(tmp_path / "b")
    (other / "GAME" / "NEWS.DAT").write_bytes(b"other news")
    write_manifest(other, game="B", version="t1")
    with pytest.raises(ImageError, match="a changed image needs a new version"):
        import_archive(db, config, pack_image(other, tmp_path / "b"), "upload")


def test_not_a_tarball_is_refused(hub, tmp_path):
    config, db = hub
    junk = tmp_path / "bre-t1.tar.gz"
    junk.write_bytes(b"PK\x03\x04 not a tar")
    with pytest.raises(ImageError, match="not a readable .tar.gz"):
        import_archive(db, config, junk, "upload")


def test_an_install_is_the_image_without_its_manifest(tmp_path):
    folder = _image(tmp_path / "build")
    install_image(folder, tmp_path / "B016")
    assert sorted(p.relative_to(tmp_path / "B016").as_posix() for p in (tmp_path / "B016").rglob("*")) == [
        "BRE.EXE", "DATA", "DATA/REPORTS", "GAME", "GAME/NEWS.DAT", "INBOUND"]
    (folder / "BRE.EXE").write_bytes(b"MZ patched")
    with pytest.raises(ImageError, match="BRE.EXE does not match"):
        install_image(folder, tmp_path / "B017")
    assert not (tmp_path / "B017").exists()


# -- the recipe ---------------------------------------------------------------

def _media(tmp_path: Path) -> Path:
    media = tmp_path / "bre_sj1.zip"
    files = {
        "bre_sj1/BRE.EXE": b"MZ", "bre_sj1/BREDATA.EXE": b"MZ", "bre_sj1/BRE.OVR": b"ovr",
        "bre_sj1/bbs.cfg": b"donor", "bre_sj1/BRNODES.DAT": b"1 HOST", "bre_sj1/brnodes.bak": b"",
        "bre_sj1/COORD.KEY": b"key", "bre_sj1/loc.bat": b"@echo", "bre_sj1/DOORFILE.SR": b"",
        "bre_sj1/DATA/GAME.DAT": b"donor game", "bre_sj1/NETMAIL/10.MSG": b"msg",
        "bre_sj1/BULLETIN/SCORES.TXT": b"scores", "bre_sj1/GAME/NEWS.DAT": b"news",
        "bre_sj1/DOCS/BRE.DOC": b"doc",
    }
    with zipfile.ZipFile(media, "w") as z:
        for d in ("bre_sj1/", "bre_sj1/DATA/", "bre_sj1/DATA/REPORTS/", "bre_sj1/INBOUND/",
                  "bre_sj1/OUTBOUND/", "bre_sj1/BACKUP/"):
            z.writestr(d, b"")
        for name, data in files.items():
            z.writestr(name, data)
    return media


def test_the_recipe_takes_out_the_donors_league_and_keeps_the_game(tmp_path):
    game = golden.unpack_media(_media(tmp_path), tmp_path / "unpacked")
    assert game.name == "bre_sj1"
    gone = golden.clean(game)
    assert set(gone) == {"bbs.cfg", "BRNODES.DAT", "brnodes.bak", "loc.bat", "DOORFILE.SR",
                         "DATA/GAME.DAT", "NETMAIL/10.MSG", "BULLETIN/SCORES.TXT"}
    left = sorted(p.relative_to(game).as_posix() for p in game.rglob("*"))
    assert left == ["BACKUP", "BRE.EXE", "BRE.OVR", "BREDATA.EXE", "BULLETIN", "COORD.KEY", "DATA",
                    "DATA/REPORTS", "DOCS", "DOCS/BRE.DOC", "GAME", "GAME/NEWS.DAT", "INBOUND",
                    "NETMAIL", "OUTBOUND"]


def test_media_reaching_outside_its_folder_is_refused(tmp_path):
    media = tmp_path / "bad.zip"
    with zipfile.ZipFile(media, "w") as z:
        z.writestr("../evil.exe", b"MZ")
    with pytest.raises(golden.GoldenError, match="outside its folder"):
        golden.unpack_media(media, tmp_path / "out")


def test_case_twins_are_found_at_any_depth(tmp_path):
    (tmp_path / "GAME").mkdir()
    (tmp_path / "GAME" / "NEWS.DAT").write_text("a")
    (tmp_path / "GAME" / "news.dat").write_text("b")
    assert golden.case_twins(tmp_path) == ["GAME/NEWS.DAT and news.dat"]


def test_the_version_names_the_media_and_the_recipe():
    assert golden.default_version(Path("/m/bre_sj1.zip")) == f"sj1-r{golden.RECIPE}"
    assert golden.default_version(Path("/m/fe_sj1.zip")) == f"sj1-r{golden.RECIPE}"


# -- fetching -----------------------------------------------------------------

def _creds(tmp_path, host="127.0.0.1", mode=0o600) -> Path:
    path = tmp_path / "netrc"
    path.write_text(f"machine {host} login nova-fetch password s3cret\n")
    path.chmod(mode)
    return path


def _cfg(tmp_path, url, creds=None):
    return games_config({"server": {"data_dir": str(tmp_path / "data")},
                         "games": {"image_url": url,
                                   **({"image_credentials": str(creds)} if creds else {})}})


def test_a_login_file_others_can_read_is_refused(tmp_path):
    cfg = _cfg(tmp_path, "https://127.0.0.1/golden", _creds(tmp_path, mode=0o644))
    assert config_problems(cfg) == [f"[games] image_credentials {tmp_path / 'netrc'} can be read "
                                    f"by others; chmod 600 it"]


def test_a_login_is_never_sent_in_the_clear_or_put_in_the_url(tmp_path):
    assert "in the clear" in " ".join(config_problems(
        _cfg(tmp_path, "http://127.0.0.1/golden", _creds(tmp_path))))
    assert config_problems(_cfg(tmp_path, "https://u:p@127.0.0.1/golden")) == [
        "[games] image_url holds a login; put it in image_credentials instead"]
    assert config_problems(_cfg(tmp_path, "https://nexus.example/golden", _creds(tmp_path))) == [
        "[games] image_credentials has no machine nexus.example"]
    assert config_problems(_cfg(tmp_path, "https://127.0.0.1/golden", _creds(tmp_path))) == []


class _Server:
    """A Nexus stand-in that wants nova-fetch's login."""

    def __init__(self, archive: Path):
        self.archive = archive

    async def handle(self, request):
        name = request.match_info["name"]
        if request.headers.get("Authorization") != "Basic bm92YS1mZXRjaDpzM2NyZXQ=":
            return web.Response(status=401)
        if name == "bre-moved.tar.gz":
            raise web.HTTPFound("https://elsewhere.example/x")
        if name != self.archive.name:
            return web.Response(status=404)
        return web.FileResponse(self.archive)

    async def __aenter__(self) -> str:
        app = web.Application()
        app.router.add_get("/golden/{name}", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/golden"

    async def __aexit__(self, *exc):
        await self.runner.cleanup()


@pytest.mark.asyncio
async def test_a_fetch_logs_in_and_lands_the_archive_for_import(tmp_path, db_session):
    archive = pack_image(_image(tmp_path / "build"), tmp_path / "build")
    # Plain http only because the stand-in has no certificate: the validator
    # refuses a login over http (above), and the API checks that before fetching.
    async with _Server(archive) as url:
        cfg = _cfg(tmp_path, url, _creds(tmp_path))
        path = await fetch(cfg, "bre-t1.tar.gz")
        assert path.parent == cfg.images and path.name.startswith(".incoming-")
        config = {"server": {"data_dir": str(tmp_path / "data")}, "games": {}}
        image, created = import_archive(db_session, config, path, "fetch")
        assert created and image.dirname == "bre-t1"
        path.unlink()

        with pytest.raises(FetchError, match="answered 404"):
            await fetch(cfg, "bre-t9.tar.gz")
        with pytest.raises(FetchError, match="answered 302"):
            await fetch(cfg, "bre-moved.tar.gz")
        with pytest.raises(FetchError, match="not an image archive name"):
            await fetch(cfg, "../etc/passwd")
        with pytest.raises(FetchError, match="answered 401"):
            await fetch(_cfg(tmp_path, url), "bre-t1.tar.gz")
    assert [p.name for p in cfg.images.iterdir() if p.name.startswith(".")] == [], \
        "a failed fetch leaves nothing behind"


# -- the API ------------------------------------------------------------------

from tests.test_league_members_api import _as_sysop, api  # noqa: E402,F401

IMAGES = "/management/api/v1/images"


@pytest.fixture
def images_api(api, tmp_path, monkeypatch):
    from backend.core.config import get_config
    raw = get_config()._raw
    monkeypatch.setitem(raw, "games", {**(raw.get("games") or {}), "images": str(tmp_path / "images")})
    return api + (raw,)


def test_an_admin_uploads_lists_checks_and_deletes_an_image(images_api, tmp_path):
    client, db, config = images_api
    archive = pack_image(_image(tmp_path / "build"), tmp_path)

    r = client.post(f"{IMAGES}/upload", files={"file": (archive.name, archive.read_bytes())})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] and body["image"]["dirname"] == "bre-t1"
    assert body["image"]["made_from"] == "bre_sj1.zip" and body["image"]["recipe"] == 1
    image_id = body["image"]["id"]

    r = client.get(IMAGES)
    assert r.status_code == 200
    assert [i["dirname"] for i in r.json()["images"]] == ["bre-t1"]
    assert r.json()["fetch_from"] is None and r.json()["waiting"] == []

    assert client.post(f"{IMAGES}/{image_id}/check").json()["problems"] == []
    (tmp_path / "images" / "bre-t1" / "BRE.EXE").write_bytes(b"MZ patched")
    assert client.post(f"{IMAGES}/{image_id}/check").json()["problems"] == [
        "BRE.EXE does not match the manifest"]

    league = League(league_id="902", game_type="B", name="902B", is_active=True)
    db.add(league)
    db.flush()
    db.add(LeagueGame(league_id=league.id, folder="B902", state="ready", image_id=image_id))
    db.commit()
    r = client.delete(f"{IMAGES}/{image_id}")
    assert r.status_code == 409 and "used by 902B" in r.json()["detail"]

    db.query(LeagueGame).delete()
    db.commit()
    assert client.delete(f"{IMAGES}/{image_id}").status_code == 204
    assert not (tmp_path / "images" / "bre-t1").exists()
    assert client.get(IMAGES).json()["images"] == []


def test_a_refused_upload_says_why(images_api, tmp_path):
    client, db, _ = images_api
    r = client.post(f"{IMAGES}/upload", files={"file": ("bre-t1.tar.gz", b"not a tar")})
    assert r.status_code == 422 and "not a readable .tar.gz" in r.json()["detail"]


def test_a_dropped_archive_waits_until_imported(images_api, tmp_path):
    client, db, config = images_api
    images = tmp_path / "images"
    images.mkdir()
    pack_image(_image(tmp_path / "build"), images)
    assert [p.name for p in waiting_archives(config)] == ["bre-t1.tar.gz"]
    assert client.get(IMAGES).json()["waiting"][0]["name"] == "bre-t1.tar.gz"

    assert client.post(f"{IMAGES}/import", json={"name": "nope.tar.gz"}).status_code == 404
    r = client.post(f"{IMAGES}/import", json={"name": "bre-t1.tar.gz"})
    assert r.status_code == 200, r.text
    assert r.json()["created"]
    assert not (images / "bre-t1.tar.gz").exists(), "the archive goes once the image is in"


def test_a_hub_with_no_image_url_cannot_fetch(images_api):
    client, _, _ = images_api
    r = client.post(f"{IMAGES}/fetch", json={"name": "bre-t1.tar.gz"})
    assert r.status_code == 409 and "no [games] image_url" in r.json()["detail"]


def test_images_are_for_admins_only(images_api):
    client, _, _ = images_api
    _as_sysop()
    assert client.get(IMAGES).status_code == 403
    assert client.post(f"{IMAGES}/upload", files={"file": ("x.tar.gz", b"x")}).status_code == 403
