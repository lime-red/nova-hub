"""A deleted league's game, kept: its folder and its reset backups.

Deleting a league archives the hub's game for it rather than leaving the
folder in [games] root, where it would block that number being set up again
and sit among the live games. The archive is written before any row goes, and
the folder removed only once the delete has committed, so a delete that fails
leaves the game where it was:

    <data_dir>/league_archives/<015B>-<20261104T051500Z>/
        game.tar.gz        the game folder, B015/ inside
        reset_backups/     the league's backups, moved from data_dir/reset_backups

To bring a game back, extract game.tar.gz into [games] root.
"""
import shutil
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.logging_config import get_logger
from backend.services.league_games import child, games_config

logger = get_logger(context="league_archive")

ARCHIVES = "league_archives"


@dataclass
class Archive:
    path: Path                 # the league's archive folder
    folder: Optional[Path]     # the game folder it holds, removed by finish()
    backups: Optional[Path]    # the reset backups it took, put back by discard()

    def describe(self) -> str:
        what = [w for w, there in (("the game", self.folder), ("reset backups", self.backups))
                if there]
        return f"{' and '.join(what)} archived in {self.path}" if what else ""


def data_dir(config: dict) -> Path:
    return Path((config or {}).get("server", {}).get("data_dir", "./data"))


def archive(config: dict, full_id: str, game_folder: Optional[str],
            now: Optional[datetime] = None) -> Optional[Archive]:
    """Write league full_id's game folder (game_folder in [games] root, its
    league_games row's) and reset backups into a new archive. None if it has
    neither. Raises if the archive can't be written, having removed what it
    wrote. Files only: safe in a worker thread."""
    root = games_config(config).root
    folder = child(root, game_folder) if root is not None and game_folder else None
    if folder is not None and not folder.is_dir():
        folder = None
    backups = data_dir(config) / "reset_backups" / full_id
    if not backups.is_dir():
        backups = None
    if folder is None and backups is None:
        return None

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    path = data_dir(config) / ARCHIVES / f"{full_id}-{stamp}"
    path.mkdir(parents=True)
    try:
        if folder is not None:
            with tarfile.open(path / "game.tar.gz", "w:gz") as tar:
                tar.add(folder, arcname=folder.name)
        if backups is not None:
            shutil.move(str(backups), str(path / "reset_backups"))
    except BaseException:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return Archive(path=path, folder=folder, backups=backups)


def discard(archived: Optional[Archive]) -> None:
    """The delete failed: put the backups back and drop the archive."""
    if archived is None:
        return
    if archived.backups is not None and (archived.path / "reset_backups").is_dir():
        archived.backups.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(archived.path / "reset_backups"), str(archived.backups))
    shutil.rmtree(archived.path, ignore_errors=True)


def finish(archived: Optional[Archive]) -> None:
    """The delete committed: the game folder goes from [games] root."""
    if archived is None or archived.folder is None:
        return
    try:
        shutil.rmtree(archived.folder)
    except OSError as e:
        # The league is gone and its game is safe in the archive; the folder
        # left behind only blocks setting the number up again.
        logger.error(f"{archived.folder} is archived in {archived.path} but could not be "
                     f"removed: {e}")
