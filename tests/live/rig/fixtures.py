"""Capture and restore pristine game trees.

Game state is mutable and cumulative -- empires grow, turns advance, GAME.DAT
changes every run -- so without a restore-to-known-state step no scenario is
repeatable and no failure is reproducible. A restore is a true reset: outbound
filenames and sequence numbers repeat exactly across runs.
"""
import shutil
import subprocess
from pathlib import Path

from rig.layout import FIXTURES, League, Node


def archive_for(league: League, node: Node, tag: str = "pristine") -> Path:
    return FIXTURES / f"{league.dirname(node.index)}_{tag}.tar.zst"


def capture(league: League, node: Node, tag: str = "pristine") -> Path:
    target = league.install_path(node)
    if not target.is_dir():
        raise FileNotFoundError(f"no such install: {target}")

    # inuse.flg is a runtime mutex. Capturing one would poison every restore made
    # from this fixture -- the game would refuse to start, exiting 1 and printing
    # nothing about why.
    (target / "inuse.flg").unlink(missing_ok=True)

    archive = archive_for(league, node, tag)
    archive.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["tar", "--zstd", "-C", str(target.parent), "-cf", str(archive), target.name],
        check=True,
    )
    return archive


def restore(league: League, node: Node, tag: str = "pristine") -> Path:
    archive = archive_for(league, node, tag)
    if not archive.is_file():
        raise FileNotFoundError(f"no such fixture: {archive}")

    target = league.install_path(node)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["tar", "--zstd", "-C", str(target.parent), "-xf", str(archive)], check=True
    )
    (target / "inuse.flg").unlink(missing_ok=True)
    return target


def captured_on(league: League, node: Node, tag: str = "pristine"):
    """The date this fixture was captured, or None if it does not exist."""
    from datetime import date

    archive = archive_for(league, node, tag)
    if not archive.exists():
        return None
    return date.fromtimestamp(archive.stat().st_mtime)


def stale(tag: str = "pristine"):
    """Installs whose fixture was not captured today.

    A pristine fixture is a virgin game *on its first day*, and that is only what
    it restores to on the day it was captured. The games derive a packet's
    sequence number from how many game days have passed since RESET, so a fixture
    taken yesterday restores to a game whose next packet is .002, not .001 -- the
    tree is identical, the game state is not.

    This is not a property the rig can restore by copying files. The game start
    date lives inside GAME.DAT, and the only supported way to move the date the
    game sees is REDATE.COM, a TSR that would have to be loaded ahead of every
    invocation -- including the ones the hub's own product code launches, which
    the rig does not get to write. So the fixture is rebuilt instead.
    """
    from datetime import date
    from rig.layout import installs

    today = date.today()
    return [
        (league, node)
        for league, node in installs()
        if captured_on(league, node, tag) != today
    ]


def exists(league: League, node: Node, tag: str = "pristine") -> bool:
    return archive_for(league, node, tag).is_file()
