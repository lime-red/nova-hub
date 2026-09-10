"""Scenario 6 - replaying a packet the hub has already processed.

Replay is the production recovery move: when a run goes wrong, the operator puts
the packets back and processes again. That is only safe if a second pass over the
same packet changes nothing, so this test is what makes the recovery procedure
trustworthy rather than hopeful.

Both BRE and FE detect duplicate data internally, so the game side is a no-op by
design. What is being checked here is that the hub side agrees.
"""
import pytest

from rig import node as node_rig
from rig.layout import LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]


async def test_reprocessing_the_same_packet_changes_nothing(pristine, hub):
    pristine("900B")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s6")
    produced = dict(node_rig.take_outbound(LEAGUE, NODE02))
    payload = produced["900b0201.001"]

    hub.deliver("900b0201.001", payload)
    await hub.process()
    first = hub.last_run()
    assert first.status == "completed", f"run {first.status}: {first.error_message}"
    after_first = sorted(hub.spool("outbound"))
    assert after_first, "the first pass produced no outbound at all"

    # Replay: the same bytes, the same name, back into inbound as a fresh packet
    # record -- exactly what an operator re-uploading it would produce.
    hub.deliver("900b0201.001", payload)
    await hub.process()
    second = hub.last_run()

    assert second.id != first.id, "the replay did not create a second run"
    assert second.status == "completed", (
        f"replaying a processed packet failed the run: {second.error_message}"
    )
    assert second.packets_unconsumed == 0, (
        "the game refused the replayed packet and left it in its inbound folder"
    )
    assert hub.game_inbound("900B") == []
    assert hub.spool("inbound") == []

    # The archive holds one copy, not two: the replay overwrote its own name
    # rather than accumulating .001 duplicates.
    assert hub.spool("processed") == ["900b0201.001"]

    # And no new traffic was invented for the peers. The game recognised the data
    # as already seen, so the second pass has nothing new to send.
    assert sorted(hub.spool("outbound")) == after_first, (
        f"the replay changed the outbound queue: {after_first} -> "
        f"{sorted(hub.spool('outbound'))}"
    )
