# tests/test_sequence_validator.py - Unit tests for sequence gap detection

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

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
