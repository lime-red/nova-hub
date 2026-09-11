"""Scenario 5 - sequence numbers, and the gap detector.

This scenario was deferred on a wrong premise. The spike notes recorded that "a
node emits one packet per game day", so exercising sequence progression looked
like it needed REDATE and a multi-day scenario.

It does not. A second PLANETARY the same day really does emit nothing -- daily
maintenance has already run -- but that is a fact about PLANETARY, not about the
games. Both will produce more traffic on demand:

    REQUEST   a recon request to every other board, and a recon back
    RECON     a recon for every other board, requesting nothing in return

Neither writes a packet itself; both queue traffic that the next OUTBOUND
packages. So sequence progression is testable inside a single day, with real
packets from a real game, and the gap detector can be fed a genuine gap rather
than a synthesised filename.
"""
import pytest

from rig import node as node_rig
from rig.layout import HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]
HUB = NODES[HUB_INDEX]


async def test_a_second_planetary_the_same_day_emits_nothing(pristine, hub):
    """The true half of the old claim, kept because everything else assumes it.

    Daily maintenance runs once. If this stopped being true, the scenarios that
    expect exactly one packet from one PLANETARY would go quietly wrong rather
    than fail.
    """
    pristine("900B")

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="q5a")
    assert node_rig.outbound(LEAGUE, NODE02) == ["900b0201.001"]
    node_rig.take_outbound(LEAGUE, NODE02)

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="q5b")
    assert node_rig.outbound(LEAGUE, NODE02) == [], (
        "a second PLANETARY on the same game day produced a packet"
    )


@pytest.mark.parametrize("kind", ["REQUEST", "RECON"])
async def test_traffic_can_be_forced_on_demand(pristine, kind):
    """The false half. Both generators produce a real packet the same day."""
    pristine("900B")

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag=f"q5f{kind}")
    node_rig.take_outbound(LEAGUE, NODE02)

    node_rig.force_packet(LEAGUE, NODE02, kind, tag=f"q5f{kind}")
    assert node_rig.outbound(LEAGUE, NODE02) == ["900b0201.002"], (
        f"{kind} + OUTBOUND did not produce a second packet the same day"
    )


async def test_sequence_advances_by_exactly_one_per_packet(pristine):
    """Three packets in one day, numbered .001 .002 .003.

    The gap detector's whole model is that a route's sequence numbers are dense.
    Nothing had ever demonstrated that against a real game -- only against
    filenames the tests made up.
    """
    pristine("900B")

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="q5s")
    seen = [node_rig.outbound(LEAGUE, NODE02)]
    node_rig.take_outbound(LEAGUE, NODE02)

    for n in range(2):
        node_rig.force_packet(LEAGUE, NODE02, "REQUEST", tag=f"q5s{n}")
        seen.append(node_rig.outbound(LEAGUE, NODE02))
        node_rig.take_outbound(LEAGUE, NODE02)

    assert seen == [["900b0201.001"], ["900b0201.002"], ["900b0201.003"]], seen


async def test_a_missing_packet_is_reported_as_a_gap(pristine, hub):
    """Scenario 5 proper: .001 and .003 arrive, .002 never does.

    All three are genuine packets from a real game, so the gap is the real thing
    the detector exists for rather than a hand-written filename.
    """
    from backend.models.database import SequenceAlert
    from backend.services.sequence_validator import SequenceValidator

    pristine("900B")
    hub.seed(["900B"])

    packets = {}
    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="q5g")
    packets.update(dict(node_rig.take_outbound(LEAGUE, NODE02)))
    for n in range(2):
        node_rig.force_packet(LEAGUE, NODE02, "REQUEST", tag=f"q5g{n}")
        packets.update(dict(node_rig.take_outbound(LEAGUE, NODE02)))

    for name in ("900b0201.001", "900b0201.002", "900b0201.003"):
        assert name in packets, f"{name} was never produced; got {sorted(packets)}"

    # Deliver the first and third. The second is lost in transit.
    hub.deliver("900b0201.001", packets["900b0201.001"])
    hub.deliver("900b0201.003", packets["900b0201.003"])

    alerts = SequenceValidator(hub.db).check_sequences()
    assert alerts, "no alert raised for a missing packet"

    rows = hub.db.query(SequenceAlert).all()
    assert len(rows) == 1, [
        (r.source_bbs_index, r.dest_bbs_index, r.expected_sequence, r.received_sequence)
        for r in rows
    ]
    alert = rows[0]
    assert alert.expected_sequence == 2, alert.description
    assert alert.received_sequence == 3, alert.description
    assert alert.gap_size == 1
    assert (alert.source_bbs_index, alert.dest_bbs_index) == ("02", "01")
    assert not alert.is_resolved


async def test_a_dense_sequence_raises_no_alert(pristine, hub):
    """The negative control: nothing missing, nothing reported.

    A detector that always fires is as useless as one that never does.
    """
    from backend.services.sequence_validator import SequenceValidator

    pristine("900B")
    hub.seed(["900B"])

    packets = {}
    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="q5n")
    packets.update(dict(node_rig.take_outbound(LEAGUE, NODE02)))
    node_rig.force_packet(LEAGUE, NODE02, "REQUEST", tag="q5n1")
    packets.update(dict(node_rig.take_outbound(LEAGUE, NODE02)))

    for name in ("900b0201.001", "900b0201.002"):
        hub.deliver(name, packets[name])

    assert SequenceValidator(hub.db).check_sequences() == [], (
        "an alert was raised for a sequence with no gap in it"
    )
