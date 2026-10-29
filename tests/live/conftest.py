"""Live rig fixtures.

This whole tree needs novatest-hl: real dosemu, real BRE installs under real node
users, real packets. It is skipped everywhere else, so `pytest tests/` on a dev
box stays fast and green.

    NOVATEST_RIG=1 /srv/novatest/venv/bin/pytest tests/live -v
"""
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rig import fixtures as fixture_store
from rig.layout import HUB_INDEX, LEAGUES, NODES, installs


def _why_not_available():
    if os.getenv("NOVATEST_RIG") != "1":
        return "NOVATEST_RIG=1 not set (live rig tests only run on novatest-hl)"
    if not shutil.which("dosemu"):
        return "dosemu is not installed"
    missing = [
        f"{league.league_id}/{node.index:02d}"
        for league, node in installs()
        if not fixture_store.exists(league, node)
    ]
    if missing:
        return (
            "rig fixtures missing for " + ", ".join(missing)
            + " - build them with: python -m rig.build"
        )
    stale = fixture_store.stale()
    if stale:
        when = fixture_store.captured_on(*stale[0])
        return (
            f"rig fixtures were captured on {when}, not today. A pristine game is "
            "only virgin on its capture date -- the games number packets by game "
            "day, so a day-old fixture starts at .002. Rebuild with: "
            "tests/live/run.sh --build"
        )
    return None


_UNAVAILABLE = _why_not_available()


LIVE_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    """Skip this tree, and only this tree.

    A conftest hook sees every item in the session, not just the ones under its
    own directory -- so without the path filter a plain `pytest tests/` silently
    skips the entire unit suite as well, and reports it as a clean run.
    """
    if not _UNAVAILABLE:
        return
    skip = pytest.mark.skip(reason=_UNAVAILABLE)
    for item in items:
        if LIVE_DIR in Path(str(item.fspath)).resolve().parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def rig_available():
    if _UNAVAILABLE:
        pytest.skip(_UNAVAILABLE)
    return True


@pytest.fixture
def pristine(rig_available):
    """Restore named installs to their virgin state before the test.

    Every scenario must start from this. Game state is cumulative -- empires grow,
    turns advance, GAME.DAT changes every run -- so without a restore no scenario
    is repeatable and no failure is reproducible.

        def test_x(pristine):
            pristine("900B")           # every node in the league
            pristine("901B", nodes=[1, 2])
    """
    restored = []

    def _restore(league_id, nodes=None):
        league = LEAGUES[league_id]
        for index in (nodes or league.members):
            node = NODES[index]
            _sudo_restore(league_id, index)
            restored.append((league_id, index))

    yield _restore


def _sudo_restore(league_id, index):
    """Restore as the owning user, so file ownership survives the round trip.

    No sudo for the hub's own installs: the rig already runs as the hub user.
    """
    node = NODES[index]
    from rig.layout import VENV_PYTHON

    prefix = [] if node.user == getpass.getuser() else ["sudo", "-u", node.user, "--"]
    subprocess.run(
        [*prefix, str(VENV_PYTHON), "-c",
         "import sys; sys.path.insert(0, '.'); "
         "from rig import fixtures; from rig.layout import LEAGUES, NODES; "
         f"fixtures.restore(LEAGUES['{league_id}'], NODES[{index}])"],
        cwd=str(Path(__file__).resolve().parent),
        capture_output=True, text=True, check=True,
    )


@pytest.fixture
def hub(tmp_path, rig_available):
    """A hub with a fresh database and empty spool, wired to the rig installs."""
    from rig.hub import test_hub

    with test_hub(tmp_path) as h:
        yield h


@pytest.fixture
def hub_factory(tmp_path, rig_available):
    """For scenarios that need a non-default hub config (term=dumb, broken path)."""
    from rig.hub import test_hub

    created = []

    def _make(**kwargs):
        ctx = test_hub(tmp_path, **kwargs)
        h = ctx.__enter__()
        created.append(ctx)
        return h

    yield _make
    for ctx in created:
        ctx.__exit__(None, None, None)


# -- Immortal Barons -------------------------------------------------------------

def pytest_generate_tests(metafunc):
    """Every IB scenario runs once per build rig.ib_builds chose: release and trunk.

    The ids name what was played (release-v0.2.6, trunk-75058b5), so a report
    says which build a failure belongs to without opening current.json.
    """
    if "ib_build" not in metafunc.fixturenames:
        return
    from rig.ib_league import builds
    from rig.layout import IB_KINDS

    chosen = builds()
    params, ids = [], []
    for kind in IB_KINDS:
        entry = chosen.get(kind)
        params.append((kind, entry))
        if entry and "binary" in entry:
            ids.append(f"{kind}-{entry['ref'] if kind == 'release' else entry['commit'][:7]}")
        else:
            ids.append(kind)
    metafunc.parametrize("ib_build", params, ids=ids, indirect=True)


@pytest.fixture
def ib_build(request, rig_available):
    """The IB this run plays. A build that failed is a failure, not a skip: trunk
    no longer building is exactly what a nightly run is there to say."""
    from rig.ib_league import Build

    kind, entry = request.param
    if not entry:
        pytest.skip(f"no IB {kind} build recorded; run.sh makes them (python -m rig.ib_builds)")
    if "error" in entry:
        pytest.fail(f"IB {kind} {entry.get('ref', '')}: {entry['error']}", pytrace=False)
    return Build(kind=kind, ref=entry["ref"], commit=entry.get("commit", ""),
                 binary=Path(entry["binary"]), version=entry["version"])


@pytest.fixture
def ib_league(tmp_path, ib_build):
    """League 900I, started: the hub serving, boards 02 and 03 set up but not synced."""
    from rig.ib_league import IbLeague

    league = IbLeague(tmp_path, ib_build)
    try:
        yield league.start()
    finally:
        league.stop()
