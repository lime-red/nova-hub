"""An Immortal Barons league across a packet protocol bump: release to trunk.

Every IB build speaks one packet protocol (game.Protocol) and holds a packet
in any other. A packet in a newer format waits in held/ until its reader
upgrades and then applies; one in an older format is never applied, and
expires after HeldPacketDays (bbs.cfg). So a league that upgrades one node at
a time strands whatever the laggards sent the nodes that moved first, and the
way across is the freeze: freeze, let every board report quiet, upgrade every
node, thaw.

Both are played here, on a league started on the release, with
current.json's trunk as the upgrade:

- without the freeze, the hub first: the hub holds every board's packets for
  their protocol and says so (the League Status "held" state); a board that
  upgrades applies what the hub sent it meanwhile and is heard again; the old
  packets stay held at the hub, and go when they expire;
- with the freeze: nothing is in flight at the upgrade, each board's quiet
  report comes back from the new build, and after the thaw nothing is held
  anywhere and a realm played before the upgrade is still there.

When the release and trunk speak the same protocol (after a release that
carries the bump) there is no bump to cross, and the scenarios skip.
"""
import datetime as dt
import os

import pytest

from rig.ib_player import Player

pytestmark = pytest.mark.asyncio

SETTLES_WITHIN = 5


def _round(league):
    for board in league.boards.values():
        board.sync()
        board.maint()
        board.sync()


def _use_rig_db(league, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from backend.services.game_reset import perform
    hub = league._hub
    monkeypatch.setattr(perform, "get_session", lambda: sessionmaker(bind=hub._engine)())
    return hub


def _hub_game(league):
    """The hub's game as the league page reads it: the protocol it judges held
    packets by is the one the hub's newest packet stated."""
    from backend.models.database import League
    from backend.services import game_status
    from rig.layout import HUB_INDEX

    db = league._hub.db
    db.expire_all()
    row = db.get(League, league.league_row_id)
    return game_status.hub_games(db, [row], game_status._hex(HUB_INDEX), league.data_dir,
                                 league.config)[row.id]


def _number(build) -> str:
    """'immortal-barons 0.2.7-dev (7ae531d)' -> '0.2.7-dev'"""
    return build.version.split()[1]


def _everything_held(league) -> str:
    lines = [f"hub: {league.held()}"]
    lines += [f"board {i:02d}: {b.held()}" for i, b in league.boards.items()]
    return "\n".join(lines)


async def test_without_a_freeze_a_staggered_upgrade_strands_the_old_packets(ib_upgrade_league,
                                                                             ib_upgrade):
    old, new = ib_upgrade
    league = ib_upgrade_league
    league.settle(SETTLES_WITHIN)
    assert league.held() == [], _everything_held(league)

    # The hub goes first, as Andy's note has it. Its game now holds what the
    # boards send, and the boards hold what it sends.
    league.upgrade_hub(new)
    _round(league)
    hub_held = league.held()
    old_protocol = {h.protocol for h in hub_held}
    if not hub_held:
        pytest.skip(f"{old.version} and {new.version} speak one protocol: the hub held nothing")
    assert len(old_protocol) == 1, _everything_held(league)
    old_protocol = old_protocol.pop()
    assert {h.from_board for h in hub_held} == {b.name for b in league.boards.values()}, (
        _everything_held(league))
    sent_down = {h.protocol for b in league.boards.values() for h in b.held()}
    assert sent_down and old_protocol not in sent_down, (
        f"the boards should hold the hub's new-format packets:\n{_everything_held(league)}")
    new_protocol = sent_down.pop()

    game = _hub_game(league)
    assert game.protocol == new_protocol, "the page judged held packets by a stale protocol"
    assert {h.reason for h in game.held} == {"protocol"}, [(h.file, h.reason) for h in game.held]
    for index in league.boards:
        assert game.board_at(index).protocol_held, f"board {index:02d} should show as held"

    # Board 02 upgrades: what the hub sent it meanwhile applies, and the hub
    # hears it again. Board 03, still on the release, is still held.
    b2, b3 = league.boards[2], league.boards[3]
    b2.upgrade(new)
    _round(league)
    assert [h for h in b2.held() if h.protocol == new_protocol] == [], (
        f"board 02 upgraded and still holds new-format packets:\n{_everything_held(league)}")
    game = _hub_game(league)
    assert not game.board_at(2).protocol_held, "the hub still holds board 02's traffic"
    assert game.board_at(3).protocol_held, "board 03 is still on the release"

    b3.upgrade(new)
    _round(league)
    _round(league)
    game = _hub_game(league)
    for index in league.boards:
        assert not game.board_at(index).protocol_held, (
            f"board {index:02d} upgraded and the hub still holds its traffic:\n"
            f"{_everything_held(league)}")
    # Nothing in the new format is held anywhere; what is left is the release's,
    # which no upgrade brings back (version.go: "Upgrading across a bump").
    for where, held in [("hub", league.held())] + [
            (f"board {i:02d}", b.held()) for i, b in league.boards.items()]:
        assert all(h.protocol == old_protocol for h in held), (
            f"{where} holds a packet in the new format:\n{_everything_held(league)}")
    leftovers = league.held()
    assert leftovers, "the hub's old-format packets should stay held until they expire"

    # They expire HeldPacketDays after they were set aside (14 by default).
    gone = (dt.datetime.now() - dt.timedelta(days=15)).timestamp()
    for h in leftovers:
        os.utime(league.game / "held" / h.file, (gone, gone))
    _round(league)
    assert league.held() == [], f"expired packets are still held:\n{_everything_held(league)}"


async def test_a_freeze_carries_the_league_across_the_protocol_bump(ib_upgrade_league,
                                                                    ib_upgrade, monkeypatch):
    from backend.models.database import League
    from backend.services.game_reset import perform

    old, new = ib_upgrade
    league = ib_upgrade_league
    league.settle(SETTLES_WITHIN)
    b2 = league.boards[2]
    player = Player(old.binary, b2.data, "Survivor")
    try:
        player.create_realm("Keepsake")
        assert player.quit() == 0, player.tail()
    finally:
        player.kill()
    _round(league)
    league.wait_idle()

    hub = _use_rig_db(league, monkeypatch)
    row = hub.db.get(League, league.league_row_id)
    freeze = perform.start_order(hub.db, row, "freeze", row.full_id, "rig", league.config,
                                 "rig: upgrading to trunk")
    await perform.run_job(freeze.id, league.config)
    hub.db.expire_all()
    assert freeze.status == "applied", f"{freeze.error}\n{freeze.transcript}"

    # Every board freezes and reports quiet; a second round lets whatever the
    # hub sent in answer be fetched, so nothing is in flight at the upgrade.
    _round(league)
    _round(league)
    league.wait_idle()
    game = _hub_game(league)
    assert game.frozen
    for index, board in league.boards.items():
        assert board.world().get("Frozen"), f"board {index:02d} did not freeze"
        assert game.board_at(index).quiet_since, f"no quiet report from board {index:02d}"
    from backend.services import game_status
    from rig.layout import HUB_INDEX
    hub_hex = game_status._hex(HUB_INDEX)
    undelivered = [p["filename"] for p in league.packets()
                   if p["source_bbs_index"] == hub_hex and p["downloaded_at"] is None]
    assert undelivered == [], f"packets still waiting at the hub at the upgrade: {undelivered}"

    # Every node takes the new build while the league is frozen.
    league.upgrade_hub(new)
    for board in league.boards.values():
        board.upgrade(new)
    _round(league)
    league.wait_idle()
    game = _hub_game(league)
    assert game.frozen
    for index in league.boards:
        b = game.board_at(index)
        assert b.version and b.version.startswith(_number(new)), (
            f"board {index:02d} reports {b.version}, not {_number(new)}")
        assert b.quiet_since, f"board {index:02d} did not report quiet on the new build"
    assert league.held() == [] and all(b.held() == [] for b in league.boards.values()), (
        _everything_held(league))

    thaw = perform.start_order(hub.db, row, "thaw", row.full_id, "rig", league.config)
    await perform.run_job(thaw.id, league.config)
    hub.db.expire_all()
    assert thaw.status == "applied", f"{thaw.error}\n{thaw.transcript}"
    thawed = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    _round(league)
    _round(league)
    league.wait_idle()

    game = _hub_game(league)
    assert not game.frozen
    for index, board in league.boards.items():
        assert not board.world().get("Frozen"), f"board {index:02d} is still frozen"
        b = game.board_at(index)
        assert b.last_packet and b.last_packet >= thawed, (
            f"the hub has not heard from board {index:02d} since the thaw ({b.last_packet})")
        assert not b.protocol_held
    assert league.held() == [] and all(b.held() == [] for b in league.boards.values()), (
        f"the freeze should have left nothing held:\n{_everything_held(league)}")
    assert "Keepsake" in b2.turns_left(), f"board 02 lost its realm:\n{b2.planetary_log()}"
