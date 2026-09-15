"""The hub's own outbound routes cannot be judged for gaps.

Packets clients upload each create their own row, so an inbound route holds a
true history. Packets the hub generates go through collect_outbound_packets,
which on a filename collision updates the existing row instead of adding one --
correct for delivery, fatal for history. Once such a route has been round the
000-999 numbering, its rows are a fixed table of 1,000 slots and asking it for
gaps returns "none" no matter what happened.

Reproduced on the rig: 1,100 forced cycles rolled 999 -> 000 at cycle 1,002 and
then reissued 900b0102.002 onward with fresh contents under names already used.
"""

from types import SimpleNamespace

import pytest

from backend.services.sequence_validator import SequenceValidator


class FakeDB:
    """Just enough database to drive check_sequences.

    `routes` are (league_id, source, dest) triples; `sequences` maps a route to
    the numbers it would return in arrival order.
    """

    def __init__(self, routes, sequences):
        self._routes = routes
        self._sequences = sequences
        self.queried = []

    def execute(self, statement, params=None):
        if params is None:
            return SimpleNamespace(fetchall=lambda: self._routes)
        key = (params["league_id"], params["source_bbs_index"], params["dest_bbs_index"])
        self.queried.append(key)
        rows = [(n,) for n in self._sequences.get(key, [])]
        return SimpleNamespace(fetchall=lambda: rows)

    def query(self, *a, **kw):  # pragma: no cover - no alert is ever created here
        raise AssertionError("no alert should be created in these tests")


INBOUND = (1, "02", "01")
HUB_OUT = (1, "01", "02")


@pytest.fixture
def sequences():
    # Both routes are missing 005. Only one of them can honestly say so.
    missing_five = [n for n in range(1, 20) if n != 5]
    return {INBOUND: missing_five, HUB_OUT: missing_five}


def test_a_hub_originated_route_is_not_examined_at_all(sequences):
    db = FakeDB([HUB_OUT], sequences)
    validator = SequenceValidator(db, hub_index="01")

    assert validator.check_sequences() == []
    assert db.queried == [], "the route's sequences should not even be fetched"


def test_an_inbound_route_is_still_examined(sequences):
    """The exclusion must be narrow: inbound rows are a real history and are
    where the epoch model earns its keep."""
    db = FakeDB([INBOUND], sequences)
    validator = SequenceValidator(db, hub_index="01")

    gaps = validator.find_gaps(sequences[INBOUND])

    assert [g["expected_sequence"] for g in gaps] == [5]


def test_without_a_hub_index_every_route_is_examined(sequences):
    """The previous behaviour, kept for callers that have no config to hand."""
    db = FakeDB([HUB_OUT], sequences)
    validator = SequenceValidator(db)

    with pytest.raises(AssertionError):
        # It gets as far as trying to raise an alert, which is the old behaviour.
        validator.check_sequences()
    assert db.queried == [HUB_OUT]


def test_the_slot_table_a_wrapped_hub_route_becomes_reports_no_gaps(sequences):
    """Why the skip exists rather than a warning.

    After a wrap the hub's route holds one row per filename, so the numbers are
    a complete 000-999 set. That is indistinguishable from a perfectly healthy
    route, which is exactly the false all-clear worth refusing to give.
    """
    validator = SequenceValidator(None, hub_index="01")
    every_slot = list(range(1000))

    assert validator.find_gaps(every_slot) == []
