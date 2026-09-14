"""
backend/core/version.py

One place that knows what this build is.

The version string used to be repeated in three places in main.py and a fourth
in the frontend's package.json, which is exactly as reliable as it sounds. It
lives here now.

The revision and its date are read from git once, at import, and cached. This
deployment ships by patch and commit rather than by tag, so the commit is the
only honest answer to "what is running". Every lookup degrades to None rather
than raising: a tree deployed without .git, or without git installed, should
report an unknown revision, not fail to start.
"""

import subprocess
from functools import lru_cache
from pathlib import Path

__version__ = "0.3.1"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_GIT_TIMEOUT = 2


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


@lru_cache(maxsize=1)
def version_info() -> dict:
    """Version, revision and revision date. Any field may be None."""
    return {
        "version": __version__,
        "revision": _git("rev-parse", "--short", "HEAD"),
        # Committer date of HEAD, ISO-8601. For a deploy-by-commit setup this
        # is the release date -- it is when the running code was created.
        "released_at": _git("log", "-1", "--format=%cs"),
        # Marks a tree with uncommitted changes, so a hand-edited production
        # host cannot quietly claim to be a clean build.
        "dirty": bool(_git("status", "--porcelain")),
    }
