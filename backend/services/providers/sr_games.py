"""The SR games, Barren Realms Elite and Falcon's Eye, as the hub carries them.

DOS doors run under dosemu. The hub's copy of the game sees every packet a
member uploads, routed through it or not; packets the game writes come out of
its OUTBOUND under 8.3 names whose 000-999 sequence wraps, so a name is reused
and a reused name is a new packet. The game itself sends the nodelist, as
BRNODES.<league> or FENODES.<league>, once the hub has put it in the game's
nodes file.
"""

import hashlib
import shutil
import time
from pathlib import Path
from typing import Optional

from backend.core.clock import utcnow
from backend.models.database import League, Packet, ProcessingRunFile
from backend.services.attack_trace import record_safely
from backend.services.games import game_for_letter, nodelist_game
from backend.services.packet_service import parse_packet_filename
from backend.services.providers.base import GameProvider, logger, publish_nodelist

# Written by the scores command into scores_folder; ingested after each run.
SCORE_FILES = (
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
)


def _find(directory: Path, filename: str) -> Optional[Path]:
    from backend.services.processing_service import find_file_case_insensitive
    return find_file_case_insensitive(directory, filename)


class SRGamesProvider(GameProvider):
    checks_sequences = True
    reuses_names = True
    expire_packet_files = False
    reads_transcripts = True

    # -- where the game is ----------------------------------------------------

    @staticmethod
    def _settings(service, league: League) -> dict:
        """[dosemu.<league>.<bre|fe>]"""
        code = game_for_letter(league.game_type).key
        return service.config.get("dosemu", {}).get(league.league_id, {}).get(code, {})

    @staticmethod
    def _default_drive(service, league: League) -> Path:
        data_dir = service.config.get("server", {}).get("data_dir", "./data")
        return Path(data_dir) / "dosemu" / league.league_id / game_for_letter(league.game_type).key

    def _game_dirs(self, service, league: League) -> tuple[Path, Path]:
        """The game's own INBOUND and OUTBOUND, from config or the default drive."""
        try:
            settings = self._settings(service, league)
            inbound, outbound = settings.get("inbound_folder"), settings.get("outbound_folder")
            if inbound and outbound:
                logger.debug(f"Using game folders from config: {inbound}, {outbound}")
                return Path(inbound), Path(outbound)
        except Exception as e:
            logger.warning(f"Error getting game folders from config: {e}, using defaults")
        drive = self._default_drive(service, league)
        logger.debug(f"Using default game folders: {drive / 'inbound'}, {drive / 'outbound'}")
        return drive / "inbound", drive / "outbound"

    def outbound_dir(self, service, league: League) -> Optional[Path]:
        try:
            outbound = self._settings(service, league).get("outbound_folder")
        except Exception as e:
            logger.warning(f"Error getting outbound folder for league {league.league_id}: {e}")
            outbound = None
        return Path(outbound) if outbound else self._default_drive(service, league) / "outbound"

    # -- one league's batch ---------------------------------------------------

    async def process_league(self, service, league: League, packets: list, run_id: int) -> dict:
        game_type = game_for_letter(league.game_type).code
        league_id = league.league_id  # the string league number, like "555"
        data_dir = service.config.get("server", {}).get("data_dir", "./data")

        # Hub packet directories (where packets arrive/depart for the hub)
        hub_inbound_dir = Path(data_dir) / "packets" / "inbound"
        hub_outbound_dir = Path(data_dir) / "packets" / "outbound"

        # Game processing directories (where BRE/FE looks for packets)
        league_config = self._settings(service, league)
        game_inbound_dir, game_outbound_dir = self._game_dirs(service, league)
        game_inbound_dir.mkdir(parents=True, exist_ok=True)
        game_outbound_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: Copy packets from hub inbound to game inbound (case-insensitive)
            # Remember what we handed the game, so step 6 can tell whether it took it.
            delivered = []
            for packet in packets:
                src = _find(hub_inbound_dir, packet.filename)

                if src:
                    dst = game_inbound_dir / packet.filename
                    shutil.copy2(src, dst)
                    delivered.append(dst)
                    logger.debug(f"Copied: {src.name} -> {dst}")
                else:
                    logger.warning(f"Source file not found in hub inbound: {packet.filename}")

            # Step 2: Run dosemu
            logger.info(f"Running {game_type} for league {league_id} via Dosemu...")
            dosemu_result = await service.dosemu_runner.run_game_process(game_type, league_id)

            if dosemu_result["status"] != "success":
                logger.error(
                    f"Dosemu error: {dosemu_result.get('error', 'Unknown')}"
                )
                return dosemu_result

            # Step 3: Mark packets as processed and archive (case-insensitive)
            hub_processed_dir = Path(data_dir) / "packets" / "processed"
            hub_processed_dir.mkdir(parents=True, exist_ok=True)
            hub_outbound_dir.mkdir(parents=True, exist_ok=True)

            hub_index = service.config["hub"]["bbs_index"]

            for packet in packets:
                packet.processed_at = utcnow()
                packet.is_processed = True

                # Archive - move from hub inbound to hub processed
                src = _find(hub_inbound_dir, packet.filename)

                if src:
                    # If direct routed (not for hub), copy to outbound first
                    if packet.dest_bbs_index != hub_index:
                        dst_outbound = hub_outbound_dir / src.name
                        shutil.copy2(src, dst_outbound)
                        logger.info(f"Direct routed packet {src.name} copied to outbound for BBS {packet.dest_bbs_index}")

                    dst = hub_processed_dir / src.name
                    src.rename(dst)
                    logger.info(f"Archived to hub processed: {src.name}")
                else:
                    logger.warning(f"Could not find file in hub inbound to archive: {packet.filename}")

            service.db.commit()

            # Step 4: Collect outbound packets from game outbound directory
            await service.collect_outbound_packets(game_type, run_id, game_outbound_dir)

            # Step 5: Run additional commands and ingest files
            await service.ingest_processing_files(game_type, league_id, league.id, run_id, league_config)

            # Step 6: Check what the game actually took.
            #
            # A healthy run consumes every packet handed to it -- the game deletes
            # each file as it ingests it. Anything still sitting here means the game
            # never saw it: a wrong or untraversable inbound path in BBS.CFG, or a
            # crash part-way through.
            #
            # Leave those files exactly where they are. The game's inbound folder is
            # where a packet legitimately waits until the game takes it, so once the
            # fault is fixed the next run picks it up with no operator action. This
            # used to unlink the whole directory, which threw away both the evidence
            # and the retry, and made a silently-ignored packet look identical to a
            # processed one. Re-offering a packet that *was* consumed is harmless:
            # both games detect duplicates.
            unconsumed = [f for f in delivered if f.exists()]
            if unconsumed:
                names = ", ".join(f.name for f in unconsumed)
                logger.warning(
                    f"{len(unconsumed)} packet(s) left unconsumed by {game_type} "
                    f"league {league_id}: {names}. The game did not ingest them - "
                    f"check the inbound path in BBS.CFG. They stay in "
                    f"{game_inbound_dir} and will be retried on the next run."
                )
            service._unconsumed_count += len(unconsumed)

            return dosemu_result

        except Exception as e:
            logger.error(f"Error processing {game_type}: {e}")
            return {"status": "error", "error": str(e)}

    @staticmethod
    def _report_listing_command(
        label: str, result: dict, game_dir: Path | None, filename: str, started: float
    ):
        """Log the outcome of a command whose real product is a listing file.

        A run counts as good when the file was written during it, whatever the
        exit code. Only a run that left the file untouched is a failure, and
        then the warning says so - the stale copy is still ingested below.
        """
        listing = _find(game_dir, filename) if game_dir else None
        # 2 s of slack: DOS file times have 2-second resolution.
        fresh = listing is not None and listing.stat().st_mtime >= started - 2

        if result["status"] == "success" and (fresh or game_dir is None):
            return
        if fresh:
            logger.debug(
                f"{label} command exited {result.get('returncode')} "
                f"but wrote {listing.name}; treating as success"
            )
            return

        cause = result.get("error") or f"status {result['status']}"
        if game_dir is None:
            logger.warning(f"{label} command failed: {cause}")
        elif listing is None:
            logger.warning(f"{label} command failed, no {filename} in {game_dir}: {cause}")
        else:
            logger.warning(
                f"{label} command did not refresh {listing.name}; "
                f"the previous copy will be ingested: {cause}"
            )

    async def ingest_processing_files(
        self, service, game_type: str, league_id: str, league_db_id: int, run_id: int,
        league_config: dict
    ):
        """
        Run scores, routes, and bbsinfo commands, then ingest their output files
        """
        logger.info(f"Running additional commands for {game_type} league {league_id}...")

        # Run scores command
        logger.info("Running scores command...")
        scores_result = await service.dosemu_runner.run_scores_command(game_type, league_id)
        if scores_result["status"] != "success":
            logger.warning(f"Scores command failed: {scores_result.get('error', 'Unknown error')}")

        # Run routes and bbsinfo commands.
        #
        # These are judged by the file they write, not by exit code: BRE and FE
        # end ROUTEINFO and BBSINFO with DOS errorlevel 1 even when they succeed
        # (every production transcript does), so since `script -e` began passing
        # the real exit code through, a working run logged two "failed" warnings.
        game_folder = league_config.get("game_folder")
        game_dir = Path(game_folder) if game_folder else None

        logger.info("Running routes command...")
        started = time.time()
        routes_result = await service.dosemu_runner.run_routes_command(game_type, league_id)
        self._report_listing_command("Routes", routes_result, game_dir, "routes.lst", started)

        logger.info("Running bbsinfo command...")
        started = time.time()
        bbsinfo_result = await service.dosemu_runner.run_bbsinfo_command(game_type, league_id)
        self._report_listing_command("BBS info", bbsinfo_result, game_dir, "bbsinfo.lst", started)

        # Now ingest the files
        logger.info("Ingesting output files...")

        def store(path: Path, file_type: str, label: str) -> None:
            try:
                file_data = path.read_text(errors="replace")
                service.db.add(ProcessingRunFile(
                    processing_run_id=run_id,
                    league_id=league_db_id,
                    file_type=file_type,
                    filename=path.name,  # the name as found
                    file_data=file_data,
                    file_size=len(file_data),
                ))
                logger.info(f"Ingested {label} file: {path.name}")
            except Exception as e:
                logger.error(f"Error ingesting {label} file {path.name}: {e}")

        scores_folder = league_config.get("scores_folder")
        if scores_folder:
            scores_dir = Path(scores_folder)
            for filename in SCORE_FILES:
                file_path = _find(scores_dir, filename)
                if file_path:
                    store(file_path, "score", "score")
                else:
                    logger.debug(f"Score file not found: {filename}")

        if game_dir is not None:
            for listing, file_type, label in (("routes.lst", "routes", "routes"),
                                              ("bbsinfo.lst", "bbsinfo", "bbsinfo")):
                path = _find(game_dir, listing)
                if path:
                    store(path, file_type, label)
                else:
                    logger.debug(f"{label} file not found: {listing}")

        service.db.commit()
        logger.info("File ingestion complete")

    # -- what the game wrote --------------------------------------------------

    async def collect_outbound(self, service, league: League, run_id: int, outbound_dir: Path):
        await self.collect_game_outbound(service, game_for_letter(league.game_type), run_id,
                                         outbound_dir)

    async def collect_game_outbound(self, service, game, run_id: int, outbound_dir: Path):
        """Collect packets generated by the game.

        Any league of the game: each packet's name says which, and one the
        database does not have yet is created.
        """
        hub_index = service.config["hub"]["bbs_index"]

        for packet_file in outbound_dir.glob("*"):
            if not packet_file.is_file():
                continue

            if nodelist_game(packet_file.name):
                await self.handle_nodelist_file(service, packet_file, game.code)
                continue

            try:
                packet_info = parse_packet_filename(packet_file.name)
                if not packet_info:
                    logger.warning(f"Not collecting {packet_file.name}: not a packet's name")
                    continue

                # The game's own (it is the hub): nothing to route
                if packet_info["dest_bbs_index"] == hub_index:
                    continue

                # Get or create league (game_type stored as single letter in DB)
                target = (
                    service.db.query(League)
                    .filter(
                        League.league_id == packet_info["league_id"],
                        League.game_type == game.letter,
                    )
                    .first()
                )

                if not target:
                    target = League(
                        league_id=packet_info["league_id"],
                        game_type=game.letter,
                        name=f"{game.code} League {packet_info['league_id']}",
                        is_active=True,
                    )
                    service.db.add(target)
                    service.db.commit()
                    service.db.refresh(target)

                # Read file
                content = packet_file.read_bytes()
                file_hash = hashlib.sha256(content).hexdigest()

                # Normalize filename to uppercase (DOS convention)
                normalized_filename = packet_file.name.upper()

                # Move to hub outbound directory
                data_dir = service.config.get("server", {}).get("data_dir", "./data")
                hub_outbound_dir = Path(data_dir) / "packets" / "outbound"
                hub_outbound_dir.mkdir(parents=True, exist_ok=True)

                # Check for case-insensitive duplicate in destination
                existing_file = _find(hub_outbound_dir, normalized_filename)
                if existing_file:
                    # Overwrite old file (sequence wraparound case)
                    existing_file.unlink()
                    logger.debug(f"Overwriting old outbound file: {existing_file.name}")

                dest = hub_outbound_dir / normalized_filename
                # shutil.move, not Path.rename: the game folder and the hub's
                # data dir are separate paths in config and need not share a
                # filesystem. rename() raises EXDEV when they don't.
                shutil.move(str(packet_file), str(dest))

                # The newest row under this name. Still pending: BRE has rewritten
                # that same packet in place (it grows under a stable name), so
                # update it. Already downloaded: the sequence number has wrapped and
                # this is a new packet -- give it its own row, as uploads get, so
                # the old one's history (when it was written and taken) survives.
                existing = (
                    service.db.query(Packet)
                    .filter(Packet.filename == normalized_filename)
                    .order_by(Packet.id.desc())
                    .first()
                )
                # downloaded_at too: rows from January 2026 have it set with
                # is_downloaded still false, and must not take a new packet's place.
                if existing is not None and (existing.is_downloaded
                                             or existing.downloaded_at is not None):
                    existing = None

                if existing:
                    # Update the pending packet with its rewritten contents
                    existing.source_bbs_index = packet_info["source_bbs_index"]
                    existing.dest_bbs_index = packet_info["dest_bbs_index"]
                    existing.sequence_number = packet_info["sequence_number"]
                    existing.file_size = len(content)
                    existing.checksum = file_hash
                    existing.processing_run_id = run_id
                    existing.processed_at = utcnow()
                    existing.is_processed = True
                    existing.file_data = None  # the file on disk is the packet now
                    logger.info(f"Updated pending outbound packet: {normalized_filename} (seq: {packet_info['sequence_number']})")
                    packet = existing
                else:
                    # Create new packet record (file stored on disk at hub_outbound_dir)
                    packet = Packet(
                        filename=normalized_filename,
                        league_id=target.id,
                        source_bbs_index=packet_info["source_bbs_index"],
                        dest_bbs_index=packet_info["dest_bbs_index"],
                        sequence_number=packet_info["sequence_number"],
                        file_size=len(content),
                        checksum=file_hash,
                        processing_run_id=run_id,
                        processed_at=utcnow(),
                        is_processed=True,
                    )
                    service.db.add(packet)
                    logger.info(f"Collected outbound: {normalized_filename}")

                service.db.commit()
                # A relayed attack is repacked here; this is the hop to its target.
                record_safely(service.db, packet, content)

            except Exception as e:
                logger.error(f"Error collecting {packet_file.name}: {e}")

    async def handle_nodelist_file(self, service, nodelist_file: Path, game_type: str):
        """
        Handle nodelist files (BRNODES.<league> or FENODES.<league>)
        These are hub-generated files that all clients should download
        """
        # Extract league ID from filename (e.g., BRNODES.013 -> 013)
        # Always normalize to uppercase for consistency
        filename_upper = nodelist_file.name.upper()
        nodelist_owner = nodelist_game(filename_upper)
        if not nodelist_owner:
            logger.warning(f"Invalid nodelist filename: {nodelist_file.name}")
            return
        league_number = nodelist_owner.nodelist_league(filename_upper)

        # Create nodelists directory structure: data_dir/nodelists/<game_type>/<league_id>/
        data_dir = service.config.get("server", {}).get("data_dir", "./data")
        nodelists_dir = Path(data_dir) / "nodelists" / game_type.lower() / league_number
        nodelists_dir.mkdir(parents=True, exist_ok=True)

        # Always use uppercase filename for consistency
        dest = nodelists_dir / filename_upper

        # Check for case-insensitive duplicate in destination
        existing_file = _find(nodelists_dir, filename_upper)
        if existing_file and existing_file != dest:
            # Remove old version with different case
            existing_file.unlink()
            logger.info(f"Removed old nodelist: {existing_file.name}")

        # Move and rename to uppercase. shutil.move for the same reason as the
        # outbound collector: this crosses from the game folder into the hub's
        # data dir, which may be a different filesystem.
        shutil.move(str(nodelist_file), str(dest))
        logger.info(f"Updated nodelist: {dest.name} for league {league_number}")

        # Find the league in the database
        letter = nodelist_owner.letter
        league = service.db.query(League).filter(
            League.league_id == league_number,
            League.game_type == letter
        ).first()

        if not league:
            logger.warning(f"League {league_number}{letter} not found in database, skipping packet creation")
            return
        publish_nodelist(service.db, data_dir, league, nodelist_owner, dest)

    # -- nodelist ---------------------------------------------------------------

    def generate_nodelist(self, db, config, league: League):
        """Write the nodelist and put it in the hub game's nodes file; the game
        sends it to the members on its next run (see nodelist_generator)."""
        from backend.services.nodelist_generator import NodelistGenerator
        data_dir = config.get("server", {}).get("data_dir", "./data")
        generator = NodelistGenerator(db, data_dir, config.get("hub", {}), config.get("dosemu", {}))
        dest = generator.generate(league.id)
        if dest is not None:
            return dest, generator.problems
        if not (league.hub_fidonet_address or "").strip():
            return None, [
                "League has no hub_fidonet_address, so the hub's own node entry "
                "and its HOST routing line cannot be written. Set it first; the "
                "existing nodelist has been left untouched."
            ]
        return None, ["No active members with a BBS index assigned — nodelist not generated"]

    # -- client config ----------------------------------------------------------

    def config_toml_league(self, entry) -> list[str]:
        from backend.services.claim_links import _toml_str
        host = f"/CHANGE-ME/.dosemu/drive_c/doors/{entry.folder.lower()}"
        dos = "C:\\CHANGE-ME\\DOORS\\" + entry.folder
        return [
            f"[leagues.{entry.code}.{entry.number}]",
            "enabled = true",
            f"bbs_index = {entry.bbs_index}  # assigned by the hub; do not change",
            f"outbound_dir = {_toml_str(host + '/OUTBOUND')}",
            f"inbound_dir = {_toml_str(host + '/INBOUND')}",
            "# The game's folder as Linux sees it, and the same folder as DOS sees it.",
            f"game_folder = {_toml_str(host)}",
            f"game_dos_path = {_toml_str(dos)}",
            f"game_command = {_toml_str(entry.code)}",
            'maintenance_args = "PLANETARY"',
        ]

    def config_psd1_league(self, entry) -> list[str]:
        from backend.services.claim_links import _psd1_str
        folder = "C:\\CHANGE-ME\\DOORS\\" + entry.folder
        outbound, inbound = folder + "\\OUTBOUND", folder + "\\INBOUND"
        return [
            "        @{",
            f"            Game            = {_psd1_str(entry.code)}",
            f"            Number          = {_psd1_str(entry.number)}",
            "            Enabled         = $true",
            f"            BbsIndex        = {entry.bbs_index}  # assigned by the hub; do not change",
            f"            OutboundDir     = {_psd1_str(outbound)}",
            f"            InboundDir      = {_psd1_str(inbound)}",
            f"            GameFolder      = {_psd1_str(folder)}",
            f"            GameCommand     = {_psd1_str(entry.code)}",
            "            MaintenanceArgs = 'PLANETARY'",
            "        }",
        ]
