"""Reading the hub's own Immortal Barons game (ib_hub_game), and what is built
on it: the attack trace's paused journeys and the hub-game endpoint.

The world files are written as v0.2.6 and trunk write them; the field names and
stamp forms are the game's (internal/game/game.go, stamp.go)."""
import datetime as dt
import json
import os
from pathlib import Path

import pytest

from backend.services import ib_hub_game
from backend.services.ib_hub_game import below, days_left, version_key, wait_days

NOW = dt.datetime(2026, 11, 1, 6, 0, 0)


def stamp(t: dt.datetime) -> str:
    """game.StoredStamp: two spaces, and the zone."""
    return t.strftime("%m/%d/%Y  %H:%M:%S UTC")


def make_game(folder: Path, world: dict, rules: dict = None, held=(), roster=None,
              bbs_cfg: str = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "world.json").write_text(json.dumps(world))
    (folder / "config.json").write_text(json.dumps(rules or {"LostForcesDays": 3}))
    names = roster or {1: "Nova Hub", 2: "Alpha #1", 3: "Bravo #1"}
    (folder / "ibnodes.dat").write_text("\n\n".join(
        f"{i}\n{n}\n13:10/{i}\n-\n-\n-" for i, n in names.items()) + "\n")
    if bbs_cfg is not None:
        (folder / "bbs.cfg").write_text(bbs_cfg)
    if held:
        (folder / "held").mkdir()
    for name, packet, arrived in held:
        f = folder / "held" / name
        f.write_text(packet if isinstance(packet, str) else json.dumps(packet))
        when = arrived.replace(tzinfo=dt.timezone.utc).timestamp()
        os.utime(f, (when, when))
    return folder


def test_version_order():
    assert version_key("0.2.10") > version_key("0.2.9")
    assert version_key("0.2.7-rc1") < version_key("0.2.7")
    assert below("0.2.6", "0.2.7") and not below("0.2.7", "0.2.7")
    assert not below(None, "0.2.7") and not below("0.2.6", None)


def test_the_world_is_read_board_by_board(tmp_path):
    folder = make_game(tmp_path / "ib", {
        "GameDay": 9, "StartedDate": "2026-10-23", "LeagueRuleset": "9d74df21",
        "LastPacketFrom": {"Alpha #1": "11/01/2026 04:54:02 UTC"},   # the report form
        "BoardVersion": {"Alpha #1": "0.2.6"},
        "BoardRuleset": {"Alpha #1": "11111111"},
        "TravelTimes": {"Alpha #1": 0.0108}, "TravelSeen": {"Alpha #1": "2026-11-01T04:54:02Z"},
        "Frozen": True, "FreezeSerial": 573, "FreezeMessage": "Updating",
        "FrozenAt": "2026-11-01T05:00:00.123+00:00",
        "QuietBoards": {"Alpha #1": stamp(NOW - dt.timedelta(minutes=30))},
    }, rules={"LostForcesDays": 4, "MinBoardVersion": "0.2.7"})
    g = ib_hub_game.read(folder, now=NOW)
    a = g.board_at(2)
    assert (g.game_day, g.started, g.ruleset, g.min_board_version, g.lost_forces_days) == (
        9, "2026-10-23", "9d74df21", "0.2.7", 4)
    assert (a.name, a.last_packet, a.version, a.ruleset) == (
        "Alpha #1", dt.datetime(2026, 11, 1, 4, 54, 2), "0.2.6", "11111111")
    assert (a.round_trip_days, a.round_trip_seen) == (0.0108, dt.datetime(2026, 11, 1, 4, 54, 2))
    assert (g.frozen, g.freeze_serial, g.freeze_message) == (True, 573, "Updating")
    assert g.frozen_at == dt.datetime(2026, 11, 1, 5, 0, 0, 123000)
    assert a.quiet_since == NOW - dt.timedelta(minutes=30)
    # The hub's newest packet says which rules the league plays now.
    assert ib_hub_game.read(folder, hub_ruleset="22222222", now=NOW).ruleset == "22222222"


def test_no_world_no_game(tmp_path):
    assert ib_hub_game.read(tmp_path, now=NOW) is None


def test_held_packets_are_given_the_games_reasons(tmp_path):
    arrived = NOW - dt.timedelta(days=2)
    world = {"LeagueRuleset": "9d74df21", "PrevLeagueRuleset": "88888888",
             "PrevRulesetAt": stamp(NOW - dt.timedelta(days=3)),
             "LastPacketFrom": {"Bravo #1": stamp(NOW - dt.timedelta(days=5))}}
    folder = make_game(tmp_path / "ib", world, held=[
        ("L902-3-1-0.brp", {"FromBoard": "Bravo #1", "FromNode": 3, "Protocol": 6,
                            "Version": "0.2.7", "Scores": [{}]}, arrived),
        ("L902-2-1-0.brp", {"FromBoard": "Alpha #1", "FromNode": 2, "Protocol": 5,
                            "Ruleset": "77777777", "Attacks": [{}]}, arrived),
        ("L902-2-2-0.brp", {"FromBoard": "Alpha #1", "FromNode": 2, "Protocol": 5,
                            "Ruleset": "88888888", "TimeChecks": [{}]}, arrived),
        ("L902-2-3-0.brp", "not json", arrived),
        ("notes.txt", "ignored", arrived),
    ], bbs_cfg="HeldPacketDays 10\n")
    g = ib_hub_game.read(folder, now=NOW)
    reasons = {h.file: (h.reason, h.type, h.from_index, h.pauses_lost_forces) for h in g.held}
    assert reasons == {
        "L902-3-1-0.brp": ("protocol", "scores", 3, True),
        "L902-2-1-0.brp": ("rules", "attacks", 2, False),
        # The previous rules, within a week of the change: not a rules hold.
        "L902-2-2-0.brp": ("signature", "time checks", 2, False),
        "L902-2-3-0.brp": ("unreadable", None, None, False),
    }
    assert g.held[0].expires == g.held[0].arrived + dt.timedelta(days=10)
    assert g.board_at(3).protocol_held and not g.board_at(2).protocol_held
    assert g.board_at(2).held == {"rules": 1, "signature": 1}


def test_a_protocol_hold_the_board_has_talked_past_pauses_nothing(tmp_path):
    """ProtocolHoldCurrent: held before the board's last accepted packet is a
    leftover, not a stalled link (IB #190)."""
    folder = make_game(tmp_path / "ib", {
        "LastPacketFrom": {"Bravo #1": stamp(NOW - dt.timedelta(hours=1))},
        "ProtocolHeldAt": {"Bravo #1": stamp(NOW - dt.timedelta(days=1))},
    }, held=[("L902-3-1-0.brp", {"FromBoard": "Bravo #1", "Protocol": 4},
              NOW - dt.timedelta(days=1))])
    g = ib_hub_game.read(folder, now=NOW)
    assert g.held[0].reason == "protocol" and not g.held[0].pauses_lost_forces
    assert not g.board_at(3).protocol_held


def test_lost_forces_days_left_as_the_game_counts_them():
    # Launched on day 10 with a 3-day wait: day 11 is the first that counts.
    assert days_left(11, 10, 3, False, 0) == 3
    assert days_left(14, 10, 3, False, 0) == 0
    # Two days held stop the wait; the backstop (5x) still runs from launch.
    assert days_left(14, 10, 3, False, 2) == 2
    assert days_left(14, 10, 3, True, 0) == 12
    assert days_left(14, 10, 0, False, 0) is None          # 0: never given back
    assert (wait_days("attack", 1), wait_days("trade", 1), wait_days("deal", 5)) == (1, 3, 5)


def test_in_flight_items_carry_their_own_wait(tmp_path):
    folder = make_game(tmp_path / "ib", {"GameDay": 12, "InFlight": [
        {"ID": 7, "Kind": "attack", "TargetBoard": "Alpha #1", "TargetEmpire": "Rome",
         "LaunchedDay": 10, "Launched": "2026-10-30T10:00:00Z"},
        {"ID": 8, "Kind": "trade", "TargetBoard": "Bravo #1", "LaunchedDay": 10},
        {"ID": 9, "Kind": "attack", "TargetBoard": "Bravo #1", "LaunchedDay": 10,
         "Held": True, "HeldDays": 1},
    ]}, rules={"LostForcesDays": 1})
    g = ib_hub_game.read(folder, now=NOW)
    assert [(f.id, f.target_index, f.wait_days, f.days_left, f.held) for f in g.in_flight] == [
        (7, 2, 1, 0, False), (8, 3, 3, 2, False), (9, 3, 1, 4, True)]


# ── the attack trace ──────────────────────────────────────────────────────

def test_a_held_targets_attack_is_paused_not_overdue(monkeypatch):
    from backend.services import attack_trace
    monkeypatch.setattr(attack_trace, "_now", lambda: dt.datetime(2026, 11, 9))
    j = attack_trace.Journey("902.2.7", 1, 2, 3, None, None, "Normal Attack",
                             launched=dt.datetime(2026, 11, 1), resolved=None,
                             game_type="I", lost_attack_days=3)
    assert j.mit == "overdue" and j.days_left == 0
    j.paused = True
    assert j.mit is None and j.days_left is None
    monkeypatch.setattr(attack_trace, "_now", lambda: dt.datetime(2026, 11, 2, 23))
    j.paused = False
    assert j.mit is None and j.days_left == 3      # due at the start of the 5th
