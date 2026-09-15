"""The filesystem half of retention.

`dosemu_runner` writes every transcript to `<data_dir>/logs/dosemu/*.log` and
`processing_service` then copies it into `ProcessingRun.dosemu_log`. Blanking the
column while leaving the file is how production came to hold 53,930 log files and
860 MB across eight months with retention apparently working. These tests hold
the disk tier to the same cutoff as the database tier, and -- more importantly --
pin down what it must refuse to touch.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.services.retention_service import RetentionService, from_config


def _log(dir_path, name, age_days, body="transcript"):
    """A log file backdated by mtime, which is what the service reads."""
    p = dir_path / name
    p.write_text(body)
    when = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(p, (when, when))
    return p


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "logs" / "dosemu").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def log_dir(data_dir):
    return data_dir / "logs" / "dosemu"


def service(data_dir, days=30):
    # db is None: every test here exercises the filesystem pass only.
    return RetentionService(None, days, data_dir=data_dir)


def test_it_deletes_logs_older_than_the_window(data_dir, log_dir):
    _log(log_dir, "013_bre_processing_command_20260111_033655.log", age_days=90)
    _log(log_dir, "015_bre_bbsinfo_command_20260915_090636.log", age_days=1)

    deleted, freed = service(data_dir).purge_logs()

    assert deleted == 1
    assert freed > 0
    remaining = {p.name for p in log_dir.iterdir()}
    assert remaining == {"015_bre_bbsinfo_command_20260915_090636.log"}


def test_a_file_exactly_inside_the_window_survives(data_dir, log_dir):
    _log(log_dir, "recent.log", age_days=29)
    assert service(data_dir).purge_logs() == (0, 0)
    assert (log_dir / "recent.log").exists()


def test_retention_days_of_zero_deletes_nothing(data_dir, log_dir):
    _log(log_dir, "ancient.log", age_days=900)
    assert service(data_dir, days=0).purge_logs() == (0, 0)
    assert (log_dir / "ancient.log").exists()


def test_without_a_data_dir_the_pass_is_inert(log_dir):
    _log(log_dir, "ancient.log", age_days=900)
    svc = RetentionService(None, 30)  # no data_dir -- the old constructor shape
    assert svc.log_dir is None
    assert svc.purge_logs() == (0, 0)
    assert (log_dir / "ancient.log").exists()


def test_a_missing_log_directory_is_not_an_error(tmp_path):
    # A hub that has never run a game has no logs/dosemu at all.
    assert service(tmp_path).purge_logs() == (0, 0)


def test_it_only_removes_dot_log_files(data_dir, log_dir):
    """The directory is the hub's, but it is not the hub's alone."""
    _log(log_dir, "old.log", age_days=90)
    keep = _log(log_dir, "notes.txt", age_days=90)
    keep2 = _log(log_dir, "old.log.gz", age_days=90)

    deleted, _ = service(data_dir).purge_logs()

    assert deleted == 1
    assert keep.exists() and keep2.exists()


def test_it_does_not_descend_into_subdirectories(data_dir, log_dir):
    """No recursion: an operator archive folder is not ours to empty."""
    nested = log_dir / "archive"
    nested.mkdir()
    buried = _log(nested, "old.log", age_days=900)

    assert service(data_dir).purge_logs() == (0, 0)
    assert buried.exists()


def test_it_does_not_follow_symlinks(data_dir, log_dir, tmp_path):
    """Deleting through a link would delete something outside the log dir."""
    target = tmp_path / "precious.log"
    target.write_text("not ours")
    when = (datetime.now(timezone.utc) - timedelta(days=900)).timestamp()
    os.utime(target, (when, when))
    (log_dir / "linked.log").symlink_to(target)

    deleted, _ = service(data_dir).purge_logs()

    assert deleted == 0
    assert target.exists()


def test_a_log_it_cannot_delete_is_skipped_not_fatal(data_dir, log_dir, monkeypatch):
    """A permissions problem is an operator's to fix, not a reason to abort."""
    _log(log_dir, "stubborn.log", age_days=90)
    _log(log_dir, "ordinary.log", age_days=90)

    real_unlink = os.unlink

    def selective(path, *a, **kw):
        if str(path).endswith("stubborn.log"):
            raise PermissionError("nope")
        return real_unlink(path, *a, **kw)

    monkeypatch.setattr(os, "unlink", selective)

    deleted, _ = service(data_dir).purge_logs()

    assert deleted == 1
    assert (log_dir / "stubborn.log").exists()
    assert not (log_dir / "ordinary.log").exists()


@pytest.fixture
def far_east_timezone(monkeypatch):
    """Run the body under UTC+14, and put the process clock back afterwards.

    monkeypatch restores the variable but not libc's cached zone, so tzset has
    to be called again on the way out or every later test inherits Kiritimati.
    """
    import time as _time

    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    if hasattr(_time, "tzset"):
        _time.tzset()
    yield
    monkeypatch.undo()
    if hasattr(_time, "tzset"):
        _time.tzset()


def test_the_window_is_read_in_utc_not_local_time(data_dir, log_dir, far_east_timezone):
    """cutoff() is a naive UTC datetime; .timestamp() reads naive as *local*.

    Under UTC+14 that mistake puts the cutoff 14 hours further back, so the
    window silently becomes 30 days and 14 hours and files that should go are
    kept. The discriminating case is therefore a file aged somewhere inside
    those 14 hours: past the real cutoff, short of the mistaken one.
    """
    _log(log_dir, "boundary.log", age_days=30 + (7 / 24))

    deleted, _ = service(data_dir).purge_logs()
    assert deleted == 1, "the cutoff was read in local time, not UTC"


def test_from_config_wires_up_the_data_dir():
    """The daily loop builds the service this way; if this drops the data_dir
    the filesystem tier silently does nothing, which is the original bug."""
    svc = from_config(None, {
        "server": {"data_dir": "/srv/nova"},
        "processing": {"retention_days": 45},
    })
    assert svc.retention_days == 45
    assert str(svc.log_dir) == "/srv/nova/logs/dosemu"


def test_from_config_survives_a_config_with_no_server_section():
    svc = from_config(None, {"processing": {"retention_days": 10}})
    assert svc.log_dir is None
