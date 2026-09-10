"""Scenario 2 - a packet that is not for the hub.

With HOST routing every node sends everything to node 1, so a node-to-node packet
never appears on its own. This uses the game's own routing override, `ROUTE 3 3`
(see DOCS/ROUTE.SAM), which restores node 3 to direct and makes node02 emit a real
`900b0203.001` alongside its usual `900b0201.001`. Real bytes, not synthesised
ones.

The hub is a store-and-forward relay for these: it never has to understand a
packet addressed to somebody else, only put it where node03 will collect it.
"""
import pytest

from rig import node as node_rig
from rig.layout import LEAGUES, NODES
from rig.transcript import Transcript

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]
NODE03 = NODES[3]


async def test_packet_for_another_node_is_relayed(pristine, hub):
    pristine("900B")
    hub.seed(["900B"])

    node_rig.set_route(LEAGUE, NODE02, "ROUTE 3 3")
    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s2")

    produced = dict(node_rig.take_outbound(LEAGUE, NODE02))
    assert "900b0203.001" in produced, (
        "ROUTE 3 3 did not make node02 address node03 directly; "
        f"outbound was {sorted(produced)}"
    )

    for name, payload in produced.items():
        hub.deliver(name, payload)

    await hub.process()
    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"

    # Both packets are accounted for. The relayed one is archived like any other
    # rather than left loose in inbound for the next run to trip over.
    assert hub.spool("inbound") == []
    assert sorted(hub.spool("processed")) == ["900b0201.001", "900b0203.001"]

    # It is queued for node03 under its own name, byte for byte.
    outbound = hub.spool("outbound")
    relayed = [n for n in outbound if n.lower() == "900b0203.001"]
    assert relayed, f"nothing queued for node03; hub outbound was {outbound}"
    body = (hub.data_dir / "packets" / "outbound" / relayed[0]).read_bytes()
    assert body == produced["900b0203.001"], (
        "the relayed packet was not passed through unchanged"
    )

    # And relaying it did not displace the reply the hub owed node02.
    assert any(n.lower().startswith("900b0102") for n in outbound), outbound


async def test_relayed_packet_is_readable_by_its_recipient(pristine, hub):
    """The point of the relay: node03 can actually ingest what node02 sent.

    A relay that handed node03 something it rejects would satisfy every filename
    assertion above and still be broken.
    """
    pristine("900B")
    hub.seed(["900B"])

    node_rig.set_route(LEAGUE, NODE02, "ROUTE 3 3")
    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s2b")
    for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
        hub.deliver(name, payload)
    await hub.process()

    relayed = next(n for n in hub.spool("outbound") if n.lower() == "900b0203.001")
    payload = (hub.data_dir / "packets" / "outbound" / relayed).read_bytes()

    node_rig.give_inbound(LEAGUE, NODE03, "900b0203.001", payload)
    log = node_rig.run(LEAGUE, NODE03, "PLANETARY", tag="s2c")

    transcript = Transcript.from_file(log)
    assert transcript.completed
    assert transcript.ingested_from(2), (
        f"node03 did not decompress the packet; items were {transcript.items}"
    )
    assert node_rig.inbound(LEAGUE, NODE03) == [], (
        "node03 left the packet sitting in its inbound folder"
    )
