"""member_watch: one log line per change of a member board's League Status.

The world is test_league_status's: 555B with the hub at 01, Alpha at 02 and
Bravo at 03; 902I with Alpha at 02. Nobody has polled anything yet.
"""
import pytest
from loguru import logger

from backend.services import member_watch
from tests.test_league_status import _healthy_game, _poll, world  # noqa: F401  (fixture)


@pytest.fixture
def logs():
    records = []
    sink = logger.add(lambda m: records.append(m.record), level="DEBUG")
    yield records
    logger.remove(sink)


def _lines(records, level):
    return [r["message"] for r in records
            if r["level"].name == level and r["message"].startswith("Member status:")]


def _config(w):
    return {"server": {"data_dir": str(w.data_dir)}, "hub": {"bbs_index": "01"}}


def test_a_state_is_logged_when_it_starts_and_not_again(world, logs):
    w = world
    seen = member_watch.check(w.db, _config(w), {})
    assert sorted(_lines(logs, "WARNING")) == [
        "Member status: 555B 02 alpha is not_polling: Never polled this league "
        "(no poll recorded yet)",
        "Member status: 555B 03 bravo is not_polling: Never polled this league "
        "(no poll recorded yet)",
        "Member status: 902I 02 alpha is not_polling: Never polled this league "
        "(no poll recorded yet)",
    ]
    logs.clear()
    seen = member_watch.check(w.db, _config(w), seen)
    assert _lines(logs, "WARNING") == [] and _lines(logs, "INFO") == []


def test_coming_back_to_ok_is_logged_once(world, logs):
    w = world
    seen = member_watch.check(w.db, _config(w), {})
    logs.clear()
    _poll(w, w.bre.id)
    _healthy_game(w)
    seen = member_watch.check(w.db, _config(w), seen)
    assert _lines(logs, "INFO") == ["Member status: 555B 02 alpha is ok again (was not_polling)"]
    assert _lines(logs, "WARNING") == []
    logs.clear()
    member_watch.check(w.db, _config(w), seen)
    assert _lines(logs, "INFO") == []


def test_a_board_first_seen_ok_is_not_logged(world, logs):
    w = world
    _poll(w, w.bre.id)
    _healthy_game(w)
    member_watch.check(w.db, _config(w), {})
    assert not any("555B 02" in line for line in _lines(logs, "WARNING") + _lines(logs, "INFO"))


def test_the_hubs_own_membership_is_not_watched(world, logs):
    w = world
    member_watch.check(w.db, _config(w), {})
    assert not any(" 01 hub " in line for line in _lines(logs, "WARNING"))


def test_an_inactive_league_is_not_watched(world, logs):
    w = world
    w.ib.is_active = False
    w.db.commit()
    member_watch.check(w.db, _config(w), {})
    assert not any("902I" in line for line in _lines(logs, "WARNING"))
