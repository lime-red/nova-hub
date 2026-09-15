"""Counting cycles instead of guessing at them, and telling a wrap from a reset.

The old detector inferred the wrap: sort the numbers, find the single gap wider
than 500, splice there. That represents exactly one wrap, and production left it
behind long ago -- league 3's route 02->01 has sent 5,891 packets over six
cycles, which sort into a flawless 000-999 run with no gap to find. These tests
pin the replacement, and the two cases the replacement exists to separate: the
space running out, and the game being reset.
"""

import pytest

from backend.services.sequence_epoch import (
    CYCLE_DROP,
    RESTART_DROP,
    SEQUENCE_RANGE,
    build_timeline,
)
from backend.services.sequence_validator import SequenceValidator


@pytest.fixture
def validator():
    return SequenceValidator(db=None)


class TestBuildTimeline:
    def test_a_route_that_never_wraps_is_left_alone(self):
        t = build_timeline([1, 2, 3, 4])
        assert t.absolute == [1, 2, 3, 4]
        assert t.cycles == 1
        assert t.reset_starts == set()

    def test_an_empty_route_does_not_explode(self):
        t = build_timeline([])
        assert t.absolute == [] and t.cycles == 1

    def test_one_wrap_becomes_one_continuous_run(self):
        t = build_timeline([997, 998, 999, 0, 1])
        assert t.absolute == [997, 998, 999, 1000, 1001]
        assert t.cycles == 2
        # A wrap needs no special handling downstream -- that is the point.
        assert t.reset_starts == set()

    def test_six_cycles_are_six_cycles(self):
        """Production's busiest route, reproduced exactly."""
        arrivals = [s for _ in range(6) for s in range(SEQUENCE_RANGE)]
        t = build_timeline(arrivals)
        assert t.cycles == 6
        assert t.absolute[0] == 0
        assert t.absolute[-1] == 5999
        assert t.absolute == sorted(t.absolute), "absolute positions only go up"

    def test_the_same_number_in_two_cycles_stays_two_packets(self):
        """The failure that blinded the detector: set() merged the cycles."""
        t = build_timeline([500, 999, 0, 500])
        assert t.absolute == [500, 999, 1000, 1500]
        assert len(set(t.absolute)) == 4

    def test_a_reset_is_recorded_as_a_reset(self):
        t = build_timeline([345, 346, 347, 0, 1, 2])
        assert t.cycles == 2
        assert t.absolute == [345, 346, 347, 1000, 1001, 1002]
        assert t.reset_starts == {1000}

    def test_a_reset_close_to_the_top_is_read_as_a_wrap(self):
        """At 995 the two events are the same thing under different names."""
        t = build_timeline([993, 995, 0, 1])
        assert t.cycles == 2
        assert t.reset_starts == set(), "no artificial gap should be suppressed"


class TestOutOfOrderArrivals:
    """Production has four backwards arrivals in 18,000. They must not
    fabricate a cycle, because doing so renumbers the route's whole history."""

    def test_a_late_packet_is_not_a_restart(self, ):
        t = build_timeline([345, 346, 347, 340, 348, 349])
        assert t.cycles == 1
        assert t.absolute == [345, 346, 347, 340, 348, 349]

    def test_a_large_dip_that_does_not_continue_is_not_a_restart(self):
        t = build_timeline([500, 501, 502, 400, 503, 504])
        assert t.cycles == 1

    def test_an_ambiguous_dip_at_the_very_end_is_left_alone(self):
        """No look-ahead means no evidence, and guessing here would renumber
        everything that came before it."""
        t = build_timeline([500, 501, 502, 100])
        assert t.cycles == 1

    def test_a_dip_below_the_threshold_is_never_a_restart(self):
        t = build_timeline([50, 60, 60 - (RESTART_DROP - 1), 70, 80])
        assert t.cycles == 1

    def test_a_restart_after_a_late_packet_is_still_caught(self):
        """The comparison is against the cycle's high-water mark, not the
        previous arrival, so one late packet cannot mask the restart behind it.

        A late arrival that falls further than CYCLE_DROP is deliberately not
        defended against: see the note on that constant. Nothing in production
        arrives 500 numbers late, and refusing to believe such a fall would cost
        the far more common case of a route that has wrapped and sent only one
        packet since.
        """
        t = build_timeline([900, 998, 970, 999, 0, 1, 2])
        assert t.cycles == 2
        assert t.absolute[-1] == 1002

    def test_a_fall_past_cycle_drop_needs_no_confirmation(self):
        t = build_timeline([999, 999 - CYCLE_DROP - 1])
        assert t.cycles == 2


class TestGapsAcrossCycles:
    """What the whole exercise is for: finding a loss the old model could not."""

    def test_a_loss_in_the_fifth_cycle_is_found(self, validator):
        arrivals = [s for _ in range(6) for s in range(SEQUENCE_RANGE)]
        del arrivals[4 * SEQUENCE_RANGE + 512]

        gaps = validator.find_gaps(arrivals)

        assert [(g["sequence_epoch"], g["expected_sequence"]) for g in gaps] == [(4, 512)]

    def test_a_packet_lost_exactly_at_the_roll_is_found(self, validator):
        """999 to the next 000 is a step of one in absolute terms, so a packet
        lost on the boundary is found the same way as any other."""
        gaps = validator.find_gaps([997, 998, 0, 1, 2])

        assert [(g["sequence_epoch"], g["expected_sequence"]) for g in gaps] == [(0, 999)]

    def test_the_roll_itself_is_never_a_gap(self, validator):
        assert validator.find_gaps([997, 998, 999, 0, 1, 2]) == []

    def test_the_same_number_lost_in_two_cycles_is_two_gaps(self, validator):
        arrivals = [s for _ in range(3) for s in range(SEQUENCE_RANGE)]
        for cycle in (2, 0):  # descending, so the earlier index stays valid
            del arrivals[cycle * SEQUENCE_RANGE + 700]

        gaps = validator.find_gaps(arrivals)

        assert sorted((g["sequence_epoch"], g["expected_sequence"]) for g in gaps) == [
            (0, 700), (2, 700),
        ]


class TestResetsDoNotLookLikeLoss:
    """A reset restarts the numbering early. Everything between where the game
    stopped and 999 was never issued, so none of it is missing."""

    def test_a_reset_reports_nothing_missing(self, validator):
        before = list(range(0, 348))
        after = list(range(0, 40))

        gaps = validator.find_gaps(before + after)

        assert gaps == [], "652 numbers were never issued, not lost"

    def test_a_wrap_in_the_same_shape_still_reports_its_loss(self, validator):
        """The control for the test above: same structure, but the cycle ran to
        the end of the space, so the missing number really is missing."""
        before = [s for s in range(0, 1000) if s != 998]
        after = list(range(0, 40))

        gaps = validator.find_gaps(before + after)

        assert [(g["sequence_epoch"], g["expected_sequence"]) for g in gaps] == [(0, 998)]

    def test_a_loss_after_a_reset_is_still_found(self, validator):
        """The reset suppresses the distance across the boundary, and nothing
        else -- the cycle that follows it is judged normally."""
        before = list(range(0, 348))
        after = [s for s in range(0, 60) if s != 30]

        gaps = validator.find_gaps(before + after)

        assert [(g["sequence_epoch"], g["expected_sequence"]) for g in gaps] == [(1, 30)]

    def test_a_reset_does_not_drag_the_stride_or_density_off(self, validator):
        """The 653-wide step across a reset is not evidence about how the route
        numbers. If it reached the density calculation it would excuse the whole
        route as sparse and silence every real gap on it."""
        before = list(range(0, 348))
        after = [s for s in range(0, 60) if s != 30]
        timeline = build_timeline(before + after)
        ordered = sorted(set(timeline.absolute))

        assert validator.route_stride(ordered, timeline.reset_starts) == 1
        assert validator.numbers_densely(ordered, timeline.reset_starts) is True
