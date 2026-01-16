# backend/services/processing_service.py - Complete with database

import asyncio
import hashlib
import shutil
import threading
import toml
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.database import League, LeagueMembership, Packet, ProcessingRun, ProcessingRunFile
from backend.core.database import get_session
from backend.logging_config import get_logger
from backend.services.dosemu_runner import DosemuRunner
from backend.services.packet_service import parse_packet_filename
from backend.services.sequence_validator import SequenceValidator

logger = get_logger(context="processing")

# Global lock for processing
_processing_lock = threading.Lock()
_processing_task: Optional[asyncio.Task] = None


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
    global _processing_task

    # Don't start multiple processing tasks
    if _processing_task and not _processing_task.done():
        logger.debug("Task already running, skipping")
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

    logger.debug("process_batch() called")

    # Try to acquire lock (non-blocking)
    if not _processing_lock.acquire(blocking=False):
        logger.debug("Already running, skipping")
        return

    try:
        # Load config
        config = toml.load("config.toml")

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
    finally:
        _processing_lock.release()


class ProcessingService:
    """Service for processing packets"""

    def __init__(self, db: Session, config: dict = None):
        self.db = db
        # Use provided config or fall back to defaults
        if config is None:
            config = {}
        self.config = config

        # DosemuRunner expects the full config
        self.dosemu_runner = DosemuRunner(config)

    async def process_batch(self):
        """Process all unprocessed packets"""
        logger.info("Starting batch...")

        # Get unprocessed packets
        packets = self.db.query(Packet).filter(Packet.processed_at == None).all()

        if not packets:
            logger.info("No packets to process")
            # Still check for nodelists and other outbound files
            await self.scan_outbound_folders()
            return

        logger.info(f"Found {len(packets)} packet(s) to process")

        # Group by game type (stored as single letter: B or F)
        bre_packets = [p for p in packets if self.get_game_type(p) == "B"]
        fe_packets = [p for p in packets if self.get_game_type(p) == "F"]

        # Create processing run
        run = ProcessingRun(status="running")
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        results = {}

        try:
            # Process BRE
            if bre_packets:
                logger.info(f"Processing {len(bre_packets)} BRE packet(s)")
                results["BRE"] = await self.process_game_batch(
                    "BRE", bre_packets, run.id
                )

            # Process FE
            if fe_packets:
                logger.info(f"Processing {len(fe_packets)} FE packet(s)")
                results["FE"] = await self.process_game_batch("FE", fe_packets, run.id)

            # Update run status
            run.completed_at = datetime.now()
            run.packets_processed = len(packets)
            run.status = "completed"

            # Combine dosemu output
            outputs = [r.get("output", "") for r in results.values() if r]
            run.dosemu_log = "\n\n".join(outputs)

        except Exception as e:
            logger.error(f"Error: {e}")
            run.completed_at = datetime.now()
            run.status = "error"
            run.error_message = str(e)

        self.db.commit()

        # Check for sequence gaps
        validator = SequenceValidator(self.db)
        validator.check_sequences()

        logger.info("Batch complete")

        # Always scan outbound folders for nodelists
        await self.scan_outbound_folders()

        # Broadcast update via WebSocket
        from backend.services.websocket_service import broadcast_processing_complete

        await broadcast_processing_complete(run.id)

    def get_game_type(self, packet: Packet) -> str:
        """Get game type for packet"""
        league = self.db.query(League).filter(League.id == packet.league_id).first()
        return league.game_type if league else None

    async def scan_outbound_folders(self):
        """Scan all league outbound folders for nodelists and packets"""
        logger.info("Scanning outbound folders for nodelists...")

        # Get all active leagues
        leagues = self.db.query(League).filter(League.is_active == True).all()

        data_dir = self.config.get("server", {}).get("data_dir", "./data")

        for league in leagues:
            game_type_str = "BRE" if league.game_type == "B" else "FE"
            league_id = league.league_id

            # Get outbound folder path from config or use default
            try:
                league_config = self.config.get("dosemu", {}).get(league_id, {}).get(game_type_str.lower(), {})
                game_outbound_folder = league_config.get("outbound_folder")

                if game_outbound_folder:
                    game_outbound_dir = Path(game_outbound_folder)
                else:
                    # Fallback to default path
                    game_outbound_dir = Path(data_dir) / "dosemu" / league_id / game_type_str.lower() / "outbound"
            except Exception as e:
                logger.warning(f"Error getting outbound folder for league {league_id}: {e}")
                game_outbound_dir = Path(data_dir) / "dosemu" / league_id / game_type_str.lower() / "outbound"

            # Check if directory exists and has files
            if game_outbound_dir.exists() and any(game_outbound_dir.iterdir()):
                logger.info(f"Checking outbound folder for league {league_id}{league.game_type}: {game_outbound_dir}")
                await self.collect_outbound_packets(game_type_str, 0, game_outbound_dir)

    async def process_game_batch(self, game_type: str, packets: list, run_id: int):
        """Process a batch of packets for one game"""
        # Get league_id from first packet (all packets in batch should be for same league)
        if not packets:
            return {"status": "success", "message": "No packets to process"}

        # Get league_id as string for config lookup
        first_packet = packets[0]
        league = self.db.query(League).filter(League.id == first_packet.league_id).first()
        if not league:
            return {"status": "error", "error": f"League not found for packet {first_packet.filename}"}

        league_id = league.league_id  # This is the string league ID like "555"

        # Get directory paths
        data_dir = self.config.get("server", {}).get("data_dir", "./data")

        # Hub packet directories (where packets arrive/depart for the hub)
        hub_inbound_dir = Path(data_dir) / "packets" / "inbound"
        hub_outbound_dir = Path(data_dir) / "packets" / "outbound"

        # Game processing directories (where BRE/FE looks for packets)
        # Get from DOSEMU league config or fallback to default
        try:
            league_config = self.config.get("dosemu", {}).get(league_id, {}).get(game_type.lower(), {})
            game_inbound_folder = league_config.get("inbound_folder")
            game_outbound_folder = league_config.get("outbound_folder")

            if game_inbound_folder and game_outbound_folder:
                game_inbound_dir = Path(game_inbound_folder)
                game_outbound_dir = Path(game_outbound_folder)
                logger.debug(f"Using game folders from config: {game_inbound_dir}, {game_outbound_dir}")
            else:
                # Fallback to default paths
                drive_path = Path(data_dir) / "dosemu" / league_id / game_type.lower()
                game_inbound_dir = drive_path / "inbound"
                game_outbound_dir = drive_path / "outbound"
                logger.debug(f"Using default game folders: {game_inbound_dir}, {game_outbound_dir}")
        except Exception as e:
            logger.warning(f"Error getting game folders from config: {e}, using defaults")
            drive_path = Path(data_dir) / "dosemu" / league_id / game_type.lower()
            game_inbound_dir = drive_path / "inbound"
            game_outbound_dir = drive_path / "outbound"

        game_inbound_dir.mkdir(parents=True, exist_ok=True)
        game_outbound_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: Copy packets from hub inbound to game inbound (case-insensitive)
            for packet in packets:
                src = find_file_case_insensitive(hub_inbound_dir, packet.filename)

                if src:
                    dst = game_inbound_dir / packet.filename
                    shutil.copy2(src, dst)
                    logger.debug(f"Copied: {src.name} -> {dst}")
                else:
                    logger.warning(f"Source file not found in hub inbound: {packet.filename}")

            # Step 2: Run dosemu
            logger.info(f"Running {game_type} for league {league_id} via Dosemu...")
            dosemu_result = await self.dosemu_runner.run_game_process(game_type, league_id)

            if dosemu_result["status"] != "success":
                logger.error(
                    f"Dosemu error: {dosemu_result.get('error', 'Unknown')}"
                )
                return dosemu_result

            # Step 3: Mark packets as processed and archive (case-insensitive)
            hub_processed_dir = Path(data_dir) / "packets" / "processed"
            hub_processed_dir.mkdir(parents=True, exist_ok=True)

            for packet in packets:
                packet.processed_at = datetime.now()

                # Archive - move from hub inbound to hub processed
                src = find_file_case_insensitive(hub_inbound_dir, packet.filename)

                if src:
                    dst = hub_processed_dir / src.name
                    src.rename(dst)
                    logger.info(f"Archived to hub processed: {src.name}")
                else:
                    logger.warning(f"Could not find file in hub inbound to archive: {packet.filename}")

            self.db.commit()

            # Step 4: Collect outbound packets from game outbound directory
            await self.collect_outbound_packets(game_type, run_id, game_outbound_dir)

            # Step 5: Run additional commands and ingest files
            await self.ingest_processing_files(game_type, league_id, run_id, league_config)

            # Step 6: Cleanup game inbound directory (remove processed files)
            for f in game_inbound_dir.glob("*"):
                if f.is_file():
                    f.unlink()
                    logger.debug(f"Cleaned up game inbound: {f.name}")

            return dosemu_result

        except Exception as e:
            logger.error(f"Error processing {game_type}: {e}")
            return {"status": "error", "error": str(e)}

    async def ingest_processing_files(
        self, game_type: str, league_id: str, run_id: int, league_config: dict
    ):
        """
        Run scores, routes, and bbsinfo commands, then ingest their output files
        """
        logger.info(f"Running additional commands for {game_type} league {league_id}...")

        # Run scores command
        logger.info("Running scores command...")
        scores_result = await self.dosemu_runner.run_scores_command(game_type, league_id)
        if scores_result["status"] != "success":
            logger.warning(f"Scores command failed: {scores_result.get('error', 'Unknown error')}")

        # Run routes command
        logger.info("Running routes command...")
        routes_result = await self.dosemu_runner.run_routes_command(game_type, league_id)
        if routes_result["status"] != "success":
            logger.warning(f"Routes command failed: {routes_result.get('error', 'Unknown error')}")

        # Run bbsinfo command
        logger.info("Running bbsinfo command...")
        bbsinfo_result = await self.dosemu_runner.run_bbsinfo_command(game_type, league_id)
        if bbsinfo_result["status"] != "success":
            logger.warning(f"BBS info command failed: {bbsinfo_result.get('error', 'Unknown error')}")

        # Now ingest the files
        logger.info("Ingesting output files...")

        # Get scores_folder and game_dos_path from config
        scores_folder = league_config.get("scores_folder")
        game_dos_path = league_config.get("game_dos_path", "C:\\")

        # Convert DOS path to Linux path
        # game_dos_path is like "C:\\bbs\\doors\\bre_013"
        # We need to convert this to the actual filesystem path
        game_folder = league_config.get("game_folder")

        # Ingest score files
        if scores_folder:
            scores_dir = Path(scores_folder)
            score_filenames = [
                "BBSLAND.ANS",
                "BBSSCORE.ANS",
                "BBSWLAND.ANS",
                "BBSWORTH.ANS",
                "PLYLAND.ANS",
                "PLYSCORE.ANS",
                "PLYWLAND.ANS",
                "PLYWORTH.ANS",
                "SCORES.ANS",
                "YESNEWS.ANS",
                "tdynews.ans",
            ]

            for filename in score_filenames:
                file_path = find_file_case_insensitive(scores_dir, filename)
                if file_path:
                    try:
                        file_data = file_path.read_text(errors="replace")
                        file_size = len(file_data)

                        # Store in database
                        run_file = ProcessingRunFile(
                            processing_run_id=run_id,
                            file_type="score",
                            filename=file_path.name,  # Use actual filename found
                            file_data=file_data,
                            file_size=file_size,
                        )
                        self.db.add(run_file)
                        logger.info(f"Ingested score file: {file_path.name}")
                    except Exception as e:
                        logger.error(f"Error ingesting score file {filename}: {e}")
                else:
                    logger.debug(f"Score file not found: {filename}")

        # Ingest routes.lst
        if game_folder:
            game_dir = Path(game_folder)
            routes_file = find_file_case_insensitive(game_dir, "routes.lst")
            if routes_file:
                try:
                    file_data = routes_file.read_text(errors="replace")
                    file_size = len(file_data)

                    run_file = ProcessingRunFile(
                        processing_run_id=run_id,
                        file_type="routes",
                        filename=routes_file.name,
                        file_data=file_data,
                        file_size=file_size,
                    )
                    self.db.add(run_file)
                    logger.info(f"Ingested routes file: {routes_file.name}")
                except Exception as e:
                    logger.error(f"Error ingesting routes file: {e}")
            else:
                logger.debug("Routes file not found: routes.lst")

        # Ingest bbsinfo.lst
        if game_folder:
            game_dir = Path(game_folder)
            bbsinfo_file = find_file_case_insensitive(game_dir, "bbsinfo.lst")
            if bbsinfo_file:
                try:
                    file_data = bbsinfo_file.read_text(errors="replace")
                    file_size = len(file_data)

                    run_file = ProcessingRunFile(
                        processing_run_id=run_id,
                        file_type="bbsinfo",
                        filename=bbsinfo_file.name,
                        file_data=file_data,
                        file_size=file_size,
                    )
                    self.db.add(run_file)
                    logger.info(f"Ingested bbsinfo file: {bbsinfo_file.name}")
                except Exception as e:
                    logger.error(f"Error ingesting bbsinfo file: {e}")
            else:
                logger.debug("BBS info file not found: bbsinfo.lst")

        self.db.commit()
        logger.info("File ingestion complete")

    async def collect_outbound_packets(
        self, game_type: str, run_id: int, outbound_dir: Path
    ):
        """Collect packets generated by the game"""
        hub_index = self.config["hub"]["bbs_index"]

        # Convert game_type to single letter for DB query (BRE->B, FE->F)
        game_type_letter = "B" if game_type == "BRE" else "F"

        for packet_file in outbound_dir.glob("*"):
            if not packet_file.is_file():
                continue

            # Check if this is a nodelist file (BRNODES.<league> or FENODES.<league>)
            filename_upper = packet_file.name.upper()
            if filename_upper.startswith("BRNODES.") or filename_upper.startswith("FENODES."):
                await self.handle_nodelist_file(packet_file, game_type)
                continue

            try:
                packet_info = parse_packet_filename(packet_file.name)

                # Skip hub-destined packets (watcher handles those)
                if packet_info["dest_bbs_index"] == hub_index:
                    continue

                # Get or create league (game_type stored as single letter in DB)
                league = (
                    self.db.query(League)
                    .filter(
                        League.league_id == packet_info["league_id"],
                        League.game_type == game_type_letter,
                    )
                    .first()
                )

                if not league:
                    league = League(
                        league_id=packet_info["league_id"],
                        game_type=game_type_letter,
                        name=f"{game_type} League {packet_info['league_id']}",
                        is_active=True,
                    )
                    self.db.add(league)
                    self.db.commit()
                    self.db.refresh(league)

                # Read file
                content = packet_file.read_bytes()
                file_hash = hashlib.sha256(content).hexdigest()

                # Normalize filename to uppercase (DOS convention)
                normalized_filename = packet_file.name.upper()

                # Move to hub outbound directory
                data_dir = self.config.get("server", {}).get("data_dir", "./data")
                hub_outbound_dir = Path(data_dir) / "packets" / "outbound"
                hub_outbound_dir.mkdir(parents=True, exist_ok=True)

                # Check for case-insensitive duplicate in destination
                existing_file = find_file_case_insensitive(hub_outbound_dir, normalized_filename)
                if existing_file:
                    # Overwrite old file (sequence wraparound case)
                    existing_file.unlink()
                    logger.debug(f"Overwriting old outbound file: {existing_file.name}")

                dest = hub_outbound_dir / normalized_filename
                packet_file.rename(dest)

                # Check if already exists in database (case-insensitive via normalization)
                existing = (
                    self.db.query(Packet)
                    .filter(Packet.filename == normalized_filename)
                    .first()
                )

                if existing:
                    # Update existing record with new data (sequence wraparound)
                    existing.source_bbs_index = packet_info["source_bbs_index"]
                    existing.dest_bbs_index = packet_info["dest_bbs_index"]
                    existing.sequence_number = packet_info["sequence_number"]
                    existing.file_size = len(content)
                    existing.file_data = content
                    existing.checksum = file_hash
                    existing.processing_run_id = run_id
                    existing.processed_at = datetime.now()
                    existing.is_downloaded = False  # Reset so clients see it as new
                    existing.downloaded_at = None
                    logger.info(f"Updated outbound packet: {normalized_filename} (seq: {packet_info['sequence_number']})")
                else:
                    # Create new packet record
                    packet = Packet(
                        filename=normalized_filename,
                        league_id=league.id,
                        source_bbs_index=packet_info["source_bbs_index"],
                        dest_bbs_index=packet_info["dest_bbs_index"],
                        sequence_number=packet_info["sequence_number"],
                        file_size=len(content),
                        file_data=content,
                        checksum=file_hash,
                        processing_run_id=run_id,
                        processed_at=datetime.now(),
                    )
                    self.db.add(packet)
                    logger.info(f"Collected outbound: {normalized_filename}")

                self.db.commit()

                # Broadcast packet available
                from backend.services.websocket_service import (
                    broadcast_packet_available,
                )

                await broadcast_packet_available(normalized_filename, packet_info["dest_bbs_index"])

            except Exception as e:
                logger.error(f"Error collecting {packet_file.name}: {e}")

    async def handle_nodelist_file(self, nodelist_file: Path, game_type: str):
        """
        Handle nodelist files (BRNODES.<league> or FENODES.<league>)
        These are hub-generated files that all clients should download
        """
        # Extract league ID from filename (e.g., BRNODES.013 -> 013)
        # Always normalize to uppercase for consistency
        filename_upper = nodelist_file.name.upper()
        if filename_upper.startswith("BRNODES."):
            league_number = filename_upper[8:]  # Skip "BRNODES."
        elif filename_upper.startswith("FENODES."):
            league_number = filename_upper[8:]  # Skip "FENODES."
        else:
            logger.warning(f"Invalid nodelist filename: {nodelist_file.name}")
            return

        # Create nodelists directory structure: data_dir/nodelists/<game_type>/<league_id>/
        data_dir = self.config.get("server", {}).get("data_dir", "./data")
        nodelists_dir = Path(data_dir) / "nodelists" / game_type.lower() / league_number
        nodelists_dir.mkdir(parents=True, exist_ok=True)

        # Always use uppercase filename for consistency
        dest = nodelists_dir / filename_upper

        # Check for case-insensitive duplicate in destination
        existing_file = find_file_case_insensitive(nodelists_dir, filename_upper)
        if existing_file and existing_file != dest:
            # Remove old version with different case
            existing_file.unlink()
            logger.info(f"Removed old nodelist: {existing_file.name}")

        # Move and rename to uppercase
        nodelist_file.rename(dest)
        logger.info(f"Updated nodelist: {dest.name} for league {league_number}")

        # Read the nodelist file for database storage
        with open(dest, 'rb') as f:
            file_data = f.read()
        file_size = len(file_data)
        checksum = hashlib.sha256(file_data).hexdigest()

        # Find the league in the database
        # Convert game_type to single letter (BRE->B, FE->F)
        game_type_letter = "B" if game_type == "BRE" else "F"
        league = self.db.query(League).filter(
            League.league_id == league_number,
            League.game_type == game_type_letter
        ).first()

        if not league:
            logger.warning(f"League {league_number}{game_type_letter} not found in database, skipping packet creation")
        else:
            # Get all active members of this league
            memberships = self.db.query(LeagueMembership).filter(
                LeagueMembership.league_id == league.id,
                LeagueMembership.is_active == True
            ).all()

            # Create a packet record for each league member
            for membership in memberships:
                if membership.bbs_index is None:
                    continue

                # Convert BBS index to 2-char hex
                dest_bbs_hex = f"{membership.bbs_index:02X}"

                # Check if packet already exists for this member (avoid duplicates)
                # Use uppercase filename for consistency
                existing = self.db.query(Packet).filter(
                    Packet.filename == filename_upper,
                    Packet.league_id == league.id,
                    Packet.dest_bbs_index == dest_bbs_hex
                ).first()

                if existing:
                    # Update existing packet with new data
                    existing.file_data = file_data
                    existing.file_size = file_size
                    existing.checksum = checksum
                    existing.uploaded_at = datetime.utcnow()
                    existing.processed_at = datetime.utcnow()  # Mark as processed
                    existing.is_processed = True
                    existing.is_downloaded = False
                    existing.downloaded_at = None
                    logger.debug(f"Updated existing nodelist packet for BBS {dest_bbs_hex}")
                else:
                    # Create new packet record (use uppercase filename)
                    packet = Packet(
                        filename=filename_upper,
                        league_id=league.id,
                        source_bbs_index="00",  # Hub/system source
                        dest_bbs_index=dest_bbs_hex,
                        sequence_number=0,  # Nodelists don't use sequence numbers
                        source_client_id=None,  # Hub-generated
                        dest_client_id=membership.client_id,
                        file_data=file_data,
                        file_size=file_size,
                        checksum=checksum,
                        is_processed=True,  # Nodelists don't need processing
                        processed_at=datetime.utcnow(),  # Mark as processed immediately
                        is_downloaded=False
                    )
                    self.db.add(packet)
                    logger.debug(f"Created nodelist packet for BBS {dest_bbs_hex}")

            self.db.commit()
            logger.info(f"Created/updated {len(memberships)} nodelist packet records")

        # Broadcast nodelist update via WebSocket
        from backend.services.websocket_service import broadcast_nodelist_available

        try:
            await broadcast_nodelist_available(league_number, game_type)
        except Exception as e:
            logger.error(f"Could not broadcast nodelist update: {e}")
