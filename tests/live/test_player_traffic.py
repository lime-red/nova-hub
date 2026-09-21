"""Scenario - traffic a player generated, not maintenance.

Every other live scenario drives the games the way the hub does: unattended
maintenance. That covers the plumbing, but it cannot produce the two things a
league actually carries once people are in it -- a score that changed because
somebody took a turn, and a message somebody wrote. An idle league emits on days
one, two and three and then goes quiet, so without a player there is nothing left
to move.

`rig.player` is the missing half: it drives BRE.EXE FULL, which `node.run()`
refuses by design.
"""
import pytest

from rig import node as node_rig, player
from rig.layout import HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["901B"]
NODE02 = NODES[2]
HUB = NODES[HUB_INDEX]
MESSAGE = "RIG PLAYER MESSAGE ONE"


async def test_a_played_turn_changes_the_score_and_produces_a_packet(pristine, hub):
    """The half that has to work before anything downstream means anything."""
    pristine("901B")
    hub.seed(["901B"])

    visit = player.visit(LEAGUE, NODE02, realm="Node Two", turns=1,
                         ip_messages=[MESSAGE], tag="pt1")

    before, after = visit["status_before"], visit["status_after"]
    assert visit["turns_played"] == 1
    assert after["turns"] == before["turns"] - 1, (
        f"turn count did not move: {before['turns']} -> {after['turns']}"
    )
    # Taking every default is still taking a turn: production runs, taxes are
    # collected, and the score moves off zero. That nominal change is exactly
    # what has to survive the trip to another host.
    assert after["score"] > before["score"], (
        f"a played turn did not move the score: {before['score']} -> {after['score']}"
    )
    # Quitting through (0) matters: FULL's outbound half runs on the way out, and
    # a killed session leaves inuse.flg behind to break every later run.
    assert visit["rc"] == 0

    produced = node_rig.take_outbound(LEAGUE, NODE02)
    names = [name for name, _ in produced]
    assert "901b0201.001" in names, (
        f"the player session produced no packet for the hub; outbound was {names}"
    )


async def test_player_traffic_reaches_the_hub_game(pristine, hub):
    """The packet a player produced goes through the hub and is really ingested."""
    pristine("901B")
    hub.seed(["901B"])

    player.visit(LEAGUE, NODE02, realm="Node Two", turns=1,
                 ip_messages=[MESSAGE], tag="pt2")
    produced = node_rig.take_outbound(LEAGUE, NODE02)

    for name, payload in produced:
        if name.lower().startswith("901b"):
            hub.deliver(name, payload)
    assert hub.spool("inbound") == ["901b0201.001"]

    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_processed == 1
    assert run.packets_unconsumed == 0, (
        "the game was handed a packet it never ingested - check bbs.cfg line 4"
    )
    assert hub.game_inbound("901B") == [], (
        "the packet is still sitting in the game's inbound folder"
    )

    transcript = hub.transcript(run)
    assert transcript.completed
    assert transcript.ingested_from(2), (
        f"no DeCompress line from node 2; items were {transcript.items}"
    )


async def test_the_score_and_message_arrive_at_the_other_node(pristine, hub):
    """The whole point: node 2 plays and writes, and node 1 sees both.

    Asserted from inside the receiving game rather than from the packet, because
    a packet that arrives and is not ingested looks identical from outside -- the
    failure mode that R-3 is about.
    """
    pristine("901B")
    hub.seed(["901B"])

    played = player.visit(LEAGUE, NODE02, realm="Node Two", turns=1,
                          ip_messages=[MESSAGE], tag="pt3")
    for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
        if name.lower().startswith("901b"):
            hub.deliver(name, payload)
    await hub.process()
    assert hub.last_run().packets_unconsumed == 0

    # Now look at the hub's own game as a player would. Playing a turn is not
    # incidental: interplanetary mail is only readable at the start of play,
    # after any local messages. It never appears under (6) Read Messages.
    seen = player.visit(LEAGUE, HUB, realm="Node One", turns=1,
                        read_ip_scores=True, tag="pt3_hub")

    assert "Node Two" in seen["ip_scores"], (
        "node 1's IPScores do not mention the realm that played;\n"
        + seen["ip_scores"][-2000:]
    )
    assert str(played["status_after"]["score"]) in seen["ip_scores"].replace(",", ""), (
        f"node 2 scored {played['status_after']['score']} but node 1 does not show "
        f"it;\n{seen['ip_scores'][-2000:]}"
    )
    assert MESSAGE in seen["turn_text"], (
        "node 2's interplanetary message was not shown to node 1 at the start of "
        f"play;\n{seen['turn_text'][:3000]}"
    )


async def test_the_agent_drives_the_traffic(pristine, hub):
    """The whole stack, end to end: bre_agent decides, the controller plays.

    Nothing here tells the game what to do. Each round reads the realm's live
    state off the status screen, asks bre_agent for an action, and carries it
    out -- and the mix that comes out is the mix the rig wants: a turn taken (so
    the score moves) and a message written (so there is something to carry).
    """
    pristine("901B")
    hub.seed(["901B"])

    session = player.visit(
        LEAGUE, NODE02, realm="Node Two", agent_rounds=2,
        # The rig's knob. At the default weight a fresh realm invests instead of
        # writing to anyone, which is correct play and useless traffic.
        activity_weight=1.0, tag="pt4",
    )

    decisions = session["decisions"]
    assert len(decisions) == 2
    actions = [d["action"] for d in decisions]
    assert "send_message" in actions, f"the agent never wrote to anyone: {actions}"
    assert session["status_after"]["score"] > session["status_before"]["score"], (
        f"no round moved the score; actions were {actions}"
    )

    # Every decision is on the record, with enough to say why.
    logged = player.read_history(NODE02, session["history"])
    assert [entry["action"] for entry in logged] == actions
    for entry in logged:
        assert entry["scores"][entry["action"]] == max(entry["scores"].values())

    # And it is real traffic: it goes through the hub like anything else.
    for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
        if name.lower().startswith("901b"):
            hub.deliver(name, payload)
    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_unconsumed == 0
    assert hub.transcript(run).ingested_from(2)
