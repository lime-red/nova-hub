"""Scenario 5b - the wrap, through the hub.

`test_sequence.py` proves the detector finds a gap in a short dense run. That is
the easy case, and it is the case the old density/stride model already handled.
It says nothing about the situation production is actually in.

Production's two busiest routes have been round the 000-999 numbering seven and
eight times. Once a route has wrapped even once, its numbers no longer form a
run with a hole in it -- they form a complete 000-999 set, over and over. The
old model sorted them, saw no hole, and reported an all-clear for months. That
is the failure this file exists to pin down: not "can it find a gap" but "can it
still find a gap after the numbering has been round".

Why these packets are synthesised rather than played out. `grind.sh` already
walked a real BRE install the whole way round -- 1,100 forced cycles, 999 -> 000
at cycle 1,002, filenames reissued from cycle 1,004 -- so what the *game* does
across a wrap is settled by measurement. What is unsettled is what the *hub*
does with 1,100 arrivals, and the validator reads exactly two things from a
packet row: its sequence number and its arrival order. Playing 1,100 real rounds
through dosemu would take hours to re-prove the half already proven and would
not touch the half that is not. So one genuine packet supplies the bytes, and
the arrival sequence is built deliberately -- including the losses, which have
to be known in advance for the assertions to mean anything.

The real-game half is still here: test_arrival_order_is_what_the_hub_records
checks the premise the synthetic tests rest on, using actual game output.
"""
import pytest

from rig import node as node_rig
from rig.layout import LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]

# Two full trips round the numbering, then a hundred into a third. Two is the
# minimum that reproduces production's problem: with only one cycle behind it, a
# lost number is simply absent and even a sorted view would notice. It is the
# second cycle supplying the number the first one lost that makes the loss
# invisible -- and production's busiest routes have been round eight times.
CYCLES = (1000, 1000, 100)


def _walk(dropped=()):
    """The numbers a route would show, in arrival order, across two wraps."""
    dropped = set(dropped)
    arrivals = []
    for epoch, span in enumerate(CYCLES):
        for n in range(span):
            if (epoch, n) not in dropped:
                arrivals.append(n)
    return arrivals


def _route(hub, league_id, source="02", dest="01"):
    """The (league row id, source, dest) triple the validator works in."""
    from backend.models.database import League as LeagueRow

    league = LEAGUES[league_id]
    row = (
        hub.db.query(LeagueRow)
        .filter(LeagueRow.league_id == league.number,
                LeagueRow.game_type == league.game)
        .one()
    )
    return (row.id, source, dest)


def _deliver_run(hub, payload, arrivals, source="02", dest="01"):
    """Feed a route's whole history to the hub, oldest first.

    uploaded_at is set explicitly rather than left to the column default: 1,100
    inserts inside one second all carry the same timestamp, and while the
    validator does break that tie on id, a test of arrival order should not be
    quietly resting on a tie-break.
    """
    from datetime import datetime, timedelta

    base = datetime(2026, 1, 1)
    for position, number in enumerate(arrivals):
        name = f"900b{source}{dest}.{number:03d}"
        packet = hub.deliver(name, payload)
        packet.uploaded_at = base + timedelta(seconds=position)
    hub.db.commit()


@pytest.fixture
def real_payload(pristine):
    """Bytes from a genuine BRE packet, so nothing here is parsing fiction."""
    pristine("900B")
    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="q5w")
    produced = dict(node_rig.take_outbound(LEAGUE, NODE02))
    assert produced, "the game produced no packet to borrow bytes from"
    return next(iter(produced.values()))


async def test_arrival_order_is_what_the_hub_records(pristine, hub, real_payload):
    """The premise the rest of this file stands on, checked against real output.

    Three genuine packets, delivered out of numerical order. The validator must
    read them back in the order they arrived -- if it reads them sorted, every
    wrap becomes invisible again and the tests below would pass for the wrong
    reason.
    """
    from backend.services.sequence_validator import SequenceValidator

    hub.seed(["900B"])

    packets = {"900b0201.001": real_payload}
    for n in range(2):
        node_rig.force_packet(LEAGUE, NODE02, "REQUEST", tag=f"q5w{n}")
        packets.update(dict(node_rig.take_outbound(LEAGUE, NODE02)))

    # Deliver .003 before .002: a late arrival, which is ordinary.
    for name in ("900b0201.001", "900b0201.003", "900b0201.002"):
        assert name in packets, f"{name} was never produced; got {sorted(packets)}"
        hub.deliver(name, packets[name])

    validator = SequenceValidator(hub.db)
    route = _route(hub, "900B")
    assert validator._sequences_for(route) == [1, 3, 2], (
        "the hub read the route in numerical order, not arrival order"
    )


async def test_a_clean_wrap_raises_no_alert(hub, real_payload):
    """2,100 arrivals, nothing lost, two rolls through 999. Silence expected.

    The negative control, and the expensive one to get wrong: a detector that
    cries loss on every wrap would bury the real thing under 1,000 alerts.
    """
    from backend.services.sequence_validator import SequenceValidator

    hub.seed(["900B"])
    _deliver_run(hub, real_payload, _walk())

    assert SequenceValidator(hub.db).check_sequences() == [], (
        "the wrap itself was reported as packet loss"
    )


async def test_losses_are_still_found_after_the_numbering_has_wrapped(hub, real_payload):
    """The regression this whole model was written for.

    Three packets go missing on the second trip round: one mid-cycle, one that
    is the 999 immediately before the roll, and one in the third cycle. Every
    one of those numbers still appears elsewhere in the history, carried by the
    cycle that did not lose it -- so sorted, nothing is missing at all, which is
    why the old model found nothing. Counted in arrival order, each is a step
    of two where a step of one was due.
    """
    from backend.models.database import SequenceAlert
    from backend.services.sequence_validator import SequenceValidator

    hub.seed(["900B"])
    lost = [(1, 500), (1, 999), (2, 42)]
    _deliver_run(hub, real_payload, _walk(dropped=lost))

    alerts = SequenceValidator(hub.db).check_sequences()
    assert alerts, "no loss reported on a route that lost three packets"

    rows = hub.db.query(SequenceAlert).all()
    found = sorted((r.sequence_epoch, r.expected_sequence) for r in rows)
    assert found == lost, found
    assert all(r.gap_size == 1 for r in rows), [r.gap_size for r in rows]


async def test_the_sorted_view_those_losses_hide_in(hub, real_payload):
    """Why the model had to change, stated as an assertion rather than a story.

    The same history the previous test finds three losses in contains, when
    sorted, every number from 000 to 999 with nothing missing -- because an
    earlier cycle supplies each number a later one dropped. Any detector
    reasoning about the sorted set is structurally incapable of seeing them,
    and adding more history only makes it blinder.
    """
    from backend.services.sequence_validator import SequenceValidator

    hub.seed(["900B"])
    arrivals = _walk(dropped=[(1, 500), (1, 999), (2, 42)])
    _deliver_run(hub, real_payload, arrivals)

    route = _route(hub, "900B")
    as_arrived = SequenceValidator(hub.db)._sequences_for(route)
    assert sorted(set(as_arrived)) == list(range(1000)), (
        "the premise is wrong: the sorted set is not gapless after all"
    )


async def test_a_restart_is_not_reported_as_a_thousand_losses(hub, real_payload):
    """Production restarted its numbering twice on route 3/02/01, at 494 and 633.

    A restart and a wrap look alike -- the number falls -- but they mean opposite
    things about the numbers the old cycle never reached. Read as a wrap, a
    restart at 494 claims 505 packets vanished. Nothing was lost here.
    """
    from backend.services.sequence_validator import SequenceValidator

    hub.seed(["900B"])
    arrivals = list(range(494)) + list(range(1, 51))
    _deliver_run(hub, real_payload, arrivals)

    assert SequenceValidator(hub.db).check_sequences() == [], (
        "a game restart was reported as mass packet loss"
    )


async def test_the_hubs_own_route_is_not_judged_after_a_wrap(hub, real_payload):
    """The all-clear worth refusing to give.

    Rows for hub-generated packets are recycled per filename, so after a wrap the
    route holds a fixed 000-999 slot table that answers "no gaps" whatever
    happened. Skipping it is honest; reporting on it is not.
    """
    from backend.services.sequence_validator import SequenceValidator

    hub.seed(["900B"])
    # Losses on the hub's own outbound, which must NOT be reported.
    _deliver_run(hub, real_payload, _walk(dropped=[(1, 500), (2, 42)]),
                 source="01", dest="02")

    assert SequenceValidator(hub.db, hub_index="01").check_sequences() == [], (
        "the hub reported gaps on a route whose rows cannot show them"
    )
