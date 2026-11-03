"""Game images: the installs leagues' games are made from.

An image is a folder in `[games] images`, named for its game and version
(bre-sj1-r1, fe-sj1-r1, ib-v0.2.6), with a game_images row. Its manifest.json
names the game and version, where it was made from, every folder in it, and
the sha256 of every file, so the hub can tell an image (or a game's copy of its
fixed files) that has been changed since. Images are never changed in place: a
fixed image is a new version.

Images travel as <dirname>.tar.gz holding that one folder. make_golden.py
builds them; the hub takes them by upload, from a file dropped into
`[games] images`, or fetched from `[games] image_url`. Tar rather than zip
because the games need their empty folders (INBOUND, DATA/REPORTS) and tar
always keeps them; the manifest lists folders as well, so a missing one fails
the check whatever carried it.

An Immortal Barons image is just its binary, which the league's game runs in
place; a league is moved to a new version by pointing its row at another image
(game_image.py set). A BRE or FE league's game is a copy of its image
(install_image), which then RESET makes into a game.
"""

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.models.database import GameImage
from backend.services.league_games import IB_BINARY, games_config

MANIFEST = "manifest.json"
ARCHIVE = ".tar.gz"
# The prefix of an image's folder name, by game letter.
PREFIX = {"B": "bre", "F": "fe", "I": "ib"}
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")
# Far above any real image (a BRE image is about 2 MB, IB's binary 11 MB);
# there to stop a bad archive filling the disk, not to judge a good one.
MAX_UNPACKED = 1 << 30
INCOMING = ".incoming-"


class ImageError(ValueError):
    """An image or an image archive the hub will not take, and why."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def image_dirname(game: str, version: str) -> str:
    if game not in PREFIX:
        raise ImageError(f"{game!r} is not a game (B, F or I)")
    if not _VERSION.match(version or ""):
        raise ImageError(f"{version!r} is not a version name (letters, digits, '.', '_', '-')")
    return f"{PREFIX[game]}-{version}"


def ib_image_dirname(version: str) -> str:
    return image_dirname("I", version)


def write_manifest(folder: Path, **about) -> str:
    """Record every folder and the sha256 of every file in an image folder in
    its manifest.json, with `about` (game, version, source, recipe), and
    return the manifest's own sha256 (game_images.manifest_sha256)."""
    files, dirs = {}, []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_dir():
            dirs.append(rel)
        elif p.is_file() and rel != MANIFEST:
            files[rel] = sha256(p)
    text = json.dumps({**about, "dirs": dirs, "files": files}, indent=2, sort_keys=True) + "\n"
    (folder / MANIFEST).write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def read_manifest(folder: Path) -> tuple[dict, str]:
    """The manifest and its sha256. Raises ImageError."""
    try:
        raw = (folder / MANIFEST).read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError) as e:
        raise ImageError(f"no readable {MANIFEST} ({e})")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise ImageError(f"{MANIFEST} has no list of files")
    return manifest, hashlib.sha256(raw).hexdigest()


def check_image(folder: Path) -> list[str]:
    """What is wrong with an image folder: a file changed, missing or added, a
    folder missing or added, a link. Nothing is fine. A manifest from before
    folders were listed (the first IB images) is checked for files only."""
    try:
        manifest, _ = read_manifest(folder)
    except ImageError as e:
        return [str(e)]
    problems = []
    files = manifest["files"]
    for rel, want in sorted(files.items()):
        f = folder / rel
        if f.is_symlink() or not f.is_file():
            problems.append(f"{rel} is missing")
        elif sha256(f) != want:
            problems.append(f"{rel} does not match the manifest")
    dirs = manifest.get("dirs")
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_symlink():
            problems.append(f"{rel} is a link; an image holds only files and folders")
        elif p.is_dir():
            if dirs is not None and rel not in dirs:
                problems.append(f"{rel}/ is not in the manifest")
        elif rel != MANIFEST and rel not in files:
            problems.append(f"{rel} is not in the manifest")
    for rel in dirs or []:
        if not (folder / rel).is_dir():
            problems.append(f"{rel}/ is missing")
    return problems


def _require_clean(folder: Path, what: str):
    problems = check_image(folder)
    if problems:
        shown = "; ".join(problems[:5]) + (f"; and {len(problems) - 5} more" if len(problems) > 5 else "")
        raise ImageError(f"{what}: {shown}")


def pack_image(folder: Path, out_dir: Path) -> Path:
    """Write <dirname>.tar.gz of an image folder into out_dir. Owners are not
    recorded: whoever unpacks it owns it. The archive is readable by its owner
    only: a BRE or FE image carries the game's registration keys."""
    folder = Path(folder)
    _require_clean(folder, folder.name)
    out = Path(out_dir) / (folder.name + ARCHIVE)

    def anonymous(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(out, "w:gz") as tar:
        tar.add(folder, arcname=folder.name, recursive=False, filter=anonymous)
        for p in sorted(folder.rglob("*")):
            tar.add(p, arcname=f"{folder.name}/{p.relative_to(folder).as_posix()}",
                    recursive=False, filter=anonymous)
    out.chmod(0o600)
    return out


def _archive_top(members: list[tarfile.TarInfo]) -> str:
    """The one folder an image archive holds. Raises ImageError for anything
    but plain files and folders inside that one folder."""
    tops, total = set(), 0
    for m in members:
        path = PurePosixPath(m.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ImageError(f"{m.name!r} is not a path inside the image")
        if not (m.isfile() or m.isdir()):
            raise ImageError(f"{m.name} is not a file or a folder")
        tops.add(path.parts[0])
        total += m.size
    if len(tops) != 1:
        raise ImageError(f"an image archive holds one folder, not {len(tops)}")
    if total > MAX_UNPACKED:
        raise ImageError(f"it would unpack to {total} bytes")
    return tops.pop()


def extract_archive(archive: Path, into: Path) -> Path:
    """Unpack an image archive into `into` and check it against its manifest.
    Returns the image folder. Raises ImageError, leaving whatever was unpacked
    for the caller to remove with `into`."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            top = _archive_top(members)
            tar.extractall(into, members=members, filter="data")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise ImageError(f"{Path(archive).name} is not a readable .tar.gz ({e})")
    folder = Path(into) / top
    manifest, _ = read_manifest(folder)
    expected = image_dirname(manifest.get("game"), manifest.get("version"))
    if expected != top:
        raise ImageError(f"the archive's folder is {top}, but its manifest says {expected}")
    _require_clean(folder, top)
    return folder


def _add(db: Session, folder: Path, manifest: dict, manifest_sha: str, source: str) -> GameImage:
    image = GameImage(game_type=manifest["game"], version=manifest["version"], dirname=folder.name,
                      manifest_sha256=manifest_sha, source=source[:255], created_at=utcnow())
    db.add(image)
    db.flush()
    return image


def import_archive(db: Session, config: dict, archive: Path, source: str) -> tuple[GameImage, bool]:
    """Take an image archive into `[games] images` and record it.

    Returns (image, created). The same image again gives the one already
    there; a different image under a name already taken is refused, as is a
    folder with no row (a half-done import). Nothing is left behind on a
    refusal. Flushes, does not commit.
    """
    images = games_config(config).images
    images.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=INCOMING, dir=images))
    try:
        folder = extract_archive(Path(archive), tmp)
        manifest, manifest_sha = read_manifest(folder)
        existing = db.query(GameImage).filter(GameImage.dirname == folder.name).first()
        if existing is not None:
            if existing.manifest_sha256 == manifest_sha:
                return existing, False
            raise ImageError(f"image {folder.name} already exists, with different files; "
                             f"a changed image needs a new version")
        dest = images / folder.name
        if dest.exists():
            raise ImageError(f"{dest} exists but no image row names it; remove it first")
        os.rename(folder, dest)
        return _add(db, dest, manifest, manifest_sha, source), True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def import_ib_image(db: Session, config: dict, binary: Path, version: str) -> tuple[GameImage, bool]:
    """Copy an Immortal Barons binary into images/ib-<version>/ and record it.

    Returns (image, created). Importing the same binary under the same version
    again gives the image already there; a different binary under a version
    already taken is refused, as is a folder with no row (a half-done import).
    Flushes, does not commit.
    """
    binary = Path(binary)
    if not binary.is_file():
        raise ImageError(f"{binary} is not a file")
    dirname = ib_image_dirname(version)
    folder = games_config(config).images / dirname
    existing = db.query(GameImage).filter(GameImage.dirname == dirname).first()
    if existing is not None:
        mine = folder / IB_BINARY
        if mine.is_file() and sha256(mine) == sha256(binary):
            return existing, False
        raise ImageError(f"image {dirname} already exists, with a different binary")
    if folder.exists():
        raise ImageError(f"{folder} exists but no image row names it; remove it first")
    folder.mkdir(parents=True)
    try:
        build_ib_image(binary, version, folder)
        manifest, manifest_sha = read_manifest(folder)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return _add(db, folder, manifest, manifest_sha, str(binary.resolve())), True


def build_ib_image(binary: Path, version: str, folder: Path) -> str:
    """Make an IB image folder (which must exist, empty) from a binary."""
    shutil.copyfile(binary, folder / IB_BINARY)
    (folder / IB_BINARY).chmod(0o755)
    return write_manifest(folder, game="I", version=version,
                          source={"name": Path(binary).name, "sha256": sha256(binary)})


def install_image(folder: Path, target: Path) -> None:
    """Copy an image's files and folders (not its manifest) to target, which
    must not exist. The image is checked first; a changed one is refused."""
    folder, target = Path(folder), Path(target)
    _require_clean(folder, folder.name)
    if target.exists():
        raise ImageError(f"{target} already exists")
    shutil.copytree(folder, target, symlinks=False,
                    ignore=lambda d, names: [MANIFEST] if Path(d) == folder else [])


def waiting_archives(config: dict) -> list[Path]:
    """Image archives dropped into `[games] images` and not yet imported."""
    images = games_config(config).images
    if not images.is_dir():
        return []
    return sorted(p for p in images.iterdir()
                  if p.is_file() and p.name.endswith(ARCHIVE) and not p.name.startswith("."))


def image_folder(config: dict, image: GameImage) -> Path:
    return games_config(config).images / image.dirname


def leftover_incoming(config: dict) -> list[Path]:
    """Unpack folders an interrupted import left behind."""
    images = games_config(config).images
    if not images.is_dir():
        return []
    return [p for p in images.iterdir() if p.name.startswith(INCOMING)]


def delete_image(db: Session, config: dict, image: GameImage) -> Optional[str]:
    """Remove an image no league uses. Returns why not, or None once done.
    Flushes, does not commit."""
    users = sorted(g.league.full_id for g in image.games)
    if users:
        return f"{image.dirname} is used by {', '.join(users)}"
    shutil.rmtree(image_folder(config, image), ignore_errors=True)
    db.delete(image)
    db.flush()
    return None
