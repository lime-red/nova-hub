# services/sequence_validator.py

from datetime import datetime
from sqlalchemy import text
from app.database import SequenceAlert
from app.logging_config import get_logger

logger = get_logger(context="sequence_validator")


class SequenceValidator:
    def __init__(self, db=None):
        """Initialize with database session"""
        self.db = db

    def check_sequences(self):
        """Detect missing sequences for each route"""
        # Get all unique routes (league, source, dest combinations)
        routes = self.db.execute(text("""
            SELECT DISTINCT league_id, source_bbs_index, dest_bbs_index
            FROM packets
        """)).fetchall()

        for route in routes:
            sequences = self.db.execute(
                text("""
                SELECT sequence_number
                FROM packets
                WHERE league_id = :league_id
                  AND source_bbs_index = :source_bbs_index
                  AND dest_bbs_index = :dest_bbs_index
                ORDER BY sequence_number
            """),
                {"league_id": route[0], "source_bbs_index": route[1], "dest_bbs_index": route[2]},
            ).fetchall()

            # Find gaps
            seq_list = [s[0] for s in sequences]
            if not seq_list:
                continue

            # Check for missing sequences (accounting for wrap at 999)
            gaps = self.find_gaps(seq_list)

            for gap in gaps:
                # Create alert if not already exists
                self.create_alert_if_new(route, gap)

    def find_gaps(self, sequences: list[int]) -> list[int]:
        """Find missing sequence numbers, handling wrap-around"""
        gaps = []
        for i in range(len(sequences) - 1):
            current = sequences[i]
            next_seq = sequences[i + 1]
            expected = (current + 1) % 1000

            if next_seq != expected:
                # Found a gap
                gaps.append(expected)

        return gaps

    def create_alert_if_new(self, route, expected_sequence: int):
        """Create a sequence alert if it doesn't already exist"""
        league_id = route[0]
        source_bbs_index = route[1]
        dest_bbs_index = route[2]

        # Check if alert already exists for this gap
        existing = self.db.query(SequenceAlert).filter(
            SequenceAlert.league_id == league_id,
            SequenceAlert.source_bbs_index == source_bbs_index,
            SequenceAlert.dest_bbs_index == dest_bbs_index,
            SequenceAlert.expected_sequence == expected_sequence,
            SequenceAlert.is_resolved == False
        ).first()

        if existing:
            return  # Alert already exists

        # Create new alert
        alert = SequenceAlert(
            league_id=league_id,
            source_bbs_index=source_bbs_index,
            dest_bbs_index=dest_bbs_index,
            expected_sequence=expected_sequence,
            received_sequence=0,  # We don't know what we got, just that we're missing expected
            gap_size=1,
            description=f"Missing packet: sequence {expected_sequence}"
        )
        self.db.add(alert)
        self.db.commit()

        logger.warning(f"Created alert for missing sequence {expected_sequence} on route {source_bbs_index}->{dest_bbs_index}")
