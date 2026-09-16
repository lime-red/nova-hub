"""Only raise a sequence alert while somebody could still act on it.

A missing packet is worth an alarm for as long as it might be chased -- a player
reports an attach went missing in transit, and the hub can name the number that
never arrived on which route. Past that it is history, and history in an alert
list is worse than useless: it is where the next real one goes to hide.

This bites hardest on the first run after the epoch model ships. Counting cycles
rather than inferring a single wrap means the detector can suddenly see the
whole history it was blind to, and against production's real data that is 623
gaps reaching back to January. All of them would be raised at once.

The gaps are still found and still counted. They just stop ringing a bell.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.services.sequence_validator import (
    DEFAULT_ALERT_MAX_AGE_DAYS,
    SequenceValidator,
)

ROUTE = (1, "02", "01")


class RecordingDB:
    """A database that reports one route and remembers the alerts raised."""

    def __init__(self, arrivals):
        self._arrivals = arrivals
        self.added = []

    def execute(self, statement, params=None):
        if params is None:
            return SimpleNamespace(fetchall=lambda: [ROUTE])
        return SimpleNamespace(fetchall=lambda: list(self._arrivals))

    # create_alert_if_new looks for an existing alert, then adds one.
    def query(self, *a, **kw):
        return SimpleNamespace(filter=lambda *a, **kw: SimpleNamespace(first=lambda: None))

    def add(self, alert):
        self.added.append(alert)

    def commit(self):
        pass

    def refresh(self, alert):
        pass


def arrivals_ending(when, numbers):
    """Numbers arriving a minute apart, the last of them at `when`."""
    start = when - timedelta(minutes=len(numbers) - 1)
    return [(n, start + timedelta(minutes=i)) for i, n in enumerate(numbers)]


# 004 never arrives. Everything either side of it does.
WITH_A_HOLE = [1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
               15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]


def test_a_gap_noticed_today_raises_an_alert():
    """The case the detector exists for, and the control for everything below."""
    db = RecordingDB(arrivals_ending(datetime.utcnow(), WITH_A_HOLE))

    alerts = SequenceValidator(db, max_age_days=14).check_sequences()

    assert len(alerts) == 1, alerts
    assert alerts[0].expected_sequence == 4


def test_a_gap_from_months_ago_does_not():
    """Production's back catalogue, which is what would otherwise flood in."""
    long_ago = datetime.utcnow() - timedelta(days=120)
    db = RecordingDB(arrivals_ending(long_ago, WITH_A_HOLE))

    assert SequenceValidator(db, max_age_days=14).check_sequences() == []
    assert db.added == [], "an alert was raised for a gap nobody can act on"


def test_the_boundary_is_the_age_of_the_arrival_that_revealed_the_gap():
    """Just inside the window alerts; just outside it does not.

    Seven hours either side of the boundary rather than a day, so that a cutoff
    computed in the wrong timezone frame -- the mistake made once already in the
    retention pass -- lands on the wrong side and fails this test. A day of slack
    would absorb it silently.
    """
    cutoff_days = 30
    inside = datetime.utcnow() - timedelta(days=cutoff_days, hours=-7)
    outside = datetime.utcnow() - timedelta(days=cutoff_days, hours=7)

    assert SequenceValidator(
        RecordingDB(arrivals_ending(inside, WITH_A_HOLE)), max_age_days=cutoff_days
    ).check_sequences()
    assert SequenceValidator(
        RecordingDB(arrivals_ending(outside, WITH_A_HOLE)), max_age_days=cutoff_days
    ).check_sequences() == []


@pytest.mark.parametrize("disabled", [None, 0])
def test_no_cutoff_means_alert_on_everything(disabled):
    """The old behaviour, still reachable: 0 or unset bounds nothing.

    Someone auditing history deliberately should be able to see all of it.
    """
    long_ago = datetime.utcnow() - timedelta(days=365)
    db = RecordingDB(arrivals_ending(long_ago, WITH_A_HOLE))

    alerts = SequenceValidator(db, max_age_days=disabled).check_sequences()

    assert len(alerts) == 1, "an explicit request for the whole history was bounded anyway"


def test_ageing_out_does_not_hide_a_recent_gap_on_the_same_route():
    """The discriminating case: one route, two gaps, different ages.

    A route that lost a packet in January and another one this morning must
    still report this morning's. If the cutoff were applied per route rather
    than per gap, the old loss would take the new one down with it.
    """
    numbers = list(range(1, 400))
    numbers.remove(5)      # the old loss
    numbers.remove(395)     # the recent one

    # A packet every hour, so 395 lands near now and 005 lands weeks back.
    now = datetime.utcnow()
    arrivals = [
        (n, now - timedelta(hours=len(numbers) - 1 - i))
        for i, n in enumerate(numbers)
    ]

    alerts = SequenceValidator(RecordingDB(arrivals), max_age_days=7).check_sequences()

    assert [a.expected_sequence for a in alerts] == [395], (
        "the recent loss was lost along with the old one"
    )


def test_the_default_is_short_enough_to_stay_actionable():
    """A guard on the constant itself.

    The whole point is a list someone reads. Set this to a year and the setting
    is still 'on' while doing nothing useful.
    """
    assert 0 < DEFAULT_ALERT_MAX_AGE_DAYS <= 30
