# backend/services/sequence_validator.py

from collections import Counter
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import text

from backend.models.database import SequenceAlert
from backend.logging_config import get_logger
from backend.services.sequence_epoch import SEQUENCE_RANGE, build_timeline

logger = get_logger(context="sequence_validator")

# Sequence numbers range from 000-999 (1000 values). SEQUENCE_RANGE comes from
# sequence_epoch, which is where the 000-999 space is now reasoned about.
MAX_SEQUENCE = 999

# Wrap-around used to be *inferred* here, by looking for one suspiciously large
# gap in the sorted numbers and splicing the list at it. That could represent
# exactly one wrap, and production passed that point long ago: league 3's route
# 02->01 has sent 5,891 packets over six complete cycles, which collapse into a
# flawless 000-999 run with no gap for the detector to find. It has been blind on
# its busiest routes for months.
#
# Cycles are now *counted* from arrival order instead -- see sequence_epoch --
# and everything below works in an absolute space that only increases, where a
# wrap is a step of one like any other and needs no special case at all.

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


# Routes the hub itself sends on cannot be judged for gaps, and the reason is
# structural rather than a threshold to tune.
#
# Inbound packets arrive over the API and each upload creates its own row, so a
# route like 02->01 holds a true history -- production's has 5,891 rows across
# 1,000 numbers, which is exactly what makes counting cycles worthwhile. Packets
# the hub *generates* go through collect_outbound_packets instead, which on a
# filename collision deliberately updates the existing row rather than adding
# one (it resets is_downloaded so the client re-fetches). That is correct for
# delivery and fatal for history: once such a route has been round the numbering
# its rows stop being packets and become a fixed table of 1,000 slots.
#
# Ask that table for gaps and it answers "none", every time, whatever happened.
# Confirmed on the rig: 1,100 forced cycles rolled 999 -> 000 at cycle 1,002 and
# then reissued 900b0102.002 onwards with fresh contents under names already
# used. A false all-clear is worse than no answer, so these routes are skipped.
HUB_ROUTES_ARE_SLOTS_NOT_HISTORY = True

# How recent a gap must be to be worth an alert, when the config does not say.
# Two weeks is well past the point where a missing attach can be chased and well
# short of turning the alert list into an archive.
DEFAULT_ALERT_MAX_AGE_DAYS = 14

# Whether a missing sequence number should raise an alert at all, when the config
# does not say. It should not, and the reason is measured rather than cautious.
#
# Both games burn a sequence number at each game-day rollover -- they consume one
# and emit nothing. Across eight months of production data every busy route shows
# exactly one lone missing number per active day: 232 days out of 239 on one
# route, 237 of 241 on another, 109 of 111 on a third, each at that route's own
# maintenance time. A lone daily gap is therefore the normal working of the game,
# and it is indistinguishable from a packet that genuinely went missing.
#
# So an alert on a single missing number is not evidence of anything. Enabled, it
# fires about three times a day on production and every one of them is the
# rollover; the real loss it exists to catch then arrives as the fourth identical
# line that day. That is how the previous detector accumulated 705 unresolved
# false alerts and made a genuine loss unfindable.
#
# What replaced it is the movements view: the games' own account of what they
# exchanged, shown to an admin who knows what their league should look like.
# Set this true to get the alerts back; the gaps are detected and recorded either
# way, so nothing is lost by leaving it off.
DEFAULT_ALERTS_ENABLED = False


class SequenceValidator:
    def __init__(self, db=None, hub_index: str | None = None,
                 max_age_days: float | None = None,
                 alerts_enabled: bool = True):
        """Initialize with database session.

        `hub_index` is the hub's own BBS index (e.g. "01"). Without it every
        route is judged, which is the previous behaviour and reports a
        meaningless all-clear on the hub's own outbound.

        `max_age_days` bounds how far back a gap may be and still be worth
        raising. None means no bound, which is the old behaviour.

        `alerts_enabled` false finds and records gaps without raising alerts for
        them -- see DEFAULT_ALERTS_ENABLED for why that is the sensible setting.
        It defaults true here so that calling this class directly, as the tests
        and tools do, asks it the question it was built to answer.
        """
        self.db = db
        self.hub_index = hub_index
        self.max_age_days = max_age_days
        self.alerts_enabled = alerts_enabled

    def _routes(self):
        return self.db.execute(text("""
            SELECT DISTINCT league_id, source_bbs_index, dest_bbs_index
            FROM packets
        """)).fetchall()

    def _sequences_for(self, route) -> list[int]:
        """One route's sequence numbers, in the order they arrived.

        Arrival order, not numerical order. Sorting here is what made the wrap
        undetectable: it is precisely the information that says how many times
        the numbering has been round. `id` breaks ties so the order is total and
        the same on every run.
        """
        return [seq for seq, _ in self._arrivals_for(route)]

    def _arrivals_for(self, route) -> list[tuple]:
        """One route's (sequence number, arrival time) pairs, in arrival order."""
        rows = self.db.execute(
            text("""
            SELECT sequence_number, uploaded_at
            FROM packets
            WHERE league_id = :league_id
              AND source_bbs_index = :source_bbs_index
              AND dest_bbs_index = :dest_bbs_index
            ORDER BY uploaded_at, id
        """),
            {"league_id": route[0], "source_bbs_index": route[1], "dest_bbs_index": route[2]},
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def check_sequences(self) -> list:
        """
        Detect missing packets for each route.

        Returns a list of newly created SequenceAlert objects so the caller
        can dispatch out-of-band notifications.
        """
        if not self.alerts_enabled:
            # Gaps are still found by find_gaps() and still visible to anyone who
            # asks; what is switched off is ringing a bell about them.
            logger.debug(
                "sequence alerting is disabled: a lone missing number is the "
                "game's daily rollover as often as it is a lost packet"
            )
            return []

        new_alerts = []
        skipped = 0
        aged_out = 0
        cutoff = self._alert_cutoff()

        for route in self._routes():
            if self.hub_index is not None and route[1] == self.hub_index:
                # Recycled rows, not a packet history -- see the note above.
                skipped += 1
                continue

            arrivals = self._arrivals_for(route)
            if not arrivals:
                continue

            seq_list = [seq for seq, _ in arrivals]
            timeline = build_timeline(seq_list)
            # When a gap was noticed is the arrival that revealed it, so map
            # absolute position back to the moment the packet after the hole
            # turned up. Later arrivals win: a position reached twice is being
            # read for recency, and the recent visit is the relevant one.
            noticed_at = {
                position: when
                for position, (_, when) in zip(timeline.absolute, arrivals)
            }

            for gap_info in self.find_gaps(seq_list):
                if cutoff is not None:
                    when = noticed_at.get(gap_info.get("received_absolute"))
                    if when is not None and when < cutoff:
                        aged_out += 1
                        continue

                # Create alert if not already exists; collect new ones for delivery
                alert = self.create_alert_if_new(route, gap_info)
                if alert is not None:
                    new_alerts.append(alert)

        if aged_out:
            logger.info(
                f"sequence check passed over {aged_out} gap(s) older than "
                f"{self.max_age_days} day(s): too late to act on"
            )

        if skipped:
            logger.debug(
                f"sequence check skipped {skipped} hub-originated route(s): their "
                f"packet rows are recycled per filename, so gaps cannot be read from them"
            )

        return new_alerts

    def _alert_cutoff(self):
        """The oldest a gap may be and still be worth raising, or None.

        A sequence alert is only ever actionable while the packet might still be
        recoverable -- somebody notices an attach went missing in transit, and
        the hub can say which number never arrived on which route. Days later
        there is nothing to do with that but file it.

        This matters most on the very first run after an upgrade. Counting
        cycles instead of inferring one wrap means the detector can suddenly see
        the whole history it was blind to, and against production's real data
        that is 623 gaps stretching back to January. Raised all at once they
        would bury the next real one, which is precisely the failure the
        detector exists to prevent. Age them out and the list stays readable.

        Nothing here rewrites history: the gaps are still found and still
        counted, they simply do not raise an alarm nobody can answer.
        """
        if not self.max_age_days:
            return None
        return datetime.utcnow() - timedelta(days=self.max_age_days)

    @staticmethod
    def _usable_deltas(ordered: list[int], reset_starts: set) -> list[int]:
        """Steps between consecutive positions, excluding restart crossings.

        `ordered` is ascending absolute positions, so every step is forward and
        no step is a wrap artefact. The one distance that must be thrown away is
        the one spanning a game reset: 347 to the next cycle's 001 is a step of
        654 that measures how early the game was reset, not how the route
        numbers, and feeding it to either the stride or the density calculation
        would corrupt both.
        """
        return [
            b - a
            for a, b in zip(ordered, ordered[1:])
            if b not in reset_starts and b > a
        ]

    def route_stride(self, ordered: list[int], reset_starts: set | None = None) -> int:
        """How far this route's sequence numbers advance per packet.

        `ordered` is ascending absolute positions. Returns 1 unless one stride
        clearly dominates, so an unproven route keeps the conservative (noisy)
        reading.
        """
        deltas = self._usable_deltas(ordered, reset_starts or set())

        if len(deltas) < MIN_DELTAS_FOR_STRIDE:
            return 1

        counts = Counter(deltas)
        # Ties go to the smaller stride: it reports more, not less.
        best = min(counts, key=lambda d: (-counts[d], d))
        if counts[best] / len(deltas) < STRIDE_DOMINANCE:
            return 1
        return best

    def numbers_densely(self, ordered: list[int], reset_starts: set | None = None) -> bool:
        """Does this route number densely enough for a missing number to mean
        a missing packet?

        `ordered` is ascending absolute positions. Density is steps per number of
        span, which is the reciprocal of the mean step. A route with too few
        packets to judge is treated as dense, which keeps it noisy rather than
        quietly excusing it.
        """
        deltas = self._usable_deltas(ordered, reset_starts or set())
        if len(deltas) + 1 < MIN_PACKETS_FOR_DENSITY:
            return True
        span = sum(deltas)
        if span <= 0:
            return True
        return len(deltas) / span >= DENSITY_THRESHOLD

    def find_gaps(self, sequences: list[int]) -> list[dict]:
        """
        Find missing *packets* across however many times the numbering has
        been round.

        `sequences` is in arrival order. It is turned into absolute positions
        first -- see sequence_epoch -- after which the numbering only ever
        increases and every wrap-around special case disappears: 999 to the next
        cycle's 000 is a step of one, and a packet lost at the roll is found the
        same way as a packet lost anywhere else.

        Three questions are then asked before any gap is reported. Did the game
        restart here, in which case the distance across that point measures a
        reset and not a loss? Does this route number densely enough for an unseen
        number to mean anything at all (Falcon's Eye does not)? And if so, how far
        does it advance per packet? `gap_size` counts missing packets, not missing
        numbers: on a route whose game advances by two, 002 -> 006 is one missing
        packet, not three, and 002 -> 004 is none at all. See the notes on both
        constants above.

        Returns a list of dicts with gap info:
            - expected_sequence: the number the missing packet would have carried
            - sequence_epoch: which time round the numbering that was
            - received_sequence: the sequence that was received instead
            - gap_size: number of missing packets in this gap
        """
        if len(sequences) < 2:
            return []

        timeline = build_timeline(sequences)
        ordered = sorted(set(timeline.absolute))
        if len(ordered) < 2:
            return []

        # A route that does not number densely has nothing to say about what is
        # missing, so do not put words in its mouth.
        if not self.numbers_densely(ordered, timeline.reset_starts):
            return []

        stride = self.route_stride(ordered, timeline.reset_starts)

        gaps = []
        for current, next_abs in zip(ordered, ordered[1:]):
            # The game restarted here. Everything between where it stopped and
            # the end of that cycle was never issued, so there is nothing
            # missing -- reporting it would recreate the false-alarm problem the
            # density and stride gates exist to prevent, at 600-odd per reset.
            if next_abs in timeline.reset_starts:
                continue

            delta = next_abs - current

            # Round rather than floor: a route striding by 2 that jumps by 3 has
            # still lost a packet, and should say so.
            missing = int(delta / stride + 0.5) - 1
            if missing <= 0:
                continue

            for j in range(1, missing + 1):
                expected = current + j * stride
                gaps.append({
                    "expected_sequence": expected % SEQUENCE_RANGE,
                    "sequence_epoch": expected // SEQUENCE_RANGE,
                    "received_sequence": next_abs % SEQUENCE_RANGE,
                    "received_absolute": next_abs,
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
        sequence_epoch = gap_info.get("sequence_epoch")

        # Check if alert already exists for this gap. The epoch is part of the
        # identity: once a route has been round the numbering, "missing 992" on
        # its own names one packet per cycle, and matching on the number alone
        # would let a stale alert from two cycles back swallow a real loss now.
        # Rows predating the column match on the number alone, which is the
        # behaviour they were raised under.
        existing = self.db.query(SequenceAlert).filter(
            SequenceAlert.league_id == league_id,
            SequenceAlert.source_bbs_index == source_bbs_index,
            SequenceAlert.dest_bbs_index == dest_bbs_index,
            SequenceAlert.expected_sequence == expected_sequence,
            SequenceAlert.is_resolved == False,
            sa.or_(
                SequenceAlert.sequence_epoch == sequence_epoch,
                SequenceAlert.sequence_epoch.is_(None),
            ),
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
            sequence_epoch=sequence_epoch,
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
            """The route's outstanding gaps, as (epoch, number) pairs."""
            key = (alert.league_id, alert.source_bbs_index, alert.dest_bbs_index)
            if key not in still_missing:
                seqs = self._sequences_for(key)
                still_missing[key] = {
                    (g["sequence_epoch"], g["expected_sequence"])
                    for g in self.find_gaps(seqs)
                }
            return still_missing[key]

        def outstanding(alert) -> bool:
            """Is this alert still one of the route's gaps?

            An alert raised before the epoch column exists cannot say which time
            round it meant, so it matches on the number in any cycle. That is
            deliberately the generous reading: it retires an old alert when the
            number is no longer missing anywhere, rather than leaving rows nobody
            can act on standing forever.
            """
            gaps = gaps_for(alert)
            if alert.sequence_epoch is None:
                return any(number == alert.expected_sequence for _, number in gaps)
            return (alert.sequence_epoch, alert.expected_sequence) in gaps

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
            elif not outstanding(alert):
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
