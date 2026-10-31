"""Performing an Immortal Barons league reset from the console, on the real game.

The unit tests drive the job against a stand-in binary; this one runs the job
PERFORM RESET starts against the IB build rig.ib_builds chose, in a league whose
boards have settled into the Coordinator's rules (rig/ib_league.py), and checks
the result where it lands:

- the hub's game: config.json holds the draft's rules, and the world restarted
  on the reset's date;
- the packets: the reset order went out and confirmed the console reset;
- the members: after a sync and a -maint each, a realm played before the reset
  is gone, and the board plays the new rules (broken: the order is refused as
  unsigned, or the rules arrive but the world is not wiped);
- and the case the backup exists for: a reset IB refuses (a frozen league)
  leaves the hub's game byte for byte as it was, and nothing goes out.

The job runs in this process, as the BRE one does; the hub server is the one
IbLeague started, so the league is left idle first and no board syncs while
the job runs: two processes must not run the hub's game at once.
"""
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from rig.ib_league import ib
from rig.ib_player import Player

pytestmark = pytest.mark.asyncio

SETTLES_WITHIN = 5
CHANGES = {"turns_per_day": 9, "lost_attack_days": 5, "maintenance_costs": "L",
           "sabre": "C", "sabre_dial": 2, "pirates": False, "game_length": 60}


def tree(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def perform_on(league, changes, monkeypatch):
    """Draft `changes` for 900I and start the reset; returns the row."""
    from sqlalchemy.orm import sessionmaker
    from backend.models.database import GameReset, League
    from backend.services.game_reset import perform
    from backend.services.game_reset.drafts import validate

    hub = league._hub
    monkeypatch.setattr(perform, "get_session", lambda: sessionmaker(bind=hub._engine)())
    row = hub.db.get(League, league.league_row_id)
    settings, errors = validate("I", changes, date.today())
    assert not errors, errors
    hub.db.add(GameReset(league_id=row.id, status="draft", source="console",
                         settings=settings, version=1, created_by="rig"))
    hub.db.commit()
    return perform.start(hub.db, row, 1, row.full_id, "rig", league.config, date.today())


async def test_an_ib_reset_wipes_every_board_into_the_new_rules(ib_league, monkeypatch):
    from backend.services.game_reset import perform

    league = ib_league
    league.settle(SETTLES_WITHIN)
    b2 = league.boards[2]
    player = Player(league.build.binary, b2.data, "Doomed")
    try:
        player.create_realm("Doomsville")
        assert player.quit() == 0, player.tail()
    finally:
        player.kill()
    assert "Doomsville" in b2.turns_left()
    league.wait_idle()

    reset = perform_on(league, CHANGES, monkeypatch)
    await perform.run_job(reset.id, league.config)
    hub = league._hub
    hub.db.expire_all()
    today = date.today().isoformat()

    assert reset.status == "applied", f"{reset.error}\n{reset.transcript}"
    assert [p["stage"] for p in reset.progress][-1] == "sent", reset.error
    rules = league.game_file("config.json")
    hub_rules = json.loads(rules.read_text())
    assert (hub_rules["TurnsPerDay"], hub_rules["LostForcesDays"], hub_rules["MaintCosts"],
            hub_rules["SabreHandling"], hub_rules["SabreConstantDial"],
            hub_rules["Pirates"], hub_rules["GameLength"]) == (9, 5, 1, 3, 2, False, 60)
    assert json.loads(league.game_file("world.json").read_text())["StartedDate"] == today

    # The reset order the hub carried confirmed the console's reset.
    assert reset.game_start is not None, "no reset order was seen in the hub's packets"
    assert reset.game_start.date().isoformat() == today
    assert reset.sent_config.get("mismatches") == [], reset.sent_config

    # Every member takes the order: its world is wiped, and it plays the new rules.
    for board in league.boards.values():
        board.sync()
        board.maint()
        board.sync()
    for board in league.boards.values():
        assert board.rules()["TurnsPerDay"] == 9, (
            f"board {board.index:02d} kept its rules:\n{board.planetary_log()}")
        assert board.world().get("StartedDate") == today, board.planetary_log()
    assert "Doomsville" not in b2.turns_left(), (
        f"board 02 was not reset:\n{b2.planetary_log()}")


async def test_a_reset_ib_refuses_puts_the_hubs_game_back(ib_league, monkeypatch):
    from backend.models.database import GameReset
    from backend.services.game_reset import perform

    league = ib_league
    league.settle(SETTLES_WITHIN)
    league.wait_idle()
    frozen = ib(league.build.binary, league.game, "-league-freeze", "rig: reset refusal")
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
    before = tree(league.game)

    reset = perform_on(league, {"turns_per_day": 9}, monkeypatch)
    await perform.run_job(reset.id, league.config)
    league._hub.db.expire_all()

    assert reset.status == "failed", reset.transcript
    assert "put back" in reset.error, reset.error
    assert tree(league.game) == before, "the hub's game was not restored byte for byte"
    assert league._hub.db.query(GameReset).filter_by(status="draft").count() == 1
