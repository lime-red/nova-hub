# tests/test_retention_service.py - ROLLOUT_PLAN card Q-5
#
# `retention_days` was configured, documented and dead for the whole life of the
# project. These tests are the difference between a setting and a promise.

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import ProcessingRun, ProcessingRunFile
from backend.services.retention_service import RetentionService


def _run(db, league, days_ago: int, log: str = "x" * 1000) -> ProcessingRun:
    run = ProcessingRun(
        league_id=league.id,
        started_at=datetime.utcnow() - timedelta(days=days_ago),
        status="completed",
        packets_processed=3,
        exit_code=0,
        dosemu_log=log,
        stdout_log="out",
        stderr_log="err",
    )
    db.add(run)
    db.commit()
    return run


def _file(db, run, league, days_ago: int, data: str = "y" * 500) -> ProcessingRunFile:
    f = ProcessingRunFile(
        processing_run_id=run.id,
        league_id=league.id,
        file_type="score",
        filename="BBSLAND.ANS",
        file_data=data,
        file_size=len(data),
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    db.add(f)
    db.commit()
    return f


class TestRetention:
    def test_aged_transcripts_are_dropped(self, db_session, sample_league):
        old = _run(db_session, sample_league, days_ago=60)
        service = RetentionService(db_session, retention_days=30)

        result = service.purge()

        db_session.refresh(old)
        assert old.dosemu_log is None
        assert old.stdout_log is None
        assert old.stderr_log is None
        assert result["rows"] == 3  # three columns on one row

    def test_recent_transcripts_are_kept(self, db_session, sample_league):
        fresh = _run(db_session, sample_league, days_ago=5)

        RetentionService(db_session, retention_days=30).purge()

        db_session.refresh(fresh)
        assert fresh.dosemu_log is not None

    def test_the_run_row_survives_its_transcript(self, db_session, sample_league):
        """The whole design of the card: history stays, bulk goes."""
        old = _run(db_session, sample_league, days_ago=60)
        run_id = old.id

        RetentionService(db_session, retention_days=30).purge()

        kept = db_session.get(ProcessingRun, run_id)
        assert kept is not None
        assert kept.status == "completed"
        assert kept.packets_processed == 3
        assert kept.exit_code == 0
        assert kept.league_id == sample_league.id

    def test_generated_file_bodies_are_dropped_but_the_sizes_remain(
        self, db_session, sample_league
    ):
        run = _run(db_session, sample_league, days_ago=60)
        old = _file(db_session, run, sample_league, days_ago=60)

        RetentionService(db_session, retention_days=30).purge()

        db_session.refresh(old)
        assert old.file_data is None
        assert old.file_size == 500, "the row should still say how big it was"
        assert old.filename == "BBSLAND.ANS"

    def test_zero_days_disables_the_pass(self, db_session, sample_league):
        """`retention_days = 0` means keep everything, and must be obeyed as
        literally as any other value -- a purge that ignored it would be the
        same class of bug as the one this card fixes."""
        ancient = _run(db_session, sample_league, days_ago=3650)

        service = RetentionService(db_session, retention_days=0)
        assert not service.enabled
        result = service.purge()

        db_session.refresh(ancient)
        assert ancient.dosemu_log is not None
        assert result["enabled"] is False

    def test_preview_changes_nothing(self, db_session, sample_league):
        old = _run(db_session, sample_league, days_ago=60)
        service = RetentionService(db_session, retention_days=30)

        result = service.preview()

        db_session.refresh(old)
        assert old.dosemu_log is not None, "--dry-run must not delete"
        assert result["rows"] == 3
        assert result["bytes"] >= 1000

    def test_preview_predicts_what_purge_does(self, db_session, sample_league):
        run = _run(db_session, sample_league, days_ago=60)
        _file(db_session, run, sample_league, days_ago=60)
        _run(db_session, sample_league, days_ago=1)
        service = RetentionService(db_session, retention_days=30)

        predicted = service.preview()
        actual = service.purge()

        assert predicted["rows"] == actual["rows"]
        assert predicted["bytes"] == actual["bytes"]

    def test_a_second_pass_is_a_no_op(self, db_session, sample_league):
        """Blanking rather than deleting makes the pass idempotent, which is
        what lets a purge be interrupted and simply resumed."""
        _run(db_session, sample_league, days_ago=60)
        service = RetentionService(db_session, retention_days=30)

        first = service.purge()
        second = service.purge()

        assert first["rows"] > 0
        assert second["rows"] == 0

    @pytest.mark.parametrize("batch_size", [1, 2, 500])
    def test_batching_does_not_change_the_result(
        self, db_session, sample_league, batch_size
    ):
        for _ in range(5):
            _run(db_session, sample_league, days_ago=60)

        result = RetentionService(
            db_session, retention_days=30, batch_size=batch_size
        ).purge()

        assert result["rows"] == 15
        assert db_session.query(ProcessingRun).filter(
            ProcessingRun.dosemu_log.isnot(None)
        ).count() == 0

    def test_a_run_with_no_transcript_is_not_counted(self, db_session, sample_league):
        """A failed run can have no log at all; it must not inflate the report."""
        _run(db_session, sample_league, days_ago=60, log=None)

        result = RetentionService(db_session, retention_days=30).purge()

        assert result["tiers"]["processing run transcripts"]["rows"] == 0
