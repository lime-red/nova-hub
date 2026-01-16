# backend/services/packet_service.py

import hashlib
import re
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from backend.models.database import Client, League, LeagueMembership, Packet


def parse_packet_filename(filename: str) -> Optional[dict]:
    """
    Parse packet filename into components

    Format: <league><game><source><dest>.<seq>
    Example: 555B0201.001

    Returns:
        dict with keys: league_id, game_type, source_bbs_index, dest_bbs_index, sequence_number
        or None if invalid
    """
    # Pattern: 3 digits + 1 letter + 2 hex + 2 hex + . + 3 digits
    pattern = r"^(\d{3})([BF])([0-9A-F]{2})([0-9A-F]{2})\.(\d{3})$"
    match = re.match(pattern, filename.upper())

    if not match:
        return None

    league_id, game_type, source_bbs, dest_bbs, sequence = match.groups()

    return {
        "league_id": league_id,
        "game_type": game_type,
        "source_bbs_index": source_bbs,
        "dest_bbs_index": dest_bbs,
        "sequence_number": int(sequence),
    }


def calculate_checksum(data: bytes) -> str:
    """Calculate SHA256 checksum of packet data"""
    return hashlib.sha256(data).hexdigest()


class PacketService:
    """Service for handling packet operations"""

    def __init__(self, db: Session):
        self.db = db

    def validate_packet_filename(self, filename: str) -> Tuple[bool, Optional[str]]:
        """
        Validate packet filename format

        Returns:
            (is_valid, error_message)
        """
        parsed = parse_packet_filename(filename)

        if not parsed:
            return (
                False,
                "Invalid filename format. Expected: <league><game><source><dest>.<seq>",
            )

        return True, None

    def get_or_create_league(self, league_id: str, game_type: str) -> League:
        """Get existing league or create new one"""
        league = (
            self.db.query(League)
            .filter(League.league_id == league_id, League.game_type == game_type)
            .first()
        )

        if not league:
            game_name = "Barren Realms Elite" if game_type == "B" else "Falcon's Eye"
            league = League(
                league_id=league_id,
                game_type=game_type,
                name=f"League {league_id} - {game_name}",
                is_active=True,
            )
            self.db.add(league)
            self.db.commit()
            self.db.refresh(league)

        return league

    def find_membership_by_index(
        self, league_id: int, bbs_index: str
    ) -> Optional["LeagueMembership"]:
        """Find league membership by league and BBS index (hex string)"""

        # Convert hex string to integer for comparison
        try:
            bbs_int = int(bbs_index, 16)
        except ValueError:
            return None

        return (
            self.db.query(LeagueMembership)
            .filter(
                LeagueMembership.league_id == league_id,
                LeagueMembership.bbs_index == bbs_int,
                LeagueMembership.is_active,
            )
            .first()
        )

    def create_packet(
        self,
        filename: str,
        file_data: bytes,
        league: League,
        source_bbs_index: str,
        dest_bbs_index: str,
        sequence_number: int,
        source_client: Optional[Client] = None,
        dest_client: Optional[Client] = None,
    ) -> Packet:
        """Create new packet record"""
        checksum = calculate_checksum(file_data)

        packet = Packet(
            filename=filename,
            league_id=league.id,
            source_bbs_index=source_bbs_index.upper(),
            dest_bbs_index=dest_bbs_index.upper(),
            sequence_number=sequence_number,
            source_client_id=source_client.id if source_client else None,
            dest_client_id=dest_client.id if dest_client else None,
            file_data=file_data,
            file_size=len(file_data),
            checksum=checksum,
            uploaded_at=datetime.utcnow(),
            is_processed=False,
            is_downloaded=False,
            has_error=False,
        )

        self.db.add(packet)
        self.db.commit()
        self.db.refresh(packet)

        return packet

    def get_pending_packets_for_client(self, bbs_index: str) -> list[Packet]:
        """Get packets destined for a specific BBS that haven't been downloaded"""
        return (
            self.db.query(Packet)
            .filter(
                Packet.dest_bbs_index == bbs_index.upper(),
                Packet.is_downloaded == False,
            )
            .order_by(Packet.uploaded_at.asc())
            .all()
        )

    def mark_packet_downloaded(self, packet: Packet):
        """Mark packet as downloaded"""
        packet.is_downloaded = True
        packet.downloaded_at = datetime.utcnow()
        self.db.commit()

    def get_packet_by_filename(self, filename: str) -> Optional[Packet]:
        """Get packet by filename"""
        return self.db.query(Packet).filter(Packet.filename == filename).first()

    def check_duplicate_packet(self, filename: str) -> bool:
        """Check if packet with filename already exists"""
        return (
            self.db.query(Packet).filter(Packet.filename == filename).first()
            is not None
        )
