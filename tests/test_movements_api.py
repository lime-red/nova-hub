"""The movements view: what the games exchanged, for an admin to judge.

These tests are about what the endpoints report, not about parsing -- the rows
are written straight into the table so the shape of the answer is the only thing
under test.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, service_app, management_app
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.database import (
    Base,
    League,
    ProcessingRun,
    ProcessingRunItem,
    SysopUser,
)

BASE = "/management/api/v1/movements"


@pytest.fixture
def api(tmp_path):
    import backend.core.database as _db_mod

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    saved_engine, saved_session = _db_mod.engine, _db_mod.SessionLocal
    _db_mod.engine, _db_mod.SessionLocal = engine, TestSession

    saved = {a: (a.dependency_overrides.get(get_db),
                 a.dependency_overrides.get(get_current_user))
             for a in (app, service_app, management_app)}
    for a in (app, service_app, management_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = lambda: SysopUser(
            id=1, username="admin", hashed_password="x", is_superuser=True
        )

    db = TestSession()
    yield TestClient(app, base_url="https://testserver"), db
    db.close()

    for a, (old_db, old_user) in saved.items():
        a.dependency_overrides.pop(get_db, None)
        a.dependency_overrides.pop(get_current_user, None)
        if old_db:
            a.dependency_overrides[get_db] = old_db
        if old_user:
            a.dependency_overrides[get_current_user] = old_user
    _db_mod.engine, _db_mod.SessionLocal = saved_engine, saved_session


def seed(db, items, league_name="Test 900B", when=None):
    """items: (direction, type, src, dst, days_ago)"""
    league = League(league_id="900", game_type="B", name=league_name, is_active=True)
    db.add(league)
    db.commit()
    db.refresh(league)

    run = ProcessingRun(status="completed")
    db.add(run)
    db.commit()
    db.refresh(run)

    for direction, item_type, src, dst, days_ago in items:
        db.add(ProcessingRunItem(
            processing_run_id=run.id,
            league_id=league.id,
            occurred_at=(when or datetime.utcnow()) - timedelta(days=days_ago),
            direction=direction,
            item_type=item_type,
            src_node=src,
            dst_node=dst,
        ))
    db.commit()
    return league, run


def test_movements_are_listed_newest_first(api):
    client, db = api
    seed(db, [
        ("in", "Recon Update", 2, 1, 3),
        ("out", "Message", 1, 3, 0),
        ("in", "Message", 3, 1, 1),
    ])

    rows = client.get(f"{BASE}/?days=7").json()

    assert [r["item_type"] for r in rows] == ["Message", "Message", "Recon Update"]
    assert rows[0]["direction"] == "out"
    assert rows[0]["league_name"] == "Test 900B"


def test_the_window_excludes_older_traffic(api):
    """A week is the default because that is the span someone chasing a report
    about "yesterday" actually looks at."""
    client, db = api
    seed(db, [("in", "Recon Update", 2, 1, 40), ("in", "Message", 3, 1, 1)])

    recent = client.get(f"{BASE}/?days=7").json()
    everything = client.get(f"{BASE}/?days=365").json()

    assert [r["item_type"] for r in recent] == ["Message"]
    assert len(everything) == 2


def test_filtering_by_node_matches_either_end(api):
    """Someone chasing one board's traffic means 'involving node 3', not 'sent
    by node 3'. Matching only the source would hide half of it."""
    client, db = api
    seed(db, [
        ("in", "Message", 3, 1, 0),
        ("out", "Message", 1, 3, 0),
        ("in", "Recon Update", 2, 1, 0),
    ])

    rows = client.get(f"{BASE}/?node=3").json()

    assert len(rows) == 2
    assert all(3 in (r["src_node"], r["dst_node"]) for r in rows)


def test_the_summary_counts_by_type_pair_and_day(api):
    client, db = api
    seed(db, [
        ("in", "Recon Update", 2, 1, 0),
        ("in", "Recon Update", 2, 1, 0),
        ("out", "Configupdate", 1, 2, 1),
    ])

    s = client.get(f"{BASE}/summary?days=7").json()

    assert s["total"] == 3
    assert s["runs"] == 1
    assert {(t["direction"], t["item_type"], t["count"]) for t in s["by_type"]} == {
        ("in", "Recon Update", 2),
        ("out", "Configupdate", 1),
    }
    assert {(p["src_node"], p["dst_node"], p["count"]) for p in s["by_pair"]} == {
        (2, 1, 2), (1, 2, 1),
    }
    assert len(s["by_day"]) == 2


def test_a_node_with_no_traffic_in_the_window_is_named_as_quiet(api):
    """The one thing worth pointing at, and still not a verdict.

    Node 4 has exchanged items before but nothing this week. That might be a
    player on holiday or a board that has stopped polling; the hub cannot tell,
    and does not pretend to. It just saves the admin reading the table to work
    out who is missing.
    """
    client, db = api
    seed(db, [
        ("in", "Recon Update", 2, 1, 0),
        ("in", "Recon Update", 4, 1, 40),   # node 4, but long ago
    ])

    s = client.get(f"{BASE}/summary?days=7").json()

    assert s["quiet_nodes"] == [4]


def test_a_league_with_no_traffic_at_all_summarises_to_zero(api):
    """An empty league must render, not explode: this is what a new hub shows."""
    client, db = api

    s = client.get(f"{BASE}/summary?days=7").json()

    assert s["total"] == 0
    assert s["runs"] == 0
    assert s["by_type"] == [] and s["by_pair"] == [] and s["quiet_nodes"] == []


def test_filtering_by_league_keeps_leagues_apart(api):
    client, db = api
    league, run = seed(db, [("in", "Recon Update", 2, 1, 0)])

    other = League(league_id="901", game_type="B", name="Test 901B", is_active=True)
    db.add(other)
    db.commit()
    db.refresh(other)
    db.add(ProcessingRunItem(
        processing_run_id=run.id, league_id=other.id,
        occurred_at=datetime.utcnow(), direction="in",
        item_type="Message", src_node=2, dst_node=1,
    ))
    db.commit()

    rows = client.get(f"{BASE}/?league_id={league.id}").json()

    assert [r["item_type"] for r in rows] == ["Recon Update"]
