"""Falcon's Eye: the game the rig had no coverage of at all.

Every other scenario here is Barren Realms Elite. FE matters more than that split
suggests:

  - R-3 -- the silently-unreadable inbound path -- bit **fe_015** in production,
    not a BRE league.
  - FE runs on a roughly four-day cadence in production, so a regression in it
    can sit unnoticed for days. BRE runs hourly and announces itself.
  - The hub carries a separate `[dosemu.<n>.fe]` config block, including the
    `completion_marker` that the R-1 work now depends on. Nothing proved FE
    actually prints that line.

The two games turn out to be close relatives -- same 7-line CRLF BBS.CFG, same
"1 HOST 2 3 4" nodes format, same PLANETARY /DETAILED, same completion marker --
so this file is deliberately not a copy of every BRE scenario. It covers the
end-to-end round trip, the marker, the R-3 failure in the game where it really
happened, and the one genuinely new case: two *different games* in one batch.

One real difference is pinned below: FE does not lay down its world at RESET.
"""
import pytest

from rig import node as node_rig
from rig.layout import COMPLETION_MARKER, HUB_INDEX, LEAGUES, NODES

pytestmark = pytest.mark.asyncio

FE = LEAGUES["900F"]
BRE = LEAGUES["900B"]
NODE02 = NODES[2]
HUB = NODES[HUB_INDEX]


async def test_packet_makes_a_full_round_trip(pristine, hub):
    """Scenario 1, in Falcon's Eye."""
    pristine("900F")
    hub.seed(["900F"])

    node_rig.run(FE, NODE02, "PLANETARY", tag="fe1")
    produced = node_rig.take_outbound(FE, NODE02)
    names = [n for n, _ in produced]
    assert "900f0201.001" in names, (
        f"node02 produced no FE packet for the hub; outbound was {names}"
    )

    for name, payload in produced:
        if name.lower().startswith("900f"):
            packet = hub.deliver(name, payload)
    assert hub.spool("inbound") == ["900f0201.001"]

    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_processed == 1
    assert run.packets_unconsumed == 0, (
        "FE was handed a packet it never ingested - check BBS.CFG line 4"
    )

    assert hub.game_inbound("900F") == [], (
        "the packet is still sitting in FE's inbound folder"
    )
    transcript = hub.transcript(run)
    assert transcript.completed
    assert transcript.ingested_from(2), (
        f"no DeCompress line from node 2; items were {transcript.items}"
    )

    assert hub.spool("processed") == ["900f0201.001"]
    assert packet.processed_at is not None
    assert "900F0102.001" in hub.spool("outbound"), (
        f"no FE reply queued for node02; hub outbound was {hub.spool('outbound')}"
    )


async def test_fe_prints_the_completion_marker(pristine, hub):
    """The marker the hub's R-1 check is configured to require, from FE itself.

    Production's `[dosemu.015.fe]` declares
    `completion_marker = "Planetary Maintenance Complete"`. If FE did not print
    exactly that, every FE run would be recorded `failed` -- the check would fire
    on healthy runs and the hub would stop archiving good packets. Worth one
    explicit test rather than an inference from BRE's behaviour.
    """
    pristine("900F")
    hub.seed(["900F"])

    node_rig.run(FE, NODE02, "PLANETARY", tag="fem")
    for name, payload in node_rig.take_outbound(FE, NODE02):
        if name.lower().startswith("900f"):
            hub.deliver(name, payload)
    await hub.process()

    run = hub.last_run()
    assert run.status == "completed"
    assert COMPLETION_MARKER in (run.dosemu_log or ""), (
        "FE did not print the completion marker the hub is configured to require"
    )


async def test_fe_builds_its_world_on_the_first_run_not_at_reset(pristine):
    """A real difference between the two games, pinned so it cannot drift.

    BRE writes its whole DATA/ at RESET. FE writes only game.dat, and creates
    planet.fe, routes.dat and the rest on the first PLANETARY run -- which is
    also game-day one, and emits real packets.

    This is why the FE fixture is captured straight after RESET: capturing after
    a first run would bake in a world, a used sequence number and a set of
    outbound packets, and the fixture would not be virgin. If FE ever started
    building its world at RESET, the fixture semantics would change and this
    test is the thing that would say so.
    """
    # The hub's own install, not a node's: this reads DATA/ directly from Linux
    # and each node's tree is private to that node's unix user.
    pristine("900F")
    install = FE.install_path(HUB)

    before = {p.name.lower() for p in (install / "DATA").iterdir()}
    assert "game.dat" in before
    assert "planet.fe" not in before, (
        "FE now creates planet.fe at RESET; the fixture is no longer virgin"
    )

    node_rig.run(FE, HUB, "PLANETARY", tag="few")

    after = {p.name.lower() for p in (install / "DATA").iterdir()}
    assert "planet.fe" in after, "the first PLANETARY run did not build the world"


async def test_a_broken_inbound_path_is_detected_and_self_heals(pristine, hub):
    """R-3, in the game it actually happened to.

    fe_015's BBS.CFG line 4 pointed at an inbound directory inherited from
    another machine. The hub copied packets into fe_015\\INBOUND, FE read
    somewhere else, found nothing, and completed successfully having ingested
    zero packets. Nothing anywhere noticed.
    """
    pristine("900F")
    hub.seed(["900F"])

    node_rig.run(FE, NODE02, "PLANETARY", tag="fer3")
    produced = dict(node_rig.take_outbound(FE, NODE02))
    payload = produced["900f0201.001"]

    node_rig.set_inbound_path(FE, HUB, "C:\\BBS\\DOORS\\FE_900_HUB\\INBOUND")
    hub.deliver("900f0201.001", payload)
    await hub.process()

    run = hub.last_run()
    # Healthy in every respect that used to be checked.
    assert run.status == "completed"
    assert hub.transcript(run).completed

    assert run.packets_unconsumed == 1, (
        "FE ingested nothing but the run reported no unconsumed packets"
    )
    assert not hub.transcript(run).ingested_from(2), (
        "FE decompressed something from a path it cannot reach"
    )
    assert hub.game_inbound("900F") == ["900f0201.001"], (
        "the hub threw away the packet FE never read"
    )

    # Repair and re-queue; nothing is moved by hand.
    node_rig.set_inbound_path(FE, HUB, f"{FE.dos_path(HUB)}\\INBOUND")
    hub.deliver("900f0201.001", payload)
    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_unconsumed == 0, "the repaired FE run still could not read it"
    assert hub.game_inbound("900F") == []
    assert hub.transcript(run).ingested_from(2)


async def test_two_different_games_process_in_one_batch(pristine, hub):
    """B-1, across games rather than across two BRE leagues.

    test_multi_league covers 900B + 901B: same game, two leagues. This covers
    900B + 900F: same league *number*, two games. That pairing is the one that
    exists in production -- 015B and 015F share the league id "015" and are
    distinguished only by game type -- and it is the arrangement most likely to
    collide in code that keys on the number alone.

    One batch is one ProcessingRun even when it spans several groups; the split
    lives in the (game_type, league_id) grouping and in the per-file league
    attribution, not in the run count.
    """
    from backend.models.database import League as LeagueRow, ProcessingRunFile
    pristine("900B")
    pristine("900F")
    hub.seed(["900B", "900F"])

    for league, prefix in ((BRE, "900b"), (FE, "900f")):
        node_rig.run(league, NODE02, "PLANETARY", tag=f"fex_{league.league_id}")
        for name, payload in node_rig.take_outbound(league, NODE02):
            if name.lower().startswith(prefix):
                hub.deliver(name, payload)

    assert sorted(hub.spool("inbound")) == ["900b0201.001", "900f0201.001"]

    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_processed == 2
    assert run.packets_unconsumed == 0

    # Neither game is holding the other's packet.
    assert hub.game_inbound("900B") == []
    assert hub.game_inbound("900F") == []

    # The files ingested in this batch are attributed to both games, not to
    # whichever ran last. Same league number, so a lookup that ignored game type
    # would land every row on one of them.
    rows = (
        hub.db.query(ProcessingRunFile)
        .filter(ProcessingRunFile.processing_run_id == run.id)
        .all()
    )
    assert rows, "the batch ingested no files at all"
    games = {
        hub.db.query(LeagueRow).filter(LeagueRow.id == row.league_id).first().game_type
        for row in rows
        if row.league_id is not None
    }
    assert games == {"B", "F"}, (
        f"ingested files were attributed to games {games}, expected both B and F"
    )

    assert sorted(hub.spool("processed")) == ["900b0201.001", "900f0201.001"]
    outbound = hub.spool("outbound")
    assert "900B0102.001" in outbound, outbound
    assert "900F0102.001" in outbound, outbound
