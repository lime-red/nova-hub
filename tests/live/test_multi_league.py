"""Scenario 3 - two leagues of the same game in one batch. The B-1 regression.

B-1 was a cross-contamination bug: a batch carrying traffic for more than one
league of the same game processed them as one, so packets and ingested files
landed under the wrong league. The fix shipped in 0.2.0 and has never been
exercised against two real leagues, because until this rig there were not two.

900B and 901B are both BRE and both have node02 in them, so a single batch has
genuine traffic for each. Everything here is about the two staying separate.
"""
import pytest

from rig import node as node_rig
from rig.layout import LEAGUES, NODES

pytestmark = pytest.mark.asyncio

L900 = LEAGUES["900B"]
L901 = LEAGUES["901B"]
NODE02 = NODES[2]
HUB = NODES[1]


async def _play_and_upload(hub, league):
    """node02 runs its maintenance in `league` and the hub receives the result."""
    node_rig.run(league, NODE02, "PLANETARY", tag=f"s3_{league.league_id}")
    delivered = []
    for name, payload in node_rig.take_outbound(league, NODE02):
        hub.deliver(name, payload)
        delivered.append(name)
    return delivered


async def test_two_leagues_process_without_contaminating_each_other(pristine, hub):
    pristine("900B")
    pristine("901B")
    hub.seed(["900B", "901B"])

    from_900 = await _play_and_upload(hub, L900)
    from_901 = await _play_and_upload(hub, L901)
    assert from_900 == ["900b0201.001"], from_900
    assert from_901 == ["901b0201.001"], from_901

    # One batch, both leagues.
    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_processed == 2
    assert run.packets_unconsumed == 0

    # Each game took its own packet and neither game folder holds the other one.
    assert hub.game_inbound("900B") == []
    assert hub.game_inbound("901B") == []

    assert sorted(hub.spool("processed")) == ["900b0201.001", "901b0201.001"]
    assert hub.spool("inbound") == []

    # Replies exist for both leagues, each addressed from its own league number.
    outbound = hub.spool("outbound")
    assert "900B0102.001" in outbound, outbound
    assert "901B0102.001" in outbound, outbound

    # 901B has only nodes 1 and 2, so it must not produce traffic for 03 or 04 --
    # the giveaway if the two leagues had been merged into one game run.
    assert not [n for n in outbound if n.upper().startswith(("901B0103", "901B0104"))], (
        f"901B produced traffic for nodes it does not have: {outbound}"
    )
    assert "900B0103.001" in outbound and "900B0104.001" in outbound, outbound


async def test_ingested_files_are_attributed_to_the_right_league(pristine, hub):
    """Every file the batch ingests carries the league it came from.

    This is the assertion B-1 would have failed: rows written with whichever
    league happened to be processed last.
    """
    from backend.models.database import League as LeagueRow, ProcessingRunFile

    pristine("900B")
    pristine("901B")
    hub.seed(["900B", "901B"])

    await _play_and_upload(hub, L900)
    await _play_and_upload(hub, L901)
    await hub.process()

    run = hub.last_run()
    rows = (
        hub.db.query(ProcessingRunFile)
        .filter(ProcessingRunFile.processing_run_id == run.id)
        .all()
    )
    assert rows, "the batch ingested no files at all"

    numbers = {
        row.id: hub.db.query(LeagueRow).filter(LeagueRow.id == row.league_id).first()
        for row in rows
    }
    assert all(numbers[row.id] is not None for row in rows), (
        "a run file was recorded with no league at all"
    )
    assert {numbers[row.id].league_id for row in rows} <= {"900", "901"}

    # Both leagues are represented: a run where one league silently contributed
    # nothing is exactly the shape of the original bug.
    assert {numbers[row.id].league_id for row in rows} == {"900", "901"}, (
        "only one league contributed files to a two-league batch"
    )
