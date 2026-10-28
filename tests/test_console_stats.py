"""What the console counts, and the scripts' config (#92).

The dashboard's 24-hour cards and a league's processed and run counts were
always zero; the activity chart was a query an hour. A script given --config
never passed it to the app it started, and /health told anyone who asked
what the database error was.
"""
import importlib
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

import main
from main import app, management_app, service_app
from backend.core.clock import utcnow
from backend.core.database import get_db
from backend.core.security import get_current_user, require_admin
from backend.models.database import (
    Base, League, Packet, ProcessingRun, ProcessingRunFile, SysopUser,
)
from backend.services.stats_service import StatsService

ROOT = Path(__file__).parent.parent


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.queries = []
    event.listen(engine, "before_cursor_execute",
                 lambda conn, cur, statement, *a: session.queries.append(statement))
    yield session
    session.close()


def _packet(db, league, name, at, **kw):
    p = Packet(filename=name, league_id=league.id, source_bbs_index=name[4:6],
               dest_bbs_index=name[6:8], sequence_number=int(name[-3:]), file_size=1,
               uploaded_at=at, **kw)
    db.add(p)
    return p


@pytest.fixture
def league(db):
    lg = League(league_id="555", game_type="B", name="BRE 555")
    db.add(lg)
    db.commit()
    return lg


def test_the_dashboards_day_is_counted(db, league):
    now = utcnow()
    _packet(db, league, "555B0201.001", now - timedelta(hours=2))
    _packet(db, league, "555B0201.002", now - timedelta(days=3))
    db.add_all([ProcessingRun(started_at=now - timedelta(hours=1), status="completed"),
                ProcessingRun(started_at=now - timedelta(days=2), status="completed")])
    db.commit()

    stats = StatsService(db).get_dashboard_stats()
    assert stats["packets_24h"] == 1 and stats["processing_runs_24h"] == 1
    assert stats["total_packets"] == 2


def test_the_chart_is_one_query_however_many_hours(db, league):
    now = utcnow()
    for seq, h in enumerate((1, 1, 5, 100), start=1):
        _packet(db, league, f"555B0201.{seq:03d}", now - timedelta(hours=h, minutes=30))
    db.commit()
    db.queries.clear()

    labels, data = StatsService(db).get_activity_chart_data(hours=168)
    assert len(db.queries) == 1
    assert len(labels) == len(data) == 168
    assert sum(data) == 4
    assert data[-2] == 2 and data[-6] == 1 and data[-101] == 1


def test_the_activity_feed_does_not_query_per_packet(db, league):
    now = utcnow()
    for i in range(1, 21):
        _packet(db, league, f"555B0201.{i:03d}", now - timedelta(minutes=i))
    db.commit()

    db.queries.clear()
    feed = StatsService(db).get_recent_activity(limit=20)
    assert len(feed) == 20
    assert len(db.queries) < 10
    assert {item["type"] for item in feed} == {"upload"}


# -- a league's own counts --------------------------------------------------------

@pytest.fixture
def console(db):
    admin = SysopUser(username="admin", hashed_password="x", is_superuser=True)
    db.add(admin)
    db.commit()
    saved = {a: dict(a.dependency_overrides) for a in (app, management_app, service_app)}
    for a in (app, management_app, service_app):
        a.dependency_overrides[get_db] = lambda: db
        a.dependency_overrides[get_current_user] = lambda: admin
        a.dependency_overrides[require_admin] = lambda: admin
    yield TestClient(app, base_url="https://testserver")
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def test_a_leagues_processed_packets_and_runs_are_counted(db, league, console):
    now = utcnow()
    other = League(league_id="556", game_type="B", name="BRE 556")
    db.add(other)
    db.flush()
    carried, made, elsewhere = (ProcessingRun(status="completed") for _ in range(3))
    db.add_all([carried, made, elsewhere])
    db.flush()
    _packet(db, league, "555B0201.001", now, processed_at=now, processing_run_id=carried.id)
    _packet(db, league, "555B0201.002", now)
    db.add(ProcessingRunFile(processing_run_id=made.id, league_id=league.id,
                             file_type="score", filename="SCORES.TXT"))
    _packet(db, other, "556B0201.001", now, processed_at=now, processing_run_id=elsewhere.id)
    db.commit()

    stats = console.get(f"/management/api/v1/leagues/{league.id}").json()["stats"]
    assert stats == {"total_packets": 2, "processed_packets": 1, "processing_runs": 2}


# -- config and health -----------------------------------------------------------

def test_purge_retention_reaches_the_dosemu_logs(tmp_path, monkeypatch, capsys):
    import purge_retention
    data = tmp_path / "data"
    config = tmp_path / "config.toml"
    config.write_text((ROOT / "config.toml.example").read_text()
                      .replace('data_dir = "./data"', f'data_dir = "{data}"')
                      .replace('path = "./data/nova-hub.db"', f'path = "{tmp_path / "hub.db"}"'))
    config_module = importlib.import_module("backend.core.config")
    saved = config_module._config, config_module.config
    built = {}

    class Recording:
        def __init__(self, *a, **kw):
            built.update(kw)

        def preview(self):
            return {"enabled": True, "cutoff": utcnow(), "retention_days": 30, "tiers": {},
                    "rows": 0, "bytes": 0, "log_files": 3, "log_bytes": 2048}

    monkeypatch.setattr(purge_retention, "RetentionService", Recording)
    monkeypatch.setattr(sys, "argv", ["purge_retention.py", "--config", str(config), "--dry-run"])
    try:
        assert purge_retention.main() == 0
    finally:
        config_module._config, config_module.config = saved
    assert built["data_dir"] == str(data)
    assert "run logs" in capsys.readouterr().out


def test_run_hands_its_config_to_the_app(tmp_path, monkeypatch):
    import run
    config = tmp_path / "elsewhere.toml"
    config.write_text((ROOT / "config.toml.example").read_text())
    started = {}
    monkeypatch.setattr(run.uvicorn, "run", lambda *a, **kw: started.update(kw))
    monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(config)])
    monkeypatch.delenv("NOVA_HUB_CONFIG", raising=False)
    run.main()
    assert os.environ.pop("NOVA_HUB_CONFIG") == str(config)


def test_health_says_unhealthy_with_a_503_and_not_why(monkeypatch):
    class Broken:
        def execute(self, *a):
            raise RuntimeError("unable to open /secret/path/nova-hub.db")

        def close(self):
            pass

    monkeypatch.setattr(main, "get_session", lambda: Broken())
    r = TestClient(app).get("/health")
    assert r.status_code == 503
    assert r.json()["database"] == "unhealthy"
    assert "secret" not in r.text
