"""The R-3 detector: a game that is handed packets it cannot read.

Production league fe_015 has an inbound path in BBS.CFG that the game cannot
traverse. Nothing reports it. The run completes, every phase marker prints, the
exit code is zero -- and not one packet is ingested. Before this, the hub then
deleted the untouched packets from the game inbound, so the evidence and the
retry both went in the same bin.

Two halves here: preflight, the hub's own check of its games that runs at
startup and from the league page, and the live proof that a broken path is
detected and that repairing it recovers on its own.
"""
import os

import pytest

# rig.dospath takes the 8.3 rule from sr-harness, a rig dependency; see
# test_player_traffic.py for why collection needs this guard off the rig.
if os.getenv("NOVATEST_RIG") != "1":
    pytest.importorskip("sr_harness", reason="sr-harness is a rig-only dependency")

from rig import node as node_rig  # noqa: E402
from rig.dospath import bbs_cfg_inbound, dos_path_problems  # noqa: E402
from rig.layout import LEAGUES, NODES  # noqa: E402

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]
HUB = NODES[1]


# ---------------------------------------------------------------------------
# Structural: no dosemu needed, no game run
# ---------------------------------------------------------------------------

def test_the_8_3_check_catches_a_long_component():
    """The check itself, so a silent detector does not pass for the wrong reason."""
    assert dos_path_problems("C:\\BBS\\DOORS\\B900N01\\INBOUND") == []
    assert dos_path_problems("C:\\BBS\\DOORS\\BRE_900_N02\\INBOUND")
    assert dos_path_problems("C:\\BBS\\DOORS\\B900N01\\INBOUND.DATA")


def test_every_hub_game_passes_preflight(pristine, hub):
    """Every league's game on the hub is its own and can be read: preflight, the
    check that would have caught fe_015 at deploy time, finds nothing."""
    from backend.services import preflight

    for league_id in ("900B", "901B", "900F"):
        pristine(league_id, nodes=[1])
    hub.seed(["900B", "901B", "900F"])
    results = preflight.run(hub.db, hub.config)
    assert results == {"900B": [], "901B": [], "900F": []}, results


def test_the_installs_agree_with_the_config(rig_available):
    """bbs.cfg line 4 must name the hub game's own INBOUND, the folder the hub
    puts packets in. Nothing at runtime notices when they drift apart."""
    for league_id in ("900B", "901B"):
        league = LEAGUES[league_id]
        install = league.install_path(HUB)
        line4 = bbs_cfg_inbound(install)
        assert dos_path_problems(line4) == [], line4
        assert line4.upper() == f"{league.dos_path(HUB)}\\INBOUND".upper(), (
            f"{league_id}: bbs.cfg line 4 is {line4!r}, "
            f"not this install's INBOUND"
        )


# ---------------------------------------------------------------------------
# Live: the failure mode itself
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_broken_inbound_path_is_detected_and_self_heals(pristine, hub):
    pristine("900B")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="r3")
    produced = dict(node_rig.take_outbound(LEAGUE, NODE02))
    payload = produced["900b0201.001"]

    # Break line 4 the way fe_015 is broken: a path the game cannot traverse.
    node_rig.set_inbound_path(LEAGUE, HUB, "C:\\BBS\\DOORS\\BRE_900_HUB\\INBOUND")
    # Preflight says so before anything runs; it alerts, it does not stop the
    # run, so what follows is what a hub whose admin missed it would do.
    from backend.services import preflight
    problems = preflight.run(hub.db, hub.config)["900B"]
    assert any("line 4" in p for p in problems), problems
    hub.deliver("900b0201.001", payload)
    await hub.process()

    run = hub.last_run()
    # The run looks entirely healthy -- that is the whole problem. It completes,
    # the game prints its completion marker, dosemu exits zero.
    assert run.status == "completed"
    assert hub.transcript(run).completed

    # The count is the only thing that gives it away.
    assert run.packets_unconsumed == 1, (
        "the game ingested nothing but the run reported no unconsumed packets"
    )
    assert not hub.transcript(run).ingested_from(2), (
        "the game decompressed something from a path it cannot reach"
    )

    # And the packet is left exactly where it was, not deleted. This is what
    # makes recovery possible at all.
    assert hub.game_inbound("900B") == ["900b0201.001"], (
        "the hub threw away the packet the game never read"
    )

    # Repair line 4 and re-queue the packet. Note what is *not* needed: nothing
    # is moved by hand in the game folder. The stale copy the game could not read
    # is simply overwritten by the same bytes, and this time it is taken.
    #
    # Re-queueing is needed only because a batch with no pending packets does not
    # run the game at all; in production the next day's traffic does the same job
    # without anyone touching anything.
    node_rig.set_inbound_path(LEAGUE, HUB, f"{LEAGUE.dos_path(HUB)}\\INBOUND")
    hub.deliver("900b0201.001", payload)
    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_unconsumed == 0, "the repaired run still could not read it"
    assert hub.game_inbound("900B") == []
    assert hub.transcript(run).ingested_from(2)
