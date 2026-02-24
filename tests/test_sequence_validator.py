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
