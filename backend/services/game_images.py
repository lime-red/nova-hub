"""Game images: the installs leagues' games are made from.

Each image is a folder in `[games] images` with a game_images row. Its
manifest.json records the sha256 of every file in it, so preflight can tell an
image (or a game's copy of its fixed files) that has been changed since.

An Immortal Barons image is just its binary, which the league's game runs in
place; a league is moved to a new version by pointing its row at another image
(game_image.py set). BRE and FE images come with the New League job.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.models.database import GameImage
from backend.services.league_games import IB_BINARY, games_config

MANIFEST = "manifest.json"
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(folder: Path) -> str:
    """Record the sha256 of every file in an image folder, in its manifest.json,
    and return the manifest's own sha256 (game_images.manifest_sha256)."""
    files = {}
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.name != MANIFEST:
            files[f.relative_to(folder).as_posix()] = sha256(f)
    text = json.dumps({"files": files}, indent=2, sort_keys=True) + "\n"
    (folder / MANIFEST).write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def ib_image_dirname(version: str) -> str:
    if not _VERSION.match(version):
        raise ValueError(f"{version!r} is not a version name (letters, digits, '.', '_', '-')")
    return f"ib-{version}"


def import_ib_image(db: Session, config: dict, binary: Path, version: str) -> tuple[GameImage, bool]:
    """Copy an Immortal Barons binary into images/ib-<version>/ and record it.

    Returns (image, created). Importing the same binary under the same version
    again gives the image already there; a different binary under a version
    already taken is refused, as is a folder with no row (a half-done import).
    Flushes, does not commit.
    """
    binary = Path(binary)
    if not binary.is_file():
        raise ValueError(f"{binary} is not a file")
    dirname = ib_image_dirname(version)
    folder = games_config(config).images / dirname
    existing = db.query(GameImage).filter(GameImage.dirname == dirname).first()
    if existing is not None:
        mine = folder / IB_BINARY
        if mine.is_file() and sha256(mine) == sha256(binary):
            return existing, False
        raise ValueError(f"image {dirname} already exists, with a different binary")
    if folder.exists():
        raise ValueError(f"{folder} exists but no image row names it; remove it first")
    folder.mkdir(parents=True)
    try:
        shutil.copy2(binary, folder / IB_BINARY)
        (folder / IB_BINARY).chmod(0o755)
        manifest = write_manifest(folder)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    image = GameImage(game_type="I", version=version, dirname=dirname, manifest_sha256=manifest,
                      source=str(binary.resolve()), created_at=utcnow())
    db.add(image)
    db.flush()
    return image, True
