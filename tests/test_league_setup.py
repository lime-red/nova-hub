"""New League: Set Up, the installed state and its gate, the first reset's
canary run, and the API.

The games here are folders of files and a stand-in IB binary. The real games
are set up by tests/live/test_new_league.py on the rig.
"""
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import (
    GameImage, GameReset, League, LeagueGame, Packet, ProcessingRun,
)
from backend.services import league_setup, preflight
from backend.services.game_images import write_manifest
from backend.services.game_reset import perform
from backend.services.game_reset.drafts import validate
from backend.services.game_reset.fields import defaults
from backend.services.league_games import games_config, league_game
from tests.conftest import install_game

TODAY = date(2026, 11, 3)

# Stands in for immortal-barons as setup uses it: -ibbs-reset makes a board's
# data folder (config.json records it), -gen-board-key prints a key, and
# -gen-coord-key makes the Coordinator's. A "fail" file beside the data folder
# makes -gen-coord-key fail.
FAKE_IB = """#!{python}
import json, sys
from pathlib import Path
args = sys.argv[1:]
data = Path(args[args.index("-data") + 1])
if args[0] == "-ibbs-reset":
    (data / "inbound").mkdir(exist_ok=True)
    (data / "outbound").mkdir(exist_ok=True)
    (data / "config.json").write_text(json.dumps({{"DataDir": str(data)}}))
    (data / "world.json").write_text("{{}}")
    print("No drop file format is set.")
elif args[0] == "-gen-board-key":
    (data / "board.key").write_text("secret")
    print("Board key: " + "ab" * 32)
    print("Keep board.key secret")
elif args[0] == "-gen-coord-key":
    if (data.parent / "fail").exists():
        print("cannot write coord.key")
        sys.exit(3)
    (data / "coord.key").write_text("secret")
    (data / "coord.pub").write_text("cd" * 32)
    print("Keep coord.key secret")
else:
    sys.exit(2)
"""


def _dos_image(images: Path, version="t1") -> Path:
    folder = images / f"bre-{version}"
    for sub in ("GAME", "DATA/REPORTS", "INBOUND", "OUTBOUND", "NETMAIL", "BULLETIN"):
        (folder / sub).mkdir(parents=True)
    (folder / "BRE.EXE").write_bytes(b"MZ" + bytes(range(64)))
    (folder / "GAME" / "NEWS.DAT").write_bytes(b"news")
    return folder


@pytest.fixture
def db_engine():
    """One in-memory database for every session and thread: the jobs and the
    API open their own sessions."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from backend.models.database import Base
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    session = sessionmaker(bind=db_engine)()
    yield session
    session.close()


@pytest.fixture
def hub(tmp_path, db_session, db_engine, monkeypatch):
    """A hub with [games] set, a BRE image, and the jobs on the test database."""
    drive_c = tmp_path / "drive_c"
    config = {
        "server": {"data_dir": str(tmp_path / "data")},
        "hub": {"bbs_index": "01", "bbs_name": "Nova Hub"},
        "dosemu": {"dosemu_path": "/usr/bin/dosemu", "timeout": 300},
        "games": {"root": str(drive_c / "leagues"), "drive_c": str(drive_c)},
    }
    images = games_config(config).images
    folder = _dos_image(images)
    image = GameImage(game_type="B", version="t1", dirname=folder.name, source="test",
                      manifest_sha256=write_manifest(folder, game="B", version="t1"))
    db_session.add(image)
    db_session.commit()
    Session = sessionmaker(bind=db_engine)
    monkeypatch.setattr(league_setup, "get_session", lambda: Session())
    monkeypatch.setattr(perform, "get_session", lambda: Session())
    return SimpleNamespace(config=config, db=db_session, image=image, tmp=tmp_path,
                           root=drive_c / "leagues", Session=Session)


def _start(h, number="902", game="B", **kw) -> GameReset:
    order = league_setup.start(h.db, h.config, number=number, game_type=game,
                               name=f"New {number}{game}", hub_address=kw.pop("address", None),
                               image_id=kw.pop("image_id", None), username="admin", **kw)
    h.db.commit()
    return order


async def _set_up(h, number="902", game="B", **kw) -> League:
    order = _start(h, number, game, **kw)
    await league_setup.run_job(order.id, h.config)
    h.db.expire_all()
    return h.db.get(League, order.league_id)


# ── starting ──────────────────────────────────────────────────────────────
def test_set_up_refuses_what_it_should(hub):
    h = hub
    with pytest.raises(league_setup.SetupRefused, match="three digits"):
        _start(h, number="9")
    with pytest.raises(league_setup.SetupRefused, match="no Falcon's Eye image"):
        _start(h, game="F")
    with pytest.raises(league_setup.SetupRefused, match="not a Falcon's Eye image"):
        _start(h, game="F", image_id=h.image.id)

    other = League(league_id="902", game_type="B", name="Taken", is_active=True)
    h.db.add(other)
    h.db.commit()
    with pytest.raises(league_setup.SetupRefused, match="902B already exists") as e:
        _start(h, number="902")
    assert e.value.status == 409

    (h.root / "B903").mkdir(parents=True)
    with pytest.raises(league_setup.SetupRefused, match="already exists") as e:
        _start(h, number="903")
    assert e.value.status == 409

    with pytest.raises(league_setup.SetupRefused, match="root is not set"):
        league_setup.start(h.db, {**h.config, "games": {}}, number="904", game_type="B",
                           name="x", hub_address=None, image_id=None, username="admin")
    h.db.rollback()
    assert h.db.query(League).count() == 1, "a refusal makes nothing"


def test_a_number_another_game_uses_is_free_for_this_one(hub):
    """015B, 015F and 015I run side by side on prod: each game reads only its
    own packets, and the hub's address may serve several leagues."""
    h = hub
    h.db.add(League(league_id="905", game_type="F", name="Falcons", is_active=True,
                    hub_fidonet_address="905:905/1"))
    h.db.commit()
    order = _start(h, number="905")
    league = h.db.get(League, order.league_id)
    assert league.full_id == "905B" and league.hub_fidonet_address == "905:905/1"


def test_a_tampered_image_is_refused_before_anything_is_made(hub):
    h = hub
    folder = games_config(h.config).images / h.image.dirname
    (folder / "GAME" / "NEWS.DAT").write_bytes(b"NEWS")
    with pytest.raises(league_setup.SetupRefused, match="doesn't match its manifest"):
        _start(h)
    assert not (h.root / "B902").exists()


# ── the setup job ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_set_up_installs_and_stamps_the_game_and_runs_nothing(hub):
    h = hub
    league = await _set_up(h)
    row = league.install
    assert row.state == "installed" and row.image_id == h.image.id
    assert league.is_active is False and league.hub_fidonet_address == "902:902/1"
    folder = h.root / "B902"
    assert (folder / "BRE.EXE").is_file() and (folder / "DATA" / "REPORTS").is_dir()
    assert not (folder / "manifest.json").exists()
    assert (folder / "bbs.cfg").read_bytes().split(b"\r\n") == [
        b"Nova Admin", b"Nova Hub", b"902:902/1", b"C:\\LEAGUES\\B902\\INBOUND",
        b"C:\\LEAGUES\\B902\\NETMAIL", b"902", b"NONE", b""]
    assert (folder / "BRNODES.DAT").read_bytes().startswith(b"1 HOST\r\nNova Hub\r\n902:902/1\r\n")
    assert row.preflight_ok is True and row.preflight_problems == []
    assert list((folder / "DATA").iterdir()) == [folder / "DATA" / "REPORTS"], "RESET never ran"

    order = h.db.query(GameReset).filter_by(kind="setup").one()
    assert order.status == "applied" and order.error is None
    assert [p["stage"] for p in order.progress] == [
        "queued", "lock", "stamped", "nodelist", "preflight", "installed"]
    assert "preflight passed" in order.transcript
    assert h.db.query(ProcessingRun).count() == 0
    assert not (h.root / ".B902.setup").exists()


@pytest.mark.asyncio
async def test_a_failed_setup_removes_only_its_folder_and_can_be_tried_again(hub, monkeypatch):
    h = hub
    elsewhere = League(league_id="900", game_type="B", name="900B", is_active=True)
    h.db.add(elsewhere)
    h.db.commit()
    theirs = install_game(h.db, h.config, elsewhere, h.tmp / "drive_c")
    (theirs / "DATA").mkdir()
    (theirs / "DATA" / "GAME.DAT").write_bytes(b"their game")

    real = preflight.run
    monkeypatch.setattr(preflight, "run", lambda db, config, leagues=None: {
        l.full_id: ["bbs.cfg line 6 says league '900', not 902"] for l in leagues})
    league = await _set_up(h)
    assert league.install.state == "failed"
    assert not (h.root / "B902").exists()
    assert (theirs / "DATA" / "GAME.DAT").read_bytes() == b"their game"
    order = h.db.query(GameReset).filter_by(kind="setup").one()
    assert order.status == "failed" and "line 6" in order.error
    assert "removed B902" in order.transcript

    monkeypatch.setattr(preflight, "run", real)
    order = league_setup.again(h.db, h.config, league, "admin")
    h.db.commit()
    await league_setup.run_job(order.id, h.config)
    h.db.expire_all()
    assert h.db.get(League, league.id).install.state == "installed"
    with pytest.raises(league_setup.SetupRefused, match="only a failed setup"):
        league_setup.again(h.db, h.config, h.db.get(League, league.id), "admin")


@pytest.mark.asyncio
async def test_an_interrupted_setup_is_settled_at_startup(hub):
    h = hub
    order = _start(h)
    folder = h.root / "B902"
    folder.mkdir(parents=True)
    (folder / "BRE.EXE").write_bytes(b"half")
    league_setup.recover(h.db, h.config)
    h.db.expire_all()
    order = h.db.get(GameReset, order.id)
    assert order.status == "failed" and order.progress[-1]["stage"] == "interrupted"
    assert order.league.install.state == "failed" and not folder.exists()


def _fake_ib(h, name="immortal-barons") -> Path:
    binary = h.tmp / "build" / name
    binary.parent.mkdir(exist_ok=True)
    binary.write_text(FAKE_IB.format(python=sys.executable))
    binary.chmod(0o755)
    return binary


def _ib_image(h) -> GameImage:
    from backend.services.game_images import import_ib_image
    image, _ = import_ib_image(h.db, h.config, _fake_ib(h), "v0.2.6")
    h.image_ib = image
    h.db.commit()
    return image


@pytest.mark.asyncio
async def test_set_up_an_immortal_barons_league(hub):
    h = hub
    _ib_image(h)
    league = await _set_up(h, number="903", game="I")
    row = league.install
    assert row.state == "installed", h.db.query(GameReset).one().error
    folder = h.root / "I903"
    assert (folder / "bbs.cfg").read_text() == (
        "BoardID Nova Hub\nBBSName Nova Hub\nLeagueNumber 903\n"
        "GameInbound inbound\nGameOutbound outbound\n")
    assert json.loads((folder / "config.json").read_text())["DataDir"] == str(folder)
    assert row.hub_board_key == "ab" * 32
    assert (folder / "coord.pub").read_text() == "cd" * 32
    roster = (folder / "ibnodes.dat").read_bytes().decode().split("\r\n")
    assert roster[:7] == ["1", "Nova Hub", "903:903/1", "-", "-", "-", "ab" * 32]
    assert league.hub_routes_mail is False


@pytest.mark.asyncio
async def test_an_immortal_barons_setup_that_fails_leaves_nothing(hub):
    h = hub
    image = _ib_image(h)
    h.root.mkdir(parents=True)
    (h.root / "fail").write_text("")
    league = await _set_up(h, number="903", game="I")
    assert league.install.state == "failed" and league.install.hub_board_key is None
    assert not (h.root / "I903").exists()
    assert "-gen-coord-key exited 3" in h.db.query(GameReset).one().error


# ── installed: nothing runs it ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_installed_game_waits_for_its_first_reset(hub):
    h = hub
    league = await _set_up(h)
    install = league_game(h.config, league)
    assert not install.runnable
    league.is_active = True
    assert not league_game(h.config, league).runnable, "enabled is not enough"
    league.is_active = False

    h.db.add(Packet(filename="902B0201.001", league_id=league.id, source_bbs_index="02",
                    dest_bbs_index="01", sequence_number=1, file_size=1))
    h.db.commit()
    from backend.services.processing_service import ProcessingService
    run = await ProcessingService(h.db, h.config).process_batch()
    assert run is None and h.db.query(ProcessingRun).count() == 0
    assert h.db.query(Packet).one().processed_at is None, "the packet waits"

    for kind in ("rules",):
        with pytest.raises(perform.ResetRefused, match="no reset yet"):
            perform.game_setup(h.config, league)


# ── the first reset ───────────────────────────────────────────────────────
class FakeProcessing:
    calls = []
    eat = None        # a canary name the "game" takes
    write = None      # (league folder, name) the "game" writes into another league's

    def __init__(self, db, config):
        self.db, self.config = db, config

    async def process_batch(self, also=(), first_reset=False):
        FakeProcessing.calls.append((also, first_reset))
        for code, number in also:
            league = self.db.query(League).filter_by(league_id=number).first()
            inbound = league_game(self.config, league).inbound
            if FakeProcessing.eat:
                (inbound / FakeProcessing.eat).unlink()
        if FakeProcessing.write:
            folder, name = FakeProcessing.write
            (folder / name).write_bytes(b"x")
        run = ProcessingRun(status="completed")
        self.db.add(run)
        self.db.commit()
        return run


@pytest.fixture
def processing(monkeypatch):
    import backend.services.processing_service as ps
    FakeProcessing.calls, FakeProcessing.eat, FakeProcessing.write = [], None, None
    monkeypatch.setattr(ps, "ProcessingService", FakeProcessing)
    return FakeProcessing


def _draft(h, league):
    s, errors = validate("B", {**defaults("B"), "turns_per_day": 12}, TODAY)
    assert not errors
    d = GameReset(league_id=league.id, status="draft", source="console", settings=s,
                  version=1, created_by="admin", updated_by="admin")
    h.db.add(d)
    h.db.commit()
    return d


def _driver(seen=None, fail=None):
    def drive(setup, game_type, settings, today, log, transcript):
        if seen is not None:
            seen.append(setup.folder)
        (setup.folder / "DATA" / "GAME.DAT").write_bytes(b"reset game")
        if fail:
            raise perform.EditorError(fail)
        log("typed it all")
    return drive


async def _first_reset(h, league, **kw):
    _draft(h, league)
    reset = perform.start(h.db, league, 1, league.full_id, "admin", h.config, TODAY)
    await perform.run_job(reset.id, h.config, drive_fn=_driver(**kw), today=TODAY)
    h.db.expire_all()
    return h.db.get(GameReset, reset.id)


def _neighbour(h, number="900") -> Path:
    """A BRE league already running, with a packet in its INBOUND and one the
    hub holds for it."""
    other = League(league_id=number, game_type="B", name=f"{number}B", is_active=True,
                   hub_fidonet_address=f"{int(number)}:{int(number)}/1")
    h.db.add(other)
    h.db.commit()
    folder = install_game(h.db, h.config, other, h.tmp / "drive_c")
    (folder / "INBOUND" / f"{number}B0301.004").write_bytes(b"theirs")
    h.db.add(Packet(filename=f"{number}B0201.007", league_id=other.id, source_bbs_index="02",
                    dest_bbs_index="01", sequence_number=7, file_size=4, file_data=b"real",
                    processed_at=TODAY))
    h.db.commit()
    return folder


@pytest.mark.asyncio
async def test_the_first_reset_runs_the_canary_and_makes_the_game_ready(hub, processing):
    h = hub
    _neighbour(h)
    league = await _set_up(h)
    r = await _first_reset(h, league)
    assert r.status == "applied" and r.error is None, r.error
    assert [p["stage"] for p in r.progress] == [
        "queued", "lock", "image", "editor", "saved", "processing", "ready"]
    assert r.backup_path is None, "the image is the before-state"
    assert processing.calls == [((("BRE", "902"),), True)]
    assert "canary for 900B: 900B0201.007" in r.transcript
    assert "canary run passed" in r.transcript
    folder = h.root / "B902"
    assert not (folder / "INBOUND" / "900B0201.007").exists(), "canaries are removed"
    h.db.expire_all()
    league = h.db.get(League, league.id)
    assert league.install.state == "ready" and league.install.preflight_ok
    assert league.is_active is False, "enabling is the admin's"


@pytest.mark.asyncio
async def test_a_canary_taken_fails_the_first_reset_and_the_game_is_made_again(hub, processing):
    h = hub
    _neighbour(h)
    league = await _set_up(h)
    processing.eat = "900B0201.007"
    r = await _first_reset(h, league)
    assert r.status == "failed"
    assert "the canary 900B0201.007 was taken by the game" in r.error
    assert "made again from its image" in r.error
    h.db.expire_all()
    league = h.db.get(League, league.id)
    assert league.install.state == "installed"
    folder = h.root / "B902"
    assert not (folder / "DATA" / "GAME.DAT").exists(), "the reset's game is gone"
    assert (folder / "bbs.cfg").is_file(), "stamped again"
    assert h.db.query(GameReset).filter_by(league_id=league.id, status="draft").count() == 1


@pytest.mark.asyncio
async def test_another_leagues_folder_changing_fails_the_first_reset(hub, processing):
    h = hub
    theirs = _neighbour(h)
    league = await _set_up(h)
    processing.write = (theirs / "OUTBOUND", "902B0102.001")
    r = await _first_reset(h, league)
    assert r.status == "failed" and "another league's, changed during the run" in r.error
    assert h.db.get(League, league.id).install.state == "installed"


@pytest.mark.asyncio
async def test_a_first_reset_the_editor_fails_makes_the_game_again(hub, processing):
    h = hub
    league = await _set_up(h)
    r = await _first_reset(h, league, fail="the editor stopped answering")
    assert r.status == "failed" and r.error.startswith("the editor stopped answering")
    assert processing.calls == []
    assert not (h.root / "B902" / "DATA" / "GAME.DAT").exists()
    assert h.db.get(League, league.id).install.state == "installed"


@pytest.mark.asyncio
async def test_a_first_reset_interrupted_is_made_again_at_startup(hub):
    h = hub
    league = await _set_up(h)
    _draft(h, league)
    reset = perform.start(h.db, league, 1, "902B", "admin", h.config, TODAY)
    reset.progress = [*reset.progress, {"stage": "lock", "at": "x"}, {"stage": "editor", "at": "x"}]
    h.db.commit()
    (h.root / "B902" / "DATA" / "GAME.DAT").write_bytes(b"half reset")
    perform.recover(h.db, h.config)
    h.db.expire_all()
    r = h.db.get(GameReset, reset.id)
    assert r.status == "failed" and "made again from its image" in r.error
    assert not (h.root / "B902" / "DATA" / "GAME.DAT").exists()
    assert h.db.get(League, league.id).install.state == "installed"


# ── the API ───────────────────────────────────────────────────────────────
@pytest.fixture
def api(hub, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.api.management.leagues as leagues_api
    from backend.core.database import get_db
    from backend.core.security import get_current_user
    from backend.models.database import SysopUser
    from main import management_app

    monkeypatch.setattr(leagues_api, "get_config", lambda: SimpleNamespace(_raw=hub.config))
    scheduled = []
    monkeypatch.setattr(league_setup, "schedule", lambda oid, cfg: scheduled.append(oid))

    def session():
        db = hub.Session()
        try:
            yield db
        finally:
            db.close()
    saved = dict(management_app.dependency_overrides)
    management_app.dependency_overrides[get_db] = session
    management_app.dependency_overrides[get_current_user] = lambda: SysopUser(
        id=1, username="admin", hashed_password="x", is_superuser=True)
    yield SimpleNamespace(client=TestClient(management_app, base_url="https://testserver"),
                          hub=hub, scheduled=scheduled)
    management_app.dependency_overrides.clear()
    management_app.dependency_overrides.update(saved)


@pytest.mark.asyncio
async def test_the_api_sets_up_and_enables_only_a_ready_league(api):
    c, h = api.client, api.hub
    form = c.get("/api/v1/leagues/new").json()
    assert form["problems"] == [] and form["hub_index"] == 1
    assert [i["dirname"] for i in form["images"]] == ["bre-t1"]

    r = c.post("/api/v1/leagues/setup", json={"league_id": "902", "game_type": "B",
                                              "name": "New League"})
    assert r.status_code == 202, r.text
    started = r.json()
    assert started["full_id"] == "902B" and api.scheduled == [started["setup_id"]]
    assert c.post("/api/v1/leagues/setup", json={"league_id": "902", "game_type": "B",
                                                 "name": "Again"}).status_code == 409

    await league_setup.run_job(started["setup_id"], h.config)
    h.db.expire_all()
    league_id = started["league_id"]
    game = c.get(f"/api/v1/leagues/{league_id}").json()["game"]
    assert game["state"] == "installed" and game["setup_status"] == "applied"
    assert game["can_enable"] is False

    r = c.put(f"/api/v1/leagues/{league_id}", json={"is_active": True})
    assert r.status_code == 409 and "no reset yet" in r.json()["detail"]

    row = h.db.get(League, league_id).install
    row.state = "ready"
    h.db.commit()
    assert c.get(f"/api/v1/leagues/{league_id}").json()["game"]["can_enable"] is True
    assert c.put(f"/api/v1/leagues/{league_id}", json={"is_active": True}).status_code == 200
    assert c.post(f"/api/v1/leagues/{league_id}/setup", json={}).status_code == 409


@pytest.mark.asyncio
async def test_a_league_is_deleted_once_its_setup_has_ended(api, monkeypatch):
    """Not while the job is making it; a failed setup's league goes cleanly."""
    c, h = api.client, api.hub
    r = c.post("/api/v1/leagues/setup", json={"league_id": "902", "game_type": "B",
                                              "name": "Doomed"})
    league_id = r.json()["league_id"]
    confirm = {"confirmation_name": "BRE_902"}
    r = c.request("DELETE", f"/api/v1/leagues/{league_id}", json=confirm)
    assert r.status_code == 409 and "being set up" in r.json()["detail"]

    def broken(*a, **k):
        raise league_setup.SetupFailed("the image would not copy")
    monkeypatch.setattr(league_setup, "_place_dos", broken)
    await league_setup.run_job(api.scheduled[-1], h.config)
    h.db.expire_all()
    assert h.db.get(League, league_id).install.state == "failed"

    r = c.request("DELETE", f"/api/v1/leagues/{league_id}", json=confirm)
    assert r.status_code == 200, r.text
    h.db.expire_all()
    assert h.db.get(League, league_id) is None
    assert h.db.query(GameReset).filter_by(league_id=league_id).count() == 0
    assert not (h.root / "B902").exists()
