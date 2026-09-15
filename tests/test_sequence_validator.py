# tests/test_sequence_validator.py - Unit tests for sequence gap detection

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.sequence_epoch import build_timeline
from backend.services.sequence_validator import SequenceValidator


class TestFindGaps:
    """Tests for SequenceValidator.find_gaps (pure function, no DB needed)."""

    def _validator(self):
        return SequenceValidator(db=None)

    def test_no_gaps_consecutive(self):
        v = self._validator()
        assert v.find_gaps([1, 2, 3, 4, 5]) == []

    def test_single_element(self):
        v = self._validator()
        assert v.find_gaps([42]) == []

    def test_empty(self):
        v = self._validator()
        assert v.find_gaps([]) == []

    def test_simple_gap(self):
        v = self._validator()
        gaps = v.find_gaps([1, 2, 5])
        expected_missing = {3, 4}
        actual_missing = {g["expected_sequence"] for g in gaps}
        assert actual_missing == expected_missing
        assert all(g["received_sequence"] == 5 for g in gaps)
        # gap_size = next_seq - current - 1 = 5 - 2 - 1 = 2
        assert all(g["gap_size"] == 2 for g in gaps)

    def test_multiple_gaps(self):
        v = self._validator()
        gaps = v.find_gaps([1, 3, 7])
        missing = {g["expected_sequence"] for g in gaps}
        # Missing 2 (gap between 1->3) and 4,5,6 (gap between 3->7)
        assert missing == {2, 4, 5, 6}

    def test_wrap_around_no_gap(self):
        """Sequence wraps 998->999->000->001 with no gaps."""
        v = self._validator()
        gaps = v.find_gaps([998, 999, 0, 1])
        assert gaps == []

    def test_wrap_around_with_gap(self):
        """Sequence wraps but has a gap before the wrap."""
        v = self._validator()
        # 997 -> 999 (missing 998), then 000 -> 001 (no gap across wrap)
        gaps = v.find_gaps([997, 999, 0, 1])
        missing = {g["expected_sequence"] for g in gaps}
        assert 998 in missing
        assert len(missing) == 1

    def test_no_wrap_large_gap_ignored(self):
        """Gaps larger than WRAP_DETECTION_THRESHOLD (500) are treated as wrap."""
        v = self._validator()
        # Only sequences 0,1,2 present — looks like early wrap; treated as no gap
        gaps = v.find_gaps([0, 1, 2])
        assert gaps == []

    def test_duplicates_ignored(self):
        """Duplicate sequence numbers should not produce false gaps."""
        v = self._validator()
        gaps = v.find_gaps([1, 1, 2, 2, 3])
        assert gaps == []


class TestRouteStride:
    """The games do not hand out sequence numbers one at a time.

    Each new game day consumes two and writes its packet at the second, so a
    healthy route reads 002, 004, 006. These tests pin the rule that decides
    when the detector is allowed to believe that -- and when it must not.
    """

    def _validator(self):
        return SequenceValidator(db=None)

    def test_a_route_that_advances_by_two_has_no_gaps(self):
        v = self._validator()
        assert v.find_gaps([2, 4, 6, 8]) == []

    def test_a_lost_packet_on_a_stride_two_route_is_still_found(self):
        v = self._validator()
        # 006 never arrived; 002 004 008 010 did.
        gaps = v.find_gaps([2, 4, 8, 10])
        assert [g["expected_sequence"] for g in gaps] == [6]
        assert gaps[0]["gap_size"] == 1
        assert gaps[0]["received_sequence"] == 8

    def test_two_lost_packets_in_a_row_count_as_two(self):
        v = self._validator()
        gaps = v.find_gaps([2, 4, 10, 12])
        assert [g["expected_sequence"] for g in gaps] == [6, 8]
        assert all(g["gap_size"] == 2 for g in gaps)

    def test_a_stride_that_is_not_a_clean_multiple_still_alerts(self):
        v = self._validator()
        # Stride 2, then a jump of 5: whatever happened, something was lost.
        gaps = v.find_gaps([2, 4, 6, 11])
        assert gaps and all(g["received_sequence"] == 11 for g in gaps)

    def test_one_step_is_not_enough_evidence_to_learn_a_stride(self):
        v = self._validator()
        # A brand new route with two packets proves nothing, so stay noisy:
        # 004 -> 008 reads as three missing numbers, the old behaviour.
        gaps = v.find_gaps([4, 8])
        assert {g["expected_sequence"] for g in gaps} == {5, 6, 7}

    def test_a_mixed_route_falls_back_to_dense_numbering(self):
        v = self._validator()
        # No stride dominates, so nothing is assumed away.
        assert v.route_stride([1, 3, 7]) == 1

    def test_stride_survives_the_wrap(self):
        v = self._validator()
        assert v.find_gaps([994, 996, 998, 0, 2]) == []


class TestRouteDensity:
    """Falcon's Eye does not number densely, and no stride can be read from it.

    Measured on eight months of production: the three BRE leagues run at density
    1.00 and 0.98, Falcon's Eye at 0.31 to 0.66, and FE's routes carry 472 of the
    492 standing alerts. These tests pin the gate that separates the two.
    """

    def _validator(self):
        return SequenceValidator(db=None)

    def test_a_dense_route_is_still_judged(self):
        v = self._validator()
        dense = list(range(1, 41))
        dense.remove(20)
        gaps = v.find_gaps(dense)
        assert [g["expected_sequence"] for g in gaps] == [20]

    def test_a_sparse_route_is_exempt(self):
        v = self._validator()
        # 30 packets spread over ~150 numbers, the shape of 015F's routes.
        sparse = [1 + 5 * n for n in range(30)]
        assert v.numbers_densely(sparse) is False
        assert v.find_gaps(sparse) == []

    def test_a_short_route_is_not_excused_by_accident(self):
        v = self._validator()
        # Four packets, one clear hole. Too small a sample to call the route
        # sparse, and no stride to read either, so the hole is still reported.
        short = [1, 2, 3, 9]
        assert v.numbers_densely(short) is True
        assert [g["expected_sequence"] for g in v.find_gaps(short)] == [4, 5, 6, 7, 8]

    def test_a_regular_wide_step_is_a_stride_not_a_loss(self):
        """[1, 5, 9, 13, 17, 21] is a route that steps by four, every time. The
        sample is too small for density to judge, and that is exactly when the
        stride rule has to carry it."""
        v = self._validator()
        assert v.route_stride([1, 5, 9, 13, 17, 21]) == 4
        assert v.find_gaps([1, 5, 9, 13, 17, 21]) == []

    def test_the_rig_pattern_is_dense_enough_to_judge(self):
        """Three game days at stride two is a small sample, so density defers and
        the stride rule is what keeps it quiet. The two gates cover different
        regimes and both are needed."""
        v = self._validator()
        assert v.numbers_densely([2, 4, 6]) is True
        assert v.find_gaps([2, 4, 6]) == []

    def test_density_is_measured_across_the_wrap(self):
        """A route that rolls over mid-window is still a dense route.

        This used to be asserted by handing numbers_densely the sorted numbers
        and trusting it to recognise the roll. It now takes absolute positions,
        where the roll is a step of one and there is nothing to recognise -- so
        the guarantee is checked end to end instead, through find_gaps, which is
        the only way the density gate is ever reached in production.
        """
        v = self._validator()
        dense = [(990 + n) % 1000 for n in range(30)]

        timeline = build_timeline(dense)
        assert timeline.cycles == 2, "the sample is meant to roll over once"
        assert v.numbers_densely(sorted(set(timeline.absolute))) is True
        assert v.find_gaps(dense) == []

    def test_a_route_six_cycles_deep_still_finds_a_loss(self):
        """The case that made this rewrite necessary.

        League 3's route 02->01 has sent 5,891 packets across six complete
        cycles. Sorted into a set those collapse to a flawless 000-999 run, so
        the old detector reported nothing missing there no matter what went
        astray -- it had been blind on that route for months.
        """
        v = self._validator()
        arrivals = [s for _ in range(6) for s in range(1000)]
        assert v.find_gaps(arrivals) == []

        # Lose one packet in the fifth cycle, where the old model could not look.
        lossy = list(arrivals)
        del lossy[4 * 1000 + 512]

        gaps = v.find_gaps(lossy)
        assert [(g["sequence_epoch"], g["expected_sequence"]) for g in gaps] == [(4, 512)]
