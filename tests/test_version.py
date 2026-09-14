# tests/test_version.py
#
# The version string used to be written out four times -- three in main.py and
# once in the frontend's package.json -- which is how a build ends up claiming
# to be something it is not. These tests hold the single source in place and,
# more importantly, make sure a tree without git metadata still starts.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core import version as version_module


@pytest.fixture(autouse=True)
def clear_cache():
    version_module.version_info.cache_clear()
    yield
    version_module.version_info.cache_clear()


def test_reports_a_version():
    info = version_module.version_info()
    assert info["version"] == version_module.__version__
    assert info["version"]


def test_main_does_not_hardcode_a_second_version():
    """Every FastAPI app must take its version from the one source."""
    main_py = (Path(__file__).parent.parent / "main.py").read_text()
    assert 'version="0.2.0"' not in main_py
    assert main_py.count("version=__version__") == 3


def test_the_frontend_reports_the_same_version():
    """The login page bakes package.json's version in at build time.

    It cannot ask the API -- it is the page you see because you are not
    authenticated yet -- so the two numbers have to be kept in step by hand.
    This is what makes that a test failure rather than a stale string in a
    corner of the UI, which is how it went wrong last time.
    """
    import json

    package_json = Path(__file__).parent.parent / "frontend" / "package.json"
    frontend_version = json.loads(package_json.read_text())["version"]
    assert frontend_version == version_module.__version__


def test_no_version_is_hardcoded_in_the_frontend():
    """LoginView carried 'Nova Hub v0.2.0' as a literal for two releases."""
    import re

    src = Path(__file__).parent.parent / "frontend" / "src"
    offenders = []
    for path in src.rglob("*.vue"):
        for match in re.finditer(r"v\d+\.\d+\.\d+", path.read_text()):
            offenders.append(f"{path.name}: {match.group()}")
    assert not offenders, f"hardcoded version(s): {offenders}"


def test_missing_git_degrades_to_none_rather_than_raising(monkeypatch):
    """A tree deployed without .git must still start."""
    monkeypatch.setattr(version_module, "_git", lambda *args: None)
    info = version_module.version_info()
    assert info["revision"] is None
    assert info["released_at"] is None
    assert info["dirty"] is False
    assert info["version"]


def test_git_failure_is_swallowed(monkeypatch):
    """git present but unhappy -- a non-zero exit is not an exception."""
    class Result:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(version_module.subprocess, "run", lambda *a, **k: Result())
    info = version_module.version_info()
    assert info["revision"] is None


def test_git_missing_entirely_is_swallowed(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(version_module.subprocess, "run", boom)
    info = version_module.version_info()
    assert info["revision"] is None
    assert info["version"]
