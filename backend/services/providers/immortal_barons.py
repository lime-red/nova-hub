"""Immortal Barons, as the hub carries it (#100).

A native Go binary, not a DOS door. What differs from the SR games, and why:

- **The hub is node 1, the League Coordinator.** Its own copy of the game runs
  in [ib.<league>] data_dir with `-maint` and holds the Coordinator's key, so
  the league's rules, roster and resets are signed by the hub's game.
- **The hub routes; the game never does.** The roster has no HOST lines, so to
  every board the league is a mesh and each packet is written once, for its
  final destination. The hub delivers it. A destination of 0 is a broadcast,
  which the hub fans out as one packet row per board, the sender excepted.
- **The hub's game sees only its own mail**: packets addressed to node 1, and
  broadcasts. A packet between two members goes straight to the member.
- **No gap detection.** The sequence is one counter per sender, across all its
  destinations, and the game itself refuses replays.
- **A name is one packet, for ever.** `L555-<src>-<seq>-<dst>.brp` never
  repeats, so different bytes under a known name are a collision to report,
  never a replacement, and the files on disk need pruning by age.
- **Only `*.brp` is a packet** (in any case). A file renamed the FTN way on a
  duplicate, `.br1`, is never read, so nothing here renames on a collision.
  A file is staged under a non-`.brp` name and renamed into place.
- **The nodelist is ibnodes.dat**: BRNODES.DAT's six lines per board, plus an
  optional seventh, the board's ed25519 public key. The hub writes it into its
  own game, which signs it and broadcasts it on every run, and also publishes
  it as IBNODES.<league> for a board that has none yet: until a board has a
  roster it writes packets without a node number, under names the hub refuses.

References: docs/inter-bbs.md and docs/dev/ibbs-packet-format.md in
andy5995/immortal-barons.
"""

import hashlib
import os
import re
import shlex
from collections import defaultdict
from pathlib import Path
from typing import Optional

from backend.core.clock import utcnow
from backend.models.database import Client, League, LeagueMembership, Packet
from backend.services.games import game_for_letter
from backend.services.native_runner import NativeRunner
from backend.services.providers.base import (
    GameProvider,
    hub_hex,
    logger,
    place_without_overwrite,
    publish_nodelist,
)

BOARD_KEY = re.compile(r"[0-9a-f]{64}")
DEFAULT_ARGS = ("-maint",)
PACKET_EXT = ".brp"


def valid_board_key(value: Optional[str]) -> Optional[str]:
    """A board's public key as the roster carries it (64 lower-case hex), or None.

    The game reads a seventh line holding anything else as no key at all, which
    would leave that board's packets unchecked without a word; refuse it here
    instead, where someone can be told.
    """
    if value is None:
        return None
    value = value.strip().lower()
    if not value:
        return None
    if not BOARD_KEY.fullmatch(value):
        raise ValueError("A board key is the 64 hex characters -gen-board-key prints")
    return value


def is_packet_file(path: Path) -> bool:
    """What the game reads as a packet: a *.brp in any case, not a temp file."""
    return (path.suffix.lower() == PACKET_EXT and not path.name.startswith(".")
            and path.is_file())


def _field(value: Optional[str]) -> str:
    """One roster line. Every one of the six must hold something: a blank line
    ends the board's entry, and the game skips an entry with an empty field."""
    if value:
        value = "".join(ch for ch in value.replace("\r", " ").replace("\n", " ")
                        if ord(ch) >= 32 and ord(ch) != 127).strip()
    return value or "-"


class ImmortalBaronsProvider(GameProvider):
    checks_sequences = False
    reuses_names = False
    expire_packet_files = True

    # -- configuration --------------------------------------------------------

    @staticmethod
    def settings(config: dict, league: League) -> dict:
        """[ib.<league>]"""
        section = (config.get("ib") or {}).get(league.league_id)
        return section if isinstance(section, dict) else {}

    def _paths(self, config: dict, league: League) -> Optional[tuple[Path, Path, Path]]:
        """The hub's game: (data_dir, inbound, outbound), or None if unconfigured.

        inbound_dir and outbound_dir default to the game's own defaults, inbound
        and outbound inside data_dir; set them to whatever the hub's bbs.cfg
        says for GameInbound and GameOutbound.
        """
        settings = self.settings(config, league)
        if not settings.get("data_dir"):
            return None
        data = Path(settings["data_dir"])
        inbound = Path(settings.get("inbound_dir") or data / "inbound")
        outbound = Path(settings.get("outbound_dir") or data / "outbound")
        if not inbound.is_absolute():
            inbound = data / inbound
        if not outbound.is_absolute():
            outbound = data / outbound
        return data, inbound, outbound

    def _members(self, db, league: League, hub: str) -> list[LeagueMembership]:
        rows = db.query(LeagueMembership).filter(
            LeagueMembership.league_id == league.id,
            LeagueMembership.is_active == True,  # noqa: E712
            LeagueMembership.bbs_index.isnot(None),
        ).order_by(LeagueMembership.bbs_index).all()
        return [m for m in rows if f"{m.bbs_index:02X}" != hub]

    # -- routing ----------------------------------------------------------------

    def recipients(self, db, league: League, packet_info: dict, hub: str) -> list[str]:
        """A broadcast (destination 00) goes to the hub's game and every member
        but its sender; anything else to the board it names."""
        if packet_info["dest_bbs_index"] != "00":
            return [packet_info["dest_bbs_index"]]
        source = packet_info["source_bbs_index"]
        boards = [hub] + [f"{m.bbs_index:02X}" for m in self._members(db, league, hub)]
        return [b for b in boards if b != source]

    # -- one league's batch -------------------------------------------------------

    async def process_league(self, service, league: League, packets: list, run_id: int) -> dict:
        config, db = service.config, service.db
        paths = self._paths(config, league)
        settings = self.settings(config, league)
        if paths is None or not settings.get("binary"):
            return {"status": "error",
                    "error": f"[ib.{league.league_id}] needs binary and data_dir to run league "
                             f"{league.full_id}"}
        data, game_inbound, game_outbound = paths
        game_inbound.mkdir(parents=True, exist_ok=True)
        game_outbound.mkdir(parents=True, exist_ok=True)

        data_dir = Path(config.get("server", {}).get("data_dir", "./data"))
        hub_inbound = data_dir / "packets" / "inbound"
        hub_outbound = data_dir / "packets" / "outbound"
        hub_processed = data_dir / "packets" / "processed"
        hub_outbound.mkdir(parents=True, exist_ok=True)
        hub_processed.mkdir(parents=True, exist_ok=True)
        hub = hub_hex(config)

        # One file, perhaps many rows: a broadcast has a row per board.
        by_name: dict[str, list[Packet]] = defaultdict(list)
        for packet in packets:
            by_name[packet.filename].append(packet)

        delivered = []
        for name, rows in by_name.items():
            src = hub_inbound / name
            if not src.is_file():
                logger.warning(f"Source file not found in hub inbound: {name}")
                continue
            if any(r.dest_bbs_index == hub for r in rows):
                dest = game_inbound / name
                outcome = place_without_overwrite(src, dest, move=False)
                if outcome == "collision":
                    logger.error(f"{dest} already holds a different packet under this name; "
                                 f"neither was changed")
                else:
                    delivered.append(dest)
            if any(r.dest_bbs_index != hub for r in rows):
                outcome = place_without_overwrite(src, hub_outbound / name, move=False)
                if outcome == "collision":
                    logger.error(f"{hub_outbound / name} already holds a different packet "
                                 f"under this name; the members are not sent this one")

        binary = settings["binary"]
        args = settings.get("args", list(DEFAULT_ARGS))
        if isinstance(args, str):
            args = shlex.split(args)
        argv = [binary, *args, "-data", str(data)]
        timeout = int(settings.get("timeout") or config.get("dosemu", {}).get("timeout", 300))
        logger.info(f"Running Immortal Barons for league {league.league_id}: {' '.join(argv)}")
        result = await NativeRunner(str(data_dir), timeout).run(
            f"{league.league_id}_ib", argv, cwd=data)

        # A run that ends non-zero has still done its work: the game exits 1 for
        # a transport fault it has not reported before (a packet refused or held),
        # after applying the rest and writing its outbox. So what it took is
        # marked taken and what it wrote is delivered either way, and the run is
        # still reported failed, which is the alarm the game meant to raise.
        for name, rows in by_name.items():
            for r in rows:
                r.processed_at = utcnow()
                r.is_processed = True
                r.processing_run_id = run_id
            src = hub_inbound / name
            if src.is_file():
                if place_without_overwrite(src, hub_processed / name, move=True) == "collision":
                    logger.error(f"{hub_processed / name} already holds a different packet; "
                                 f"{src} is left in hub inbound")
                else:
                    logger.info(f"Archived to hub processed: {name}")
        db.commit()

        await self.collect_outbound(service, league, run_id, game_outbound)

        unconsumed = [f for f in delivered if f.exists()]
        if unconsumed:
            logger.warning(
                f"{len(unconsumed)} packet(s) left in {game_inbound} by Immortal Barons "
                f"league {league.league_id}: {', '.join(f.name for f in unconsumed)}. The game "
                f"reads its GameInbound from bbs.cfg; check it matches. They stay there and "
                f"are retried on the next run."
            )
        service._unconsumed_count += len(unconsumed)
        return result

    def outbound_dir(self, service, league: League) -> Optional[Path]:
        paths = self._paths(service.config, league)
        return paths[2] if paths else None

    async def collect_outbound(self, service, league: League, run_id: int, outbound_dir: Path):
        """Every *.brp the hub's game wrote, into hub outbound with a row per board.

        The game publishes each file complete under its final name (it writes a
        temp file and renames it), so a *.brp here is whole.
        """
        if not outbound_dir.is_dir():
            return
        db, config = service.db, service.config
        game = game_for_letter(league.game_type)
        hub = hub_hex(config)
        hub_outbound = Path(config.get("server", {}).get("data_dir", "./data")) / "packets" / "outbound"
        hub_outbound.mkdir(parents=True, exist_ok=True)

        for path in sorted(outbound_dir.iterdir()):
            if not is_packet_file(path):
                continue
            info = game.parse_packet_name(path.name)
            if info is None or info["league_id"] != league.league_id:
                logger.warning(f"Not collecting {path.name} from {outbound_dir}: not a packet "
                               f"of league {league.full_id}")
                continue
            if info["dest_bbs_index"] == hub:
                logger.warning(f"Not collecting {path.name}: the hub's game wrote it to itself")
                continue

            name = path.name.upper()
            content = path.read_bytes()
            outcome = place_without_overwrite(path, hub_outbound / name, move=True)
            if outcome == "collision":
                logger.error(f"{hub_outbound / name} already holds a different packet; "
                             f"{path} is left where it is")
                continue

            checksum = hashlib.sha256(content).hexdigest()
            for dest in self.recipients(db, league, info, hub):
                exists = db.query(Packet.id).filter(
                    Packet.league_id == league.id, Packet.filename == name,
                    Packet.dest_bbs_index == dest,
                ).first()
                if exists:
                    continue
                db.add(Packet(
                    filename=name,
                    league_id=league.id,
                    source_bbs_index=info["source_bbs_index"],
                    dest_bbs_index=dest,
                    sequence_number=info["sequence_number"],
                    file_size=len(content),
                    checksum=checksum,
                    processing_run_id=run_id or None,
                    processed_at=utcnow(),
                    is_processed=True,
                ))
            db.commit()
            logger.info(f"Collected outbound: {name}")

    # -- the roster ---------------------------------------------------------------

    def roster(self, db, config: dict, league: League) -> tuple[Optional[str], list[str]]:
        """ibnodes.dat for the league, or None and why not.

        No HOST lines: the hub does the routing, so the game must see a mesh.
        """
        problems = []
        hub = hub_hex(config)
        if hub != "01":
            return None, [f"{league.full_id}: the League Coordinator is node 1, and the hub "
                          f"is [hub] bbs_index {hub}; Immortal Barons needs the hub at 1"]
        members = self._members(db, league, hub)
        if not members:
            return None, [f"{league.full_id}: no active members with a BBS index"]

        hub_config = config.get("hub", {})
        settings = self.settings(config, league)
        try:
            hub_key = valid_board_key(settings.get("hub_board_key"))
        except ValueError:
            hub_key = None
            problems.append(f"{league.full_id}: [ib.{league.league_id}] hub_board_key is not "
                            f"a 64-character hex key; the hub's entry is written without one")

        def entry(number, name, address, city, state, country, key):
            lines = [str(number), _field(name), _field(address),
                     _field(city), _field(state), _field(country)]
            return lines + ([key] if key else []) + [""]

        lines = entry(1, hub_config.get("bbs_name") or "Nova Hub", league.hub_fidonet_address,
                      hub_config.get("city"), hub_config.get("state"), hub_config.get("country"),
                      hub_key)
        for m in members:
            client = db.get(Client, m.client_id)
            lines += entry(m.bbs_index, client.bbs_name if client else f"BBS {m.bbs_index}",
                           m.fidonet_address,
                           client.city if client else None, client.state if client else None,
                           client.country if client else None, m.board_key)
        return "\r\n".join(lines) + "\r\n", problems

    def generate_nodelist(self, db, config, league: League):
        content, problems = self.roster(db, config, league)
        if content is None:
            for p in problems:
                logger.warning(f"ibnodes: {p}")
            return None, problems
        data = content.encode("utf-8")
        game = game_for_letter(league.game_type)

        # The hub's own game: it signs this and broadcasts it every run.
        paths = self._paths(config, league)
        if paths is not None and paths[0].is_dir():
            target = paths[0] / game.nodes_file
            if not target.is_file() or target.read_bytes() != data:
                _write_atomic(target, data)
                logger.info(f"Installed {target} for league {league.full_id}; the hub's game "
                            f"broadcasts it on its next run")
        else:
            problems.append(f"{league.full_id}: [ib.{league.league_id}] data_dir is not set or "
                            f"does not exist, so the hub's game was not given the roster and "
                            f"will not broadcast it")
        for p in problems:
            logger.warning(f"ibnodes: {p}")

        # IBNODES.<league>, for a board that has no roster yet.
        data_dir = config.get("server", {}).get("data_dir", "./data")
        directory = Path(data_dir) / "nodelists" / game.key / league.league_id
        directory.mkdir(parents=True, exist_ok=True)
        published = directory / game.nodelist_filename(league.league_id)
        if not (published.is_file() and published.read_bytes() == data):
            _write_atomic(published, data)
            logger.info(f"Generated {published.name} for league {league.full_id}")
            publish_nodelist(db, data_dir, league, game, published)
        return published, problems

    # -- client config ------------------------------------------------------------

    def config_toml_league(self, entry) -> list[str]:
        from backend.services.claim_links import _toml_str
        data = f"/CHANGE-ME/immortal-barons/{entry.folder.lower()}"
        return [
            f"[leagues.{entry.code}.{entry.number}]",
            "enabled = true",
            f"bbs_index = {entry.bbs_index}  # assigned by the hub; do not change",
            "# Immortal Barons runs natively, not under dosemu. game_folder is its data",
            "# directory (-data); inbound_dir and outbound_dir are bbs.cfg's GameInbound",
            "# and GameOutbound.",
            'runner = "native"',
            f"game_folder = {_toml_str(data)}",
            f"inbound_dir = {_toml_str(data + '/inbound')}",
            f"outbound_dir = {_toml_str(data + '/outbound')}",
            f"game_command = {_toml_str('/CHANGE-ME/immortal-barons/immortal-barons')}",
            f"maintenance_args = {_toml_str('-maint -data ' + data)}",
        ]

    def config_psd1_league(self, entry) -> list[str]:
        from backend.services.claim_links import _psd1_str
        install = "C:\\CHANGE-ME\\immortal-barons"
        data = install + "\\" + entry.folder
        outbound, inbound = data + "\\outbound", data + "\\inbound"
        binary = install + "\\immortal-barons.exe"
        return [
            "        @{",
            f"            Game            = {_psd1_str(entry.code)}",
            f"            Number          = {_psd1_str(entry.number)}",
            "            Enabled         = $true",
            f"            BbsIndex        = {entry.bbs_index}  # assigned by the hub; do not change",
            "            # Immortal Barons: GameFolder is its data directory (-data).",
            f"            OutboundDir     = {_psd1_str(outbound)}",
            f"            InboundDir      = {_psd1_str(inbound)}",
            f"            GameFolder      = {_psd1_str(data)}",
            f"            GameCommand     = {_psd1_str(binary)}",
            f"            MaintenanceArgs = {_psd1_str('-maint -data ' + data)}",
            "        }",
        ]


def _write_atomic(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
