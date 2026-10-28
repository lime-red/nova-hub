# backend/services/processing_service.py - Complete with database

import asyncio
import threading
import toml
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.core.config import default_config_path
from backend.models.database import League, Packet, ProcessingRun
from backend.core.database import get_session
from backend.logging_config import get_logger
from backend.services.dosemu_runner import DosemuRunner
from backend.services.games import game_for_code, game_for_letter
from backend.services.providers import provider_for
from backend.services.providers.sr_games import SRGamesProvider
from backend.services.sequence_validator import (
    DEFAULT_ALERT_MAX_AGE_DAYS,
    DEFAULT_ALERTS_ENABLED,
    SequenceValidator,
)

logger = get_logger(context="processing")

_SR = SRGamesProvider()

# Global lock for processing
_processing_lock = threading.Lock()
_processing_task: Optional[asyncio.Task] = None
# Asked for while a batch ran. There is no poll loop: a packet uploaded during a
# run would otherwise wait for the next upload, which can be a day away.
_rerun = False


def find_file_case_insensitive(directory: Path, filename: str) -> Optional[Path]:
    """
    Find a file in directory with case-insensitive matching.
    Returns the actual file path if found, None otherwise.
    This handles DOS case-insensitivity on Linux filesystems.
    """
    if not directory.exists():
        return None

    # Try exact match first (fast path)
    exact_path = directory / filename
    if exact_path.exists():
        return exact_path

    # Case-insensitive search
    filename_lower = filename.lower()
    for file in directory.iterdir():
        if file.name.lower() == filename_lower:
            return file

    return None


def trigger_processing():
    """Trigger background processing (non-blocking)"""
    global _processing_task, _rerun

    # One batch at a time; the one running goes round again when it finishes.
    if _processing_task and not _processing_task.done():
        logger.debug("Task already running, another pass queued")
        _rerun = True
        return

    try:
        # Get the running event loop
        loop = asyncio.get_event_loop()
        # Create new task
        _processing_task = loop.create_task(process_batch())
        logger.debug("Background processing task started")
    except RuntimeError as e:
        logger.warning(f"Failed to start task: {e}")
        # Fallback: try to get running loop
        try:
            loop = asyncio.get_running_loop()
            _processing_task = loop.create_task(process_batch())
            logger.debug("Background processing task started (running loop)")
        except Exception as e2:
            logger.error(f"Failed to start task (fallback): {e2}")


async def process_batch():
    """Main processing batch - runs asynchronously"""

    global _rerun
    logger.debug("process_batch() called")

    # Try to acquire lock (non-blocking)
    if not _processing_lock.acquire(blocking=False):
        logger.debug("Already running, another pass queued")
        _rerun = True
        return

    try:
        while True:
            _rerun = False
            await _process_once()
            if not _rerun:
                break
            logger.info("Packets arrived during the run; processing again")
    finally:
        _processing_lock.release()


async def _process_once():
    try:
        # Read afresh each batch, so a league added to the file is processed
        # without a restart -- but the file the hub was started with.
        config = toml.load(default_config_path())

        db = get_session()
        try:
            processor = ProcessingService(db, config)
            await processor.process_batch()
        except Exception as e:
            logger.exception(f"Error in process_batch: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.exception(f"Fatal error in process_batch: {e}")


class ProcessingService:
    """Service for processing packets"""

    def __init__(self, db: Session, config: dict = None):
        self.db = db
        # Use provided config or fall back to defaults
        if config is None:
            config = {}
        self.config = config

        self._dosemu_runner = None

        # Packets handed to a game that it never ingested, accumulated across the
        # league groups of one batch. See step 6 of process_game_batch().
        self._unconsumed_count = 0

    @property
    def dosemu_runner(self) -> DosemuRunner:
        """Built when a dosemu game first needs it, so a hub that runs only
        native games needs no [dosemu] section."""
        if self._dosemu_runner is None:
            self._dosemu_runner = DosemuRunner(self.config)
        return self._dosemu_runner

    @dosemu_runner.setter
    def dosemu_runner(self, runner: DosemuRunner):
        self._dosemu_runner = runner

    async def process_batch(self, also: tuple = ()) -> Optional[ProcessingRun]:
        """Process all unprocessed packets, and return the run (None if there
        was nothing to run).

        `also` names leagues, as (game code, league number) like ("BRE", "015"),
        whose game runs even with no packets waiting: a game reset's first
        PLANETARY, which is what sends the league its Game Start.
        """
        logger.info("Starting batch...")

        # Get unprocessed packets
        packets = self.db.query(Packet).filter(Packet.processed_at == None).all()

        if not packets and not also:
            logger.info("No packets to process")
            # Still check for nodelists and other outbound files
            await self.scan_outbound_folders()
            return

        logger.info(f"Found {len(packets)} packet(s) to process")

        # Group packets by (game_type_str, league_id_str) so each league is
        # processed independently with its own DOSEMU config and file ingestion.
        from collections import defaultdict
        groups: dict = defaultdict(list)
        for p in packets:
            league = self.db.query(League).filter(League.id == p.league_id).first()
            if not league:
                logger.warning(f"Skipping packet {p.filename}: league not found")
                continue
            try:
                game_type_str = game_for_letter(league.game_type).code
            except KeyError:
                logger.warning(f"Skipping packet {p.filename}: unknown game type '{league.game_type}'")
                continue
            groups[(game_type_str, league.league_id)].append(p)
        for key in also:
            groups.setdefault(tuple(key), [])

        if not groups:
            logger.warning("No packets with valid league associations found")
            await self.scan_outbound_folders()
            return

        # Create a single processing run for this batch
        run = ProcessingRun(status="running")
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        results = {}
        league_of_group = {}
        self._unconsumed_count = 0

        try:
            for (game_type_str, league_id_str), group_packets in groups.items():
                key = f"{game_type_str}_{league_id_str}"
                # Each group is one league, and the transcript for that group is
                # the only place its movements are named. Remember which league
                # it was before the outputs get concatenated together.
                if group_packets:
                    league_of_group[key] = group_packets[0].league_id
                else:
                    row = self._league(game_type_str, league_id_str)
                    league_of_group[key] = row.id if row else None
                logger.info(
                    f"Processing {len(group_packets)} packet(s) for "
                    f"{game_type_str} league {league_id_str}"
                )
                results[key] = await self.process_game_batch(
                    game_type_str, group_packets, run.id, league_id_str
                )

            run.completed_at = utcnow()

            # A group that failed leaves its packets unprocessed and its files in
            # hub inbound, so the run did not do what it was asked. Reporting that
            # as "completed" is how a dead dosemu run looked like a successful one.
            failed = {
                key: r for key, r in results.items()
                if not r or r.get("status") != "success"
            }
            if failed:
                run.status = "failed"
                run.error_message = "; ".join(
                    f"{key}: {r.get('error') or r.get('status', 'unknown')}"
                    for key, r in failed.items()
                )
                logger.error(f"Processing run {run.id} failed - {run.error_message}")
            else:
                run.status = "completed"

            # Count only what actually got through, not everything we were handed.
            run.packets_processed = sum(
                1 for p in packets if p.processed_at is not None
            )
            run.packets_unconsumed = self._unconsumed_count

            # Combine dosemu output from all groups
            outputs = [r.get("output", "") for r in results.values() if r]
            run.dosemu_log = "\n\n".join(filter(None, outputs))

            self._record_movements(run, results, league_of_group)

        except Exception as e:
            logger.error(f"Error: {e}")
            run.completed_at = utcnow()
            run.status = "failed"
            run.error_message = str(e)

        self.db.commit()

        # Retire what is no longer missing before looking for what is. Until
        # 2026-09-12 auto_resolve_alerts() had no caller anywhere in the codebase,
        # so no alert had ever been resolved automatically -- production carried
        # 213 alerts for packets that had long since arrived, on top of the false
        # ones. Resolving first also means a gap that closed in this very run is
        # never re-reported.
        validator = SequenceValidator(
            self.db,
            hub_index=self.config["hub"]["bbs_index"],
            max_age_days=self.config.get("processing", {}).get(
                "sequence_alert_max_age_days", DEFAULT_ALERT_MAX_AGE_DAYS
            ),
            alerts_enabled=self.config.get("processing", {}).get(
                "sequence_alerts_enabled", DEFAULT_ALERTS_ENABLED
            ),
        )
        validator.auto_resolve_alerts()

        # Check for sequence gaps; dispatch out-of-band alerts for new ones
        new_alerts = validator.check_sequences()
        if new_alerts:
            from backend.services.alert_service import dispatch_alert
            from backend.models.database import League as _League
            for _alert in new_alerts:
                _league = self.db.query(_League).filter(_League.id == _alert.league_id).first()
                _league_name = f"{_league.game_type} {_league.league_id}" if _league else "Unknown"
                await dispatch_alert(_alert, _league_name, self.config)

        logger.info("Batch complete")

        # Regenerate hub-side nodelists for all processed leagues
        for (game_type_str, league_id_str) in groups.keys():
            league_obj = self._league(game_type_str, league_id_str)
            if league_obj:
                provider_for(league_obj.game_type).generate_nodelist(self.db, self.config, league_obj)

        # Always scan outbound folders for nodelists
        await self.scan_outbound_folders()
        return run


    def get_game_type(self, packet: Packet) -> str:
        """Get game type for packet"""
        league = self.db.query(League).filter(League.id == packet.league_id).first()
        return league.game_type if league else None

    async def scan_outbound_folders(self):
        """Scan all league outbound folders for nodelists and packets"""
        logger.info("Scanning outbound folders for nodelists...")

        # Get all active leagues
        leagues = self.db.query(League).filter(League.is_active == True).all()

        for league in leagues:
            try:
                provider = provider_for(league.game_type)
            except KeyError:
                logger.warning(f"Skipping league {league.league_id}: unknown game type '{league.game_type}'")
                continue

            game_outbound_dir = provider.outbound_dir(self, league)

            # Check if directory exists and has files
            if game_outbound_dir and game_outbound_dir.exists() and any(game_outbound_dir.iterdir()):
                logger.info(f"Checking outbound folder for league {league.full_id}: {game_outbound_dir}")
                await provider.collect_outbound(self, league, 0, game_outbound_dir)

    def _record_movements(self, run, results, league_of_group):
        """Store what the games said moved between nodes in this run.

        Parsed here rather than on demand because retention drops the transcript
        after 30 days, and a movement history that silently empties out after a
        month is worse than none. The rows are small -- a busy run yields about
        twenty -- and they are what the admin view reads.

        Attribution is per group: one group is one league, and its own transcript
        is the only place its items are named. Once the outputs are concatenated
        into run.dosemu_log that association is gone, which is why this runs over
        `results` instead of over the combined log.

        Never fatal. A transcript the parser cannot make sense of costs the run
        its movement records, not its packets.
        """
        from backend.models.database import ProcessingRunItem
        from backend.services.transcript_service import parse

        recorded = 0
        for key, result in results.items():
            output = (result or {}).get("output") or ""
            if not output:
                continue
            league = self.db.get(League, league_of_group.get(key)) if league_of_group.get(key) else None
            if league is not None and not provider_for(league.game_type).reads_transcripts:
                continue  # not a DOS game's transcript
            try:
                records, _ = parse(output.encode("utf-8", errors="replace"))
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"Could not read the transcript for {key}: {exc}")
                continue

            for record in records:
                if record.get("kind") != "item":
                    continue
                self.db.add(ProcessingRunItem(
                    processing_run_id=run.id,
                    league_id=league_of_group.get(key),
                    occurred_at=run.started_at,
                    direction=record["direction"],
                    item_type=record["type"],
                    src_node=record["src"],
                    dst_node=record["dst"],
                    size_before=record.get("size_before"),
                    size_after=record.get("size_after"),
                    phase=record.get("phase"),
                ))
                recorded += 1

        if recorded:
            logger.info(f"Run {run.id}: recorded {recorded} item movement(s)")

    def _league(self, game_type: str, league_number: str) -> Optional[League]:
        return (self.db.query(League)
                .filter(League.league_id == league_number,
                        League.game_type == game_for_code(game_type).letter)
                .first())

    async def process_game_batch(self, game_type: str, packets: list, run_id: int,
                                 league_number: Optional[str] = None):
        """Process a batch of packets for one game.

        With `league_number` the game runs even when `packets` is empty (see
        process_batch's `also`); without it, the league is the packets' own.
        What running the game means is the league's provider's business.
        """
        if league_number is not None:
            league = self._league(game_type, league_number)
            if not league:
                return {"status": "error", "error": f"League {league_number} not found"}
        else:
            if not packets:
                return {"status": "success", "message": "No packets to process"}
            first_packet = packets[0]
            league = self.db.query(League).filter(League.id == first_packet.league_id).first()
            if not league:
                return {"status": "error", "error": f"League not found for packet {first_packet.filename}"}

        return await provider_for(league.game_type).process_league(self, league, packets, run_id)

    # The SR games' steps, kept here by name: the SR provider calls them through
    # the service, so a caller (or test) can drive or replace one step at a time.

    async def ingest_processing_files(
        self, game_type: str, league_id: str, league_db_id: int, run_id: int, league_config: dict
    ):
        """Run scores, routes, and bbsinfo commands, then ingest their output files"""
        await _SR.ingest_processing_files(
            self, game_type, league_id, league_db_id, run_id, league_config)

    async def collect_outbound_packets(self, game_type: str, run_id: int, outbound_dir: Path):
        """Collect the packets an SR game wrote into outbound_dir, for whichever
        of its leagues each one names."""
        await _SR.collect_game_outbound(self, game_for_code(game_type), run_id, outbound_dir)
