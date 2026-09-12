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

from backend.models.database import Packet
from rig import node as node_rig
from rig.layout import HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]
HUB = NODES[HUB_INDEX]

DAY1, DAY2, DAY3, DAY4, DAY5, DAY6 = ("20261001", "20261002", "20261003",
                                     "20261004", "20261005", "20261006")


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


async def test_the_gap_detector_stays_quiet_when_nothing_was_lost(pristine, hub):
    """The test that reproduced production's 705 false alerts, now inverted.

    Every packet this node produced is delivered to the hub. None is lost, none
    is delayed, nothing is out of order -- but the game skipped a sequence number
    of its own accord on each new day. The detector used to alert on every one of
    those, which is the entire content of production's alert table.

    ROLLOUT_PLAN card B-4. This was xfail until the detector learned that a
    route's numbering is its own business.
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

    assert len(delivered) >= 3, f"not enough traffic to test with: {delivered}"

    alerts = SequenceValidator(hub.db).check_sequences()
    assert alerts == [], (
        f"delivered every packet the game produced ({delivered}) and the gap "
        f"detector raised {len(alerts)} alert(s): "
        + "; ".join(a.description for a in alerts)
    )


async def test_an_idle_league_stops_producing_after_three_game_days(pristine):
    """Six game days, three packets. Days four onward emit nothing at all.

    Found while trying to build a stride-two route long enough to hide a lost
    packet in: it cannot be done from game days alone. Maintenance runs and
    prints its completion marker on every one of the six days -- the game is
    healthy and doing its job -- there is simply nothing left to say once the
    opening recon exchange has settled and no player has touched the game.

    Two things follow. The first is a limit on this scenario: the numbering
    evidence a route can offer is bounded by its traffic, so anything needing a
    long route needs player activity to generate it. The second is a caution for
    production monitoring -- silence from a node is not evidence of a fault, and
    a health check built on "a packet a day" would cry wolf exactly the way the
    gap detector did (ROLLOUT_PLAN B-4).
    """
    pristine("900B")

    produced = []
    for n, date in enumerate((DAY1, DAY2, DAY3, DAY4, DAY5, DAY6)):
        log = node_rig.run(LEAGUE, NODE02, "PLANETARY", tag=f"i{n}", date=date)
        assert _maintenance_fired(log), f"day {n + 1} did not run maintenance"
        produced.append([
            name for name, _ in node_rig.take_outbound(LEAGUE, NODE02)
            if name.lower().startswith("900b0201.")
        ])

    assert [len(day) for day in produced] == [1, 1, 1, 0, 0, 0], (
        f"the emission pattern changed: {produced}. If an idle league now keeps "
        f"producing, the multi-day scenarios can be extended and this test "
        f"should say so rather than be deleted"
    )


async def test_a_stale_false_alert_resolves_itself(pristine, hub):
    """Production carries 705 of these. Nobody is going to close them by hand.

    Plant an alert at a number the games never issue -- exactly the shape of the
    rows sitting in production -- and confirm the next auto-resolve pass retires
    it with a note saying why, rather than leaving it for an operator to tell
    apart from a real one.
    """
    from backend.models.database import SequenceAlert
    from backend.services.sequence_validator import SequenceValidator

    pristine("900B")
    hub.seed(["900B"])

    for tag, date in (("s1", DAY1), ("s2", DAY2), ("s3", DAY3)):
        node_rig.run(LEAGUE, NODE02, "PLANETARY", tag=tag, date=date)
        for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
            if name.lower().startswith("900b0201."):
                hub.deliver(name, payload)

    # source/dest are stored as the two-character hex text from the filename.
    packet = hub.db.query(Packet).filter(
        Packet.source_bbs_index == "02", Packet.dest_bbs_index == "01"
    ).order_by(Packet.sequence_number).first()
    assert packet is not None, "no packet reached the hub"

    stale = SequenceAlert(
        league_id=packet.league_id,
        source_bbs_index=packet.source_bbs_index,
        dest_bbs_index=packet.dest_bbs_index,
        expected_sequence=packet.sequence_number + 1,
        received_sequence=packet.sequence_number + 2,
        gap_size=1,
        description="planted: the shape of the rows in production",
    )
    hub.db.add(stale)
    hub.db.commit()

    assert SequenceValidator(hub.db).auto_resolve_alerts() == 1
    hub.db.refresh(stale)
    assert stale.is_resolved
    assert "never issued" in stale.resolution_note


async def test_a_processing_run_resolves_stale_alerts_by_itself(pristine, hub):
    """auto_resolve_alerts() had no caller anywhere until 2026-09-12.

    That is why production accumulated 213 alerts for packets that had arrived
    months earlier: nothing ever asked. Resolving is now part of a processing
    run, so this asserts the wiring and not just the function.
    """
    from backend.models.database import SequenceAlert

    pristine("900B")
    hub.seed(["900B"])

    for tag, date in (("w1", DAY1), ("w2", DAY2), ("w3", DAY3)):
        node_rig.run(LEAGUE, NODE02, "PLANETARY", tag=tag, date=date)
        for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
            if name.lower().startswith("900b0201."):
                hub.deliver(name, payload)

    packet = hub.db.query(Packet).filter(
        Packet.source_bbs_index == "02", Packet.dest_bbs_index == "01"
    ).order_by(Packet.sequence_number).first()
    assert packet is not None, "no packet reached the hub"

    stale = SequenceAlert(
        league_id=packet.league_id,
        source_bbs_index=packet.source_bbs_index,
        dest_bbs_index=packet.dest_bbs_index,
        expected_sequence=packet.sequence_number + 1,
        received_sequence=packet.sequence_number + 2,
        gap_size=1,
        description="planted: the shape of the rows in production",
    )
    hub.db.add(stale)
    hub.db.commit()

    await hub.process()

    hub.db.refresh(stale)
    assert stale.is_resolved, (
        "a processing run did not resolve a stale alert -- "
        "auto_resolve_alerts() is unwired again"
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
