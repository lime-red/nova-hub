"""What every game provider answers, and the parts they share.

games.py says what a game is called. A provider says how the hub carries it:
how the hub's own copy of the game runs, who a packet goes to, whether its
sequence numbers can be checked for gaps, how its nodelist reaches the members,
and what a member's client config looks like. ProcessingService runs batches
and owns the run record; everything in a batch that differs from game to game
is asked of the league's provider.

Two families exist (#100):

- SR games (sr_games.py): Barren Realms Elite and Falcon's Eye, DOS doors run
  under dosemu, 8.3 packet names whose 000-999 sequence wraps.
- Immortal Barons (immortal_barons.py): a native Go binary, JSON packets with
  names that never repeat, and a broadcast address the hub fans out.
"""

import hashlib
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from backend.core.clock import utcnow
from backend.logging_config import get_logger
from backend.models.database import League, LeagueMembership, Packet

if TYPE_CHECKING:  # pragma: no cover
    from backend.services.claim_links import LeagueEntry
    from backend.services.processing_service import ProcessingService

logger = get_logger(context="processing")


def index_hex(raw) -> str:
    """[hub] bbs_index, a string like "01", as packet rows store a board: two
    upper-case hex digits. Read as the number it names, so "1" and "01" are
    the same board."""
    raw = str(raw)
    try:
        return format(int(raw, 10), "02X")
    except ValueError:
        return raw.upper()


def hub_hex(config: dict) -> str:
    """The hub's own board, from the raw config dict."""
    return index_hex((config.get("hub") or {}).get("bbs_index", "01"))


class GameProvider:
    """The behaviour of one family of games. One instance serves every league
    of its games; nothing here keeps per-league state."""

    # Whether a route's sequence numbers can say a packet went missing. Only
    # true where numbers are per route and their wrap is understood.
    checks_sequences: bool = False

    # Whether one packet name can name different packets over time. True for
    # the SR games, whose 000-999 sequence wraps; then a second upload under a
    # name already seen is a new packet. False means a name is one packet for
    # ever, and different bytes under it are a collision, never a replacement.
    reuses_names: bool = True

    # Whether the packet files on disk need pruning by age. Names that wrap
    # bound themselves; names that never repeat grow without limit.
    expire_packet_files: bool = False

    # Whether a run's output is a game transcript that transcript_service can
    # read item movements from.
    reads_transcripts: bool = False

    def recipients(self, db, league: League, packet_info: dict, hub: str) -> list[str]:
        """The boards (hex indices) a packet goes to: one packet row each.

        The default is the board the name addresses.
        """
        return [packet_info["dest_bbs_index"]]

    async def process_league(self, service: "ProcessingService", league: League,
                             packets: list, run_id: int) -> dict:
        """Run the hub's copy of the game over these packets, deliver what it
        writes, and return a result: {"status": "success"|..., "output": ...}."""
        raise NotImplementedError

    def outbound_dir(self, service: "ProcessingService", league: League) -> Optional[Path]:
        """Where the hub's own game leaves packets for others, or None."""
        return None

    async def collect_outbound(self, service: "ProcessingService", league: League,
                               run_id: int, outbound_dir: Path) -> None:
        """Take what the hub's game wrote into the hub's outbound, with a row for
        each board it is for."""
        raise NotImplementedError

    def generate_nodelist(self, db, config, league: League) -> tuple[Optional[Path], list[str]]:
        """Bring the league's nodelist up to date, after a batch or when an admin
        asks. Returns the published file, or None if none could be written, and
        whatever an admin should be told: why not, or what did not reach the
        hub's game. `config` is the raw config dict, or the parsed Config."""
        raise NotImplementedError

    def config_toml_league(self, entry: "LeagueEntry") -> list[str]:
        """The client config.toml lines for one membership."""
        raise NotImplementedError

    def config_psd1_league(self, entry: "LeagueEntry") -> list[str]:
        """The PowerShell config.psd1 entry for one membership."""
        raise NotImplementedError


def publish_nodelist(db, data_dir, league: League, game, path: Path) -> int:
    """Queue a league's nodelist file to every active member: a packet row each.

    The file itself stays where it is, in <data_dir>/nodelists/<game>/<league>/,
    and the download serves it from there. A member's existing row for it is
    reset to unread rather than duplicated. Returns the number of members.
    """
    content = path.read_bytes()
    file_size, checksum = len(content), hashlib.sha256(content).hexdigest()
    filename = path.name.upper()

    memberships = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league.id,
        LeagueMembership.is_active == True,  # noqa: E712
    ).all()

    for membership in memberships:
        if membership.bbs_index is None:
            continue
        dest = f"{membership.bbs_index:02X}"
        existing = db.query(Packet).filter(
            Packet.filename == filename,
            Packet.league_id == league.id,
            Packet.dest_bbs_index == dest,
        ).first()
        if existing:
            existing.file_size = file_size
            existing.checksum = checksum
            existing.uploaded_at = utcnow()
            existing.processed_at = utcnow()
            existing.is_processed = True
            existing.is_downloaded = False
            existing.downloaded_at = None
            logger.debug(f"Updated existing nodelist packet for BBS {dest}")
        else:
            db.add(Packet(
                filename=filename,
                league_id=league.id,
                source_bbs_index="00",  # hub/system source
                dest_bbs_index=dest,
                sequence_number=0,  # nodelists have no sequence
                source_client_id=None,
                dest_client_id=membership.client_id,
                file_size=file_size,
                checksum=checksum,
                is_processed=True,
                processed_at=utcnow(),
                is_downloaded=False,
            ))
            logger.debug(f"Created nodelist packet for BBS {dest}")

    db.commit()
    logger.info(f"Created/updated {len(memberships)} nodelist packet records")
    return len(memberships)


def same_bytes(a: Path, b: Path) -> bool:
    return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()


def place_without_overwrite(src: Path, dest: Path, *, move: bool) -> str:
    """Put src at dest unless dest already holds something else.

    For names that are one packet for ever: an identical file already there is
    a duplicate ("duplicate"); different bytes are a collision ("collision"),
    and neither file is touched, so both survive for someone to look at. The
    new file arrives under a temporary name beside dest and is renamed into
    place, so a reader of that directory never sees it half-written.
    Returns "placed", "duplicate" or "collision".
    """
    if dest.exists():
        if same_bytes(src, dest):
            if move:
                src.unlink()
            return "duplicate"
        return "collision"
    tmp = dest.with_name(f".{dest.name}.part")
    if move:
        shutil.move(str(src), str(tmp))
    else:
        shutil.copy2(src, tmp)
    os.replace(tmp, dest)
    return "placed"
