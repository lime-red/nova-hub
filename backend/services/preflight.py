"""Preflight: can a league's game touch another league's packets?

The games ignore packets for other leagues in their INBOUND; they are built to
share one mailer inbound and pick out their own. So the danger is not a shared
folder but a game that believes it is a different league: it takes that
league's packets wherever its inbound points, and writes packets under that
league's number, which the hub would deliver to that league's members. These
checks look for every way that can be set up by mistake:

- every league's folder, INBOUND and OUTBOUND are its own: no two leagues'
  equal, and none inside another league's folder;
- the game's bbs.cfg names this league: its number, its hub address, and for a
  DOS game an inbound line that resolves (through [games] drive_c) to this
  league's INBOUND, every component 8.3;
- the nodes file matches the league's memberships;
- an image's files match its manifest.

Run at hub startup for every league, by run.py --validate, and on demand from
the league page. The result is kept on the league_games row.
"""

import json
import os
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.logging_config import get_logger
from backend.models.database import League
from backend.services.game_images import MANIFEST, sha256
from backend.services.league_games import (
    GameInstall,
    IB_BINARY,
    child,
    games_config,
    host_path,
    ib_bbs_cfg,
    is_dos_name,
    league_game,
)

logger = get_logger(context="preflight")

def _real(p: Path) -> Path:
    return Path(os.path.realpath(p))


def _inside(p: Path, folder: Path) -> bool:
    return p == folder or folder in p.parents


def _overlaps(install: GameInstall, others: list[GameInstall]) -> list[str]:
    def paths(i: GameInstall) -> dict:
        return {"folder": _real(i.folder), "INBOUND": _real(i.inbound),
                "OUTBOUND": _real(i.outbound)}

    mine = paths(install)
    problems = []
    for other in others:
        theirs = paths(other)
        for name, path in mine.items():
            same = next((n for n, q in theirs.items() if q == path), None)
            if same:
                problems.append(f"{name} {path} is also {other.full_id}'s {same}")
            elif _inside(path, theirs["folder"]):
                problems.append(f"{name} {path} is inside {other.full_id}'s folder")
            else:
                continue
            break
        else:
            if _inside(theirs["folder"], mine["folder"]):
                problems.append(f"{other.full_id}'s folder {theirs['folder']} is inside this "
                                f"league's folder")
    return problems


def _case_twins(folder: Path) -> list[str]:
    """Names in the folder that differ only in case: two files on the host, one
    to DOS, which shows the game only one of them (BBS.CFG left from a donor
    install beside the bbs.cfg written for this league, say)."""
    names: dict[str, list[str]] = {}
    try:
        for p in folder.iterdir():
            names.setdefault(p.name.lower(), []).append(p.name)
    except OSError:
        return []
    return [f"{' and '.join(sorted(n))} in {folder} are one name to DOS; remove the one "
            f"that is not this league's" for n in names.values() if len(n) > 1]


def _dos_bbs_cfg(config: dict, league: League, install: GameInstall) -> list[str]:
    path = child(install.folder, "bbs.cfg")
    try:
        lines = path.read_bytes().decode("latin-1").replace("\r\n", "\n").split("\n")
    except OSError:
        return [f"{path} cannot be read"]
    if len(lines) < 6:
        return [f"{path} has {len(lines)} lines; a bbs.cfg has 7"]
    problems = []
    address, inbound, number = lines[2].strip(), lines[3].strip(), lines[5].strip()
    if not number.isdigit() or int(number) != int(league.league_id):
        problems.append(f"bbs.cfg line 6 says league {number!r}, not {league.league_id}")
    if league.hub_fidonet_address and address != league.hub_fidonet_address.strip():
        problems.append(f"bbs.cfg line 3 says {address!r}; the hub's address in this league "
                        f"is {league.hub_fidonet_address}")
    parts = [p for p in inbound.split("\\")[1:] if p]
    bad = [p for p in parts if not is_dos_name(p)]
    if bad:
        problems.append(f"bbs.cfg line 4 ({inbound}): {', '.join(bad)} is not a DOS 8.3 name; "
                        f"the game would silently read nothing")
    resolved = host_path(games_config(config), inbound)
    if resolved is None:
        problems.append(f"bbs.cfg line 4 ({inbound}) is not a path on C:")
    elif _real(resolved) != _real(install.inbound):
        problems.append(f"bbs.cfg line 4 ({inbound}) is {resolved}, not this league's "
                        f"INBOUND {install.inbound}")
    return problems


def _ib_bbs_cfg(league: League, install: GameInstall) -> list[str]:
    bbs = ib_bbs_cfg(install.folder)
    if not bbs:
        return [f"{install.folder}/bbs.cfg cannot be read"]
    number = bbs.get("LeagueNumber", "")
    problems = []
    if not number.isdigit() or int(number) != int(league.league_id):
        problems.append(f"bbs.cfg LeagueNumber is {number!r}, not {int(league.league_id)}")
    if install.binary is None:
        problems.append("no image, so no binary to run")
    elif not (install.binary.is_file() and os.access(install.binary, os.X_OK)):
        problems.append(f"{install.binary} is not an executable file")
    return problems


def _nodes_file(db: Session, config: dict, league: League, install: GameInstall) -> list[str]:
    from backend.services.validator import HubValidator
    v = HubValidator(db_session=db)
    v.config = config
    v.validate_nodes_file_against_db(install.game.code, league.league_id,
                                     {"game_folder": str(install.folder)})
    return [e.message for e in v.errors]


def check_image(config: dict, install: GameInstall, image) -> list[str]:
    """The image's own files against its manifest, and for a DOS game the
    install's copies of the files the game never writes (EXEs, GAME/)."""
    folder = games_config(config).images / image.dirname
    try:
        manifest = json.loads((folder / MANIFEST).read_text())
    except (OSError, ValueError) as e:
        return [f"image {image.dirname}: no readable {MANIFEST} ({e})"]
    problems = []
    for rel, want in (manifest.get("files") or {}).items():
        f = folder / rel
        if not f.is_file() or sha256(f) != want:
            problems.append(f"image {image.dirname}: {rel} does not match its manifest")
            continue
        fixed = rel.upper().endswith(".EXE") or rel.upper().startswith("GAME/")
        if install.game.dos and fixed:
            mine = install.folder
            for part in Path(rel).parts:
                mine = child(mine, part)
            if not mine.is_file() or sha256(mine) != want:
                problems.append(f"{mine} differs from image {image.dirname}")
    if not install.game.dos and IB_BINARY not in (manifest.get("files") or {}):
        problems.append(f"image {image.dirname}: its manifest has no {IB_BINARY}")
    return problems


def check(db: Session, config: dict, league: League,
          others: Optional[list[GameInstall]] = None) -> list[str]:
    """What is wrong with the league's game, as sentences for the admin."""
    install = league_game(config, league)
    if install is None:
        if league.install is not None:
            return ["[games] root is not set in config.toml"]
        return ["the hub has no game for this league"]
    if not install.folder.is_dir():
        return [f"{install.folder} does not exist"]
    if others is None:
        others = [i for i in (league_game(config, l) for l in db.query(League).all()
                              if l.id != league.id and l.install is not None) if i]
    problems = []
    for name in ("inbound", "outbound"):
        if not getattr(install, name).is_dir():
            problems.append(f"{getattr(install, name)} does not exist")
    problems += _overlaps(install, others)
    if install.game.dos:
        if install.dos_path is None:
            problems.append(f"{install.folder} has no DOS path: it must be inside [games] "
                            f"drive_c, with every name 8.3")
        problems += _case_twins(install.folder)
        problems += _dos_bbs_cfg(config, league, install)
        problems += _nodes_file(db, config, league, install)
    else:
        problems += _ib_bbs_cfg(league, install)
    if league.install.image is not None:
        problems += check_image(config, install, league.install.image)
    return problems


def run(db: Session, config: dict, leagues: Optional[list[League]] = None) -> dict[str, list[str]]:
    """Check every league with a game (or those given), keep each result on its
    row, and return {full id: problems}."""
    every = [l for l in db.query(League).all() if l.install is not None]
    installs = {l.id: league_game(config, l) for l in every}
    results = {}
    for league in (leagues if leagues is not None else every):
        if league.install is None:
            continue
        others = [i for lid, i in installs.items() if i is not None and lid != league.id]
        try:
            problems = check(db, config, league, others)
        except Exception as e:  # a check that crashes is a failed check, not a crash
            logger.exception(f"Preflight of {league.full_id} failed to run")
            problems = [f"preflight could not run: {e}"]
        row = league.install
        row.preflight_ok, row.preflight_at = not problems, utcnow()
        row.preflight_problems = problems
        results[league.full_id] = problems
        for p in problems:
            logger.error(f"Preflight {league.full_id}: {p}")
    db.commit()
    return results
