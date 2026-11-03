"""The rig's golden images.

Every BRE and FE install on the rig, the hub's and the nodes', is made from an
image built from the rig's media by make_golden's own recipe
(backend/services/golden.py), so the nightly run plays the images a hub is
given rather than a copy of them made another way.

An image is built once, by whoever runs rig.build, and kept beside the
fixtures. It is rebuilt when the recipe changes (the version names it) or the
media does (the manifest records the media's sha256).
"""
import json
import sys
import tarfile
import tempfile
from pathlib import Path

from rig.layout import DOSEMU_CONF, FIXTURES, LOGS, REPO, Game

sys.path.insert(0, str(REPO))

from backend.services import golden  # noqa: E402
from backend.services.game_images import (  # noqa: E402
    MANIFEST, extract_archive, image_dirname, install_image, sha256,
)

IMAGES = FIXTURES / "images"


def dirname(game: Game) -> str:
    return image_dirname(game.code, golden.default_version(game.media))


def archive(game: Game) -> Path:
    return IMAGES / f"{dirname(game)}.tar.gz"


def _made_from(path: Path) -> str:
    with tarfile.open(path, "r:gz") as tar:
        manifest = json.load(tar.extractfile(f"{path.name[:-len('.tar.gz')]}/{MANIFEST}"))
    return (manifest.get("source") or {}).get("sha256", "")


def ensure(game: Game) -> Path:
    """The game's image archive, built first if it is missing or its media
    has changed. Runs dosemu as the current user, in their own drive_c."""
    path = archive(game)
    if path.is_file() and _made_from(path) == sha256(game.media):
        return path
    path.unlink(missing_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)
    IMAGES.chmod(0o755)
    dos = golden.Dosemu(drive_c=Path.home() / ".dosemu" / "drive_c", conf=DOSEMU_CONF,
                        log_dir=LOGS)
    print(f"== building golden image {path.name} from {game.media.name}")
    made = golden.build_dos_image(game.code, game.media, IMAGES, dos)
    # The node users install from it too. The rig's media is as readable.
    made.chmod(0o644)
    return made


def install(game: Game, target: Path) -> None:
    """Make target (which must not exist) a copy of the game's image."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        install_image(extract_archive(archive(game), Path(tmp)), target)
