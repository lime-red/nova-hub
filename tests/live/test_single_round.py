"""Scenario 1 - one full round through the hub, with real packets.

node02 plays its maintenance, produces a genuine packet, the hub processes it
through real dosemu, and a reply comes back addressed to node02.

This is the baseline every other scenario builds on: if it goes red, nothing
downstream means anything.
"""
import pytest

from rig import node as node_rig
from rig.layout import HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]
HUB = NODES[HUB_INDEX]


async def test_packet_makes_a_full_round_trip(pristine, hub):
    pristine("900B")
    hub.seed(["900B"])

    # 1. node02 does its maintenance and produces a real packet for the hub.
    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s1")
    produced = node_rig.take_outbound(LEAGUE, NODE02)
    names = [n for n, _ in produced]
    assert "900b0201.001" in names, (
        f"node02 produced no packet for the hub; outbound was {names}"
    )

    # 2. Hand it to the hub, the way an upload would.
    for name, payload in produced:
        if name.lower().startswith("900b"):
            packet = hub.deliver(name, payload)

    assert hub.spool("inbound") == ["900b0201.001"]

    # 3. Process.
    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", (
        f"run {run.status}: {run.error_message}"
    )
    assert run.packets_processed == 1
    assert run.packets_unconsumed == 0, (
        "the game was handed a packet it never ingested - check bbs.cfg line 4"
    )

    # 4. The game really took it. This is the assertion that survives a broken
    #    inbound path, where every phase marker still prints.
    assert hub.game_inbound("900B") == [], (
        "the packet is still sitting in the game's inbound folder"
    )
    transcript = hub.transcript(run)
    assert transcript.completed
    assert transcript.ingested_from(2), (
        f"no DeCompress line from node 2; items were {transcript.items}"
    )

    # 5. The hub archived it and produced a reply for node02.
    assert hub.spool("processed") == ["900b0201.001"]
    assert hub.spool("inbound") == []
    assert packet.processed_at is not None

    # The reply does not stay in the game's outbound: the same batch collects it
    # into the hub's own outbound spool, normalised to upper case, ready for
    # node02 to download.
    assert hub.game_outbound("900B") == [], (
        "the hub left the game's reply sitting in the game outbound folder"
    )
    outbound = hub.spool("outbound")
    assert "900B0102.001" in outbound, (
        f"no reply queued for node02; hub outbound was {outbound}"
    )


async def test_a_node_emits_one_packet_per_game_day(pristine, hub):
    """Running PLANETARY twice in one day produces one packet, not two.

    BRE's outbound is tied to its daily maintenance, not to the invocation: the
    second and third runs of the same calendar day emit nothing at all. That is
    why sequence numbers cannot be exercised by looping in a single test -- doing
    that needs REDATE, and belongs to the deferred multi-day scenario.

    Worth pinning down because everything else here assumes one packet per node
    per day; if that stopped being true, the sequence assertions elsewhere would
    go quietly wrong rather than fail.
    """
    pristine("900B")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s1d1")
    assert node_rig.outbound(LEAGUE, NODE02) == ["900b0201.001"]
    node_rig.take_outbound(LEAGUE, NODE02)

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s1d2")
    assert node_rig.outbound(LEAGUE, NODE02) == [], (
        "node02 produced a second packet on the same game day"
    )


async def test_hub_replies_to_every_peer_at_sequence_one(pristine, hub):
    """One round from a virgin league: one packet per peer, all at .001.

    The hub answers node02, but also has traffic for node03 and node04 -- it is
    the router, so it forwards what node02 addressed to them. Sequence numbers
    are the gap detector, so a first round has to be exactly .001 everywhere,
    not merely low.
    """
    pristine("900B")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s1p")
    for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
        if name.lower().startswith("900b"):
            hub.deliver(name, payload)
    await hub.process()
    assert hub.last_run().status == "completed"

    assert sorted(hub.spool("outbound")) == [
        "900B0102.001", "900B0103.001", "900B0104.001",
    ], hub.spool("outbound")


async def test_transcript_reports_what_moved(pristine, hub):
    """The packet-type data the hub will eventually want to store.

    Deliberately asserts on the (direction, type, src, dst) set rather than
    compressed sizes: sizes drift by a byte between otherwise identical runs.
    """
    pristine("900B")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s1t")
    for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
        if name.lower().startswith("900b"):
            hub.deliver(name, payload)
    await hub.process()

    transcript = hub.transcript()
    assert transcript.completed

    inbound_types = transcript.types("in")
    assert inbound_types, "nothing was decompressed from the incoming packet"
    # The no-player set established by the spikes. Player List appears even on a
    # virgin game -- an empty roster is still a roster -- so it is NOT evidence
    # that anybody played.
    assert inbound_types <= {
        "Recon Update", "Recon Request", "Routing List",
        "Configupdate", "Player List", "Time Check", "Dummy Data",
    }, f"unexpected data type with no player turns taken: {inbound_types}"

    # Nothing player-generated can appear when nobody has played.
    assert not (transcript.types() & {"Message", "Attack", "Gooie Kablooie"}), (
        "player-only traffic appeared in a run where no player played"
    )

    # Everything decompressed came from node02 -- it is the only node that ran.
    # The destinations are not all 1: node02's packet to the hub carries items
    # addressed to node03 and node04 as well, because the hub is the router and
    # every node reaches the others through it.
    incoming = transcript.moved("in")
    assert incoming, "the game decompressed nothing"
    assert {src for _, _, src, _ in incoming} == {2}, incoming
    assert {dst for _, _, _, dst in incoming} <= {1, 3, 4}, incoming
