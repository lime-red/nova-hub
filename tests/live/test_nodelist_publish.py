"""A nodelist the hub generates reaches every member, through the hub's own game.

Members get a nodelist one way: the hub's game sends BRNODES.<league> from its
OUTBOUND after its own nodes file changes, and the hub queues that to each
member. Generating used to write only the hub-side copy, so a board added
through self-service never reached anyone (prod 015, Oct 2026). Generation now
writes the hub game's nodes file too; this proves the real game takes it from
there.
"""
import datetime

import pytest

from rig import node as node_rig
from rig.layout import HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]
HUB = NODES[HUB_INDEX]
NEW_INDEX = 5  # a board joining the league; it needs no install of its own


def _day(n: int) -> str:
    """A game date n days after today. Fixtures are rebuilt daily, and a date
    before the game's start makes BRE refuse to run ("Computer Clock has been
    tampered with") -- so never a fixed date."""
    return (datetime.date.today() + datetime.timedelta(days=n)).strftime("%Y%m%d")


async def test_a_new_member_reaches_every_board_through_the_game(pristine, hub):
    from backend.models.database import League, Packet
    from backend.services.applications import regenerate_nodelists

    pristine("900B")
    hub.seed(["900B"])
    league = hub.db.get(League, _add_newcomer(hub))

    # 1. What an approval does.
    problems = regenerate_nodelists(hub.db, [league.id], hub.config)
    assert problems == []
    install = LEAGUE.install_path(HUB)
    nodes_file = next(p for p in install.iterdir() if p.name.lower() == "brnodes.dat")
    assert nodes_file.read_bytes().startswith(f"1 HOST 2 3 4 {NEW_INDEX}\r\n".encode())

    # 2. Ordinary traffic makes the hub's game run.
    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="nodelist")
    for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
        if name.lower().startswith("900b"):
            hub.deliver(name, payload)
    await hub.process()
    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"

    # 3. The game sent its nodelist, and the hub queued it to every member,
    #    the new one included. (On a fresh install the game would send one on
    #    this first run anyway; the test below shows it is the change that
    #    makes it send on any later day.)
    queued = sorted(
        p.dest_bbs_index
        for p in hub.db.query(Packet).filter(Packet.filename == "BRNODES.900",
                                             Packet.league_id == league.id)
    )
    assert queued == ["02", "03", "04", f"{NEW_INDEX:02X}"], (
        f"BRNODES.900 queued for {queued}; game outbound is {hub.game_outbound('900B')}"
    )
    assert not any(n.upper() == "BRNODES.900" for n in hub.game_outbound("900B"))



def _sent_nodelist(node):
    """Take the node's OUTBOUND; return the BRNODES.900 it sent, or None."""
    sent = dict(node_rig.take_outbound(LEAGUE, node))
    return next((payload for name, payload in sent.items() if name.upper() == "BRNODES.900"), None)


async def test_the_game_sends_its_nodelist_when_the_file_changes(pristine, hub):
    """Why installing the file is enough, and why an unchanged one is left alone.

    Played on the hub's own install over faked game days. A fresh install sends
    its nodelist on its first maintenance; after that it sends one ("Updating
    NodeList") only on a day its nodes file has changed -- not every day, and
    no member's traffic is needed to trigger it.
    """
    from backend.services.applications import regenerate_nodelists

    pristine("900B")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, HUB, "PLANETARY", tag="nl-d1", date=_day(1))
    assert _sent_nodelist(HUB) is not None, "a fresh install sent no nodelist"

    node_rig.run(LEAGUE, HUB, "PLANETARY", tag="nl-d2", date=_day(2))
    assert _sent_nodelist(HUB) is None, (
        "the game sent its nodelist on a day nothing changed - it is not change-"
        "driven, and generate() leaving identical files alone buys nothing"
    )

    league_id = _add_newcomer(hub)
    assert regenerate_nodelists(hub.db, [league_id], hub.config) == []
    node_rig.run(LEAGUE, HUB, "PLANETARY", tag="nl-d3", date=_day(3))
    sent = _sent_nodelist(HUB)
    assert sent is not None, "the nodes file changed and the game sent no nodelist"
    assert b"Newcomer BBS" in sent, "the game sent a nodelist without the new board"


def _add_newcomer(hub) -> int:
    from backend.models.database import Client, FtnAddress, League, LeagueMembership

    league = hub.db.query(League).filter(League.league_id == LEAGUE.number,
                                         League.game_type == LEAGUE.game).one()
    league.hub_fidonet_address = LEAGUE.fido_for(HUB_INDEX)
    newcomer = Client(client_id="newcomer", client_secret="x", bbs_name="Newcomer BBS",
                      is_active=True)
    hub.db.add(newcomer)
    hub.db.flush()
    hub.db.add(LeagueMembership(
        client_id=newcomer.id, league_id=league.id, bbs_index=NEW_INDEX,
        ftn_address=FtnAddress.get_or_create(hub.db, newcomer.id, LEAGUE.fido_for(NEW_INDEX)),
        is_active=True,
    ))
    hub.db.commit()
    return league.id
