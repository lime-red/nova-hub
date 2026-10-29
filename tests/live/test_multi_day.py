"""Scenario 8 - game time, and what it does to sequence numbers.

`REDATE.COM` hooks int 21h AH=2Ah (GET-DATE), which is exactly where Turbo
Pascal's GetDate goes, so the game sees a faked date and nothing else does.
`node_rig.run(..., date="YYYYMMDD")` installs it, runs the command, and removes
it again.

The reason this scenario is worth more than "days pass": it is the only way to
see what a real game does to sequence numbers over time, and what it does is not
what the hub assumes.

Two things about game time that every test here has to respect, both learned the
hard way on 2026-10-27:

1. Dates only move forward. The game refuses to run a date before its own last
   run, and a fresh RESET stamps the fixture with today, so no hardcoded
   constant survives contact with the calendar. These dates are derived.

2. WHICH game day emits a packet is governed by the absolute date, not by the
   day's index in the run. The same calendar dates emit regardless of where in a
   run they fall, measured across windows starting +1, +2, +3, +20, +36 and +67
   days from the reset. So "three consecutive game days" is not a repeatable
   quantity of traffic, and anything that needs N packets has to play until it
   has N packets rather than assume a fixed number of days will supply them.
   That is what _play_until is for.

   The schedule itself is now known, and it is simple: BRE emits when
   `ordinal % 5 == 1` or the day is a Saturday, FE every fourth day. See
   test_emission_schedule.py. That does not rescue a fixed-length window here --
   it is a fixed number of PACKETS these tests need -- but it does mean a window
   DERIVED from a phase is repeatable, which is how the schedule itself is
   pinned.
"""
from datetime import date, timedelta

import pytest

from backend.models.database import Packet
from rig import node as node_rig
from rig.layout import HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]
HUB = NODES[HUB_INDEX]

# Game dates have to be strictly ahead of the game's own last run -- BRE refuses
# to run if the date precedes it -- and a fresh RESET stamps the fixture with
# today's date. So hardcoded constants work only until the wall clock passes
# them. These were "20261001".."20261006" and on 2026-10-26 all nine tests in
# this file failed with "nothing reached the hub": the game silently declined to
# run 25 days backwards and emitted no packets, which looks like a routing bug
# and is not one. Derive them from today instead, starting the day after the
# reset so DAY1 is itself an unplayed game day.
#
# Only three are named. The tests that need a known quantity of traffic rather
# than a known number of days use _play_until, for the reason in point 2 of the
# module docstring.
_FIRST_GAME_DAY = date.today() + timedelta(days=1)
DAY1, DAY2, DAY3 = (
    (_FIRST_GAME_DAY + timedelta(days=n)).strftime("%Y%m%d") for n in range(3)
)

# Node 02's route to the hub, and the hub's to node 01. A packet filename is
# <league><game><source><dest>.<seq>, so these select "sent by that node".
ROUTE_02 = "900b0201."
ROUTE_HUB = "900b0102."


# Daily maintenance leaves several marks, and this looks for ANY of them.
#
# One literal is not safe. The transcript is a terminal capture, so dosemu's vga
# repaint can write a line in fragments via cursor positioning, and the string
# never appears in the byte stream at all. Observed on 2026-10-27, game day
# 20261031:
#
#     ^[[6;15Hly Maintenance
#
# -- "Running Dai" was already on screen from an earlier repaint, so only the
# tail was written. Measured over 87 consecutive game-day transcripts from one
# run: 1 lacked "Running Daily Maintenance" and all 87 carried the other three.
# That is ~1.1% per game day, which was tolerable when this ran six times and is
# a coin flip over the ~70 game days these scenarios now play.
#
# Every marker here is specific to DAILY maintenance, which matters for
# test_daily_maintenance_fires_once_per_game_day: re-running the same game day
# must read as "did not fire". Checked against a new-day and a repeat-day
# transcript -- all four appear once on a new day and not at all on a repeat.
# "Planetary Maintenance Complete" is NOT usable: the PLANETARY command prints
# it either way. Nor is the bare fragment "ly Maintenance", which turns up on a
# repeat day too.
#
# "Maintainence" is the game's own spelling, not a typo here.
_MAINTENANCE_MARKERS = (
    "Running Daily Maintenance",
    "Daily Maintainence Complete",
    "Checking for Dead Empires",
    "Updating Daily News Bulletin",
)


def _maintenance_fired(log_path) -> bool:
    text = log_path.read_bytes().decode("latin-1")
    return any(marker in text for marker in _MAINTENANCE_MARKERS)


def _play(node, prefix, days, tag):
    """Play `days` consecutive game days. Returns a list per day of filenames.

    Each day's packets are collected and removed, so a day's entry is what that
    day produced rather than everything still sitting in OUTBOUND.

    Asserts maintenance on every day, which is the control: an empty day has to
    mean "the game ran and had nothing to send", not "the game did not run".
    """
    out = []
    for n in range(days):
        day = (_FIRST_GAME_DAY + timedelta(days=n)).strftime("%Y%m%d")
        log = node_rig.run(LEAGUE, node, "PLANETARY", tag=f"{tag}{n}", date=day)
        assert _maintenance_fired(log), (
            f"game day {n + 1} ({day}) did not run maintenance, so a silent day "
            f"here would not mean anything"
        )
        out.append([
            name for name, _ in node_rig.take_outbound(LEAGUE, node)
            if name.lower().startswith(prefix)
        ])
    return out


def _play_until(node, prefix, want, tag, max_days=15, enough=None):
    """Play game days until `want` packets have been emitted.

    Or, given `enough`, until `enough(packets)` is true as well, for a test that
    needs traffic of a particular shape and not just a quantity of it.

    Bounded-length rather than fixed-length ON PURPOSE. Which game day emits is
    a property of the calendar the faked dates land on, not of the game day's
    index -- see the module docstring -- so "three consecutive days" buys an
    unknown number of packets and "until three packets" buys a known one.

    Returns (days, packets): one entry per day played, and the flat list.
    """
    days, packets = [], []
    while len(packets) < want or (enough and not enough(packets)):
        if len(days) >= max_days:
            raise AssertionError(
                f"only {len(packets)} packet(s) in {max_days} game days "
                f"({packets}). An idle league is sparse but it is not silent; "
                f"if this is now reliably true the game's traffic has changed."
            )
        n = len(days)
        day = (_FIRST_GAME_DAY + timedelta(days=n)).strftime("%Y%m%d")
        log = node_rig.run(LEAGUE, node, "PLANETARY", tag=f"{tag}{n}", date=day)
        assert _maintenance_fired(log), (
            f"game day {n + 1} ({day}) did not run maintenance"
        )
        today = [
            (name, payload) for name, payload in node_rig.take_outbound(LEAGUE, node)
            if name.lower().startswith(prefix)
        ]
        days.append(today)
        packets.extend(today)
    return days, packets


def _numbers(packets):
    return [int(name.rsplit(".", 1)[1]) for name, _ in packets]


def _play_busy(node, prefix, days, forced, tag):
    """Play `days` game days, asking for `forced` extra packets on each.

    A second PLANETARY on the same game day emits nothing, so same-day traffic
    has to be asked for: REQUEST queues recon traffic and OUTBOUND packages it.
    That is node_rig.force_packet, and it MUST be given the game day. Without
    it the forced runs execute on the real date, which is behind the date this
    install has already been advanced to, and BRE answers a date regression by
    printing nothing and exiting -- no packet, no error, a transcript of pure
    dosemu banner. The first version of this scenario did exactly that and
    measured eight game days of "load" that was really eight idle days.

    Returns [(game_day_index, filename)] in emission order, 1-based days.
    """
    out = []
    for n in range(days):
        day = (_FIRST_GAME_DAY + timedelta(days=n)).strftime("%Y%m%d")
        log = node_rig.run(LEAGUE, node, "PLANETARY", tag=f"{tag}{n}", date=day)
        assert _maintenance_fired(log), (
            f"game day {n + 1} ({day}) did not run maintenance"
        )
        today = [name for name, _ in node_rig.take_outbound(LEAGUE, node)
                 if name.lower().startswith(prefix)]
        for k in range(forced):
            node_rig.force_packet(LEAGUE, node, "REQUEST",
                                  tag=f"{tag}{n}f{k}", date=day)
            today += [name for name, _ in node_rig.take_outbound(LEAGUE, node)
                      if name.lower().startswith(prefix)]
        assert len(today) >= forced, (
            f"game day {n + 1} ({day}) produced {len(today)} packet(s) having "
            f"been asked for {forced} on top of maintenance. Forced traffic "
            f"that silently produces nothing is the date-regression failure "
            f"described above, so this is not a quiet day."
        )
        out.extend((n + 1, name) for name in today)
    return out


async def test_daily_maintenance_fires_once_per_game_day(pristine):
    """The control that makes every other date-based result meaningful.

    If maintenance fired on every invocation regardless, a faked date would
    prove nothing -- the game would just be doing the same thing twice. It does
    not: a repeat of a date already played is skipped.
    """
    pristine("900B")

    first = node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="d2a", date=DAY2)
    assert _maintenance_fired(first), "maintenance did not run on a new game day"

    again = node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="d2b", date=DAY2)
    assert not _maintenance_fired(again), (
        "maintenance ran twice for the same game day - the game is not tracking "
        "the faked date, so no date-based assertion here means anything"
    )


async def test_sequence_numbers_are_not_dense_across_days(pristine):
    """The finding this scenario exists for.

    The hub's gap detector assumes a route's sequence numbers are dense: any
    number it has not seen is a lost packet. A real game does not honour that.
    Playing consecutive days on one install skips numbers with nothing lost and
    nothing wrong -- the game consumes sequence numbers for traffic it decides
    not to send.

    This test does not assert a particular pattern of skips, because the pattern
    is not stable. It asserts the property that matters: the numbers advance by
    MORE than one per emitted packet, so density cannot be relied on.

    What CAN tell a quiet day from a lost packet is the game's own record of
    what it sent: the `/DETAILED` transcript, already enabled on production,
    which prints `Outbound mail for <name> - Node N created.` only when BRE
    actually had something to send. Not ROUTEINFO -- that reports routing paths
    (routes.lst, routes.bad), not packet history.
    """
    pristine("900B")
    _, packets = _play_until(NODE02, ROUTE_02, 3, "m")
    seen = _numbers(packets)

    assert len(seen) >= 2, f"not enough packets: {seen}"
    assert seen == sorted(seen), f"sequence went backwards: {seen}"
    span = seen[-1] - seen[0]
    assert span > len(seen) - 1, (
        f"sequence numbers {seen} were dense across game days. If this is now "
        f"reliably true, the gap detector's assumption holds and "
        f"docs/ROLLOUT_PLAN.md card B-4 should be revisited"
    )


async def test_an_uncollected_packet_is_rewritten_in_place(pristine):
    """Running again while a packet is still pending rewrites it, same number.

    The sequence number is not re-allocated: the pending file keeps its name and
    gains the new traffic. Measured across three runs on one game day -- 176,
    232, then 288 bytes under the same filename, a different hash each time.

    This matters to the hub for a reason that has nothing to do with sequence
    numbers: a packet sitting in a game OUTBOUND folder is not a finished
    artefact. Its *contents* change under a stable name until something collects
    it. Anything that reads a packet, leaves it there, and trusts it later is
    reading a file that may since have grown.
    """
    pristine("900B")
    hub_node = HUB

    node_rig.run(LEAGUE, hub_node, "PLANETARY", tag="ip1", date=DAY1)
    first = node_rig.outbound(LEAGUE, hub_node)
    assert first, "no packet to rewrite"
    name = first[0]
    path = LEAGUE.install_path(hub_node) / "OUTBOUND" / name
    before = path.read_bytes()

    # Same game day, more traffic, packet deliberately left in place.
    node_rig.run(LEAGUE, hub_node, "REQUEST", tag="ip2", date=DAY1)
    node_rig.run(LEAGUE, hub_node, "OUTBOUND", tag="ip3", date=DAY1)

    after_names = node_rig.outbound(LEAGUE, hub_node)
    assert name in after_names, (
        f"the pending packet was renamed: {first} became {after_names}"
    )
    after = path.read_bytes()
    assert after != before, "the pending packet was not rewritten at all"
    assert len(after) > len(before), (
        f"expected the pending packet to gain traffic, {len(before)} -> {len(after)}"
    )


async def test_a_game_day_consumes_a_sequence_number_whether_it_emits_or_not(
    pristine,
):
    """Where production's gap alerts come from, stated exactly.

    This used to assert "one file per game day, numbering advances by two",
    measured as .002, .004, .006 across three consecutive days. That holds only
    when every day emits, and most days do not -- so the test was really
    asserting a property of the three calendar dates it happened to use.

    The actual law, measured over 60+ game days across seven different date
    windows with no exceptions:

        every game day consumes ONE sequence number;
        a day that emits consumes TWO and writes its packet at the second.

    So after D game days of which E emitted, the highest number issued is
    D + E. Worked through one 20-day window: days 1, 5, 6, 11, 12, 16 and 19
    emitted, giving .002 .007 .009 .015 .017 .022 .026 -- and 19 + 7 = 26.

    That is what the gap detector is up against. The unused numbers are not
    lost packets; they are quiet days.
    """
    pristine("900B")
    days, packets = _play_until(HUB, ROUTE_HUB, 3, "t")
    numbers = _numbers(packets)

    assert numbers == sorted(numbers), f"sequence went backwards: {numbers}"

    # The last day played is an emitting one -- _play_until stops on the packet
    # that completes the count -- so D + E is exactly the number it wrote.
    assert numbers[-1] == len(days) + len(packets), (
        f"the numbering law changed: {len(days)} game days and {len(packets)} "
        f"packets {numbers} should put the last packet at "
        f"{len(days) + len(packets)}, not {numbers[-1]}. Either a game day no "
        f"longer consumes a number, or an emitting day no longer consumes two."
    )

    steps = [b - a for a, b in zip(numbers, numbers[1:])]
    assert all(s > 1 for s in steps), (
        f"sequence numbers {numbers} advanced densely. If that is now reliably "
        f"true, the gap detector's assumption holds and ROLLOUT_PLAN card B-4 "
        f"should be revisited"
    )


async def test_the_gap_detector_stays_quiet_when_nothing_was_lost(pristine, hub):
    """The test that reproduced production's 705 false alerts, now inverted.

    Every packet this node produced is delivered to the hub. None is lost, none
    is delayed, nothing is out of order -- but the game skipped sequence numbers
    of its own accord on the days it stayed quiet. The detector used to alert on
    every one of those, which is the entire content of production's alert table.

    ROLLOUT_PLAN card B-4.

    Why this plays sixty-odd game days instead of three. The route has to carry
    at least MIN_PACKETS_FOR_DENSITY packets before the density gate will judge
    it at all. Below that, numbers_densely() returns True no matter how sparse
    the numbering actually looks, and the route stays noisy -- deliberately, so
    that a route with six packets cannot be excused by an accident of its first
    week. Measured on this very route:

        3 packets  -> numbers [2, 7, 9], density 0.286,
                      numbers_densely() True (sample too small), 5 alerts
        22 packets -> density 0.239, numbers_densely() False, 0 alerts

    So three game days was asking the detector to exempt a route on three
    packets of evidence, which is exactly what it is built to refuse. Give it the
    evidence it asks for and it goes quiet. The companion test below pins the
    other half, so that nobody makes this one pass by lowering the constant.
    """
    from backend.services.sequence_validator import (
        MIN_PACKETS_FOR_DENSITY,
        SequenceValidator,
    )

    pristine("900B")
    hub.seed(["900B"])

    # ~35 game days per 11 packets on an idle league, so 20 packets needs about
    # 60. max_days is generous rather than tight: running out is a real finding
    # and _play_until says so.
    _, packets = _play_until(
        NODE02, ROUTE_02, MIN_PACKETS_FOR_DENSITY, "g", max_days=110
    )
    delivered = []
    for name, payload in packets:
        hub.deliver(name, payload)
        delivered.append(name)

    assert len(delivered) >= MIN_PACKETS_FOR_DENSITY, (
        f"need {MIN_PACKETS_FOR_DENSITY} packets before the density gate will "
        f"judge this route, got {len(delivered)}: {delivered}"
    )

    alerts = SequenceValidator(hub.db).check_sequences()
    assert alerts == [], (
        f"delivered every packet the game produced ({delivered}) and the gap "
        f"detector raised {len(alerts)} alert(s): "
        + "; ".join(a.description for a in alerts)
    )


async def test_a_route_below_the_minimum_sample_is_left_noisy(pristine, hub):
    """The other half of the trade, asserted so it cannot be tuned away quietly.

    A young route is NOT exempted. A handful of packets from an idle league,
    say [2, 7, 9] -- a real density of 0.286, far below DENSITY_THRESHOLD --
    and the detector still alerts on every unused number, because
    MIN_PACKETS_FOR_DENSITY has not been reached and it will not excuse a route
    on that little evidence.

    It has to be a route whose stride is unproven, which is a property of the
    calendar and not of the hub, so the test plays until it has one. Every game
    day uses one sequence number and every packet one more, so a route's deltas
    are "days since the last packet, plus one". Three packets on three
    consecutive days read [2, 4, 6]: route_stride() learns a stride of 2 from
    that, as designed (MIN_DELTAS_FOR_STRIDE), nothing is a gap, and the route
    is quiet for a reason that has nothing to do with the density gate. That is
    what DAY1 = 2026-10-30 dealt, and this test failed on it.

    This is the deliberate cost of the gate above, and it is the knob someone
    would reach for first if these alerts became annoying. Lowering
    MIN_PACKETS_FOR_DENSITY would quieten a genuinely lost packet on any route
    in its first week, which is the failure the 705 alerts were hiding. So the
    noise is asserted, not tolerated: if it ever goes away, that was a decision
    and this test should be the thing that makes someone say so out loud.
    """
    from backend.services.sequence_validator import (
        MIN_PACKETS_FOR_DENSITY,
        SequenceValidator,
    )

    pristine("900B")
    hub.seed(["900B"])

    validator = SequenceValidator(hub.db)

    # Up to 7 packets over 16 game days, measured from DAY1 = 2026-10-30.
    _, packets = _play_until(
        NODE02, ROUTE_02, 3, "n", max_days=40,
        enough=lambda p: validator.route_stride(_numbers(p)) == 1,
    )
    for name, payload in packets:
        hub.deliver(name, payload)

    numbers = _numbers(packets)
    assert len(numbers) < MIN_PACKETS_FOR_DENSITY, (
        f"this test is about the below-minimum case and the route now has "
        f"{len(numbers)} packets: it took that many for the stride to stop "
        f"looking regular ({numbers})"
    )
    assert validator.route_stride(numbers) == 1, numbers

    assert validator.numbers_densely(numbers) is True, (
        f"a {len(numbers)}-packet route should be treated as dense regardless "
        f"of its apparent density ({numbers}), so that it stays noisy"
    )

    alerts = validator.check_sequences()
    assert alerts, (
        f"a route below MIN_PACKETS_FOR_DENSITY went quiet on {numbers}. If "
        f"that is now intended, the cost is that a genuine loss in a route's "
        f"first week no longer alerts either -- say so deliberately rather than "
        f"deleting this test."
    )


async def test_the_law_holds_when_a_node_emits_several_times_a_day(pristine):
    """The same law, in the regime production actually runs in.

    test_a_game_day_consumes_a_sequence_number_whether_it_emits_or_not measures
    an idle league, where at most one packet goes out a day. That cannot tell
    the law's two terms apart: with one packet per emitting day, "one number per
    rollover plus one per packet" and a dozen other rules all fit. The other
    sample this project had -- grind.sh forcing 1,100 packets inside a single
    game day -- cannot separate them either, from the other side.

    A node that plays several days AND emits several times a day can, and it is
    the shape every production node has. Stated per packet:

        the k-th packet's number == (game days elapsed, incl. its own) + k

    Measured here on four days at three packets a day, and off-rig against the
    29-number and 37-number samples in tests/test_sequence_validator.py
    (TestWhatABusyNodeLooksLike), which were recorded at 3 and 10 packets a day
    over 12 and 4 game days. Exact in all three, no exceptions.

    Those unit tests are where the consequence for the gap detector lives: at
    about nine packets a day the route crosses DENSITY_THRESHOLD, stops being
    exempted, and the daily rollover burn starts reading as one lost packet per
    day. This test is the part only a real game can answer -- that the law is
    the game's, and still holds when the node is busy.
    """
    pristine("900B")

    seen = _play_busy(NODE02, ROUTE_02, days=4, forced=2, tag="bz")

    assert len(seen) >= 8, (
        f"four days at two forced packets each should produce at least eight; "
        f"got {len(seen)}: {seen}"
    )
    for k, (day, name) in enumerate(seen, start=1):
        number = int(name.rsplit(".", 1)[1])
        assert number == day + k, (
            f"packet {k} ({name}) went out on game day {day}, so the law "
            f"predicts {day + k} and the game issued {number}. Full run: {seen}"
        )


async def test_an_idle_league_emits_sparsely_but_never_stops(pristine):
    """An idle league is sparse. It does NOT go quiet, which is what this said.

    The claim here was "six game days, three packets, days four onward emit
    nothing at all", asserted as the exact pattern [1, 1, 1, 0, 0, 0]. That
    pattern was real but it was not a property of the game: it was a property of
    the six calendar dates the test hardcoded. Playing twenty consecutive game
    days instead gives

        [1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 1, 0]

    -- seven packets, still arriving on day 19, with maintenance firing on all
    twenty. Emission follows the absolute date, not the game day index: the same
    calendar dates emit no matter which day of the run they land on, confirmed
    across windows starting +1, +2, +3, +20, +36 and +67 days from the fixture's
    reset. Oct 31 and Nov 1 emitted in every window that contained them, at
    indices 4/5, 3/4 and 2/3 respectively.

    Two consequences. The first is that no derived date sequence can restore the
    old assertion, so this asserts the shape instead of the schedule. The second
    is the caution the old version was right about for the wrong reason: silence
    from a node is not evidence of a fault, and a health check built on "a
    packet a day" would cry wolf -- not because an idle node stops, but because
    it never emitted daily in the first place.
    """
    pristine("900B")

    produced = _play(NODE02, ROUTE_02, 8, "i")
    counts = [len(day) for day in produced]

    assert counts[0] == 1, (
        f"the first game day after a restore should emit the opening packet, "
        f"got {counts}"
    )
    assert all(c <= 1 for c in counts), (
        f"a game day emitted more than one packet on this route: {produced}"
    )
    assert 0 in counts[1:], (
        f"every game day emitted. An idle league is not supposed to be chatty: "
        f"{produced}"
    )
    assert 2 <= sum(counts) < len(counts), (
        f"expected sparse-but-present traffic over {len(counts)} game days, got "
        f"{sum(counts)} packet(s): {produced}. Nothing at all would mean the "
        f"game stopped running; one per day would mean it now emits densely, "
        f"and either is worth looking at rather than asserting around."
    )


def _plant_a_stale_alert(hub, tag):
    """Deliver a gapless route, then plant an alert at a number inside it.

    This is the shape of the rows production carries: an alert at a sequence
    number the game never handed out, on a route where nothing is actually
    missing. Auto-resolve should retire it.

    It plays until exactly ONE packet rather than for a fixed three days, and
    that is load-bearing. Both tests that use this used to play DAY1..DAY3 and
    plant at `first_packet + 1`, which made the result depend on how many
    packets those three dates happened to emit:

      - one packet  -> find_gaps() reports nothing, the planted number is not a
                       gap, auto-resolve retires it as never issued. Passes.
      - two packets -> there is now a real gap between them, the planted number
                       falls INSIDE it, the detector still considers it missing
                       and declines to resolve it. Fails.

    Which of those you got depended on the calendar. Both tests passed on
    2026-10-27 (DAY1-3 = Oct 28-30, one packet) and both failed on 2026-10-28
    (DAY1-3 = Oct 29-31, two packets, because Oct 31 is a Saturday and BRE emits
    every Saturday -- see test_emission_schedule.py). Nothing about the hub
    changed in between; it was the date. One packet makes the precondition
    deterministic, and it is asserted below so that it cannot drift back.

    Returns the planted SequenceAlert, unsaved refreshes included.
    """
    from backend.models.database import SequenceAlert
    from backend.services.sequence_validator import SequenceValidator

    _, packets = _play_until(NODE02, ROUTE_02, 1, tag)
    for name, payload in packets:
        hub.deliver(name, payload)

    # source/dest are stored as the two-character hex text from the filename.
    rows = hub.db.query(Packet).filter(
        Packet.source_bbs_index == "02", Packet.dest_bbs_index == "01"
    ).order_by(Packet.sequence_number).all()
    assert len(rows) == 1, (
        f"this needs a route with exactly one packet, so that nothing on it is "
        f"genuinely missing; got {[r.sequence_number for r in rows]}"
    )

    numbers = [r.sequence_number for r in rows]
    assert SequenceValidator(hub.db).find_gaps(numbers) == [], (
        f"precondition failed: the detector already considers something missing "
        f"on this route ({numbers}), so an alert planted here would be a real "
        f"gap rather than a stale one and auto-resolve is right to keep it"
    )

    alert = SequenceAlert(
        league_id=rows[0].league_id,
        source_bbs_index=rows[0].source_bbs_index,
        dest_bbs_index=rows[0].dest_bbs_index,
        expected_sequence=rows[0].sequence_number + 1,
        received_sequence=rows[0].sequence_number + 2,
        gap_size=1,
        description="planted: the shape of the rows in production",
    )
    hub.db.add(alert)
    hub.db.commit()
    return alert


async def test_a_stale_false_alert_resolves_itself(pristine, hub):
    """Production carries 705 of these. Nobody is going to close them by hand.

    Plant an alert at a number the games never issue -- exactly the shape of the
    rows sitting in production -- and confirm the next auto-resolve pass retires
    it with a note saying why, rather than leaving it for an operator to tell
    apart from a real one.
    """
    from backend.services.sequence_validator import SequenceValidator

    pristine("900B")
    hub.seed(["900B"])

    stale = _plant_a_stale_alert(hub, "s")

    assert SequenceValidator(hub.db).auto_resolve_alerts() == 1
    hub.db.refresh(stale)
    assert stale.is_resolved
    assert "never issued" in stale.resolution_note


async def test_a_processing_run_resolves_stale_alerts_by_itself(pristine, hub):
    """auto_resolve_alerts() had no caller anywhere until 2026-09-12.

    That is why production accumulated 213 alerts for packets that had arrived
    months earlier: nothing ever asked. Resolving is now part of a processing
    run, so this asserts the wiring and not just the function.
    """
    pristine("900B")
    hub.seed(["900B"])

    stale = _plant_a_stale_alert(hub, "w")

    await hub.process()

    hub.db.refresh(stale)
    assert stale.is_resolved, (
        "a processing run did not resolve a stale alert -- "
        "auto_resolve_alerts() is unwired again"
    )


async def test_a_multi_day_round_trip_still_processes(pristine, hub):
    """Whatever the numbering does, the packets themselves must still flow.

    The point of separating this from the sequence assertions: a false alert is
    noise, but silently failing to process real multi-day traffic would be an
    outage.
    """
    pristine("900B")
    hub.seed(["900B"])

    for tag, date in (("r1", DAY1), ("r2", DAY2)):
        node_rig.run(LEAGUE, NODE02, "PLANETARY", tag=tag, date=date)
        for name, payload in node_rig.take_outbound(LEAGUE, NODE02):
            if name.lower().startswith("900b0201."):
                hub.deliver(name, payload)

    queued = hub.spool("inbound")
    assert queued, "nothing reached the hub across two game days"

    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_unconsumed == 0
    assert hub.game_inbound("900B") == []
    assert hub.transcript(run).ingested_from(2)
    assert sorted(hub.spool("processed")) == sorted(queued)
