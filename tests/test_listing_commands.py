# tests/test_listing_commands.py - ROUTEINFO / BBSINFO outcome reporting (#83)
#
# BRE and FE end ROUTEINFO and BBSINFO with DOS errorlevel 1 even when they
# succeed. Once `script -e` passed that through, every processing run logged
# "Routes command failed: Unknown error" and "BBS info command failed: Unknown
# error" while routes.lst / bbsinfo.lst were being written and ingested fine.

import os
import sys
import time
from pathlib import Path

import pytest
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.processing_service import ProcessingService

report = ProcessingService._report_listing_command

ERRORLEVEL_1 = {"status": "error", "returncode": 1, "error": "dosemu exited 1 (transcript: x.log)"}


@pytest.fixture
def logs():
    records = []
    sink = logger.add(lambda m: records.append(m.record), level="DEBUG")
    yield records
    logger.remove(sink)


def _warnings(records):
    return [r["message"] for r in records if r["level"].name == "WARNING"]


def test_errorlevel_1_with_fresh_file_is_not_a_warning(tmp_path, logs):
    started = time.time()
    (tmp_path / "ROUTES.LST").write_text("routes")  # case-insensitive, as on DOS
    report("Routes", ERRORLEVEL_1, tmp_path, "routes.lst", started)
    assert _warnings(logs) == []


def test_errorlevel_1_with_stale_file_warns_and_says_why(tmp_path, logs):
    listing = tmp_path / "routes.lst"
    listing.write_text("old")
    old = time.time() - 3600
    os.utime(listing, (old, old))

    report("Routes", ERRORLEVEL_1, tmp_path, "routes.lst", time.time())

    [msg] = _warnings(logs)
    assert "did not refresh routes.lst" in msg
    assert "dosemu exited 1" in msg


def test_missing_file_warns(tmp_path, logs):
    report("BBS info", ERRORLEVEL_1, tmp_path, "bbsinfo.lst", time.time())
    [msg] = _warnings(logs)
    assert "no bbsinfo.lst" in msg


def test_success_with_stale_file_still_warns(tmp_path, logs):
    """Exit 0 is not proof either: the file is what the run is for."""
    listing = tmp_path / "bbsinfo.lst"
    listing.write_text("old")
    old = time.time() - 3600
    os.utime(listing, (old, old))

    report("BBS info", {"status": "success", "returncode": 0}, tmp_path, "bbsinfo.lst", time.time())
    assert len(_warnings(logs)) == 1


def test_timeout_without_error_key_is_never_unknown(tmp_path, logs):
    report("Routes", {"status": "timeout"}, None, "routes.lst", time.time())
    [msg] = _warnings(logs)
    assert "Unknown" not in msg
    assert "timeout" in msg
