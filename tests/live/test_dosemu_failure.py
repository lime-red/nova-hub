"""Scenario 7 - the R-1 regression test. A dosemu run that dies must fail the run.

This is the July outage in miniature. dosemu2 refuses to start when TERM names a
terminal it cannot drive, and the hub used to invoke it through `script -c`, which
returns its own exit status (always 0) rather than the child's. Every failed run
was therefore recorded as a success, packets were marked processed, and the
evidence was thrown away -- silently, for days.

`term = "dumb"` is the deliberate lever: it kills dosemu the same way, on demand.

Verified by reverting the fixes on the rig: with `-e` gone *and* the league
completion_marker unset -- the exact July configuration -- all three tests here
fail. Restoring either one on its own is enough to make them pass again, so the
hub now has two independent nets under a dead dosemu rather than none.
"""
import pytest

from rig import node as node_rig
from rig.layout import LEAGUES, NODES

pytestmark = pytest.mark.asyncio

LEAGUE = LEAGUES["900B"]
NODE02 = NODES[2]


async def test_a_dead_dosemu_fails_the_run_and_keeps_the_packet(
    pristine, hub_factory
):
    pristine("900B")
    hub = hub_factory(term="dumb")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s7")
    produced = dict(node_rig.take_outbound(LEAGUE, NODE02))
    packet = hub.deliver("900b0201.001", produced["900b0201.001"])

    await hub.process()

    run = hub.last_run()
    assert run.status == "failed", (
        "dosemu could not start, but the run was recorded as "
        f"{run.status!r} -- this is R-1"
    )
    assert run.error_message, "a failed run recorded no reason"

    # The packet is untouched: not marked processed, not archived, still in the
    # spool where the next run will pick it up. Losing this is what made the
    # outage unrecoverable rather than merely annoying.
    hub.db.refresh(packet)
    assert packet.processed_at is None, "a packet was marked processed by a dead run"
    assert hub.spool("inbound") == ["900b0201.001"]
    assert hub.spool("processed") == []


async def test_the_transcript_survives_the_failure(pristine, hub_factory):
    """The log is the only diagnostic a failed run leaves, so it must be kept.

    Guards the latent bug that surfaced with the `-e` fix: with a real non-zero
    exit code the error path became reachable, and it overwrote the transcript
    with stderr.
    """
    pristine("900B")
    hub = hub_factory(term="dumb")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s7b")
    produced = dict(node_rig.take_outbound(LEAGUE, NODE02))
    hub.deliver("900b0201.001", produced["900b0201.001"])

    await hub.process()
    run = hub.last_run()
    assert run.status == "failed"

    transcript = hub.transcript(run)
    assert not transcript.completed, "a dead dosemu reported the completion marker"
    assert not transcript.booted, "dosemu was supposed to die before reaching DOS"


async def test_recovery_after_the_fault_is_cleared(pristine, hub_factory):
    """The packet a failed run left behind is processed by the next healthy one.

    Same spool, same database, same packet record -- only the config changes, the
    way an operator would fix it and restart the service. Recovery must need no
    file-shuffling by hand.
    """
    pristine("900B")
    hub = hub_factory(term="dumb")
    hub.seed(["900B"])

    node_rig.run(LEAGUE, NODE02, "PLANETARY", tag="s7c")
    produced = dict(node_rig.take_outbound(LEAGUE, NODE02))
    packet = hub.deliver("900b0201.001", produced["900b0201.001"])
    await hub.process()
    assert hub.last_run().status == "failed"
    assert hub.spool("inbound") == ["900b0201.001"]

    hub.reconfigure()
    await hub.process()

    run = hub.last_run()
    assert run.status == "completed", f"run {run.status}: {run.error_message}"
    assert run.packets_unconsumed == 0
    assert hub.spool("processed") == ["900b0201.001"]
    assert hub.spool("inbound") == []
    hub.db.refresh(packet)
    assert packet.processed_at is not None
