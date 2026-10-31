"""Performing a game reset: the job behind the console's PERFORM RESET.

In order, each step recorded in the row's `progress`:

  queued      the draft became this reset (status "applying")
  lock        the processing lock is held: no batch runs until the reset is done
  backup      the game folder is archived; the only way back once RESET has
              been answered Y, which deletes GAME.DAT on the spot
  editor      editor.EditorDriver types the draft into `<EXE> RESET`
  saved       the read-back matched, ESC saved, league-wide was answered Y
  processing  a PLANETARY run for the league, even with no packets waiting
  sent        that run went through: the Game Start (IB: the order) is on its
              way to members

Anything that goes wrong before "saved" kills dosemu, puts the backup back
byte for byte, and marks the reset failed with the driver's log. The draft's
settings come back as a new draft, so nothing typed is lost.

One reset runs at a time across the hub. The job holds the processing lock
throughout, so packets that arrive meanwhile wait for the run after it.

Immortal Barons has no editor. Its "editor" step is drive_ib: the settings are
written into the Coordinator's config.json and the game is ordered to reset
the league (`-league-reset`), which resets the hub's own game and writes the
signed order and the new rules for every member. Those packets are the
read-back. The run after it only delivers them.
"""
import asyncio
import json
import shutil
import subprocess
import tarfile
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.core.database import get_session
from backend.logging_config import get_logger
from backend.models.database import GameReset, League
from backend.services import ib_packet
from backend.services.game_reset import drafts, history
from backend.services.game_reset.editor import EditorDriver, EditorError
from backend.services.games import game_for_letter

logger = get_logger(context="game_reset")

KEEP_BACKUPS = 5               # per league; the newest are kept
LOCK_WAIT = 15 * 60            # seconds to wait for a running batch to finish
GAME_EXIT_WAIT = 300           # seconds for the game to finish after league-wide

_jobs: set = set()             # running job tasks, so they aren't collected


class ResetRefused(Exception):
    """The reset can't start. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class GameSetup:
    """Where a league's game is, from [dosemu.<league>.<game>] in config.toml."""
    folder: Path
    dos_path: str
    exe: str
    dosemu: str


@dataclass
class IbSetup:
    """Where a league's Immortal Barons game is, from [ib.<league>]: the hub's
    own game, the Coordinator's. `folder` is its data dir."""
    folder: Path
    binary: str
    outbound: Path
    timeout: int


def ib_setup(config: dict, league: League) -> IbSetup:
    from backend.services.providers import provider_for
    provider = provider_for(league.game_type)
    settings = provider.settings(config, league)
    paths = provider._paths(config, league)
    if paths is None or not settings.get("binary"):
        raise ResetRefused(f"config.toml's [ib.{league.league_id}] needs binary and "
                           f"data_dir", 422)
    timeout = int(settings.get("timeout") or config.get("dosemu", {}).get("timeout", 300))
    return IbSetup(paths[0], settings["binary"], paths[2], timeout)


def game_setup(config: dict, league: League):
    if league.game_type == "I":
        return ib_setup(config, league)
    game = game_for_letter(league.game_type)
    dosemu = config.get("dosemu", {})
    section = dosemu.get(league.league_id, {}).get(game.key)
    if not section:
        raise ResetRefused(f"config.toml has no [dosemu.{league.league_id}."
                           f"{game.key}] section for this league", 422)
    folder, dos_path = section.get("game_folder"), section.get("game_dos_path")
    # The game's EXE is the first word of the commands the hub already runs.
    command = section.get("reset_command") or section.get("processing_command") or ""
    exe = command.split()[0] if command.split() else ""
    if not (folder and dos_path and exe):
        raise ResetRefused(f"[dosemu.{league.league_id}.{game.key}] needs "
                           f"game_folder, game_dos_path and processing_command", 422)
    return GameSetup(Path(folder), dos_path, exe, dosemu.get("dosemu_path", "/usr/bin/dosemu"))


def _find(folder: Path, name: str) -> Optional[Path]:
    """`folder/name` matched as DOS would, ignoring case."""
    if not folder.is_dir():
        return None
    for entry in folder.iterdir():
        if entry.name.lower() == name.lower():
            return entry
    return None


def _now() -> datetime:
    return utcnow()


def _stage(db: Session, reset: GameReset, stage: str):
    reset.progress = [*(reset.progress or []), {"stage": stage, "at": _now().isoformat()}]
    db.commit()


# ── starting one ──────────────────────────────────────────────────────────
def start(db: Session, league: League, version: int, code: str, username: str,
          config: dict, today: date) -> GameReset:
    """Check everything that can be checked without touching the game, then
    turn the draft into a reset under way. The caller schedules run_job()."""
    if (code or "").strip().upper() != league.full_id.upper():
        raise ResetRefused(f"Type the league code, {league.full_id}, to confirm.", 422)
    draft = drafts.current_draft(db, league)
    if draft is None:
        raise ResetRefused("There is no draft to perform.", 404)
    if draft.version != version:
        raise ResetRefused(f"{draft.updated_by} saved this draft since you opened it. "
                           f"Check their changes before performing it.")
    busy = db.query(GameReset).filter(GameReset.status == "applying").first()
    if busy is not None:
        raise ResetRefused(f"A reset is already under way (league "
                           f"{busy.league.full_id if busy.league else busy.league_id}).")
    settings, errors = drafts.validate(league.game_type, draft.settings, today)
    if errors:
        raise ResetRefused("The draft no longer passes the game's limits: " +
                           "; ".join(f"{f}: {m}" for f, m in errors), 422)
    setup = game_setup(config, league)
    if not setup.folder.is_dir():
        raise ResetRefused(f"The game folder {setup.folder} isn't there.", 422)
    if isinstance(setup, IbSetup):
        if not Path(setup.binary).is_file():
            raise ResetRefused(f"The game, {setup.binary}, isn't there.", 422)
        for name, why in (("config.json", "the league's rules"),
                          ("coord.key", "the Coordinator's key, to sign the order")):
            if not (setup.folder / name).is_file():
                raise ResetRefused(f"{setup.folder} has no {name} ({why}).", 422)
    elif _find(setup.folder, setup.exe) is None:
        raise ResetRefused(f"{setup.exe} isn't in {setup.folder}.", 422)
    if isinstance(setup, GameSetup) and _find(setup.folder, "inuse.flg") is not None:
        raise ResetRefused("The game's inuse.flg is there: either the game is running, "
                           "or a run was killed and left it behind. Check before "
                           "resetting.", 409)

    draft.status = "applying"
    draft.settings = settings
    draft.applied_by = username
    draft.progress = []
    draft.error = None
    _stage(db, draft, "queued")
    logger.info(f"Reset {draft.id} of {league.full_id} started by {username}")
    return draft


def schedule(reset_id: int, config: dict, **kw) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(run_job(reset_id, config, **kw))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    return task


# ── the game folder ───────────────────────────────────────────────────────
def backup(folder: Path, into: Path, stamp: str) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    archive = into / f"{stamp}.tar.gz"
    partial = archive.with_suffix(".partial")
    with tarfile.open(partial, "w:gz") as tar:
        tar.add(folder, arcname=".")
    partial.rename(archive)
    return archive


def restore(archive: Path, folder: Path):
    """Put `folder` back exactly as `archive` holds it. The archive is unpacked
    beside it first, so a bad archive leaves the folder as it is, and the two
    are swapped by rename, so at no point is there no game folder at all."""
    staging = folder.with_name(f".{folder.name}.restoring")
    replaced = folder.with_name(f".{folder.name}.replaced")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(replaced, ignore_errors=True)
    staging.mkdir()
    with tarfile.open(archive, "r:gz") as tar:
        # "data": nothing outside staging, no devices. A game folder holds
        # neither, and the archive is the hub's own, but a restore is no place
        # to find out otherwise.
        tar.extractall(staging, filter="data")
    folder.rename(replaced)
    staging.rename(folder)
    shutil.rmtree(replaced)


def prune(into: Path, keep: int = KEEP_BACKUPS):
    archives = sorted(into.glob("*.tar.gz"))
    for old in archives[:-keep] if keep else archives:
        old.unlink(missing_ok=True)


# ── driving the game ──────────────────────────────────────────────────────
def drive(setup: GameSetup, game_type: str, settings: dict, today: date,
          log: Callable[[str], None], transcript: Path) -> dict:
    """Run `<EXE> RESET` and type `settings` in. Blocking; run it in a thread.
    Returns the read-back, or raises EditorError with dosemu killed."""
    from sr_harness.install import Install

    install = Install(setup.folder, exe=setup.exe, dos_path=setup.dos_path,
                      dosemu=setup.dosemu)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    session = install.session("RESET", transcript=str(transcript), batch="RESET.BAT")
    try:
        driver = EditorDriver(session, game_type, settings, today, log=log)
        readback = driver.run()
        log("waiting for the game to finish")
        if not session.drain(GAME_EXIT_WAIT):
            raise EditorError("the game didn't exit after the league-wide question")
        data = _find(setup.folder, "DATA")
        if data is None or _find(data, "GAME.DAT") is None:
            raise EditorError("the game exited without writing DATA/GAME.DAT")
        return readback
    finally:
        session.close(grace=2)
        (setup.folder / "RESET.BAT").unlink(missing_ok=True)


def _write_ib_config(folder: Path, settings: dict):
    """Set the league's rules in config.json, leaving the board's own keys as
    they are. IB reads the required version from ibversion.cfg over config.json,
    so that is written too, as IB writes it."""
    path = folder / "config.json"
    rules = ib_packet.config_json(settings)
    current = json.loads(path.read_text())
    current.update(rules)
    partial = path.with_name("config.json.partial")
    partial.write_text(json.dumps(current, indent=2))
    partial.rename(path)
    version = rules.get("MinBoardVersion", "")
    (folder / "ibversion.cfg").write_text(
        "# The game version every board in this league must run.\n"
        "# Set by the League Coordinator and sent to every board.\n"
        "# Blank means no requirement. A board below it has its packets refused.\n"
        + (version + "\n" if version else "\n"))


def _packets(outbound: Path) -> set:
    from backend.services.providers.immortal_barons import is_packet_file
    return {p.name for p in outbound.iterdir() if is_packet_file(p)} if outbound.is_dir() else set()


def drive_ib(setup: IbSetup, game_type: str, settings: dict, today: date,
             log: Callable[[str], None], transcript: Path) -> dict:
    """Write `settings` into the Coordinator's config.json and order the league
    reset, starting on the Start Date (blank: today). Blocking; run it in a
    thread. Returns what the signed LeagueConfig carries, as history.sent()
    gives it, or raises EditorError having removed any packet the run wrote;
    the caller puts the data dir back."""
    on = settings.get("start_date") or today.isoformat()
    _write_ib_config(setup.folder, settings)
    log("config.json written")
    before = _packets(setup.outbound)
    argv = [setup.binary, "-league-reset", on, "-data", str(setup.folder)]
    log(" ".join(argv))
    transcript.parent.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        try:
            run = subprocess.run(argv, cwd=setup.folder, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 timeout=setup.timeout, start_new_session=True)
        except subprocess.TimeoutExpired:
            raise EditorError(f"the game didn't finish within {setup.timeout}s")
        finally:
            written = sorted(_packets(setup.outbound) - before)
        output = run.stdout.decode("utf-8", errors="replace")
        transcript.write_text(output)
        last = output.strip().splitlines()[-1] if output.strip() else ""
        log(f"exit {run.returncode}: {last}")
        log(f"wrote {', '.join(written) or 'no packets'}")
        return _check_ib(setup, settings, on, written, run.returncode, last)
    except Exception:
        for name in written:
            (setup.outbound / name).unlink(missing_ok=True)
            log(f"removed {name}")
        raise


def _check_ib(setup: IbSetup, settings: dict, on: str, written: list,
              returncode: int, last: str) -> dict:
    """The run's evidence, not its exit code: IB exits 1 for a transport fault
    it met on the way, after doing the rest. The world must have restarted on
    `on`, and the order and the rules be on their way, as the draft has them."""
    exited = f" (it exited {returncode}: {last})" if returncode else ""
    try:
        world = json.loads((setup.folder / "world.json").read_text())
    except (OSError, ValueError) as exc:
        raise EditorError(f"the game's world.json can't be read after the reset: {exc}")
    if world.get("StartedDate") != on:
        raise EditorError(f"the game didn't reset{exited}")
    reset_on, rules = None, None
    for name in written:
        try:
            p = ib_packet.parse((setup.outbound / name).read_bytes())
        except (OSError, ib_packet.PacketFormatError):
            continue
        if isinstance(p.get("Reset"), dict):
            reset_on = p["Reset"].get("OnDate")
        if isinstance(p.get("LeagueConfig"), dict):
            rules = p["LeagueConfig"]
    if reset_on != on:
        raise EditorError(f"the game reset itself but wrote no order for the members{exited}")
    if rules is None:
        raise EditorError(f"the game wrote the reset order but not the rules{exited}")
    got = history.sent(ib_packet.league_settings(rules))
    wrong = history.mismatches(settings, got)
    if wrong:
        raise EditorError("the rules the game sent differ from the draft: " +
                          ", ".join(f"{f} {got.get(f)!r} not {settings.get(f)!r}"
                                    for f in wrong))
    return got


# ── the job ───────────────────────────────────────────────────────────────
async def _take_lock(wait: float) -> bool:
    from backend.services.processing_service import _processing_lock
    deadline = time.monotonic() + wait
    while not _processing_lock.acquire(blocking=False):
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(1)
    return True


def _release_lock():
    from backend.services.processing_service import _processing_lock
    _processing_lock.release()


def _keep_as_draft(db: Session, reset: GameReset):
    """A failed reset's settings come back as the league's draft, unless someone
    has started a new one meanwhile."""
    league = reset.league or db.get(League, reset.league_id)
    if drafts.current_draft(db, league) is None:
        now = _now()
        db.add(GameReset(league_id=reset.league_id, status="draft", source="console",
                         settings=reset.settings, notes=reset.notes, version=1,
                         created_by=reset.applied_by, created_at=now,
                         updated_by=reset.applied_by, updated_at=now))


async def run_job(reset_id: int, config: dict, drive_fn=None,
                  lock_wait: float = LOCK_WAIT, today: Optional[date] = None):
    db = get_session()
    try:
        await _run(db, reset_id, config, drive_fn, lock_wait, today or date.today())
    except Exception as exc:                     # a bug, not an editor surprise
        logger.exception(f"Reset {reset_id} failed unexpectedly")
        db.rollback()
        reset = db.get(GameReset, reset_id)
        if reset is None:
            return
        if reset.status == "applying":
            # Before the editor: the game was never touched.
            reset.status = "failed"
            reset.error = f"The hub hit an error before the game was touched: {exc}"
            _keep_as_draft(db, reset)
            _stage(db, reset, "failed")
        elif reset.status == "applied" and not reset.error:
            reset.error = (f"The reset is saved, but the hub hit an error after it: "
                           f"{exc}. It reaches the members with the next run that works.")
            db.commit()
    finally:
        db.close()


async def _run(db: Session, reset_id: int, config: dict, drive_fn, lock_wait, today):
    reset = db.get(GameReset, reset_id)
    league = reset.league or db.get(League, reset.league_id)
    setup = game_setup(config, league)
    if drive_fn is None:
        drive_fn = drive_ib if isinstance(setup, IbSetup) else drive
    data_dir = Path(config.get("server", {}).get("data_dir", "./data"))
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    log_lines = []

    def log(line: str):
        log_lines.append(f"{datetime.now():%H:%M:%S} {line}")

    if not await _take_lock(lock_wait):
        reset.status = "failed"
        reset.error = (f"A processing run held the lock for over {lock_wait // 60:.0f} "
                       f"minutes. Nothing was changed.")
        _keep_as_draft(db, reset)
        _stage(db, reset, "failed")
        return
    try:
        _stage(db, reset, "lock")
        into = data_dir / "reset_backups" / league.full_id
        archive = await asyncio.to_thread(backup, setup.folder, into, stamp)
        reset.backup_path = str(archive)
        _stage(db, reset, "backup")

        _stage(db, reset, "editor")
        transcript = data_dir / "logs" / "reset" / f"{league.full_id}_{stamp}.log"
        log(f"transcript: {transcript}")
        try:
            await asyncio.to_thread(drive_fn, setup, league.game_type, reset.settings,
                                    today, log, transcript)
        except Exception as exc:
            log(f"FAILED: {exc}")
            reset.status = "failed"
            try:
                await asyncio.to_thread(restore, archive, setup.folder)
                log(f"restored {setup.folder} from {archive}")
                reset.error = (f"{exc}. The game was put back as it was before the "
                               f"reset, and the settings are back in the draft.")
                ending = "restored"
            except Exception as restore_exc:
                log(f"RESTORE FAILED: {restore_exc}")
                reset.error = (f"{exc}. Putting the game back then failed too "
                               f"({restore_exc}): restore {archive} into "
                               f"{setup.folder} by hand before the next run.")
                ending = "failed"
            reset.transcript = "\n".join(log_lines)
            _keep_as_draft(db, reset)
            _stage(db, reset, ending)
            logger.warning(f"Reset {reset.id} of {league.full_id} failed ({ending}): "
                           f"{reset.error}")
            return

        reset.status = "applied"
        reset.applied_at = _now()
        reset.transcript = "\n".join(log_lines)
        _stage(db, reset, "saved")
        prune(into)
        logger.info(f"Reset {reset.id} of {league.full_id} saved; running PLANETARY")

        from backend.services.processing_service import ProcessingService
        game = game_for_letter(league.game_type)
        run = await ProcessingService(db, config).process_batch(
            also=((game.code, league.league_id),))
        db.refresh(reset)
        reset.processing_run_id = run.id if run is not None else None
        _stage(db, reset, "processing")
        if run is None or run.status != "completed":
            reset.error = ("The reset is saved, but the processing run after it failed"
                           + (f": {run.error_message}" if run is not None and
                              run.error_message else "")
                           + ". It reaches the members with the next run that works.")
            db.commit()
            return
        _stage(db, reset, "sent")
    finally:
        _release_lock()


# ── after a restart ───────────────────────────────────────────────────────
def recover(db: Session, config: dict):
    """Settle resets left "applying" by a hub that stopped mid-job.

    Stopped in the editor: the game is half-reset, so it is restored. Stopped
    after the save: the reset stands, and the next processing run sends it.
    Before the backup finished, nothing was touched.
    """
    for reset in db.query(GameReset).filter(GameReset.status == "applying").all():
        stages = [p["stage"] for p in reset.progress or []]
        last = stages[-1] if stages else "queued"
        if "saved" in stages:
            reset.status = "applied"
            reset.error = ("The hub stopped before the processing run after the reset. "
                           "It reaches the members with the next run.")
        elif last == "editor":
            league = reset.league or db.get(League, reset.league_id)
            try:
                folder = game_setup(config, league).folder
                restore(Path(reset.backup_path), folder)
                reset.error = ("The hub stopped while the game was being reset. The game was "
                               "put back as it was before the reset.")
            except Exception as exc:
                reset.error = (f"The hub stopped while the game was being reset, and putting "
                               f"the game back failed: {exc}. Restore {reset.backup_path} "
                               f"by hand before the next run.")
            reset.status = "failed"
            _keep_as_draft(db, reset)
        else:
            reset.status = "failed"
            reset.error = "The hub stopped before the game was touched."
            _keep_as_draft(db, reset)
        reset.progress = [*(reset.progress or []),
                          {"stage": "interrupted", "at": _now().isoformat()}]
        logger.warning(f"Reset {reset.id} was interrupted at {last}: {reset.error}")
    db.commit()
