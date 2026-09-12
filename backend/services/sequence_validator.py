# backend/services/sequence_validator.py

from collections import Counter
from datetime import datetime

from sqlalchemy import text

from backend.models.database import SequenceAlert
from backend.logging_config import get_logger

logger = get_logger(context="sequence_validator")

# Sequence numbers range from 000-999 (1000 values)
MAX_SEQUENCE = 999
SEQUENCE_RANGE = 1000

# Threshold for detecting wrap-around: if the gap between consecutive
# sorted sequences is larger than this, it's likely a wrap-around point
WRAP_DETECTION_THRESHOLD = 500

# A route's sequence numbers are NOT dense. BRE and FE consume more than one
# number per packet they actually emit -- in the rig, each new game day burns two
# and writes its file at the second, so a perfectly healthy install produces
# .002, .004, .006 with nothing lost and nothing delayed. Treating every unseen
# number as a lost packet is what produced 705 unresolved alerts in production,
# all of them false, which made a genuine loss unfindable.
#
# So instead of assuming a stride of 1, learn the stride the route actually uses
# and alert only on departures from it. Two equal gaps in a row is the minimum
# evidence accepted: that is three consecutive packets spaced identically, and
# requiring more would leave a young route noisy for its first week. Where the
# evidence is weaker the stride falls back to 1, which is the old behaviour --
# this only ever makes the detector quieter where it has grounds to be.
MIN_DELTAS_FOR_STRIDE = 2
STRIDE_DOMINANCE = 0.6

# A stride only helps where there IS one. Measured against eight months of
# production data, the three BRE leagues number densely -- 1000 packets covering
# 1000 numbers, every step exactly 1 -- and Falcon's Eye does not. 015F's routes
# step by 1, 2, 3, 4, 5 and occasionally 45, with no value holding a majority, and
# they carry 472 of production's 492 standing alerts between them. One route sent
# 103 packets across a span of 328 numbers: that is not 225 lost packets, it is a
# game that does not number densely, and no stride can be read from it.
#
# So the second question, asked before any gap is reported: does this route's
# numbering support the inference at all? Density is packets per number of span,
# which is just the reciprocal of the mean step, and it separates the two
# populations cleanly -- 1.00 and 0.98 for BRE, 0.31 to 0.66 for FE.
#
# Below the threshold the route is exempt: its unseen numbers are not evidence of
# anything. That does lose the ability to spot a genuine FE loss, which is the
# honest cost of the trade -- an alarm that has fired 472 times without once being
# real has no value to trade away. Telling the two apart needs the game's own
# record of what it sent (ROUTEINFO), not arithmetic on what arrived.
#
# The minimum sample matters as much as the threshold: a route with six packets
# can look sparse by accident, so below MIN_PACKETS_FOR_DENSITY the route stays
# noisy rather than being quietly excused.
MIN_PACKETS_FOR_DENSITY = 20
DENSITY_THRESHOLD = 0.9


class SequenceValidator:
    def __init__(self, db=None):
        """Initialize with database session"""
        self.db = db

    def _routes(self):
        return self.db.execute(text("""
            SELECT DISTINCT league_id, source_bbs_index, dest_bbs_index
            FROM packets
        """)).fetchall()

    def _sequences_for(self, route) -> list[int]:
        rows = self.db.execute(
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
        return [r[0] for r in rows]

    def check_sequences(self) -> list:
        """
        Detect missing packets for each route.

        Returns a list of newly created SequenceAlert objects so the caller
        can dispatch out-of-band notifications.
        """
        new_alerts = []

        for route in self._routes():
            seq_list = self._sequences_for(route)
            if not seq_list:
                continue

            for gap_info in self.find_gaps(seq_list):
                # Create alert if not already exists; collect new ones for delivery
                alert = self.create_alert_if_new(route, gap_info)
                if alert is not None:
                    new_alerts.append(alert)

        return new_alerts

    def route_stride(self, ordered: list[int]) -> int:
        """How far this route's sequence numbers advance per packet.

        `ordered` is chronological, not sorted -- wrap-around has already been
        unwound by the caller. Returns 1 unless one stride clearly dominates,
        so an unproven route keeps the conservative (noisy) reading.
        """
        deltas = []
        for current, next_seq in zip(ordered, ordered[1:]):
            delta = self._delta(current, next_seq)
            # A wrap-sized step is the wrap itself, not evidence about stride.
            if 0 < delta < WRAP_DETECTION_THRESHOLD:
                deltas.append(delta)

        if len(deltas) < MIN_DELTAS_FOR_STRIDE:
            return 1

        counts = Counter(deltas)
        # Ties go to the smaller stride: it reports more, not less.
        best = min(counts, key=lambda d: (-counts[d], d))
        if counts[best] / len(deltas) < STRIDE_DOMINANCE:
            return 1
        return best

    def numbers_densely(self, ordered: list[int]) -> bool:
        """Does this route number densely enough for a missing number to mean
        a missing packet?

        `ordered` is chronological. Density is steps per number of span, which is
        the reciprocal of the mean step. A route with too few packets to judge is
        treated as dense, which keeps it noisy rather than quietly excusing it.
        """
        deltas = [d for d in (self._delta(a, b) for a, b in zip(ordered, ordered[1:]))
                  if 0 < d < WRAP_DETECTION_THRESHOLD]
        if len(deltas) + 1 < MIN_PACKETS_FOR_DENSITY:
            return True
        span = sum(deltas)
        if span <= 0:
            return True
        return len(deltas) / span >= DENSITY_THRESHOLD

    @staticmethod
    def _delta(current: int, next_seq: int) -> int:
        """Distance from one sequence number to the next, across the wrap."""
        if next_seq > current:
            return next_seq - current
        return (SEQUENCE_RANGE - current) + next_seq

    def find_gaps(self, sequences: list[int]) -> list[dict]:
        """
        Find missing *packets*, properly handling wrap-around.

        Sequence numbers are 000-999. When we receive packets out of order or
        after wrap-around (999 -> 000), we need to detect actual gaps without
        flagging the wrap-around transition itself.

        Two questions are asked before any gap is reported. Does this route number
        densely enough for an unseen number to mean anything at all (Falcon's Eye
        does not), and if so, how far does it advance per packet? `gap_size` then
        counts missing packets, not missing numbers: on a route whose game
        advances by two, 002 -> 006 is one missing packet, not three, and
        002 -> 004 is none at all. See the notes on both constants above.

        Returns a list of dicts with gap info:
            - expected_sequence: the number the missing packet would have carried
            - received_sequence: the sequence that was received instead
            - gap_size: number of missing packets in this gap
        """
        if len(sequences) < 2:
            return []

        # Sort sequences numerically
        sorted_seqs = sorted(set(sequences))

        if len(sorted_seqs) < 2:
            return []

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
            # Sequences after wrap_index are chronologically earlier, so putting
            # them first unwinds the wrap and leaves one chronological run.
            ordered = sorted_seqs[wrap_index + 1:] + sorted_seqs[:wrap_index + 1]
        else:
            ordered = sorted_seqs

        # A route that does not number densely has nothing to say about what is
        # missing, so do not put words in its mouth.
        if not self.numbers_densely(ordered):
            return []

        stride = self.route_stride(ordered)

        gaps = []
        for current, next_seq in zip(ordered, ordered[1:]):
            delta = self._delta(current, next_seq)

            # The splice point of an unwound wrap, not a gap.
            if delta >= WRAP_DETECTION_THRESHOLD:
                continue

            # Round rather than floor: a route striding by 2 that jumps by 3 has
            # still lost a packet, and should say so.
            missing = int(delta / stride + 0.5) - 1
            if missing <= 0:
                continue

            for j in range(1, missing + 1):
                gaps.append({
                    "expected_sequence": (current + j * stride) % SEQUENCE_RANGE,
                    "received_sequence": next_seq,
                    "gap_size": missing,
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
            return None  # Alert already exists

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
        self.db.refresh(alert)

        logger.warning(
            f"Created alert for missing sequence {expected_sequence:03d} on route "
            f"{source_bbs_index}->{dest_bbs_index} (received {received_sequence:03d}, gap={gap_size})"
        )

        return alert

    def auto_resolve_alerts(self):
        """
        Resolve alerts that are no longer true, and say which kind of untrue.

        Two ways an alert stops being real:

        1. The packet turned up. That is the ordinary case -- late, not lost.
        2. The gap was never a gap. Alerts raised before the detector learned the
           route's stride say a packet is missing at a number the games never
           hand out. Re-deriving the route's gaps here retires those without an
           operator having to tell one false alarm from 704 others.

        Call this after new packets are uploaded.
        """
        unresolved = self.db.query(SequenceAlert).filter(
            SequenceAlert.is_resolved == False
        ).all()
        if not unresolved:
            return 0

        # Route key -> the sequence numbers the detector still considers missing.
        still_missing: dict[tuple, set] = {}

        def gaps_for(alert) -> set:
            key = (alert.league_id, alert.source_bbs_index, alert.dest_bbs_index)
            if key not in still_missing:
                seqs = self._sequences_for(key)
                still_missing[key] = {
                    g["expected_sequence"] for g in self.find_gaps(seqs)
                }
            return still_missing[key]

        resolved_count = 0
        stale_count = 0
        for alert in unresolved:
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
                note = "Missing packet received"
            elif alert.expected_sequence not in gaps_for(alert):
                note = (
                    "Not a lost packet: this route's sequence numbers do not "
                    "advance one at a time, so this number was never issued"
                )
                stale_count += 1
            else:
                continue

            alert.is_resolved = True
            alert.resolved_at = datetime.utcnow()
            alert.resolution_note = note
            resolved_count += 1
            logger.info(
                f"Auto-resolved alert for sequence {alert.expected_sequence:03d} "
                f"on route {alert.source_bbs_index}->{alert.dest_bbs_index}: {note}"
            )

        if resolved_count > 0:
            self.db.commit()
            logger.info(
                f"Auto-resolved {resolved_count} sequence alert(s)"
                + (f", {stale_count} of them never real gaps" if stale_count else "")
            )

        return resolved_count
