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


def exists(league: League, node: Node, tag: str = "pristine") -> bool:
    return archive_for(league, node, tag).is_file()
