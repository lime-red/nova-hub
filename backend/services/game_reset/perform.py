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

An IB league takes three more orders from the console, each a GameReset row
of its own `kind`, run by the same job with the same lock, backup and
put-back, its own drive step in place of the editor:

  rules   the draft's rules without a reset (drive_ib_rules): config.json, then
          `-league-config`, which signs and broadcasts them. Boards adopt them
          on their next run; games in progress carry on under them.
  freeze  `-league-freeze <message>` (drive_ib_freeze): every board stops play
          and shows the message, and reports to the hub's game once nothing
          but the freeze has moved there for a while (its quiet report). The
          league page lists the reports as they come in (ib_hub_game).
  thaw    `-league-thaw`: play resumes, and every deadline moves on by the
          time spent frozen.
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
from backend.services.league_games import league_game

logger = get_logger(context="game_reset")

KEEP_BACKUPS = 5               # per league; the newest are kept
LOCK_WAIT = 15 * 60            # seconds to wait for a running batch to finish
GAME_EXIT_WAIT = 300           # seconds for the game to finish after league-wide

_jobs: set = set()             # running job tasks, so they aren't collected


WRITTEN = "_written"               # drive_ib*'s result: the packets the order wrote
KINDS = {"reset": "reset", "rules": "rules change", "freeze": "freeze", "thaw": "thaw"}
DRAFTED = ("reset", "rules")       # the kinds made from the league's draft
MESSAGE_MAX = 200                  # a freeze's message, shown to every caller


class ResetRefused(Exception):
    """The reset can't start. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class GameSetup:
    """Where a league's DOS game is (league_games.league_game())."""
    folder: Path
    dos_path: str
    exe: str
    dosemu: str


@dataclass
class IbSetup:
    """Where a league's Immortal Barons game is: the hub's own game, the
    Coordinator's. `folder` is its data folder."""
    folder: Path
    binary: str
    outbound: Path
    timeout: int


def game_setup(config: dict, league: League):
    install = league_game(config, league)
    if install is None:
        raise ResetRefused(f"The hub has no game for {league.full_id}", 422)
    if install.state != "ready":
        raise ResetRefused(f"{league.full_id}'s game is {install.state}, not ready", 409)
    if not install.game.dos:
        if install.binary is None:
            raise ResetRefused(f"{league.full_id}'s game has no image to run", 422)
        return IbSetup(install.folder, str(install.binary), install.outbound, install.timeout)
    if not install.dos_path:
        raise ResetRefused(f"{install.folder} has no DOS path: it must be inside [games] "
                           f"drive_c, with every name 8.3", 422)
    return GameSetup(install.folder, install.dos_path, install.game.exe, install.dosemu_path)


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
def _not_busy(db: Session):
    busy = db.query(GameReset).filter(GameReset.status == "applying").first()
    if busy is not None:
        raise ResetRefused(f"A {KINDS.get(busy.kind, 'reset')} is already under way (league "
                           f"{busy.league.full_id if busy.league else busy.league_id}).")


def _confirmed(league: League, code: str):
    if (code or "").strip().upper() != league.full_id.upper():
        raise ResetRefused(f"Type the league code, {league.full_id}, to confirm.", 422)


def start(db: Session, league: League, version: int, code: str, username: str,
          config: dict, today: date, kind: str = "reset") -> GameReset:
    """Check everything that can be checked without touching the game, then
    turn the draft into a reset (or, IB, a rules change) under way. The caller
    schedules run_job()."""
    _confirmed(league, code)
    if kind not in DRAFTED:
        raise ResetRefused(f"A {kind} is not made from a draft.", 422)
    if kind == "rules" and league.game_type != "I":
        raise ResetRefused("Only an Immortal Barons league's rules can change "
                           "without a reset.", 422)
    draft = drafts.current_draft(db, league)
    if draft is None:
        raise ResetRefused("There is no draft to perform.", 404)
    if draft.version != version:
        raise ResetRefused(f"{draft.updated_by} saved this draft since you opened it. "
                           f"Check their changes before performing it.")
    _not_busy(db)
    settings, errors = drafts.validate(league.game_type, draft.settings, today)
    if errors:
        raise ResetRefused("The draft no longer passes the game's limits: " +
                           "; ".join(f"{f}: {m}" for f, m in errors), 422)
    setup = _checked_setup(config, league)
    if kind == "rules":
        settings = {k: v for k, v in settings.items() if k != "start_date"}

    draft.status = "applying"
    draft.kind = kind
    draft.settings = settings
    draft.applied_by = username
    draft.progress = []
    draft.error = None
    _stage(db, draft, "queued")
    logger.info(f"{KINDS[kind].capitalize()} {draft.id} of {league.full_id} started by {username}")
    return draft


def start_order(db: Session, league: League, kind: str, code: str, username: str,
                config: dict, message: Optional[str] = None) -> GameReset:
    """A freeze or thaw of an IB league, under way. The caller schedules
    run_job()."""
    _confirmed(league, code)
    if league.game_type != "I" or kind not in ("freeze", "thaw"):
        raise ResetRefused("Only an Immortal Barons league can be frozen or thawed.", 422)
    message = " ".join((message or "").split())
    if kind == "freeze" and not message:
        raise ResetRefused("Say what callers are told while the league is frozen.", 422)
    if len(message) > MESSAGE_MAX:
        raise ResetRefused(f"Keep the message to {MESSAGE_MAX} characters.", 422)
    _not_busy(db)
    setup = _checked_setup(config, league)
    try:
        frozen = bool(json.loads((setup.folder / "world.json").read_text()).get("Frozen"))
    except (OSError, ValueError) as exc:
        raise ResetRefused(f"The hub's game can't be read ({exc}).", 422)
    if kind == "freeze" and frozen:
        raise ResetRefused("The league is already frozen.")
    if kind == "thaw" and not frozen:
        raise ResetRefused("The league isn't frozen.")
    order = GameReset(league_id=league.id, status="applying", kind=kind, source="console",
                      settings={}, notes=message or None, version=1, created_by=username,
                      created_at=_now(), updated_by=username, updated_at=_now(),
                      applied_by=username, progress=[])
    db.add(order)
    db.flush()
    _stage(db, order, "queued")
    logger.info(f"{KINDS[kind].capitalize()} {order.id} of {league.full_id} started by {username}")
    return order


def _checked_setup(config: dict, league: League):
    """The game's setup, with everything the job needs there."""
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
    return setup


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


def _run_ib(setup: IbSetup, args: list, log: Callable[[str], None], transcript: Path,
            check: Callable[[list, int, str], dict]) -> dict:
    """Run the hub's game with `args` and judge it with check(written, exit
    code, last line). Whatever goes wrong, the packets the run wrote are
    removed before the error goes on; the caller puts the data dir back."""
    before = _packets(setup.outbound)
    argv = [setup.binary, *args, "-data", str(setup.folder)]
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
        return {**check(written, run.returncode, last), WRITTEN: written}
    except Exception:
        for name in written:
            (setup.outbound / name).unlink(missing_ok=True)
            log(f"removed {name}")
        raise


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
    return _run_ib(setup, ["-league-reset", on], log, transcript,
                   lambda written, code, last: _check_ib(setup, settings, on, written,
                                                         code, last))


def _world(setup: IbSetup) -> dict:
    try:
        return json.loads((setup.folder / "world.json").read_text())
    except (OSError, ValueError) as exc:
        raise EditorError(f"the game's world.json can't be read: {exc}")


def _written(setup: IbSetup, written: list):
    for name in written:
        try:
            yield ib_packet.parse((setup.outbound / name).read_bytes())
        except (OSError, ib_packet.PacketFormatError):
            continue


def _sent_rules(setup: IbSetup, settings: dict, written: list, exited: str) -> dict:
    rules = next((p["LeagueConfig"] for p in _written(setup, written)
                  if isinstance(p.get("LeagueConfig"), dict)), None)
    if rules is None:
        raise EditorError(f"the game wrote no rules for the members{exited}")
    got = history.sent(ib_packet.league_settings(rules))
    wrong = history.mismatches(settings, got)
    if wrong:
        raise EditorError("the rules the game sent differ from the draft: " +
                          ", ".join(f"{f} {got.get(f)!r} not {settings.get(f)!r}"
                                    for f in wrong))
    return got


def drive_ib_rules(setup: IbSetup, game_type: str, settings: dict, today: date,
                   log: Callable[[str], None], transcript: Path) -> dict:
    """Write `settings` into the Coordinator's config.json and broadcast them
    now (`-league-config`), the game going on. Returns what the signed
    LeagueConfig carries, as drive_ib does."""
    started = _world(setup).get("StartedDate")
    _write_ib_config(setup.folder, settings)
    log("config.json written")

    def check(written, code, last):
        exited = f" (it exited {code}: {last})" if code else ""
        if _world(setup).get("StartedDate") != started:
            raise EditorError(f"the game restarted, which a rules change must not do{exited}")
        return _sent_rules(setup, settings, written, exited)
    return _run_ib(setup, ["-league-config"], log, transcript, check)


def drive_ib_freeze(setup: IbSetup, frozen: bool, message: Optional[str],
                    log: Callable[[str], None], transcript: Path) -> dict:
    """Freeze the league with `message`, or thaw it. Returns the order as sent:
    {"serial", "frozen", "message"}."""
    args = ["-league-freeze", message or ""] if frozen else ["-league-thaw"]

    def check(written, code, last):
        exited = f" (it exited {code}: {last})" if code else ""
        if bool(_world(setup).get("Frozen")) != frozen:
            raise EditorError(f"the game didn't {'freeze' if frozen else 'thaw'}{exited}")
        order = next((p["Freeze"] for p in _written(setup, written)
                      if isinstance(p.get("Freeze"), dict)), None)
        if order is None or bool(order.get("Frozen")) != frozen:
            raise EditorError(f"the game wrote no {'freeze' if frozen else 'thaw'} order "
                              f"for the members{exited}")
        return {"serial": order.get("Serial"), "frozen": frozen,
                "message": order.get("Message")}
    return _run_ib(setup, args, log, transcript, check)


def _check_ib(setup: IbSetup, settings: dict, on: str, written: list,
              returncode: int, last: str) -> dict:
    """The run's evidence, not its exit code: IB exits 1 for a transport fault
    it met on the way, after doing the rest. The world must have restarted on
    `on`, and the order and the rules be on their way, as the draft has them."""
    exited = f" (it exited {returncode}: {last})" if returncode else ""
    if _world(setup).get("StartedDate") != on:
        raise EditorError(f"the game didn't reset{exited}")
    reset_on = next((p["Reset"].get("OnDate") for p in _written(setup, written)
                     if isinstance(p.get("Reset"), dict)), None)
    if reset_on != on:
        raise EditorError(f"the game reset itself but wrote no order for the members{exited}")
    return _sent_rules(setup, settings, written, exited)


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
    has started a new one meanwhile. A freeze or thaw had no draft."""
    if reset.kind not in DRAFTED:
        return
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
            reset.error = (f"The {KINDS.get(reset.kind, 'reset')} is saved, but the hub hit "
                           f"an error after it: {exc}. It reaches the members with the next "
                           f"run that works.")
            db.commit()
    finally:
        db.close()


def _drive_for(reset: GameReset, setup):
    """The step that changes the game, for this kind of order."""
    if reset.kind == "rules":
        return drive_ib_rules
    if reset.kind in ("freeze", "thaw"):
        frozen, message = reset.kind == "freeze", reset.notes
        return lambda setup, game_type, settings, today, log, transcript: \
            drive_ib_freeze(setup, frozen, message, log, transcript)
    return drive_ib if isinstance(setup, IbSetup) else drive


async def _run(db: Session, reset_id: int, config: dict, drive_fn, lock_wait, today):
    reset = db.get(GameReset, reset_id)
    league = reset.league or db.get(League, reset.league_id)
    setup = game_setup(config, league)
    noun = KINDS.get(reset.kind, "reset")
    if drive_fn is None:
        drive_fn = _drive_for(reset, setup)
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
            sent = await asyncio.to_thread(drive_fn, setup, league.game_type, reset.settings,
                                           today, log, transcript)
            written = sent.pop(WRITTEN, None) if isinstance(sent, dict) else None
        except Exception as exc:
            log(f"FAILED: {exc}")
            reset.status = "failed"
            try:
                await asyncio.to_thread(restore, archive, setup.folder)
                log(f"restored {setup.folder} from {archive}")
                reset.error = (f"{exc}. The game was put back as it was before the "
                               f"{noun}" + (", and the settings are back in the draft."
                                            if reset.kind in DRAFTED else "."))
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
            logger.warning(f"{noun.capitalize()} {reset.id} of {league.full_id} failed "
                           f"({ending}): {reset.error}")
            return

        reset.status = "applied"
        reset.applied_at = _now()
        reset.transcript = "\n".join(log_lines)
        if reset.kind == "rules" and isinstance(sent, dict):
            # A reset is confirmed by its order passing through the hub
            # (history.record_game_start); a rules change has no order, and
            # drive_ib_rules has already checked what it sent.
            reset.sent_config = {**sent, "mismatches": []}
        elif reset.kind in ("freeze", "thaw") and isinstance(sent, dict):
            reset.sent_config = sent
        _stage(db, reset, "saved")
        prune(into)
        logger.info(f"{noun.capitalize()} {reset.id} of {league.full_id} saved; "
                    f"running PLANETARY")

        from backend.services.processing_service import ProcessingService
        game = game_for_letter(league.game_type)
        run = await ProcessingService(db, config).process_batch(
            also=((game.code, league.league_id),))
        db.refresh(reset)
        reset.processing_run_id = run.id if run is not None else None
        _stage(db, reset, "processing")
        if run is not None and run.status != "completed" and _collected(db, league, written):
            # IB exits 1 for any fault it reports, after writing its packets: a
            # board's packet held under old rules, say. The order went out with
            # the rest; what the game complained of is the run's, not this.
            reset.transcript = "\n".join(log_lines + [
                f"{datetime.now():%H:%M:%S} the hub run after it reported a fault, but "
                f"collected {', '.join(written)}: {run.error_message}"])
            db.commit()
        elif run is None or run.status != "completed":
            reset.error = (f"The {noun} is saved, but the processing run after it failed"
                           + (f": {run.error_message}" if run is not None and
                              run.error_message else "")
                           + ". It reaches the members with the next run that works.")
            db.commit()
            return
        _stage(db, reset, "sent")
    finally:
        _release_lock()


def _collected(db: Session, league: League, written: Optional[list]) -> bool:
    """Whether the hub collected every packet the order wrote, for delivery."""
    from backend.models.database import Packet
    if not written:
        return False
    return all(db.query(Packet.id).filter(Packet.league_id == league.id,
                                          Packet.filename == name.upper()).first()
               for name in written)


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
        noun = KINDS.get(reset.kind, "reset")
        if "saved" in stages:
            reset.status = "applied"
            reset.error = (f"The hub stopped before the processing run after the {noun}. "
                           "It reaches the members with the next run.")
        elif last == "editor":
            league = reset.league or db.get(League, reset.league_id)
            try:
                folder = game_setup(config, league).folder
                restore(Path(reset.backup_path), folder)
                reset.error = (f"The hub stopped while the game was being changed. The game "
                               f"was put back as it was before the {noun}.")
            except Exception as exc:
                reset.error = (f"The hub stopped while the game was being changed, and "
                               f"putting the game back failed: {exc}. Restore "
                               f"{reset.backup_path} by hand before the next run.")
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
