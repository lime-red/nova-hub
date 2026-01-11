# app/services/processing_service.py - Complete with database

import asyncio
import hashlib
import shutil
import threading
import toml
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.database import League, Packet, ProcessingRun, SessionLocal
from app.services.dosemu_runner import DosemuRunner
from app.services.packet_service import parse_packet_filename
from app.services.sequence_validator import SequenceValidator

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
        print("[Processing] Task already running, skipping")
        return

    try:
        # Get the running event loop
        loop = asyncio.get_event_loop()
        # Create new task
        _processing_task = loop.create_task(process_batch())
        print("[Processing] Background processing task started")
    except RuntimeError as e:
        print(f"[Processing] Failed to start task: {e}")
        # Fallback: try to get running loop
        try:
            loop = asyncio.get_running_loop()
            _processing_task = loop.create_task(process_batch())
            print("[Processing] Background processing task started (running loop)")
        except Exception as e2:
            print(f"[Processing] Failed to start task (fallback): {e2}")


async def process_batch():
    """Main processing batch - runs asynchronously"""

    print("[Processing] process_batch() called")

    # Try to acquire lock (non-blocking)
    if not _processing_lock.acquire(blocking=False):
        print("[Processing] Already running, skipping")
        return

    try:
        # Load config
        config = toml.load("config.toml")

        db = SessionLocal()
        try:
            processor = ProcessingService(db, config)
            await processor.process_batch()
        except Exception as e:
            print(f"[Processing] Error in process_batch: {e}")
            import traceback
            traceback.print_exc()
        finally:
            db.close()
    except Exception as e:
        print(f"[Processing] Fatal error in process_batch: {e}")
        import traceback
        traceback.print_exc()
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
        print("[Processing] Starting batch...")

        # Get unprocessed packets
        packets = self.db.query(Packet).filter(Packet.processed_at == None).all()

        if not packets:
            print("[Processing] No packets to process")
            return

        print(f"[Processing] Found {len(packets)} packet(s) to process")

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
                print(f"[Processing] Processing {len(bre_packets)} BRE packet(s)")
                results["BRE"] = await self.process_game_batch(
                    "BRE", bre_packets, run.id
                )

            # Process FE
            if fe_packets:
                print(f"[Processing] Processing {len(fe_packets)} FE packet(s)")
                results["FE"] = await self.process_game_batch("FE", fe_packets, run.id)

            # Update run status
            run.completed_at = datetime.now()
            run.packets_processed = len(packets)
            run.status = "completed"

            # Combine dosemu output
            outputs = [r.get("output", "") for r in results.values() if r]
            run.dosemu_log = "\n\n".join(outputs)

        except Exception as e:
            print(f"[Processing] Error: {e}")
            run.completed_at = datetime.now()
            run.status = "error"
            run.error_message = str(e)

        self.db.commit()

        # Check for sequence gaps
        validator = SequenceValidator(self.db)
        validator.check_sequences()

        print("[Processing] Batch complete")

        # Broadcast update via WebSocket
        from app.services.websocket_service import broadcast_processing_complete

        await broadcast_processing_complete(run.id)

    def get_game_type(self, packet: Packet) -> str:
        """Get game type for packet"""
        league = self.db.query(League).filter(League.id == packet.league_id).first()
        return league.game_type if league else None

    async def process_game_batch(self, game_type: str, packets: list, run_id: int):
        """Process a batch of packets for one game"""
        # Get league_id from first packet (all packets in batch should be for same league)
        if not packets:
            return {"status": "success", "message": "No packets to process"}

        # Get league_id as string for config lookup
        from app.database import League
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
                print(f"[Processing] Using game folders from config: {game_inbound_dir}, {game_outbound_dir}")
            else:
                # Fallback to default paths
                drive_path = Path(data_dir) / "dosemu" / league_id / game_type.lower()
                game_inbound_dir = drive_path / "inbound"
                game_outbound_dir = drive_path / "outbound"
                print(f"[Processing] Using default game folders: {game_inbound_dir}, {game_outbound_dir}")
        except Exception as e:
            print(f"[Processing] Error getting game folders from config: {e}, using defaults")
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
                    print(f"[Processing] Copied: {src.name} -> {dst}")
                else:
                    print(f"[Processing] Source file not found in hub inbound: {packet.filename}")

            # Step 2: Run dosemu
            print(f"[Processing] Running {game_type} for league {league_id} via Dosemu...")
            dosemu_result = await self.dosemu_runner.run_game_process(game_type, league_id)

            if dosemu_result["status"] != "success":
                print(
                    f"[Processing] Dosemu error: {dosemu_result.get('error', 'Unknown')}"
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
                    print(f"[Processing] Archived to hub processed: {src.name}")
                else:
                    print(f"[Processing] Could not find file in hub inbound to archive: {packet.filename}")

            self.db.commit()

            # Step 4: Collect outbound packets from game outbound directory
            await self.collect_outbound_packets(game_type, run_id, game_outbound_dir)

            # Step 5: Cleanup game inbound directory (remove processed files)
            for f in game_inbound_dir.glob("*"):
                if f.is_file():
                    f.unlink()
                    print(f"[Processing] Cleaned up game inbound: {f.name}")

            return dosemu_result

        except Exception as e:
            print(f"[Processing] Error processing {game_type}: {e}")
            return {"status": "error", "error": str(e)}

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
                    print(f"[Processing] Skipping duplicate outbound file: {packet_file.name} (found as {existing_file.name})")
                    packet_file.unlink()  # Remove from game outbound
                    continue

                dest = hub_outbound_dir / normalized_filename
                packet_file.rename(dest)

                # Check if already exists in database (case-insensitive via normalization)
                existing = (
                    self.db.query(Packet)
                    .filter(Packet.filename == normalized_filename)
                    .first()
                )

                if not existing:
                    # Create packet record
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
                    self.db.commit()

                    print(f"[Processing] Collected outbound: {normalized_filename}")

                    # Broadcast packet available
                    from app.services.websocket_service import (
                        broadcast_packet_available,
                    )

                    await broadcast_packet_available(normalized_filename, packet_info["dest_bbs_index"])

            except Exception as e:
                print(f"[Processing] Error collecting {packet_file.name}: {e}")

    async def handle_nodelist_file(self, nodelist_file: Path, game_type: str):
        """
        Handle nodelist files (BRNODES.<league> or FENODES.<league>)
        These are hub-generated files that all clients should download
        """
        # Extract league ID from filename (e.g., BRNODES.013 -> 013)
        filename_upper = nodelist_file.name.upper()
        if filename_upper.startswith("BRNODES."):
            league_id = filename_upper[8:]  # Skip "BRNODES."
        elif filename_upper.startswith("FENODES."):
            league_id = filename_upper[8:]  # Skip "FENODES."
        else:
            print(f"[Processing] Invalid nodelist filename: {nodelist_file.name}")
            return

        # Create nodelists directory structure: data_dir/nodelists/<game_type>/<league_id>/
        data_dir = self.config.get("server", {}).get("data_dir", "./data")
        nodelists_dir = Path(data_dir) / "nodelists" / game_type.lower() / league_id
        nodelists_dir.mkdir(parents=True, exist_ok=True)

        # Move nodelist to nodelists directory (overwrite if exists)
        dest = nodelists_dir / nodelist_file.name

        # Check for case-insensitive duplicate in destination
        existing_file = find_file_case_insensitive(nodelists_dir, nodelist_file.name)
        if existing_file and existing_file != dest:
            # Remove old version with different case
            existing_file.unlink()
            print(f"[Processing] Removed old nodelist: {existing_file.name}")

        nodelist_file.rename(dest)
        print(f"[Processing] Updated nodelist: {dest.name} for league {league_id}")

        # Broadcast nodelist update via WebSocket
        from app.services.websocket_service import broadcast_nodelist_available

        try:
            await broadcast_nodelist_available(league_id, game_type)
        except Exception as e:
            print(f"[Processing] Could not broadcast nodelist update: {e}")
