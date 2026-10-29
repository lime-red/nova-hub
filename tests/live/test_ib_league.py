"""Immortal Barons, end to end: real boards, real clients, the hub as a server.

League 900I: the hub's game is node 1, the League Coordinator, and boards 02 and
03 join it the way a sysop's board does. Each scenario runs once per IB build
rig.ib_builds chose -- the latest release and trunk -- so a change upstream that
the hub or the client has to answer shows up before it is released. See
rig/ib_league.py for what is real here and what the test drives itself.

What each one proves, and what a regression would look like if it failed:

- a board that has nothing but a board key gets the Coordinator's key and the
  roster from its first sync, and ends up playing the Coordinator's rules
  (broken: the board refuses "unsigned" orders and plays its own defaults);
- a packet from one member to another goes to that member alone, unchanged
  (broken: the hub's game is fed mail that is not its own, or it never arrives);
- a Coordinator broadcast reaches every member once, and not the hub;
- a board joining later is adopted by every board through the signed roster,
  though the client never replaces a roster the game already has;
- a packet signed with another key is refused by the board it reaches, however
  faithfully the hub carried it;
- with nothing to say, the league is quiet: a sync with nothing in it starts no
  run (the eqx-hl loop of 2026-10-29 was the opposite).
"""
import json
import shutil

from rig.ib_league import TURNS_PER_DAY, _new_game, parse_name

# What the Coordinator's run says about a board that has not adopted the
# league's rules yet: its packets are held, and the run exits 1.
HELD = "are being held: the rules it is playing by are not the league's"


def _by_name(paths):
    return {p.name.upper(): p for p in paths}


def test_boards_join_and_play_the_league_rules(ib_league):
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

    # 2. Their opening packets go up, and the hub's game answers them.
    for board in boards:
        board.maint()
        assert board.outbound(), f"board {board.index:02d} wrote nothing on its first -maint"
        board.sync()
        assert board.outbound() == [], f"board {board.index:02d} left {board.outbound()}"
    # A newcomer is not yet playing the league's rules, so the Coordinator holds
    # what it sends until it is, and its run exits 1 to say so: the hub reports
    # that run failed, once per board that joins. Anything else is a real fault.
    runs = league.runs()
    assert runs, f"the hub never ran its game:\n{league.hub_log()[-3000:]}"
    for run in runs:
        assert run["status"] == "completed" or HELD in (run["error_message"] or ""), runs

    # 3. Each takes the answer, and plays the Coordinator's rules from then on.
    for board in boards:
        board.sync()
        result = board.maint()
        log = board.planetary_log()
        assert board.inbound() == [], f"board {board.index:02d} left {board.inbound()}"
        assert "verified orders" in log, f"board {board.index:02d}:\n{log}\n{result.stdout}"
        assert "refused" not in log.lower(), log
        assert board.rules()["TurnsPerDay"] == TURNS_PER_DAY

    # 4. Once they play by the rules, the Coordinator takes their mail cleanly.
    seen = len(runs)
    for board in boards:
        board.sync()
    later = league.runs()[seen:]
    assert later and all(r["status"] == "completed" for r in later), later

    # A Coordinator broadcast is a row per member, each fetched once; never the hub.
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
    league.join_all()
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

    b3.sync()
    result = b3.maint()
    assert b3.inbound() == [], f"board 03 left {b3.inbound()}:\n{result.stdout}"
    highest = max(parse_name(n)[2] for n in to3)
    seen = b3.ledger().get("HighSeq", {}).get(b2.name, 0)
    assert seen >= highest, (
        f"board 03 has applied board 02's packets up to {seen}, not {highest}:\n"
        f"{b3.planetary_log()}")
    assert "refused" not in b3.planetary_log().lower(), b3.planetary_log()


def test_a_board_joining_later_is_adopted_by_every_board(ib_league):
    league = ib_league
    league.join_all()
    b2, b3 = league.boards[2], league.boards[3]

    b4 = league.add_board(4)
    assert league.boards[2].roster_boards() == [1, 2, 3]

    # The newcomer has no roster, so its client installs the hub's.
    b4.sync()
    assert b4.roster_boards() == [1, 2, 3, 4], b4.last_sync
    assert b4.file("coord.pub").read_bytes() == league.game_file("coord.pub").read_bytes()
    b4.maint()
    b4.sync()

    # The others already have one, so their clients leave it, and the game takes
    # the new one from the Coordinator's signed broadcast instead.
    for board in (b2, b3):
        board.sync()
        assert "left it as it is" in board.last_sync, board.last_sync
        board.maint()
        assert board.roster_boards() == [1, 2, 3, 4], (
            f"board {board.index:02d} did not adopt the new roster:\n{board.planetary_log()}")

    # And the newcomer is reachable from a member, not just from the hub.
    b2.maint()
    to4 = [n for n in b2.outbound() if parse_name(n)[3] == 4]
    assert to4, f"board 02 wrote nothing for board 04: {b2.outbound()}"
    b2.sync()
    b4.sync()
    b4.maint()
    highest = max(parse_name(n)[2] for n in to4)
    assert b4.ledger().get("HighSeq", {}).get(b2.name, 0) >= highest, b4.planetary_log()


def test_a_packet_forged_in_a_members_name_is_refused(ib_league):
    league = ib_league
    league.join_all()
    b2, b3 = league.boards[2], league.boards[3]

    # A game that calls itself board 02 but holds its own key: the roster, the
    # Coordinator's key and board 02's counter are copied, so the only thing
    # wrong with what it writes is the signature.
    rogue = league.root / "rogue"
    _new_game(league.build.binary, rogue, b2.name)
    for name in ("ibnodes.dat", "coord.pub"):
        shutil.copy(b2.file(name), rogue / name)
    ledger = b2.ledger()
    ledger["OutSeq"] = ledger.get("OutSeq", 0) + 100
    (rogue / "packet-ledger.json").write_text(json.dumps(ledger))
    from rig.ib_league import ib
    ib(league.build.binary, rogue, "-maint")
    forged = [p for p in (rogue / "outbound").iterdir() if parse_name(p.name)
              and parse_name(p.name)[3] == 3]
    assert forged, f"the rogue wrote nothing for board 03: {list((rogue / 'outbound').iterdir())}"

    # Board 02's client uploads it as board 02's: the hub cannot tell, and does
    # not try. It is the board's job.
    for p in forged:
        shutil.move(str(p), b2.data / "outbound" / p.name)
    b2.sync()
    b3.sync()
    before = b3.ledger().get("HighSeq", {}).get(b2.name, 0)
    result = b3.maint()
    log = b3.planetary_log()

    assert result.returncode != 0, (
        f"a refused packet is a new fault, and the run should exit non-zero:\n{result.stdout}")
    assert "refused" in log.lower() and b2.name in log, log
    assert b3.ledger().get("HighSeq", {}).get(b2.name, 0) == before, (
        "board 03 counted the forged packets as board 02's")


def test_a_league_with_nothing_to_say_is_quiet(ib_league):
    league = ib_league
    league.join_all()
    # join_all ends on a -maint, whose packets are still to go up.
    for board in league.boards.values():
        board.sync()
    runs = len(league.runs())
    for board in league.boards.values():
        board.sync()
    assert len(league.runs()) == runs, league.runs()
