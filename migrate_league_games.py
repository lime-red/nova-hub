#!/usr/bin/env python3
"""
Move a hub's leagues' games out of config.toml and into the database.

Until league_games, config.toml said where each league's game was, in a
[dosemu.<league>.<bre|fe>] or [ib.<league>] section of folders and commands.
The hub now keeps that as a league_games row naming one folder, B015, F015 or
I015, in `[games] root`, and refuses to start while a league section is left.
For each section this:

  1. checks its league is in the database, its folder exists, and its folders
     and commands are the ones the hub now derives (the game's own INBOUND,
     OUTBOUND and BULLETIN; the game's own commands) -- anything else would
     change what runs, so it is refused, not converted;
  2. moves the folder to <root>/<letter><number>;
  3. points the game at its new place: bbs.cfg lines 4 and 5 for BRE and FE;
     for Immortal Barons, bbs.cfg GameInbound/GameOutbound if they name a path
     inside the folder, and config.json's DataDir. Each file's old copy is kept
     beside it as <name>.pre-league-games;
  4. for Immortal Barons, copies the binary into [games] images as an image
     (ib-<version>), and keeps hub_board_key on the row;
  5. writes the league_games rows, ready;
  6. replaces the sections in config.toml with [games] (the old file is kept
     as config.toml.pre-league-games);
  7. runs preflight on every league.

Before each change it writes the change's reverse to league-games-undo.sh,
beside config.toml, so a failure at any point -- preflight included -- is
undone by running that script, which this does itself. Keep the script until
the hub has run cleanly; run it by hand (hub stopped) to go back.

Stop the hub and run `alembic upgrade head` first. Dry run by default:

    .venv/bin/python migrate_league_games.py
    .venv/bin/python migrate_league_games.py --apply

[games] drive_c is worked out from the BRE/FE sections (game_folder against
game_dos_path) and root defaults to <drive_c>/leagues; --drive-c and --root
override. An Immortal Barons binary's version is taken from its release folder
(.../v0.2.6/immortal-barons, through a `current` link); --ib-version otherwise.
"""

import argparse
import difflib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import toml
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).parent))

from backend.models.database import League, LeagueGame
from backend.services import preflight
from backend.services.game_images import ib_image_dirname, import_ib_image
from backend.services.games import DOS_COMPLETION_MARKER, IB_ARGS, Game, game_for_code
from backend.services.league_games import (
    GamesConfig,
    GamesConfigError,
    child,
    dos_path,
    folder_name,
    games_config,
    ib_bbs_cfg,
    legacy_sections,
)

KEPT = ".pre-league-games"
UNDO = "league-games-undo.sh"
# A setting whose value the printed diff must not show: the dry run's output
# ends up in terminals, transcripts and tickets.
SECRET = re.compile(r"^(\s*#?\s*\w*(?:secret|password|token|key|credentials)\w*\s*=\s*)\S.*$",
                    re.IGNORECASE)


def _masked(text: str) -> list[str]:
    return [SECRET.sub(r'\1"(hidden)"', line) for line in text.splitlines(keepends=True)]

DOS_KEYS = {"game_folder", "game_dos_path", "inbound_folder", "outbound_folder",
            "scores_folder", "completion_marker", "processing_command", "scores_command",
            "routeinfo_command", "bbsinfo_command", "reset_command"}
DOS_SUBDIRS = (("inbound_folder", "INBOUND"), ("outbound_folder", "OUTBOUND"),
               ("scores_folder", "BULLETIN"))
IB_KEYS = {"binary", "data_dir", "inbound_dir", "outbound_dir", "args", "timeout",
           "hub_board_key"}
_RELEASE = re.compile(r"^v?\d+(\.\d+)+([.-][A-Za-z0-9.]+)?$")


class Refused(Exception):
    pass


@dataclass
class Move:
    """One league's game: where it is, where it goes, what changes in it."""
    league: League
    section: str  # as written in config.toml
    old: Path
    new: Path
    edits: dict[str, tuple[bytes, bytes]] = field(default_factory=dict)  # name: (old, new)
    ib_binary: Optional[Path] = None
    ib_version: Optional[str] = None
    hub_board_key: Optional[str] = None
    notes: list[str] = field(default_factory=list)


# ── working out what to do ────────────────────────────────────────────────

def _same(a, b) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


def _norm(command: str) -> str:
    return " ".join(str(command).split()).upper()


def _under(dos: str, prefix: str) -> bool:
    d, p = dos.strip().lower(), prefix.strip().rstrip("\\").lower()
    return d == p or d.startswith(p + "\\")


def _device(path: Path) -> int:
    while not path.exists():
        path = path.parent
    return path.stat().st_dev


def find_drive_c(config: dict) -> Optional[Path]:
    """drive_c from the BRE/FE sections: game_folder with game_dos_path's
    components taken off the end. None if no section says, or they disagree."""
    found = set()
    for number, games in ((config.get("dosemu") or {}).items()):
        if not isinstance(games, dict):
            continue
        for s in games.values():
            if not isinstance(s, dict) or not s.get("game_folder") or not s.get("game_dos_path"):
                continue
            m = re.match(r"^[Cc]:\\?(.*)$", s["game_dos_path"].strip())
            folder = Path(s["game_folder"])
            parts = [p for p in m.group(1).split("\\") if p] if m else None
            if not parts or len(parts) >= len(folder.parts):
                continue
            if [p.lower() for p in folder.parts[-len(parts):]] == [p.lower() for p in parts]:
                found.add(Path(*folder.parts[:-len(parts)]))
    return found.pop() if len(found) == 1 else None


def _league(db: Session, number: str, letter: str) -> Optional[League]:
    return db.query(League).filter(League.league_id == number,
                                   League.game_type == letter).first()


def _dos_move(db: Session, cfg: GamesConfig, number: str, code: str, s: dict,
              problems: list[str]) -> Optional[Move]:
    section = f"[dosemu.{number}.{code}]"
    try:
        game: Game = game_for_code(code)
    except KeyError:
        problems.append(f"{section}: {code!r} is not a game")
        return None
    league = _league(db, number, game.letter)
    if league is None:
        problems.append(f"{section}: league {number}{game.letter} is not in the database; "
                        f"create it, or delete the section")
        return None
    if league.install is not None:
        problems.append(f"{section}: {league.full_id} already has a game ({league.install.folder})")
        return None
    unknown = sorted(set(s) - DOS_KEYS)
    if unknown:
        problems.append(f"{section}: {', '.join(unknown)} is not something the hub knows; "
                        f"nothing would take its place, so it is not dropped silently")
    for what in ("processing", "scores", "routeinfo", "bbsinfo", "reset"):
        key = f"{what}_command"
        if key in s and _norm(s[key]) != _norm(game.command(what)):
            problems.append(f"{section}: {key} is {s[key]!r}; the hub now always runs "
                            f"{game.command(what)!r}")
    if "completion_marker" in s and s["completion_marker"] != DOS_COMPLETION_MARKER:
        problems.append(f"{section}: completion_marker is {s['completion_marker']!r}; the hub "
                        f"now always looks for {DOS_COMPLETION_MARKER!r}")
    if not s.get("game_folder"):
        problems.append(f"{section}: no game_folder")
        return None
    old = Path(s["game_folder"])
    if not old.is_dir():
        problems.append(f"{section}: {old} is not a folder")
        return None
    for key, sub in DOS_SUBDIRS:
        if key in s and not _same(s[key], child(old, sub)):
            problems.append(f"{section}: {key} is {s[key]}, not {old}/{sub}; the hub now uses "
                            f"the game's own {sub}")
    move = Move(league, section, old, cfg.root / folder_name(league))
    try:
        new_dos = dos_path(cfg, move.new)
        old_dos = s.get("game_dos_path") or dos_path(cfg, old)
    except GamesConfigError as e:
        problems.append(f"{section}: {e}")
        return move
    bbs = child(old, "bbs.cfg")
    try:
        before = bbs.read_bytes()
    except OSError as e:
        problems.append(f"{section}: {bbs} cannot be read ({e})")
        return move
    lines = before.decode("latin-1").splitlines(keepends=True)
    if len(lines) < 5 or not _under(lines[3], old_dos):
        problems.append(f"{section}: bbs.cfg line 4 ({lines[3].strip() if len(lines) > 3 else ''})"
                        f" is not inside the game's folder {old_dos}; fix it first")
        return move
    for i in (3, 4):
        text = lines[i].rstrip("\r\n")
        if _under(text, old_dos):
            rest = text.strip()[len(old_dos.rstrip("\\")):]
            lines[i] = new_dos + rest.upper() + lines[i][len(text):]
    move.edits[bbs.name] = (before, "".join(lines).encode("latin-1"))
    return move


def _ib_version(binary: Path, given: Optional[str]) -> Optional[str]:
    if given:
        return given
    release = Path(os.path.realpath(binary)).parent.name
    return release if _RELEASE.match(release) else None


def _ib_move(db: Session, cfg: GamesConfig, config: dict, number: str, s: dict,
             ib_version: Optional[str], problems: list[str]) -> Optional[Move]:
    section = f"[ib.{number}]"
    league = _league(db, number, "I")
    if league is None:
        problems.append(f"{section}: league {number}I is not in the database; create it, "
                        f"or delete the section")
        return None
    if league.install is not None:
        problems.append(f"{section}: {league.full_id} already has a game ({league.install.folder})")
        return None
    unknown = sorted(set(s) - IB_KEYS)
    if unknown:
        problems.append(f"{section}: {', '.join(unknown)} is not something the hub knows; "
                        f"nothing would take its place, so it is not dropped silently")
    if "args" in s:
        args = shlex.split(s["args"]) if isinstance(s["args"], str) else list(s["args"])
        if args != list(IB_ARGS):
            problems.append(f"{section}: args is {s['args']!r}; the hub now always runs "
                            f"{' '.join(IB_ARGS)}")
    if not s.get("data_dir") or not s.get("binary"):
        problems.append(f"{section}: needs data_dir and binary")
        return None
    old, binary = Path(s["data_dir"]), Path(s["binary"])
    if not old.is_dir():
        problems.append(f"{section}: {old} is not a folder")
        return None
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        problems.append(f"{section}: {binary} is not an executable file")
    move = Move(league, section, old, cfg.root / folder_name(league), ib_binary=binary,
                hub_board_key=s.get("hub_board_key"))
    move.ib_version = _ib_version(binary, ib_version)
    if move.ib_version is None:
        problems.append(f"{section}: cannot tell {binary}'s version from its folder; "
                        f"give it with --ib-version")
    else:
        try:
            ib_image_dirname(move.ib_version)
        except ValueError as e:
            problems.append(f"{section}: {e}")
    timeout = (config.get("dosemu") or {}).get("timeout")
    if "timeout" in s and int(s["timeout"]) != int(timeout or 300):
        move.notes.append(f"timeout {s['timeout']} is dropped; [dosemu] timeout ({timeout}) "
                          f"applies to every game now")

    bbs_path = child(old, "bbs.cfg")
    bbs = ib_bbs_cfg(old)
    if not bbs:
        problems.append(f"{section}: {bbs_path} cannot be read")
        return move
    rewrite = {}
    for key, legacy, default in (("GameInbound", "inbound_dir", "inbound"),
                                 ("GameOutbound", "outbound_dir", "outbound")):
        value = Path(bbs.get(key) or default)
        now = value if value.is_absolute() else old / value
        if legacy in s:
            was = Path(s[legacy]) if Path(s[legacy]).is_absolute() else old / s[legacy]
            if not _same(was, now):
                problems.append(f"{section}: {legacy} is {s[legacy]}, but bbs.cfg {key} is "
                                f"{bbs.get(key) or default}; the hub now follows bbs.cfg, so "
                                f"make them agree first")
        if value.is_absolute():
            try:
                rewrite[key] = Path(os.path.realpath(value)).relative_to(os.path.realpath(old))
            except ValueError:
                move.notes.append(f"bbs.cfg {key} {value} is outside the folder and stays there")
    if rewrite:
        before = bbs_path.read_bytes()
        lines = before.decode("utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            key = line.strip().partition(" ")[0]
            if key in rewrite:
                end = line[len(line.rstrip("\r\n")):]
                lines[i] = f"{key} {rewrite[key].as_posix()}{end}"
        move.edits[bbs_path.name] = (before, "".join(lines).encode("utf-8"))

    config_json = child(old, "config.json")
    if config_json.is_file():
        before = config_json.read_bytes()
        text = before.decode("utf-8")
        m = re.search(r'("DataDir"\s*:\s*)("(?:[^"\\]|\\.)*")', text)
        if m and _same(json.loads(m.group(2)), old):
            after = text[:m.start(2)] + json.dumps(str(move.new)) + text[m.end(2):]
            move.edits[config_json.name] = (before, after.encode("utf-8"))
        elif m:
            move.notes.append(f"config.json DataDir is {json.loads(m.group(2))}, not this "
                              f"folder; left as it is")
    return move


_HEADER = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")


def _drop_trailing_paragraph(out: list[str]) -> None:
    """Drop trailing blank lines, and a comment paragraph standing on its own
    above them (one with a blank line before it): it was about what follows.
    Comments directly under a setting stay with it."""
    end = len(out)
    while end and not out[end - 1].strip():
        end -= 1
    start = end
    while start and out[start - 1].lstrip().startswith("#"):
        start -= 1
    if start < end and (start == 0 or not out[start - 1].strip()):
        end = start
        while end and not out[end - 1].strip():
            end -= 1
    del out[end:]


def rewrite_config(text: str, games_block: Optional[str]) -> str:
    """config.toml without its league sections, and with games_block where the
    first of them was. A section runs from its header to the next; a comment
    paragraph just above a dropped section goes with it."""
    out: list[str] = []
    dropping = False
    for line in text.splitlines(keepends=True):
        m = _HEADER.match(line)
        if m:
            parts = [p.strip().strip("\"'") for p in m.group(1).split(".")]
            drop = (parts[0] == "dosemu" and len(parts) > 1) or parts[0] == "ib"
            if drop and not dropping:
                _drop_trailing_paragraph(out)
                if out:
                    out.append("\n")
                if games_block is not None:
                    out.append(games_block + "\n")
                    games_block = None
            dropping = drop
        if not dropping:
            out.append(line)
    if games_block is not None:
        out.append("\n" + games_block)
    return "".join(out)


def _games_block(cfg: GamesConfig) -> str:
    return ("# Where the hub's own copy of each league's game lives: one folder per league\n"
            "# in root (B015, F015, I015). See config.toml.example.\n"
            f"[games]\nroot = {json.dumps(str(cfg.root))}\n"
            f"drive_c = {json.dumps(str(cfg.drive_c))}\n")


def _without_legacy(config: dict) -> dict:
    out = json.loads(json.dumps(config))
    dosemu = out.get("dosemu") or {}
    for k in [k for k, v in dosemu.items() if isinstance(v, dict)]:
        del dosemu[k]
    out.pop("ib", None)
    return out


def plan(db: Session, config: dict, text: str, *, root: Optional[str], drive_c: Optional[str],
         ib_version: Optional[str]) -> tuple[list[Move], str, list[str]]:
    """The moves and the new config.toml text, or the problems that stop them."""
    problems: list[str] = []
    section = config.get("games") or {}
    have = games_config(config)
    drive_c = Path(drive_c) if drive_c else have.drive_c or find_drive_c(config)
    if drive_c is None:
        raise Refused("cannot tell where dosemu's C: is from the [dosemu] sections; give it "
                      "with --drive-c")
    root = Path(root) if root else have.root or drive_c / "leagues"
    for key, value in (("root", root), ("drive_c", drive_c)):
        if section.get(key) and Path(section[key]) != value:
            raise Refused(f"config.toml already has [games] {key} = {section[key]}, not {value}")
    cfg = GamesConfig(root=root, drive_c=drive_c, images=have.images)
    try:
        dos_path(cfg, root)
    except GamesConfigError as e:
        raise Refused(f"[games] root: {e}")

    moves: list[Move] = []
    for number, games in sorted((config.get("dosemu") or {}).items()):
        if isinstance(games, dict):
            for code, s in sorted(games.items()):
                if isinstance(s, dict):
                    moves.append(_dos_move(db, cfg, number, code, s, problems))
    for number, s in sorted((config.get("ib") or {}).items()):
        if isinstance(s, dict):
            moves.append(_ib_move(db, cfg, config, number, s, ib_version, problems))
    moves = [m for m in moves if m is not None]

    for m in moves:
        if m.new.exists() or m.new.is_symlink():
            problems.append(f"{m.section}: {m.new} already exists")
        if _device(m.old) != _device(root):
            problems.append(f"{m.section}: {m.old} is on another filesystem from {root}; "
                            f"a move would be a copy, and this does not copy games")
        if _same(m.old, root) or Path(os.path.realpath(root)).is_relative_to(
                os.path.realpath(m.old)):
            problems.append(f"{m.section}: [games] root {root} is inside {m.old}")

    new_text = rewrite_config(text, None if section.get("root") else _games_block(cfg))
    new_config = toml.loads(new_text)
    want = _without_legacy(config)
    want.setdefault("games", {}).update({"root": str(root), "drive_c": str(drive_c)})
    if legacy_sections(new_config) or new_config != want:
        problems.append("config.toml could not be rewritten cleanly (an inline table, or a "
                        "section written in an unusual way?); move its league sections into "
                        "plain [dosemu.N.game] / [ib.N] form and try again")
    return moves, new_text, problems


# ── doing it ──────────────────────────────────────────────────────────────

class Undo:
    """league-games-undo.sh, rewritten after every step with the reverse of
    everything done so far, newest first."""

    def __init__(self, path: Path):
        self.path = path
        self.steps: list[tuple[str, str]] = []

    def add(self, what: str, command: str) -> None:
        self.steps.append((what, command))
        lines = ["#!/bin/sh",
                 "# Undoes migrate_league_games.py. Run it with the hub stopped.",
                 "set -eu"]
        for what, command in reversed(self.steps):
            lines += ["", f"# {what}", command]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.chmod(0o755)
        tmp.replace(self.path)

    def run(self) -> int:
        print(f"Undoing, with {self.path}")
        return subprocess.run(["sh", str(self.path)]).returncode


def _q(p) -> str:
    return shlex.quote(str(p))


def apply(db: Session, db_path: Path, config_path: Path, config: dict, moves: list[Move],
          new_text: str, undo: Undo) -> None:
    root = games_config(toml.loads(new_text)).root
    if not root.exists():
        root.mkdir(parents=True)
        undo.add(f"remove {root}, made for the games", f"rmdir {_q(root)}")
    for m in moves:
        os.rename(m.old, m.new)
        undo.add(f"{m.section}: move the game back", f"mv {_q(m.new)} {_q(m.old)}")
        print(f"{m.league.full_id}: moved {m.old} to {m.new}")
        for name, (before, after) in m.edits.items():
            path, kept = m.new / name, m.new / (name + KEPT)
            if path.read_bytes() != before:
                raise Refused(f"{path} changed while this was running")
            shutil.copy2(path, kept)
            undo.add(f"{m.section}: put back {name}", f"mv {_q(kept)} {_q(path)}")
            path.write_bytes(after)
            print(f"{m.league.full_id}: rewrote {name} (old copy {kept.name})")

    images = []
    for m in moves:
        image_id = None
        if m.ib_binary is not None:
            image, created = import_ib_image(db, config, m.ib_binary, m.ib_version)
            if created:
                folder = games_config(config).images / image.dirname
                undo.add(f"{m.section}: remove image {image.dirname}", f"rm -rf {_q(folder)}")
                images.append(image.id)
                print(f"{m.league.full_id}: image {image.dirname} from {m.ib_binary}")
            image_id = image.id
        db.add(LeagueGame(league_id=m.league.id, folder=m.new.name, image_id=image_id,
                          hub_board_key=m.hub_board_key, state="ready"))
    db.commit()
    leagues = ", ".join(str(m.league.id) for m in moves) or "NULL"
    images_sql = ", ".join(map(str, images)) or "NULL"
    undo.add("delete the league_games rows and images this made",
             f"{_q(sys.executable)} - {_q(db_path)} <<'PY'\n"
             f"import sqlite3, sys\n"
             f"c = sqlite3.connect(sys.argv[1])\n"
             f"c.execute('DELETE FROM league_games WHERE league_id IN ({leagues})')\n"
             f"c.execute('DELETE FROM game_images WHERE id IN ({images_sql})')\n"
             f"c.commit()\n"
             f"PY")

    kept = config_path.with_name(config_path.name + KEPT)
    shutil.copy2(config_path, kept)
    undo.add("put back config.toml", f"mv {_q(kept)} {_q(config_path)}")
    config_path.write_text(new_text)
    print(f"config.toml: league sections replaced by [games] (old copy {kept.name})")


def _hub_answers(config: dict) -> bool:
    port = int((config.get("server") or {}).get("port", 8000))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def run(config_path: Path, *, do_apply: bool, root: Optional[str] = None,
        drive_c: Optional[str] = None, ib_version: Optional[str] = None,
        keep: bool = False, check_hub: bool = True) -> int:
    config_path = Path(config_path).resolve()
    text = config_path.read_text()
    config = toml.loads(text)
    if not legacy_sections(config):
        print("config.toml has no league sections; nothing to move")
        return 0
    if do_apply and check_hub and _hub_answers(config):
        print(f"Something answers on port {config.get('server', {}).get('port', 8000)}: stop "
              f"the hub first", file=sys.stderr)
        return 2
    db_path = Path((config.get("database") or {}).get("path", "./data/nova-hub.db")).resolve()
    engine = create_engine(f"sqlite:///{db_path}")
    if "league_games" not in inspect(engine).get_table_names():
        print("The database has no league_games table: run `alembic upgrade head` first",
              file=sys.stderr)
        return 2
    db = sessionmaker(bind=engine)()
    try:
        try:
            moves, new_text, problems = plan(db, config, text, root=root, drive_c=drive_c,
                                             ib_version=ib_version)
        except Refused as e:
            print(f"Refused: {e}", file=sys.stderr)
            return 1
        undo_path = config_path.with_name(UNDO)
        for leftover in (undo_path, config_path.with_name(config_path.name + KEPT)):
            if leftover.exists():
                problems.append(f"{leftover} is left from an earlier run; run or remove it")

        for m in moves:
            print(f"{m.section} -> {m.league.full_id}: {m.old} -> {m.new}")
            for name, (before, after) in m.edits.items():
                for a, b in zip(before.decode("latin-1").splitlines(),
                                after.decode("latin-1").splitlines()):
                    if a != b:
                        print(f"    {name}: {a.strip()}  ->  {b.strip()}")
            if m.ib_binary:
                print(f"    image ib-{m.ib_version} from {m.ib_binary}")
            for note in m.notes:
                print(f"    note: {note}")
        print()
        sys.stdout.writelines(line if line.endswith("\n") else line + "\n"
                              for line in difflib.unified_diff(
                                  _masked(text), _masked(new_text),
                                  "config.toml", "config.toml (new)"))
        if problems:
            print("\nNothing was changed:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        if not do_apply:
            print("\nDry run; nothing was changed. --apply to do it.")
            return 0

        undo = Undo(undo_path)
        try:
            apply(db, db_path, config_path, config, moves, new_text, undo)
        except Exception as e:
            db.rollback()
            print(f"\nFailed: {e}", file=sys.stderr)
            undo.run()
            return 1

        db.expire_all()
        results = preflight.run(db, toml.loads(new_text))
        failed = {k: v for k, v in results.items() if v}
        for full_id, problems in sorted(results.items()):
            print(f"preflight {full_id}: " + ("ok" if not problems else "; ".join(problems)))
        if failed and not keep:
            print("\nPreflight failed, so the move is undone. Fix what it says (or pass --keep "
                  "to keep the move and fix it afterwards).", file=sys.stderr)
            db.close()
            undo.run()
            return 1
        print(f"\nDone. {undo_path} undoes it (hub stopped); keep it until the hub has run "
              f"cleanly.")
        return 0
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--apply", action="store_true", help="make the changes")
    parser.add_argument("--root", help="[games] root (default <drive_c>/leagues)")
    parser.add_argument("--drive-c", help="[games] drive_c: what dosemu calls C:")
    parser.add_argument("--ib-version", help="the Immortal Barons binary's version, as v0.2.6")
    parser.add_argument("--keep", action="store_true",
                        help="keep the move even if preflight fails afterwards")
    args = parser.parse_args(argv)
    return run(Path(args.config), do_apply=args.apply, root=args.root, drive_c=args.drive_c,
               ib_version=args.ib_version, keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
