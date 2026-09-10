# tests/test_processing_service.py - Unit tests for ProcessingService bookkeeping
#
# These guard two failure modes found while building the test rig:
#
#   R-1  a failed dosemu run was recorded as a "completed" processing run
#   R-3  a packet the game silently ignored was deleted by the hub afterwards,
#        so it looked identical to one that had been processed, and the retry
#        was thrown away along with the evidence

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import League, Packet, ProcessingRun
from backend.services.processing_service import ProcessingService


def _config(tmp_path, game_dir):
    return {
        "server": {"data_dir": str(tmp_path)},
        "hub": {"bbs_index": "01"},
        "dosemu": {
            "dosemu_path": "/usr/bin/dosemu",
            "timeout": 30,
            "900": {
                "bre": {
                    "game_folder": str(game_dir),
                    "inbound_folder": str(game_dir / "INBOUND"),
                    "outbound_folder": str(game_dir / "OUTBOUND"),
                    "processing_command": "BRE.EXE PLANETARY /DETAILED",
                }
            },
        },
    }


@pytest.fixture
def league(db_session):
    lg = League(league_id="900", game_type="B", name="Test 900B", is_active=True)
    db_session.add(lg)
    db_session.commit()
    db_session.refresh(lg)
    return lg


@pytest.fixture
def rig(tmp_path, db_session, league):
    """A hub data dir with one packet queued in hub inbound, ready to process."""
    game_dir = tmp_path / "b900n01"
    (game_dir / "INBOUND").mkdir(parents=True)
    (game_dir / "OUTBOUND").mkdir(parents=True)

    hub_inbound = tmp_path / "packets" / "inbound"
    hub_inbound.mkdir(parents=True)
    (hub_inbound / "900B0201.001").write_bytes(b"packet payload")

    packet = Packet(
        filename="900B0201.001",
        league_id=league.id,
        source_bbs_index="02",
        dest_bbs_index="01",
        sequence_number=1,
        file_size=len(b"packet payload"),
        uploaded_at=datetime.now(),
    )
    db_session.add(packet)
    db_session.commit()
    db_session.refresh(packet)

    service = ProcessingService(db_session, _config(tmp_path, game_dir))
    return service, packet, game_dir, hub_inbound


def _dosemu(service, monkeypatch, *, status="success", consume=None):
    """Stub the dosemu run, optionally deleting inbound files like a real game."""

    async def fake_run(game_type, league_id):
        if consume:
            for f in consume():
                f.unlink()
        return {"status": status, "output": "transcript", "returncode": 0}

    monkeypatch.setattr(service.dosemu_runner, "run_game_process", fake_run)


# ---------------------------------------------------------------------------
# R-3: what the game did not take
# ---------------------------------------------------------------------------

class TestUnconsumedPackets:
    @pytest.mark.asyncio
    async def test_consumed_packet_leaves_nothing_behind(
        self, rig, monkeypatch
    ):
        service, packet, game_dir, _ = rig
        inbound = game_dir / "INBOUND"
        _dosemu(service, monkeypatch, consume=lambda: list(inbound.glob("*")))

        await service.process_game_batch("BRE", [packet], run_id=1)

        assert list(inbound.glob("*")) == []
        assert service._unconsumed_count == 0

    @pytest.mark.asyncio
    async def test_ignored_packet_is_left_in_place_and_counted(
        self, rig, monkeypatch
    ):
        """The game ignored it (bad BBS.CFG inbound path). Do not delete it.

        Leaving it is what makes recovery self-healing: fix the path, and the
        next run picks it up with no operator action.
        """
        service, packet, game_dir, _ = rig
        inbound = game_dir / "INBOUND"
        _dosemu(service, monkeypatch)  # consumes nothing

        await service.process_game_batch("BRE", [packet], run_id=1)

        assert (inbound / "900B0201.001").exists(), (
            "an un-ingested packet must survive the run, both as evidence and "
            "so the next run retries it"
        )
        assert service._unconsumed_count == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_once_the_fault_is_fixed(self, rig, monkeypatch):
        """Second run, game now working: the leftover is picked up."""
        service, packet, game_dir, _ = rig
        inbound = game_dir / "INBOUND"

        _dosemu(service, monkeypatch)
        await service.process_game_batch("BRE", [packet], run_id=1)
        assert service._unconsumed_count == 1

        service._unconsumed_count = 0
        _dosemu(service, monkeypatch, consume=lambda: list(inbound.glob("*")))
        await service.process_game_batch("BRE", [packet], run_id=2)

        assert list(inbound.glob("*")) == []
        assert service._unconsumed_count == 0

    @pytest.mark.asyncio
    async def test_failed_run_leaves_hub_inbound_alone(self, rig, monkeypatch):
        """R-1: nothing is archived or marked processed when dosemu failed."""
        service, packet, _, hub_inbound = rig
        _dosemu(service, monkeypatch, status="error")

        result = await service.process_game_batch("BRE", [packet], run_id=1)

        assert result["status"] == "error"
        assert (hub_inbound / "900B0201.001").exists()
        assert packet.processed_at is None


# ---------------------------------------------------------------------------
# R-1: how the run is recorded
# ---------------------------------------------------------------------------

class TestRunStatus:
    @pytest.mark.asyncio
    async def test_failed_group_marks_the_run_failed(
        self, rig, db_session, monkeypatch
    ):
        service, _, _, _ = rig

        async def fake_group(game_type, packets, run_id):
            return {"status": "error", "error": "dosemu exited 1"}

        monkeypatch.setattr(service, "process_game_batch", fake_group)
        monkeypatch.setattr(service, "scan_outbound_folders", _noop)

        await service.process_batch()

        run = db_session.query(ProcessingRun).one()
        assert run.status == "failed", (
            "a run whose dosemu died must not be recorded as completed"
        )
        assert "dosemu exited 1" in run.error_message
        assert run.packets_processed == 0

    @pytest.mark.asyncio
    async def test_successful_group_marks_the_run_completed(
        self, rig, db_session, monkeypatch
    ):
        service, packet, _, _ = rig

        async def fake_group(game_type, packets, run_id):
            for p in packets:
                p.processed_at = datetime.now()
            return {"status": "success", "output": "transcript"}

        monkeypatch.setattr(service, "process_game_batch", fake_group)
        monkeypatch.setattr(service, "scan_outbound_folders", _noop)

        await service.process_batch()

        run = db_session.query(ProcessingRun).one()
        assert run.status == "completed"
        assert run.packets_processed == 1
        assert run.packets_unconsumed == 0

    @pytest.mark.asyncio
    async def test_one_failed_league_fails_the_whole_run(
        self, rig, db_session, league, monkeypatch
    ):
        """Two leagues, one broken: the run must not report success."""
        service, packet, _, _ = rig

        other = League(
            league_id="901", game_type="B", name="Test 901B", is_active=True
        )
        db_session.add(other)
        db_session.commit()
        db_session.add(
            Packet(
                filename="901B0201.001",
                league_id=other.id,
                source_bbs_index="02",
                dest_bbs_index="01",
                sequence_number=1,
                file_size=4,
                uploaded_at=datetime.now(),
            )
        )
        db_session.commit()

        async def fake_group(game_type, packets, run_id):
            lg = db_session.query(League).filter(
                League.id == packets[0].league_id
            ).first()
            if lg.league_id == "901":
                return {"status": "error", "error": "boom"}
            for p in packets:
                p.processed_at = datetime.now()
            return {"status": "success", "output": "ok"}

        monkeypatch.setattr(service, "process_game_batch", fake_group)
        monkeypatch.setattr(service, "scan_outbound_folders", _noop)

        await service.process_batch()

        run = db_session.query(ProcessingRun).one()
        assert run.status == "failed"
        assert run.packets_processed == 1  # the healthy league still got through


async def _noop(*args, **kwargs):
    return None
