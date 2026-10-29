"""Immortal Barons, end to end: real boards, real clients, the hub as a server.

League 900I: the hub's game is node 1, the League Coordinator, and boards 02 and
03 join it the way a sysop's board does. Each scenario runs once per IB build
rig.ib_builds chose -- the latest release and trunk -- so a change upstream that
the hub or the client has to answer shows up before it is released. See
rig/ib_league.py for what is real here and what the test drives itself.

What each one proves, and what a regression would look like if it failed:

- a board that has nothing but a board key gets the Coordinator's key and the
  roster from its first sync, and settles into the Coordinator's rules
  (broken: the board refuses "unsigned" orders and plays its own defaults);
- a packet from one member to another goes to that member alone, unchanged,
  and is applied there (broken: the hub's game is fed mail that is not its
  own, or it never arrives);
- a Coordinator broadcast reaches every member once, and not the hub;
- a board joining later is adopted by every board through the signed roster,
  though the client never replaces a roster the game already has;
- a packet signed with another key is refused by the board it reaches, however
  faithfully the hub carried it;
- with nothing to say, the league is quiet: a sync with nothing in it starts no
  run (the eqx-hl loop of 2026-10-29 was the opposite);
- a new day rolled over by -maint while barons are in the game, one at a menu
  and one part-way through a turn, gives both the day's turns and costs neither
  a save (broken: a session fails its save and drops the caller, or the turn in
  hand is lost or charged to the day that ended).

A new league is noisy before it is quiet: until a board plays the league's
rules, the Coordinator and the other members hold its packets and say so, and
the hub records the Coordinator's run that first meets each newcomer as failed
(IB exits 1 for a fault it has not reported before). IbLeague.settle() runs
exchanges until a round is clean, and the scenarios look only at what happens
after that.
"""
import json
import shutil
import time
from datetime import datetime, timedelta

from rig.ib_league import TURNS_PER_DAY, _new_game, ib, parse_name
from rig.ib_player import Player

# What the Coordinator's run says about a board that has not adopted the
# league's rules yet: its packets are held, and the run exits 1.
HELD = "are being held: the rules it is playing by are not the league's"
# The steady state is reached on round 4 on v0.2.6 and round 3 on trunk
# (2026-10-29); one round of slack before that is a regression.
SETTLES_WITHIN = 5


def _by_name(paths):
    return {p.name.upper(): p for p in paths}


def test_boards_join_and_settle_into_the_league_rules(ib_league):
    league = ib_league
    boards = list(league.boards.values())
    coord_pub = league.game_file("coord.pub").read_bytes()
    roster = league.game_file("ibnodes.dat").read_bytes()

    # 1. The first sync brings what a sysop would otherwise be sent by hand.
    for board in boards:
        board.sync()
        assert board.file("coord.pub").read_bytes() == coord_pub, board.last_sync
        assert board.file("ibnodes.dat").read_bytes() == roster, board.last_sync
        check = board.league_check()
        assert "FAIL" not in check, f"board {board.index:02d}:\n{check}"

    # 2. Their opening packets go up, and the hub's game takes them. A newcomer's
    #    are held, and that run is the hub's one failure per board; nothing else.
    for board in boards:
        board.maint()
        assert board.outbound(), f"board {board.index:02d} wrote nothing on its first -maint"
        board.sync()
        assert board.outbound() == [], f"board {board.index:02d} left {board.outbound()}"
    runs = league.runs()
    assert runs, f"the hub never ran its game:\n{league.hub_log()[-3000:]}"
    for run in runs:
        assert run["status"] == "completed" or HELD in (run["error_message"] or ""), runs

    # 3. Within a few exchanges every board plays the Coordinator's rules, has
    #    taken its signed orders, and nothing is held anywhere. Both boards wrote
    #    to each other before either had the rules, as two boards joining at once
    #    do, so each holds the other's opening packets until the sender is back
    #    in step. (IB trunk 514557a, 2026-10-27, keeps re-reporting such a hold
    #    on every run until the packet expires: the check that should stop it
    #    reads a ruleset fingerprint the report itself has just reset.)
    rounds = league.settle(SETTLES_WITHIN)
    print(f"{league.build.version}: quiet after {rounds} round(s)")
    for board in boards:
        assert board.rules()["TurnsPerDay"] == TURNS_PER_DAY
        assert "verified orders" in board.planetary_log(), board.planetary_log()
        assert board.inbound() == [], f"board {board.index:02d} left {board.inbound()}"

    # A Coordinator broadcast is a row per member, each fetched once; never the hub.
    # settle() ends on an upload, so the hub's answer to it is fetched first.
    for board in boards:
        board.sync()
    broadcasts = {}
    for p in league.packets():
        name = parse_name(p["filename"])
        if name and name[1] == 1 and name[3] == 0:
            broadcasts.setdefault(p["filename"], []).append(p)
    assert broadcasts, "the Coordinator broadcast nothing"
    for filename, rows in broadcasts.items():
        assert sorted(r["dest_bbs_index"] for r in rows) == ["02", "03"], (filename, rows)
        assert all(r["downloaded_at"] for r in rows), (filename, rows)


def test_a_packet_between_members_goes_straight_to_the_member(ib_league):
    league = ib_league
    league.settle(SETTLES_WITHIN)
    b2, b3 = league.boards[2], league.boards[3]

    b2.maint()
    sent = {n.upper(): (b2.data / "outbound" / n).read_bytes() for n in b2.outbound()}
    to3 = {n for n in sent if parse_name(n)[3] == 3}
    assert to3, f"board 02 wrote nothing for board 03: {sorted(sent)}"
    b2.sync()

    # Routed by the name alone: one row each, for board 03, and the hub's game
    # (which takes only rows for 01) was never handed it.
    rows = [p for p in league.packets() if p["filename"].upper() in to3]
    assert sorted(p["filename"].upper() for p in rows) == sorted(to3), rows
    assert {p["dest_bbs_index"] for p in rows} == {"03"}, rows
    queued = _by_name(league.spool("outbound").iterdir())
    for name in to3:
        assert queued[name].read_bytes() == sent[name], f"{name} was changed on the way"

    mark = b3.log_mark()
    b3.sync()
    result = b3.maint()
    assert b3.inbound() == [], f"board 03 left {b3.inbound()}:\n{result.stdout}"
    highest = max(parse_name(n)[2] for n in to3)
    seen = b3.ledger().get("HighSeq", {}).get(b2.name, 0)
    assert seen >= highest, (
        f"board 03 has applied board 02's packets up to {seen}, not {highest}:\n"
        f"{b3.log_since(mark)}")
    assert b3.faults_since(mark) == [], b3.log_since(mark)


def test_a_board_joining_later_is_adopted_by_every_board(ib_league):
    league = ib_league
    league.settle(SETTLES_WITHIN)
    b2, b3 = league.boards[2], league.boards[3]

    b4 = league.add_board(4)
    assert b2.roster_boards() == [1, 2, 3]

    # The newcomer has no roster, so its client installs the hub's.
    b4.sync()
    assert b4.roster_boards() == [1, 2, 3, 4], b4.last_sync
    assert b4.file("coord.pub").read_bytes() == league.game_file("coord.pub").read_bytes()

    # The others already have one, so their clients leave the hub's file alone...
    for board in (b2, b3):
        board.sync()
        assert "left it as it is" in board.last_sync, board.last_sync

    # ...and their games take the new roster from the Coordinator's signed
    # broadcast instead, once the newcomer has settled in.
    league.settle(SETTLES_WITHIN)
    for board in (b2, b3, b4):
        assert board.roster_boards() == [1, 2, 3, 4], (
            f"board {board.index:02d} did not adopt the new roster:\n{board.planetary_log()}")
        assert board.rules()["TurnsPerDay"] == TURNS_PER_DAY

    # And the newcomer is reachable from a member, not just from the hub.
    b2.maint()
    to4 = [n for n in b2.outbound() if parse_name(n)[3] == 4]
    assert to4, f"board 02 wrote nothing for board 04: {b2.outbound()}"
    b2.sync()
    mark = b4.log_mark()
    b4.sync()
    b4.maint()
    highest = max(parse_name(n)[2] for n in to4)
    assert b4.ledger().get("HighSeq", {}).get(b2.name, 0) >= highest, b4.log_since(mark)
    assert b4.faults_since(mark) == [], b4.log_since(mark)


def test_a_packet_forged_in_a_members_name_is_refused(ib_league):
    league = ib_league
    league.settle(SETTLES_WITHIN)
    b2, b3 = league.boards[2], league.boards[3]
    # Board 02's own traffic goes first, so all that reaches board 03 below is forged.
    b2.sync()
    b3.sync()
    b3.maint()

    # A game that calls itself board 02 but holds its own key. The roster, the
    # Coordinator's key, the league's rules and board 02's counter (moved well on,
    # so nothing is mistaken for a replay) are copied: the only thing wrong with
    # what it writes is the signature.
    rogue = league.root / "rogue"
    _new_game(league.build.binary, rogue, b2.name)
    for name in ("ibnodes.dat", "coord.pub"):
        shutil.copy(b2.file(name), rogue / name)
    rules = b2.rules()
    if "DataDir" in rules:
        rules["DataDir"] = str(rogue)
    (rogue / "config.json").write_text(json.dumps(rules, indent=2))
    ledger = b2.ledger()
    ledger["OutSeq"] = ledger.get("OutSeq", 0) + 100
    (rogue / "packet-ledger.json").write_text(json.dumps(ledger))
    ib(league.build.binary, rogue, "-maint")
    forged = [p for p in (rogue / "outbound").iterdir()
              if parse_name(p.name) and parse_name(p.name)[3] == 3]
    assert forged, f"the rogue wrote nothing for board 03: {list((rogue / 'outbound').iterdir())}"
    lowest = min(parse_name(p.name)[2] for p in forged)

    # Board 02's client uploads it as board 02's: the hub cannot tell, and does
    # not try. Telling is the board's job.
    for p in forged:
        shutil.move(str(p), b2.data / "outbound" / p.name)
    b2.sync()
    mark = b3.log_mark()
    b3.sync()
    result = b3.maint()
    faults = b3.faults_since(mark)

    assert any(b2.name in line for line in faults), (
        f"board 03 applied a packet signed with another key:\n{b3.log_since(mark)}\n{result.stdout}")
    assert result.returncode != 0, (
        f"a refused packet is a new fault, and the run should exit non-zero:\n{result.stdout}")
    assert b3.ledger().get("HighSeq", {}).get(b2.name, 0) < lowest, (
        "board 03 counted the forged packets as board 02's")


def test_a_league_with_nothing_to_say_is_quiet(ib_league):
    league = ib_league
    league.settle(SETTLES_WITHIN)
    # settle() ends on a sync, so there is nothing left to go up.
    runs = len(league.runs())
    for board in league.boards.values():
        board.sync()
    assert len(league.runs()) == runs, league.runs()


def _clock_before_midnight(lead: int):
    """An IB_CLOCK_OFFSET that puts the game `lead` seconds before the end of
    tomorrow, and the time.monotonic() at which its clock reaches midnight.

    Tomorrow rather than today, so the shift is forwards whatever the hour (IB
    makes a backwards one be asked for twice), and the first -maint under it
    starts a fresh game day of its own. One offset for every process: each one
    reads the real clock plus the same shift, so they pass midnight together,
    as a BBS's door and its maintenance do.
    """
    now = datetime.now()
    end_of_tomorrow = datetime.combine(now.date() + timedelta(days=2), datetime.min.time())
    shift = int((end_of_tomorrow - now).total_seconds()) - lead
    midnight_in = (end_of_tomorrow - now).total_seconds() - shift
    return f"{shift}s", time.monotonic() + midnight_in, (now + timedelta(seconds=shift)).date()


def _ours(board) -> dict:
    return {k: v for k, v in board.turns_left().items() if k in ("Idleton", "Busyburg")}


# Time before the game's midnight for both barons to make realms and play a turn.
LEAD = 90


def test_a_new_day_while_barons_are_playing(ib_league):
    """-maint rolls the day over under two barons in the game on board 02.

    On a BBS, the client's daemon runs -maint on its schedule, whoever is
    playing; the first run after midnight is the one that starts a new day:
    fresh turns for every realm, and any turn left uncommitted abandoned (IB
    #10). A caller can be anywhere in the game when it happens. Here one sits
    at the Entry menu and one is in the middle of a turn, with a turn behind
    each of them, when the game clock passes midnight and -maint runs.
    """
    league = ib_league
    league.settle(SETTLES_WITHIN)
    board = league.boards[2]
    binary = league.build.binary
    clock, midnight, day = _clock_before_midnight(LEAD)
    new_day = (day + timedelta(days=1)).isoformat()

    opened = board.maint(clock)
    assert "Daily maintenance ran" in opened.stdout, opened.stdout + opened.stderr

    idle = Player(binary, board.data, "Idler", clock)
    busy = Player(binary, board.data, "Busy", clock)
    try:
        for player, realm in ((idle, "Idleton"), (busy, "Busyburg")):
            player.create_realm(realm)
            # The menus count the turn in hand as spent; the save, not until it ends.
            assert player.play() == TURNS_PER_DAY - 1, player.tail()
            player.finish_turn()
        idle.stop_playing()  # at the Entry menu
        busy.next_turn()  # in the buy menu, a turn under way
        assert _ours(board) == {"Idleton": TURNS_PER_DAY - 1,
                                      "Busyburg": TURNS_PER_DAY - 1}
        assert time.monotonic() < midnight, (
            f"the barons took longer than LEAD ({LEAD}s) to be in place before midnight")

        time.sleep(max(0.0, midnight - time.monotonic()) + 2)
        rolled = board.maint(clock)
        output = rolled.stdout + rolled.stderr
        assert "advanced 1 day(s)" in rolled.stdout, output
        assert rolled.returncode == 0, output
        assert board.world()["LastMaintDate"] == new_day, output
        # Every realm has the new day's turns, the one mid-turn included.
        assert _ours(board) == {"Idleton": TURNS_PER_DAY, "Busyburg": TURNS_PER_DAY}

        # The turn in hand finishes, and is charged to the new day, once.
        busy.finish_turn()
        busy.stop_playing()
        # The baron at the menu plays on into the new day.
        assert idle.play() == TURNS_PER_DAY - 1, idle.tail()
        idle.finish_turn()
        idle.stop_playing()
        statuses = {"Idler": idle.quit(), "Busy": busy.quit()}
    finally:
        idle.kill()
        busy.kill()

    # A failed save ends a session with an error; both left as callers do.
    assert statuses == {"Idler": 0, "Busy": 0}, f"{idle.tail()}\n---\n{busy.tail()}"
    world = board.world()
    assert world["LastMaintDate"] == new_day
    assert _ours(board) == {"Idleton": TURNS_PER_DAY - 1,
                                  "Busyburg": TURNS_PER_DAY - 1}, busy.tail()
