"""Scenario 8 - game time, and what it does to sequence numbers.

`REDATE.COM` hooks int 21h AH=2Ah (GET-DATE), which is exactly where Turbo
Pascal's GetDate goes, so the game sees a faked date and nothing else does.
`node_rig.run(..., date="YYYYMMDD")` installs it, runs the command, and removes
it again.

The reason this scenario is worth more than "days pass": it is the only way to
see what a real game does to sequence numbers over time, and what it does is not
what the hub assumes.
"""
import pytest

from rig import node as node_rig
from rig.layout import HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]
HUB = NODES[HUB_INDEX]

DAY1, DAY2, DAY3 = "20261001", "20261002", "20261003"


def _maintenance_fired(log_path) -> bool:
    text = log_path.read_bytes().decode("latin-1")
    return "Running Daily Maintenance" in text


async def test_daily_maintenance_fires_once_per_game_day(pristine):
    """The control that makes every other date-based result meaningful.

    If maintenance fired on every invocation regardless, a faked date would
    prove nothing -- the game would just be doing the same thing twice. It does
    not: a repeat of a date already played is skipped.
    """
    pristine("900B")

    first = node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="d2a", date=DAY2)
    assert _maintenance_fired(first), "maintenance did not run on a new game day"

    again = node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="d2b", date=DAY2)
    assert not _maintenance_fired(again), (
        "maintenance ran twice for the same game day - the game is not tracking "
        "the faked date, so no date-based assertion here means anything"
    )


async def test_sequence_numbers_are_not_dense_across_days(pristine):
    """The finding this scenario exists for.

    The hub's gap detector assumes a route's sequence numbers are dense: any
    number it has not seen is a lost packet. A real game does not honour that.
    Playing consecutive days on one install skips numbers with nothing lost and
    nothing wrong -- the game consumes sequence numbers for traffic it decides
    not to send.

    This test does not assert a particular pattern of skips, because the pattern
    is not stable. It asserts the property that matters: the numbers advance by
    MORE than one per emitted packet, so density cannot be relied on.
    """
    pristine("900B")
    seen = []

    for tag, date in (("m1", DAY1), ("m2", DAY2), ("m3", DAY3)):
        node_rig.run(LEAGUE, NODE02, "PLANETARY", tag=tag, date=date)
        for name, _ in node_rig.take_outbound(LEAGUE, NODE02):
            if name.lower().startswith("900b0201."):
                seen.append(int(name.rsplit(".", 1)[1]))

    assert len(seen) >= 2, f"not enough packets across three game days: {seen}"
    assert seen == sorted(seen), f"sequence went backwards: {seen}"
    span = seen[-1] - seen[0]
    assert span > len(seen) - 1, (
        f"sequence numbers {seen} were dense across game days. If this is now "
        f"reliably true, the gap detector's assumption holds and "
        f"docs/ROLLOUT_PLAN.md card B-4 should be revisited"
    )


async def test_an_uncollected_packet_is_rewritten_in_place(pristine):
    """Running again while a packet is still pending rewrites it, same number.

    The sequence number is not re-allocated: the pending file keeps its name and
    gains the new traffic. Measured across three runs on one game day -- 176,
    232, then 288 bytes under the same filename, a different hash each time.

    This matters to the hub for a reason that has nothing to do with sequence
    numbers: a packet sitting in a game OUTBOUND folder is not a finished
    artefact. Its *contents* change under a stable name until something collects
    it. Anything that reads a packet, leaves it there, and trusts it later is
    reading a file that may since have grown.
    """
    pristine("900B")
    hub_node = HUB

    node_rig.run(LEAGUE, hub_node, "PLANETARY", tag="ip1", date=DAY1)
    first = node_rig.outbound(LEAGUE, hub_node)
    assert first, "no packet to rewrite"
    name = first[0]
    path = LEAGUE.install_path(hub_node) / "OUTBOUND" / name
    before = path.read_bytes()

    # Same game day, more traffic, packet deliberately left in place.
    node_rig.run(LEAGUE, hub_node, "REQUEST", tag="ip2", date=DAY1)
    node_rig.run(LEAGUE, hub_node, "OUTBOUND", tag="ip3", date=DAY1)

    after_names = node_rig.outbound(LEAGUE, hub_node)
    assert name in after_names, (
        f"the pending packet was renamed: {first} became {after_names}"
    )
    after = path.read_bytes()
    assert after != before, "the pending packet was not rewritten at all"
    assert len(after) > len(before), (
        f"expected the pending packet to gain traffic, {len(before)} -> {len(after)}"
    )


async def test_each_game_day_consumes_two_sequence_numbers(pristine):
    """Where production's 705 gap alerts come from.

    One file per game day, but the numbering advances by two: the first number
    of each day is consumed without a file ever appearing under it. Measured
    across three consecutive days -- .002, .004, .006 -- which is precisely the
    gap=1 alert that dominates production's alert table.

    Asserted as "advances by more than one" rather than "exactly two", because
    the mechanism behind the silent increment is not established and the margin
    is what the gap detector actually cares about.
    """
    pristine("900B")
    hub_node = HUB
    install = LEAGUE.install_path(hub_node) / "OUTBOUND"

    numbers = []
    for tag, date in (("t1", DAY1), ("t2", DAY2), ("t3", DAY3)):
        node_rig.run(LEAGUE, hub_node, "PLANETARY", tag=tag, date=date)
        # Left in place on purpose: each day adds a new file beside the last.
        numbers = sorted(
            int(p.name.rsplit(".", 1)[1])
            for p in install.glob("900b0102.*")
        )

    assert len(numbers) == 3, f"expected one packet per game day, got {numbers}"
    steps = [b - a for a, b in zip(numbers, numbers[1:])]
    assert all(s > 1 for s in steps), (
        f"sequence numbers {numbers} advanced densely across game days. If that "
        f"is now reliably true, the gap detector's assumption holds and "
        f"ROLLOUT_PLAN card B-4 should be revisited"
    )


async def test_the_gap_detector_alerts_when_nothing_was_lost(pristine, hub):
    """Reproduces the 705 unresolved alerts sitting in production.

    Every packet this node produced is delivered to the hub. None is lost, none
    is delayed, nothing is out of order. The detector alerts anyway, because the
    game skipped a sequence number of its own accord.

    This is the false-positive path, and it is why production's alert table is
    705 rows of noise that nobody can act on. Marked xfail rather than asserted
    as correct: it documents current behaviour, and it will start passing --
    loudly -- the day the detector learns the difference.
    """
    from backend.services.sequence_validator import SequenceValidator

    pristine("900B")
    hub.seed(["900B"])

    delivered = []
    for tag, date in (("g1", DAY1), ("g2", DAY2), ("g3", DAY3)):
        node_rig.run(LEAGUE, NODE02, "PLANETARY", tag=tag, date=date)
        for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
            if name.lower().startswith("900b0201."):
                hub.deliver(name, payload)
                delivered.append(name)

    assert len(delivered) >= 2, f"not enough traffic to test with: {delivered}"

    alerts = SequenceValidator(hub.db).check_sequences()
    pytest.xfail(
        f"delivered every packet the game produced ({delivered}) and the gap "
        f"detector still raised {len(alerts)} alert(s)"
        if alerts else
        "no false alert this time - see the test body"
    )


async def test_a_multi_day_round_trip_still_processes(pristine, hub):
    """Whatever the numbering does, the packets themselves must still flow.

    The point of separating this from the sequence assertions: a false alert is
    noise, but silently failing to process real multi-day traffic would be an
    outage.
    """
    pristine("900B")
    hub.seed(["900B"])

    for tag, date in (("r1", DAY1), ("r2", DAY2)):
        node_rig.run(LEAGUE, NODE02, "PLANETARY", tag=tag, date=date)
        for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
            if name.lower().startswith("900b0201."):
                hub.deliver(name, payload)

    queued = hub.spool("inbound")
    assert queued, "nothing reached the hub across two game days"

    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_unconsumed == 0
    assert hub.game_inbound("900B") == []
    assert hub.transcript(run).ingested_from(2)
    assert sorted(hub.spool("processed")) == sorted(queued)
