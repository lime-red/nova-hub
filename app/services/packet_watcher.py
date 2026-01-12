# services/packet_watcher.py

import asyncio
import hashlib
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from app.logging_config import get_logger

logger = get_logger(context="watcher")

class PacketWatcher(FileSystemEventHandler):
    """
    Watch dosemu outbound folders for independently generated packets
    (when hub BBS generates packets from gameplay, not from API-triggered processing)

    NOTE: Uses watchdog library (Linux inotify) - Windows implementation will differ
    """

    def __init__(self, packet_service, config, loop):
        self.packet_service = packet_service
        self.config = config
        self.processing_files = set()  # Track files being processed
        self.loop = loop  # Reference to main event loop

    def on_created(self, event):
        """Handle new file creation"""
        if event.is_directory:
            return

        filepath = Path(event.src_path)

        # Only process packet files
        if not self._is_packet_file(filepath.name):
            return

        # Schedule async processing in the main event loop
        asyncio.run_coroutine_threadsafe(self._process_new_packet(filepath), self.loop)

    def on_modified(self, event):
        """Handle file modifications (game might write in chunks)"""
        # We'll rely on a settling time in _process_new_packet
        pass

    async def _process_new_packet(self, filepath: Path):
        """Process a newly detected packet file"""
        filename = filepath.name

        # Prevent duplicate processing
        if filename in self.processing_files:
            return

        self.processing_files.add(filename)

        try:
            # Check if file still exists (may have been moved by processing service)
            if not filepath.exists():
                logger.debug(f"File {filename} already processed by main processing service")
                return

            # Wait for file to be completely written
            # Game should have file locking, but be safe
            await asyncio.sleep(2)

            # Verify file still exists and is stable (size hasn't changed)
            if not filepath.exists():
                logger.debug(f"File {filename} was moved during processing")
                return

            size1 = filepath.stat().st_size
            await asyncio.sleep(1)

            if not filepath.exists():
                logger.debug(f"File {filename} was moved during processing")
                return

            size2 = filepath.stat().st_size

            if size1 != size2:
                # Still being written, wait more
                await asyncio.sleep(3)

            # Parse packet info
            from app.services.packet_service import parse_packet_filename

            packet_info = parse_packet_filename(filename)

            if not packet_info:
                logger.warning(f"Skipping {filename} - invalid packet filename format")
                return

            # Final check before reading
            if not filepath.exists():
                logger.debug(f"File {filename} was moved before reading")
                return

            # Read file for hash
            content = filepath.read_bytes()
            file_hash = hashlib.sha256(content).hexdigest()

            # Normalize filename to uppercase (DOS convention)
            normalized_filename = filename.upper()

            # Get data directory from config
            if isinstance(self.config, dict):
                data_dir = self.config.get("server", {}).get("data_dir", "./data")
            else:
                data_dir = self.config.server.data_dir

            # Move to outbound directory
            dest_path = Path(data_dir) / "packets" / "outbound" / normalized_filename
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            filepath.rename(dest_path)

            # Register in database - need to look up the league in the database
            from app.database import League
            league = self.packet_service.db.query(League).filter(
                League.league_id == packet_info["league_id"],
                League.game_type == packet_info["game_type"]
            ).first()

            if not league:
                logger.warning(f"No league found for {filename} (league {packet_info['league_id']}, game {packet_info['game_type']})")
                # Move it back or handle appropriately
                dest_path.rename(filepath)
                return

            # Create packet record
            from app.database import Packet
            packet = Packet(
                filename=normalized_filename,
                league_id=league.id,
                source_bbs_index=packet_info["source_bbs_index"],
                dest_bbs_index=packet_info["dest_bbs_index"],
                sequence_number=packet_info["sequence_number"],
                file_data=content,  # Store the actual packet data
                file_size=len(content),
                checksum=file_hash,  # Use correct field name
                is_processed=False,  # Hub-generated packets are already "processed" by the game
            )
            self.packet_service.db.add(packet)
            self.packet_service.db.commit()

            logger.info(f"Registered hub-generated packet: {normalized_filename}")

        except FileNotFoundError:
            # File was moved by processing service - this is normal during automated processing
            logger.debug(f"File {filename} was moved by processing service (race condition resolved)")
        except Exception as e:
            logger.error(f"Error processing {filename}: {e}")
        finally:
            self.processing_files.discard(filename)

    def _is_packet_file(self, filename: str) -> bool:
        """Check if filename matches packet pattern"""
        from app.services.packet_service import parse_packet_filename

        return parse_packet_filename(filename) is not None


class WatcherService:
    """Manage file watchers for all game outbound directories"""

    def __init__(self, packet_service, config, loop):
        self.packet_service = packet_service
        self.config = config
        self.loop = loop  # Reference to main event loop
        self.observers = []

    def start(self):
        """Start watching all game outbound directories"""
        import platform

        if platform.system() == 'Windows':
            logger.warning("File watcher not yet implemented for Windows")
            return

        # Iterate through all league configurations to find outbound folders
        if isinstance(self.config, dict):
            dosemu_config = self.config.get("dosemu", {})
        else:
            dosemu_config = self.config.dosemu

        # Track which directories we're watching
        watched_count = 0

        # Iterate through league configurations
        for key, value in dosemu_config.items():
            # Skip non-league keys (like "dosemu_path", "config_dir", etc.)
            if not isinstance(value, dict):
                continue

            # This is a league_id (e.g., "555")
            league_id = key

            for game_key, game_config in value.items():
                # This is game_type (e.g., "bre", "fe")
                if not isinstance(game_config, dict):
                    continue

                game_type = game_key.upper()

                # Get outbound_folder from config
                if isinstance(game_config, dict):
                    outbound_folder = game_config.get("outbound_folder")
                else:
                    outbound_folder = getattr(game_config, "outbound_folder", None)

                if outbound_folder:
                    outbound_path = Path(outbound_folder)
                    outbound_path.mkdir(parents=True, exist_ok=True)
                    self._start_observer(outbound_path, f"{game_type} League {league_id}")
                    watched_count += 1

        if watched_count > 0:
            logger.info(f"Started monitoring {watched_count} outbound directory/directories for hub-generated packets")
        else:
            logger.warning("No outbound folders configured to watch")

    def _start_observer(self, path: Path, game_type: str):
        """Start observer for specific directory"""
        handler = PacketWatcher(self.packet_service, self.config, self.loop)
        observer = Observer()
        observer.schedule(handler, str(path), recursive=False)
        observer.start()
        self.observers.append(observer)
        logger.info(f"Monitoring {game_type}: {path}")

        # Process any existing files in the directory
        self._process_existing_files(path, handler)

    async def _process_existing_files_async(self, path: Path, handler: PacketWatcher):
        """Process files that already exist in the watched directory (async version)"""
        try:
            existing_files = [f for f in path.glob("*") if f.is_file()]
            if existing_files:
                logger.info(f"Found {len(existing_files)} existing file(s) in {path}")
                for filepath in existing_files:
                    if handler._is_packet_file(filepath.name):
                        logger.debug(f"Processing existing packet: {filepath.name}")
                        # Process the existing file
                        await handler._process_new_packet(filepath)
        except Exception as e:
            logger.error(f"Error scanning existing files in {path}: {e}")

    def _process_existing_files(self, path: Path, handler: PacketWatcher):
        """Schedule processing of existing files"""
        # Store for later async processing
        if not hasattr(self, '_pending_scans'):
            self._pending_scans = []
        self._pending_scans.append((path, handler))

    async def process_pending_scans(self):
        """Process all pending directory scans (call this after event loop is available)"""
        if not hasattr(self, '_pending_scans'):
            return

        for path, handler in self._pending_scans:
            await self._process_existing_files_async(path, handler)

        # Clear the pending scans
        self._pending_scans = []

    def stop(self):
        """Stop all observers"""
        for observer in self.observers:
            observer.stop()
            observer.join()
        logger.info("Stopped all file watchers")
