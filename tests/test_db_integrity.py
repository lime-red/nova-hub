"""The database's references hold (#91).

The hub's engine runs SQLite with foreign keys on, so a delete that leaves a
row pointing at nothing fails instead of quietly orphaning it. These run on
that engine: deleting a league or a BBS has to clear everything that names
it first.
"""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app, service_app
from backend.core import database
from backend.core.database import get_db, sqlite_pragmas
from backend.core.security import get_current_user, require_admin
from backend.models.database import (
    AttackSighting, AuditEvent, Base, Client, ClientLeaguePoll, GameReset, League, LeagueGameSettings,
    LeagueMembership, Packet, ProcessingRun, ProcessingRunFile, ProcessingRunItem,
    SequenceAlert, SysopUser, TrafficSighting,
)

LEAGUES = "/management/api/v1/leagues"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'hub.db'}",
                           connect_args={"check_same_thread": False})
    event.listen(engine, "connect", sqlite_pragmas)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    admin = SysopUser(username="admin", hashed_password="x", is_superuser=True)
    db.add(admin)
    db.commit()

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    saved = {a: dict(a.dependency_overrides) for a in (app, management_app, service_app)}
    for a in (app, management_app, service_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = lambda: Session().get(SysopUser, admin.id)
        a.dependency_overrides[require_admin] = lambda: Session().get(SysopUser, admin.id)

    import importlib
    config_module = importlib.import_module("backend.core.config")
    real = config_module.get_config

    class FakeConfig:
        def get(self, section, default=None):
            return {"data_dir": str(tmp_path)} if section == "server" else real().get(section, default)

        def __getattr__(self, name):
            return getattr(real(), name)

    monkeypatch.setattr(config_module, "get_config", lambda: FakeConfig())

    yield TestClient(app, base_url="https://testserver"), db
    db.close()
    engine.dispose()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def test_the_hubs_engine_enforces_references_and_writes_ahead(tmp_path):
    engine = database.init_database(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
    engine.dispose()


def _a_league_with_everything(db):
    league = League(league_id="555", game_type="B", name="BRE 555")
    other = League(league_id="556", game_type="B", name="BRE 556")
    board = Client(client_id="board", client_secret="x", bbs_name="Board")
    db.add_all([league, other, board])
    db.flush()
    run = ProcessingRun(league_id=league.id, status="completed")
    db.add(run)
    db.flush()
    mine = Packet(filename="555B0201.001", league_id=league.id, source_bbs_index="02",
                  dest_bbs_index="01", sequence_number=1, file_size=1,
                  processing_run_id=run.id, source_client_id=board.id)
    theirs = Packet(filename="556B0201.001", league_id=other.id, source_bbs_index="02",
                    dest_bbs_index="01", sequence_number=1, file_size=1,
                    processing_run_id=run.id)
    db.add_all([mine, theirs, LeagueMembership(client_id=board.id, league_id=league.id, bbs_index=2)])
    db.flush()
    db.add_all([
        AttackSighting(attack_id="a" * 16, packet_id=mine.id, league_id=league.id,
                       from_planet=2, to_planet=1, attacker="A", target="B"),
        TrafficSighting(event_key="e" * 16, packet_id=mine.id, league_id=league.id,
                        record_type=0x15, kind="Report", from_planet=2, to_planet=1),
        LeagueGameSettings(league_id=league.id, packet_id=mine.id),
        GameReset(league_id=league.id, settings={}, status="applied"),
        SequenceAlert(league_id=league.id, source_bbs_index="02", dest_bbs_index="01",
                      expected_sequence=2, received_sequence=3, gap_size=1),
        ProcessingRunItem(processing_run_id=run.id, league_id=league.id,
                          direction="in", item_type="Report"),
        ProcessingRunFile(processing_run_id=run.id, league_id=league.id,
                          file_type="score", filename="SCORES.TXT"),
        ClientLeaguePoll(client_id=board.id, league_id=league.id),
    ])
    db.commit()
    return league, other, theirs


def test_deleting_a_league_takes_everything_that_names_it(hub):
    client, db = hub
    league, other, theirs = _a_league_with_everything(db)

    r = client.request("DELETE", f"{LEAGUES}/{league.id}", json={"confirmation_name": "BRE_555"})
    assert r.status_code == 200, r.text

    db.expire_all()
    for model in (AttackSighting, TrafficSighting, LeagueGameSettings, GameReset, SequenceAlert,
                  ProcessingRunItem, ProcessingRunFile, ProcessingRun, LeagueMembership,
                  ClientLeaguePoll):
        assert db.query(model).count() == 0, model.__name__
    # Another league's packet the run carried keeps its row, without the run.
    assert db.query(Packet).one().id == theirs.id
    assert db.get(Packet, theirs.id).processing_run_id is None
    assert db.query(League).one().id == other.id
    assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    assert db.query(AuditEvent).filter(AuditEvent.action == "league.deleted").count() == 1


def test_deleting_a_bbs_keeps_the_packets_it_sent(hub):
    client, db = hub
    board = Client(client_id="gone", client_secret="x", bbs_name="Gone")
    league = League(league_id="557", game_type="B", name="BRE 557")
    db.add_all([board, league])
    db.flush()
    db.add(Packet(filename="557B0201.001", league_id=league.id, source_bbs_index="02",
                  dest_bbs_index="01", sequence_number=1, file_size=1,
                  source_client_id=board.id, dest_client_id=board.id))
    # It polled the league once, and later left it: its poll row stays.
    db.add(ClientLeaguePoll(client_id=board.id, league_id=league.id))
    db.commit()

    r = client.delete(f"/management/api/v1/clients/{board.id}")
    assert r.status_code == 200, r.text
    db.expire_all()
    packet = db.query(Packet).one()
    assert packet.source_client_id is None and packet.dest_client_id is None
    assert db.query(ClientLeaguePoll).count() == 0


def test_creating_a_league_is_audited_and_its_number_is_three_digits(hub):
    client, db = hub
    assert client.post(LEAGUES, json={"league_id": "5555", "game_type": "B", "name": "x"}).status_code == 422
    r = client.post(LEAGUES, json={"league_id": "558", "game_type": "B", "name": "BRE 558"})
    assert r.status_code == 200, r.text
    assert db.query(AuditEvent).filter(AuditEvent.action == "league.created").count() == 1


def test_a_league_and_a_membership_exist_once(hub):
    _, db = hub
    league = League(league_id="559", game_type="B", name="a")
    board = Client(client_id="b", client_secret="x", bbs_name="B")
    db.add_all([league, board])
    db.commit()

    db.add(League(league_id="559", game_type="B", name="again"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add_all([LeagueMembership(client_id=board.id, league_id=league.id, bbs_index=2),
                LeagueMembership(client_id=board.id, league_id=league.id, bbs_index=3)])
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_one_reset_under_way_on_the_whole_hub(hub):
    _, db = hub
    a = League(league_id="560", game_type="B", name="a")
    b = League(league_id="561", game_type="B", name="b")
    db.add_all([a, b])
    db.commit()
    db.add_all([GameReset(league_id=a.id, settings={}, status="applying"),
                GameReset(league_id=b.id, settings={}, status="applying")])
    with pytest.raises(IntegrityError):
        db.commit()
