"""An Immortal Barons league's other console orders, on the real game: a rules
change without a reset, and the freeze and thaw.

As test_ib_reset.py: the job runs in this process against the IB build
rig.ib_builds chose, in a league settled into the Coordinator's rules, with the
league left idle first. Each test checks the result where it lands:

- a rules change: the hub's config.json holds the draft's rules and its world
  did not restart; every member plays the new rules after a sync and a -maint,
  and a realm played before the change is still there (broken: the members
  never adopt them, or the change restarts the game);
- the freeze: every member is frozen after it runs the order, and each one's
  quiet report reaches the hub's game, which is what the league page's
  checklist reads (ib_hub_game); the thaw unfreezes every member.
"""
import json
from datetime import date

import pytest

from rig.ib_player import Player

pytestmark = pytest.mark.asyncio

SETTLES_WITHIN = 5


def _use_rig_db(league, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from backend.services.game_reset import perform
    hub = league._hub
    monkeypatch.setattr(perform, "get_session", lambda: sessionmaker(bind=hub._engine)())
    return hub


def _round(league):
    for board in league.boards.values():
        board.sync()
        board.maint()
        board.sync()


async def test_an_ib_rules_change_reaches_every_board_and_wipes_nothing(ib_league, monkeypatch):
    from backend.models.database import GameReset, League
    from backend.services.game_reset import perform
    from backend.services.game_reset.drafts import validate

    league = ib_league
    league.settle(SETTLES_WITHIN)
    b2 = league.boards[2]
    player = Player(league.build.binary, b2.data, "Survivor")
    try:
        player.create_realm("Keepsake")
        assert player.quit() == 0, player.tail()
    finally:
        player.kill()
    league.wait_idle()
    started = league.boards[2].world().get("StartedDate")

    hub = _use_rig_db(league, monkeypatch)
    row = hub.db.get(League, league.league_row_id)
    settings, errors = validate("I", {"turns_per_day": 9, "dupe_checking": False}, date.today())
    assert not errors, errors
    hub.db.add(GameReset(league_id=row.id, status="draft", source="console",
                         settings=settings, version=1, created_by="rig"))
    hub.db.commit()
    order = perform.start(hub.db, row, 1, row.full_id, "rig", league.config, date.today(),
                          kind="rules")
    await perform.run_job(order.id, league.config)
    hub.db.expire_all()

    assert order.status == "applied", f"{order.error}\n{order.transcript}"
    assert [p["stage"] for p in order.progress][-1] == "sent", order.error
    assert order.sent_config["mismatches"] == [] and order.sent_config["turns_per_day"] == 9
    hub_rules = json.loads(league.game_file("config.json").read_text())
    assert (hub_rules["TurnsPerDay"], hub_rules["DupeChecking"]) == (9, False)

    _round(league)
    for board in league.boards.values():
        assert (board.rules()["TurnsPerDay"], board.rules()["DupeChecking"]) == (9, False), (
            f"board {board.index:02d} kept its rules:\n{board.planetary_log()}")
    assert b2.world().get("StartedDate") == started, "the rules change restarted the game"
    assert "Keepsake" in b2.turns_left(), f"board 02 lost its realm:\n{b2.planetary_log()}"


async def test_an_ib_freeze_stops_every_board_and_the_quiet_reports_come_home(ib_league,
                                                                              monkeypatch):
    from backend.models.database import League
    from backend.services import ib_hub_game
    from backend.services.game_reset import perform

    league = ib_league
    league.settle(SETTLES_WITHIN)
    league.wait_idle()
    hub = _use_rig_db(league, monkeypatch)
    row = hub.db.get(League, league.league_row_id)

    freeze = perform.start_order(hub.db, row, "freeze", row.full_id, "rig", league.config,
                                 "rig: frozen for an update")
    await perform.run_job(freeze.id, league.config)
    hub.db.expire_all()
    assert freeze.status == "applied", f"{freeze.error}\n{freeze.transcript}"
    assert freeze.sent_config["frozen"] is True
    assert freeze.sent_config["message"] == "rig: frozen for an update"

    # A board freezes on the run that applies the order, and reports quiet on
    # it; the report reaches the hub's game with the board's next upload.
    _round(league)
    for board in league.boards.values():
        w = board.world()
        assert w.get("Frozen") and w.get("FreezeMessage") == "rig: frozen for an update", (
            f"board {board.index:02d} did not freeze:\n{board.planetary_log()}")
    league.wait_idle()
    game = ib_hub_game.read(league.game)
    assert game.frozen
    quiet = {b.index for b in game.boards.values() if b.quiet_since}
    assert set(league.boards) <= quiet, (
        f"quiet reports from {sorted(quiet)}, members {sorted(league.boards)}")

    thaw = perform.start_order(hub.db, row, "thaw", row.full_id, "rig", league.config)
    await perform.run_job(thaw.id, league.config)
    hub.db.expire_all()
    assert thaw.status == "applied", f"{thaw.error}\n{thaw.transcript}"
    _round(league)
    for board in league.boards.values():
        assert not board.world().get("Frozen"), (
            f"board {board.index:02d} is still frozen:\n{board.planetary_log()}")
    assert not ib_hub_game.read(league.game).frozen
