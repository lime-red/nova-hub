# services/sequence_validator.py

from datetime import datetime
from sqlalchemy import text
from app.database import SequenceAlert
from app.logging_config import get_logger

logger = get_logger(context="sequence_validator")

# Sequence numbers range from 000-999 (1000 values)
MAX_SEQUENCE = 999
SEQUENCE_RANGE = 1000

# Threshold for detecting wrap-around: if the gap between consecutive
# sorted sequences is larger than this, it's likely a wrap-around point
WRAP_DETECTION_THRESHOLD = 500


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

            for gap_info in gaps:
                # Create alert if not already exists
                self.create_alert_if_new(route, gap_info)

    def find_gaps(self, sequences: list[int]) -> list[dict]:
        """
        Find missing sequence numbers, properly handling wrap-around.

        Sequence numbers are 000-999. When we receive packets out of order or
        after wrap-around (999 -> 000), we need to detect actual gaps without
        flagging the wrap-around transition itself.

        Returns a list of dicts with gap info:
            - expected_sequence: the missing sequence number
            - received_sequence: the sequence that was received instead
            - gap_size: number of missing sequences in this gap
        """
        if len(sequences) < 2:
            return []

        # Sort sequences numerically
        sorted_seqs = sorted(set(sequences))

        if len(sorted_seqs) < 2:
            return []

        gaps = []

        # Find the wrap-around point (if any) - it's the largest gap
        # between consecutive sorted sequences
        max_gap = 0
        wrap_index = -1

        for i in range(len(sorted_seqs) - 1):
            gap = sorted_seqs[i + 1] - sorted_seqs[i]
            if gap > max_gap:
                max_gap = gap
                wrap_index = i

        # Also check the "virtual gap" from the last sequence wrapping to the first
        # This represents: if last=999 and first=2, the wrap gap is (1000-999)+(2-0)=3
        wrap_gap = (SEQUENCE_RANGE - sorted_seqs[-1]) + sorted_seqs[0]

        # Determine if wrap-around occurred
        # If the largest internal gap is bigger than the wrap gap, that's the wrap point
        # Otherwise, the wrap is at the end (normal case)
        if max_gap > WRAP_DETECTION_THRESHOLD and max_gap > wrap_gap:
            # The wrap-around point is at wrap_index
            # Sequences after wrap_index are "old" (before wrap)
            # Sequences at or before wrap_index are "new" (after wrap)
            #
            # Reorder: put sequences after wrap_index first (they're chronologically earlier)
            reordered = sorted_seqs[wrap_index + 1:] + sorted_seqs[:wrap_index + 1]

            # Now detect gaps in the reordered sequence
            # The transition at the splice point is the wrap-around, not a gap
            for i in range(len(reordered) - 1):
                current = reordered[i]
                next_seq = reordered[i + 1]
                expected = (current + 1) % SEQUENCE_RANGE

                # Calculate actual gap size
                if next_seq > current:
                    gap_size = next_seq - current - 1
                else:
                    # Wrap-around case: current=999, next_seq=2 means gap of 2 (missing 0, 1)
                    gap_size = (SEQUENCE_RANGE - current - 1) + next_seq

                if gap_size > 0 and gap_size < WRAP_DETECTION_THRESHOLD:
                    # This is a real gap, not a wrap-around transition
                    # Add each missing sequence
                    for j in range(gap_size):
                        missing_seq = (current + 1 + j) % SEQUENCE_RANGE
                        gaps.append({
                            "expected_sequence": missing_seq,
                            "received_sequence": next_seq,
                            "gap_size": gap_size
                        })
        else:
            # No significant wrap-around detected in the middle
            # Just check for sequential gaps
            for i in range(len(sorted_seqs) - 1):
                current = sorted_seqs[i]
                next_seq = sorted_seqs[i + 1]
                gap_size = next_seq - current - 1

                if gap_size > 0 and gap_size < WRAP_DETECTION_THRESHOLD:
                    # Real gap - add each missing sequence
                    for j in range(gap_size):
                        missing_seq = current + 1 + j
                        gaps.append({
                            "expected_sequence": missing_seq,
                            "received_sequence": next_seq,
                            "gap_size": gap_size
                        })

        return gaps

    def create_alert_if_new(self, route, gap_info: dict):
        """Create a sequence alert if it doesn't already exist"""
        league_id = route[0]
        source_bbs_index = route[1]
        dest_bbs_index = route[2]

        expected_sequence = gap_info["expected_sequence"]
        received_sequence = gap_info["received_sequence"]
        gap_size = gap_info["gap_size"]

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

        # Create new alert with context about what was received
        description = (
            f"Missing packet: expected sequence {expected_sequence:03d}, "
            f"but received {received_sequence:03d} (gap of {gap_size} packet(s))"
        )

        alert = SequenceAlert(
            league_id=league_id,
            source_bbs_index=source_bbs_index,
            dest_bbs_index=dest_bbs_index,
            expected_sequence=expected_sequence,
            received_sequence=received_sequence,
            gap_size=gap_size,
            description=description
        )
        self.db.add(alert)
        self.db.commit()

        logger.warning(
            f"Created alert for missing sequence {expected_sequence:03d} on route "
            f"{source_bbs_index}->{dest_bbs_index} (received {received_sequence:03d}, gap={gap_size})"
        )

    def auto_resolve_alerts(self):
        """
        Check if any missing packets have been received and resolve their alerts.
        Call this after new packets are uploaded.
        """
        # Get all unresolved alerts
        unresolved = self.db.query(SequenceAlert).filter(
            SequenceAlert.is_resolved == False
        ).all()

        resolved_count = 0
        for alert in unresolved:
            # Check if the missing packet now exists
            exists = self.db.execute(
                text("""
                    SELECT 1 FROM packets
                    WHERE league_id = :league_id
                      AND source_bbs_index = :source_bbs_index
                      AND dest_bbs_index = :dest_bbs_index
                      AND sequence_number = :sequence_number
                    LIMIT 1
                """),
                {
                    "league_id": alert.league_id,
                    "source_bbs_index": alert.source_bbs_index,
                    "dest_bbs_index": alert.dest_bbs_index,
                    "sequence_number": alert.expected_sequence
                }
            ).fetchone()

            if exists:
                alert.is_resolved = True
                alert.resolved_at = datetime.utcnow()
                alert.resolution_note = "Missing packet received"
                resolved_count += 1
                logger.info(
                    f"Auto-resolved alert for sequence {alert.expected_sequence:03d} "
                    f"on route {alert.source_bbs_index}->{alert.dest_bbs_index}"
                )

        if resolved_count > 0:
            self.db.commit()
            logger.info(f"Auto-resolved {resolved_count} sequence alert(s)")

        return resolved_count
