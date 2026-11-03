"""Setting up a new league's game from the console: New League's Set Up.

Two steps, and nothing runs in the game between them:

  setup        this module's job. The league row (disabled), its league_games
               row and the game itself: the image copied in as the league's
               folder, its bbs.cfg written, a nodelist with the hub alone put in
               it, and preflight's checks. The game ends `installed`.
  first reset  an ordinary Game Reset (game_reset/perform.py), sent by the admin
               with settings they chose. It finds the game installed, so it
               skips the backup, and after RESET (IB: -league-reset) it runs the
               game once with a canary for every other league of the same game
               in its INBOUND (canary_* below), then preflight in full. It ends
               `ready`; anything wrong and the folder is built again from the
               image (rebuild), still `installed`.

Until the game is ready and the league enabled, nothing else runs it: a packet
for it waits, Run Now refuses, schedules pass it by (league_games.runnable).
Settings the admin didn't choose never reach the league's history.

A setup that fails deletes the folder it made, and nothing else; the league is
left `failed` with its transcript, and Set Up can be pressed again. The folder
did not exist when it began (start() refuses one that does), so it is the
setup's own to delete.

The job is a GameReset row of kind "setup", run under the reset job's lock, so
it shows in the league's history with its progress and transcript, and no reset
runs while it does.
"""
import asyncio
import hashlib
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.core.database import get_session
from backend.logging_config import get_logger
from backend.models.database import GameImage, GameReset, League, LeagueGame, Packet
from backend.services import preflight
from backend.services.game_images import ImageError, check_image, image_folder, install_image
from backend.services.games import Game, game_for_letter
from backend.services.league_games import (
    IB_BINARY,
    GamesConfigError,
    child,
    dos_path,
    folder_name,
    games_config,
    league_game,
)

logger = get_logger(context="league_setup")

SETUP = "setup"
SYSOP = "Nova Admin"         # bbs.cfg line 1 on every hub game
IB_TIMEOUT = 120
KEY = re.compile(r"\b[0-9a-f]{64}\b")
CANARY_BYTES = b"nova-hub canary: a packet for another league, which this game must leave be\r\n"

_jobs: set = set()


class SetupRefused(Exception):
    """Set Up can't start. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class SetupFailed(Exception):
    """A step of building the game went wrong; the message is for the admin."""


def _now() -> datetime:
    return utcnow()


def _stage(db: Session, order: GameReset, stage: str):
    order.progress = [*(order.progress or []), {"stage": stage, "at": _now().isoformat()}]
    db.commit()


def hub_index(config: dict) -> int:
    try:
        return int(str((config.get("hub") or {}).get("bbs_index", "01")), 10)
    except ValueError:
        return 1


def hub_name(config: dict) -> str:
    return ((config.get("hub") or {}).get("bbs_name") or "Nova Hub").strip()


def default_address(config: dict, number: str) -> str:
    n = int(number)
    return f"{n}:{n}/{hub_index(config)}"


def newest_image(db: Session, game_type: str) -> Optional[GameImage]:
    return (db.query(GameImage).filter(GameImage.game_type == game_type)
            .order_by(GameImage.created_at.desc(), GameImage.id.desc()).first())


# ── starting one ──────────────────────────────────────────────────────────
def _not_busy(db: Session):
    busy = db.query(GameReset).filter(GameReset.status == "applying").first()
    if busy is not None:
        what = "setup" if busy.kind == SETUP else "reset or league order"
        raise SetupRefused(f"A {what} is already under way (league "
                           f"{busy.league.full_id if busy.league else busy.league_id}).", 409)


def _checked_image(db: Session, config: dict, game_type: str,
                   image_id: Optional[int]) -> GameImage:
    game = game_for_letter(game_type)
    image = db.get(GameImage, image_id) if image_id else newest_image(db, game_type)
    if image is None:
        raise SetupRefused(f"The hub has no {game.name} image. Add one on Game Images first."
                           if not image_id else "No such image.")
    if image.game_type != game_type:
        raise SetupRefused(f"{image.dirname} is not a {game.name} image.")
    problems = check_image(image_folder(config, image))
    if problems:
        raise SetupRefused(f"{image.dirname} doesn't match its manifest: " + "; ".join(problems))
    return image


def _checked_place(config: dict, game: Game, folder: str) -> Path:
    cfg = games_config(config)
    if cfg.root is None:
        raise SetupRefused("[games] root is not set in config.toml, so the hub has nowhere "
                           "to put a league's game.")
    target = cfg.root / folder
    if child(cfg.root, folder).exists():
        raise SetupRefused(f"{child(cfg.root, folder)} already exists. A new league's game "
                           f"goes in a folder of its own; move that one aside first.", 409)
    if game.dos:
        try:
            dos_path(cfg, target)
        except GamesConfigError as e:
            raise SetupRefused(f"The game could not be reached from dosemu: {e}")
    elif hub_index(config) != 1:
        raise SetupRefused("An Immortal Barons league's Coordinator is node 1, and the hub "
                           "is not: [hub] bbs_index must be 01.")
    return target


def start(db: Session, config: dict, *, number: str, game_type: str, name: str,
          hub_address: Optional[str], image_id: Optional[int], username: str,
          description: Optional[str] = None) -> GameReset:
    """Check everything that can be checked without making anything, then make
    the league (disabled), its game's row (provisioning) and the setup order.
    The caller audits, commits and schedules run_job()."""
    from backend.api.management.leagues import _check_hub_address_is_free

    number = (number or "").strip()
    if not re.fullmatch(r"\d{3}", number) or number == "000":
        raise SetupRefused("A league number is three digits, 001 to 999.")
    try:
        game = game_for_letter(game_type)
    except KeyError:
        raise SetupRefused(f"{game_type!r} is not a game the hub runs.")
    name = (name or "").strip()
    if not name:
        raise SetupRefused("Give the league a name.")
    # Unique within the game, not across games: each game reads only its own
    # packets (900B, 900F), so 015B, 015F and 015I live side by side on prod.
    taken = (db.query(League)
             .filter(League.league_id == number, League.game_type == game.letter).first())
    if taken is not None:
        raise SetupRefused(f"League {taken.full_id} already exists ({taken.name}).", 409)
    address = (hub_address or "").strip() or default_address(config, number)
    try:
        _check_hub_address_is_free(db, address)
    except Exception as e:  # an HTTPException with the reason
        raise SetupRefused(getattr(e, "detail", str(e)), 409)
    image = _checked_image(db, config, game.letter, image_id)
    _not_busy(db)
    league = League(league_id=number, game_type=game.letter, name=name,
                    description=(description or "").strip() or None,
                    hub_fidonet_address=address, hub_routes_mail=game.dos, is_active=False)
    _checked_place(config, game, folder_name(league))
    db.add(league)
    db.flush()
    db.add(LeagueGame(league_id=league.id, folder=folder_name(league), image_id=image.id,
                      state="provisioning"))
    return _order(db, league, username)


def again(db: Session, config: dict, league: League, username: str,
          image_id: Optional[int] = None) -> GameReset:
    """Set Up once more, for a league whose setup failed."""
    row = league.install
    if row is None or row.state != "failed":
        raise SetupRefused(f"{league.full_id}'s game is "
                           f"{row.state if row else 'not the hub’s'}; only a failed setup "
                           f"can be set up again.", 409)
    game = game_for_letter(league.game_type)
    image = _checked_image(db, config, game.letter, image_id or row.image_id)
    _not_busy(db)
    _checked_place(config, game, row.folder)
    row.image_id = image.id
    row.state = "provisioning"
    row.hub_board_key = None
    return _order(db, league, username)


def _order(db: Session, league: League, username: str) -> GameReset:
    now = _now()
    order = GameReset(league_id=league.id, status="applying", kind=SETUP, source="console",
                      settings={}, version=1, created_by=username, created_at=now,
                      updated_by=username, updated_at=now, applied_by=username, progress=[])
    db.add(order)
    db.flush()
    _stage(db, order, "queued")
    logger.info(f"Setup {order.id} of {league.full_id} started by {username}")
    return order


def schedule(order_id: int, config: dict) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(run_job(order_id, config))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    return task


# ── building the game ─────────────────────────────────────────────────────
def _crlf(*lines) -> bytes:
    return "".join(f"{line}\r\n" for line in lines).encode("latin-1", errors="replace")


def _ib(binary: Path, folder: Path, log: Callable[[str], None], *args) -> str:
    argv = [str(binary), *args, "-data", str(folder)]
    log(" ".join(argv))
    try:
        run = subprocess.run(argv, cwd=folder, stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=IB_TIMEOUT, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise SetupFailed(f"{args[0]} didn't finish within {IB_TIMEOUT}s")
    output = (run.stdout + run.stderr).strip()
    last = output.splitlines()[-1] if output else ""
    log(f"exit {run.returncode}: {last}")
    if run.returncode != 0:
        raise SetupFailed(f"{args[0]} exited {run.returncode}: {last}")
    return output


def _place_dos(config: dict, number: str, address: str, image_dir: Path, target: Path,
               log: Callable[[str], None]):
    """The image copied in beside the root, then renamed into place: the folder
    appears whole or not at all."""
    staging = target.with_name(f".{target.name}.setup")
    shutil.rmtree(staging, ignore_errors=True)
    install_image(image_dir, staging)
    staging.rename(target)
    log(f"copied {image_dir.name} to {target}")
    dos = dos_path(games_config(config), target)
    (target / "bbs.cfg").write_bytes(_crlf(
        SYSOP, hub_name(config), address, f"{dos}\\INBOUND", f"{dos}\\NETMAIL", number, "NONE"))
    log(f"wrote bbs.cfg: league {number}, {address}, inbound {dos}\\INBOUND")


def _place_ib(config: dict, number: str, image_dir: Path, target: Path,
              log: Callable[[str], None]) -> str:
    """The Coordinator's data folder, set up as a sysop sets up a board, plus the
    Coordinator's key. Made where it stays: its config.json records the folder.
    Returns the hub's board key."""
    binary = image_dir / IB_BINARY
    target.mkdir(parents=True)
    _ib(binary, target, log, "-ibbs-reset", "-board-id", hub_name(config))
    (target / "bbs.cfg").write_text(
        f"BoardID {hub_name(config)}\nBBSName {hub_name(config)}\n"
        f"LeagueNumber {int(number)}\nGameInbound inbound\nGameOutbound outbound\n")
    log(f"wrote bbs.cfg: LeagueNumber {int(number)}")
    found = KEY.search(_ib(binary, target, log, "-gen-board-key"))
    if not found:
        raise SetupFailed("-gen-board-key printed no key")
    _ib(binary, target, log, "-gen-coord-key")
    for name in ("inbound", "outbound"):
        (target / name).mkdir(exist_ok=True)
    return found.group(0)


def _place(config: dict, game: Game, number: str, address: str, image_dir: Path,
           target: Path, log: Callable[[str], None]) -> Optional[str]:
    """The game's files, from its image: no database, so it can run in a
    thread. Returns an IB game's board key."""
    try:
        if game.dos:
            _place_dos(config, number, address, image_dir, target, log)
            return None
        return _place_ib(config, number, image_dir, target, log)
    except ImageError as e:
        raise SetupFailed(str(e))


def _placing(config: dict, league: League, log: Callable[[str], None]) -> tuple:
    row = league.install
    return (config, game_for_letter(league.game_type), league.league_id,
            league.hub_fidonet_address, image_folder(config, row.image),
            games_config(config).root / row.folder, log)


def _finish(db: Session, config: dict, league: League, board_key: Optional[str],
            log: Callable[[str], None], stage: Callable[[str], None]):
    """After the files: the board key kept, the nodelist with the hub alone,
    and preflight's checks."""
    from backend.services.providers import provider_for

    league.install.hub_board_key = board_key
    db.commit()
    stage("stamped")

    path, problems = provider_for(league.game_type).generate_nodelist(db, config, league)
    db.commit()
    if path is None or problems:
        raise SetupFailed("the nodelist could not be written: " + "; ".join(problems))
    log(f"wrote the nodelist with the hub alone: {path.name}")
    stage("nodelist")

    problems = preflight.run(db, config, [league])[league.full_id]
    db.refresh(league)
    for p in problems:
        log(f"preflight: {p}")
    if problems:
        raise SetupFailed("preflight failed: " + "; ".join(problems))
    log("preflight passed")
    stage("preflight")


def build(db: Session, config: dict, league: League, log: Callable[[str], None],
          stage: Callable[[str], None] = lambda s: None):
    """Make the league's game from its image: copy, stamp, nodelist, preflight's
    file checks. Raises SetupFailed (or anything else) with the folder in
    whatever state it reached; the caller removes it."""
    _finish(db, config, league, _place(*_placing(config, league, log)), log, stage)


async def build_async(db: Session, config: dict, league: League, log: Callable[[str], None],
                      stage: Callable[[str], None] = lambda s: None):
    """build(), with the copying and the game's own commands in a thread."""
    key = await asyncio.to_thread(_place, *_placing(config, league, log))
    _finish(db, config, league, key, log, stage)


def remove(config: dict, league: League):
    """Delete the league's game folder, and a half-copied one beside it."""
    root = games_config(config).root
    folder = league.install.folder
    for path in (root / folder, root / f".{folder}.setup"):
        if path.exists():
            shutil.rmtree(path)


def rebuild(db: Session, config: dict, league: League, log: Callable[[str], None]):
    """The game made again from its image, as setup left it: after a first
    reset that failed. The league stays installed. Used at startup; the reset
    job uses rebuild_async."""
    remove(config, league)
    log(f"removed {league.install.folder}; making it again from {league.install.image.dirname}")
    try:
        build(db, config, league, log)
    except Exception:
        remove(config, league)
        raise
    league.install.state = "installed"
    db.commit()


async def rebuild_async(db: Session, config: dict, league: League,
                        log: Callable[[str], None]):
    remove(config, league)
    log(f"removed {league.install.folder}; making it again from {league.install.image.dirname}")
    try:
        await build_async(db, config, league, log)
    except Exception:
        remove(config, league)
        raise
    league.install.state = "installed"
    db.commit()


# ── the job ───────────────────────────────────────────────────────────────
async def run_job(order_id: int, config: dict):
    from backend.services.game_reset.perform import _release_lock, _take_lock, LOCK_WAIT

    db = get_session()
    lines = []

    def log(line: str):
        lines.append(f"{datetime.now():%H:%M:%S} {line}")

    try:
        order = db.get(GameReset, order_id)
        league = order.league or db.get(League, order.league_id)
        if not await _take_lock(LOCK_WAIT):
            _fail(db, config, league, order, lines, "A processing run held the lock for over "
                  f"{LOCK_WAIT // 60} minutes. Nothing was made.", made=False)
            return
        try:
            _stage(db, order, "lock")
            await build_async(db, config, league, log, lambda s: _stage(db, order, s))
            league.install.state = "installed"
            order.status = "applied"
            order.applied_at = _now()
            order.transcript = "\n".join(lines)
            _stage(db, order, "installed")
            logger.info(f"Setup {order.id}: {league.full_id} is installed")
        except Exception as e:
            logger.warning(f"Setup {order_id} of {league.full_id} failed: {e}")
            db.rollback()
            _fail(db, config, league, order, lines, str(e))
        finally:
            _release_lock()
    except Exception:
        logger.exception(f"Setup {order_id} failed unexpectedly")
    finally:
        db.close()


def _fail(db: Session, config: dict, league: League, order: GameReset, lines: list,
          why: str, made: bool = True):
    db.expire_all()
    if made:
        try:
            remove(config, league)
            lines.append(f"{datetime.now():%H:%M:%S} removed {league.install.folder}")
        except Exception as e:
            why += f". Removing {league.install.folder} failed too ({e}): remove it by hand"
    lines.append(f"{datetime.now():%H:%M:%S} FAILED: {why}")
    league.install.state = "failed"
    league.install.hub_board_key = None
    order.status = "failed"
    order.error = why
    order.transcript = "\n".join(lines)
    _stage(db, order, "failed")


def recover(db: Session, config: dict):
    """Settle setups a hub that stopped mid-job left under way: the folder, if
    any, is the setup's own, so it goes, and the league can be set up again."""
    for order in (db.query(GameReset)
                  .filter(GameReset.status == "applying", GameReset.kind == SETUP).all()):
        league = order.league or db.get(League, order.league_id)
        lines = (order.transcript or "").splitlines()
        _fail(db, config, league, order, lines, "The hub stopped during the setup.")
        order.progress = [*(order.progress or [])[:-1],
                          {"stage": "interrupted", "at": _now().isoformat()}]
        logger.warning(f"Setup {order.id} of {league.full_id} was interrupted")
    db.commit()


# ── the canary run (a first reset) ────────────────────────────────────────
def _snapshot(folder: Path) -> dict:
    found = {}
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.is_file():
                found[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return found


def _canary_for(db: Session, other: League, hub: int) -> tuple[str, bytes]:
    """A packet of `other`'s: its newest the hub still holds, or one made up
    with its name."""
    packet = (db.query(Packet)
              .filter(Packet.league_id == other.id, Packet.file_data.isnot(None))
              .order_by(Packet.id.desc()).first())
    if packet is not None:
        return packet.filename, bytes(packet.file_data)
    if other.game_type == "I":
        return f"L{other.league_id}-2-{'Z' * 13}-{hub}.brp", CANARY_BYTES
    return f"{other.league_id}{other.game_type}02{hub:02X}.999", CANARY_BYTES


def place_canaries(db: Session, config: dict, league: League,
                   log: Callable[[str], None]) -> dict:
    """Before the new game's first run: a canary of every other league of the
    same game in its INBOUND, and a record of every other league's INBOUND and
    OUTBOUND. Returns what check_canaries needs."""
    install = league_game(config, league)
    others = [o for o in db.query(League).all() if o.id != league.id and o.install is not None]
    watched = {}
    for other in others:
        theirs = league_game(config, other)
        if theirs is None:
            continue
        for name in ("inbound", "outbound"):
            watched[str(getattr(theirs, name))] = _snapshot(getattr(theirs, name))
    canaries = {}
    install.inbound.mkdir(exist_ok=True)
    for other in others:
        if other.game_type != league.game_type:
            continue
        name, data = _canary_for(db, other, hub_index(config))
        (install.inbound / name).write_bytes(data)
        canaries[name] = hashlib.sha256(data).hexdigest()
        log(f"canary for {other.full_id}: {name}")
    if not canaries:
        log("no other league of this game, so no canary")
    return {"canaries": canaries, "watched": watched}


def check_canaries(config: dict, league: League, placed: dict, run,
                   log: Callable[[str], None]) -> list[str]:
    """After the run: every canary still there, unchanged; every other league's
    folders as they were; nothing the game wrote quarantined. The canaries are
    removed whatever the result."""
    install = league_game(config, league)
    problems = []
    now = _snapshot(install.inbound)
    for name, digest in placed["canaries"].items():
        if now.get(name) != digest:
            problems.append(f"the canary {name} was {'changed' if name in now else 'taken'} "
                            f"by the game: it reads another league's packets")
        (install.inbound / name).unlink(missing_ok=True)
    for folder, before in placed["watched"].items():
        if _snapshot(Path(folder)) != before:
            problems.append(f"{folder}, another league's, changed during the run")
    if run is not None and (run.packets_quarantined or 0):
        problems.append(f"the game wrote {run.packets_quarantined} packet(s) under another "
                        f"league's number (run {run.id})")
    for p in problems:
        log(f"canary: {p}")
    if not problems:
        log(f"canary run passed: {len(placed['canaries'])} canary(ies) untouched, "
            f"{len(placed['watched'])} other folder(s) unchanged")
    return problems
