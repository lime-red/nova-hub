# tests/conftest.py - Shared fixtures for all test modules

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import Base, Client, League, LeagueMembership, SysopUser


@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_league(db_session):
    league = League(
        league_id="555",
        game_type="B",
        name="BRE League 555",
        is_active=True,
    )
    db_session.add(league)
    db_session.commit()
    db_session.refresh(league)
    return league


@pytest.fixture
def sample_client(db_session):
    client = Client(
        client_id="test-client-001",
        client_secret="secret",
        bbs_name="Test BBS",
        is_active=True,
    )
    db_session.add(client)
    db_session.commit()
    db_session.refresh(client)
    return client


@pytest.fixture
def sample_membership(db_session, sample_league, sample_client):
    membership = LeagueMembership(
        client_id=sample_client.id,
        league_id=sample_league.id,
        bbs_index=2,
        fidonet_address="13:10/102",
        is_active=True,
    )
    db_session.add(membership)
    db_session.commit()
    db_session.refresh(membership)
    return membership
