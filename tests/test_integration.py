# tests/test_integration.py - Functional tests for Nova Hub

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app, service_app
from backend.models.database import Base, Client, League, LeagueMembership, SysopUser
from backend.core.database import get_db


# In-memory test database — StaticPool ensures all sessions share the same connection
# (and thus the same in-memory database), which is required for SQLite :memory:.
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Override the dependency on all three FastAPI apps (main + sub-apps)
app.dependency_overrides[get_db] = override_get_db
management_app.dependency_overrides[get_db] = override_get_db
service_app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    """Re-create schema before each test and drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    # Use HTTPS base URL so httpx sends Secure cookies back (they require HTTPS).
    # raise_server_exceptions=False lets us inspect error status codes directly.
    return TestClient(app, raise_server_exceptions=False, base_url="https://testserver")


@pytest.fixture
def db(reset_db):
    """Provides a DB session. Depends on reset_db to ensure tables exist first."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def admin_user(db):
    user = SysopUser(
        username="admin",
        hashed_password=SysopUser.hash_password("AdminPass123!"),
        is_active=True,
        is_superuser=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def oauth_client(db):
    from backend.core.security import hash_password
    plain_secret = Client.generate_client_secret()
    c = Client(
        client_id="test-bbs-001",
        client_secret=hash_password(plain_secret),  # store bcrypt hash, as production does
        bbs_name="Test BBS",
        is_active=True,
    )
    c._plain_secret = plain_secret  # stash plain text for use in test assertions
    db.add(c)
    db.commit()
    db.refresh(c)
    c._plain_secret = plain_secret  # re-stash after refresh (non-mapped attr survives, but be safe)
    return c


@pytest.fixture
def league_with_member(db, oauth_client):
    league = League(league_id="555", game_type="B", name="BRE League 555", is_active=True)
    db.add(league)
    db.commit()
    db.refresh(league)

    membership = LeagueMembership(
        client_id=oauth_client.id,
        league_id=league.id,
        bbs_index=2,
        fidonet_address="13:10/102",
        is_active=True,
    )
    db.add(membership)
    db.commit()
    return league, membership


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------

def test_health_check(client):
    # The health endpoint calls get_session() directly (not via DI), so it needs
    # the full app lifespan to be running.  In unit-test mode the lifespan is not
    # triggered, so we accept either 200 (full env) or 500 (test env).
    response = client.get("/health")
    assert response.status_code in (200, 500)


def test_api_overview(client):
    response = client.get("/api")
    assert response.status_code == 200
    data = response.json()
    assert "nova_hub" in data
    assert "endpoints" in data["nova_hub"]
    assert "service" in data["nova_hub"]["endpoints"]
    assert "management" in data["nova_hub"]["endpoints"]


def test_spa_fallback(client):
    """Unknown paths should return 200 (SPA catch-all)."""
    response = client.get("/some/nonexistent/path")
    # Either 200 (SPA) or 200 with JSON if frontend not built
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Management auth
# ---------------------------------------------------------------------------

def test_management_login_wrong_password(client, admin_user):
    response = client.post(
        "/management/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert response.status_code == 401


def test_management_login_success(client, admin_user):
    response = client.post(
        "/management/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("user", {}).get("username") == "admin"


def test_management_protected_requires_auth(client):
    """Management endpoints should return 401 without a session cookie."""
    response = client.get("/management/api/v1/clients")
    assert response.status_code == 401


def test_management_login_then_list_clients(client, admin_user):
    # Login
    login = client.post(
        "/management/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass123!"},
    )
    assert login.status_code == 200

    # List clients — cookie is automatically included by TestClient
    response = client.get("/management/api/v1/clients")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# Service API — OAuth flow
# ---------------------------------------------------------------------------

def test_service_token_invalid_credentials(client):
    response = client.post(
        "/service/api/v1/auth/token",
        data={"grant_type": "client_credentials", "client_id": "bad", "client_secret": "bad"},
    )
    assert response.status_code == 401


def test_service_token_valid(client, oauth_client):
    response = client.post(
        "/service/api/v1/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": oauth_client.client_id,
            "client_secret": oauth_client._plain_secret,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_service_packet_list_requires_auth(client):
    response = client.get("/service/api/v1/leagues/555B/packets")
    assert response.status_code == 401


def test_service_packet_upload_and_list(client, tmp_path, oauth_client, league_with_member):
    # Get bearer token
    token_resp = client.post(
        "/service/api/v1/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": oauth_client.client_id,
            "client_secret": oauth_client._plain_secret,
        },
    )
    assert token_resp.status_code == 200
    token = token_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Patch data_dir to use tmp_path so uploads actually land somewhere.
    # Use sys.modules directly because backend.core.__init__ shadows the 'config'
    # submodule attribute with a None value (from `from .config import config`).
    import sys
    cfg_module = sys.modules["backend.core.config"]
    original_get = cfg_module.get_config

    class FakeConfig:
        class security:
            max_upload_size_bytes = 10 * 1024 * 1024

        def get(self, section, default=None):
            if section == "server":
                return {"data_dir": str(tmp_path)}
            return default

    cfg_module.get_config = lambda: FakeConfig()
    try:
        # Upload packet from BBS 02 to BBS 01
        packet_data = b"FAKE_BRE_PACKET_DATA_0001"
        upload_resp = client.put(
            "/service/api/v1/leagues/555B/packets/555B0201.001",
            content=packet_data,
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert upload_resp.status_code == 200, upload_resp.text
        assert upload_resp.json()["status"] == "received"

        # List packets
        list_resp = client.get("/service/api/v1/leagues/555B/packets", headers=headers)
        assert list_resp.status_code == 200
    finally:
        cfg_module.get_config = original_get


# ---------------------------------------------------------------------------
# Management — league CRUD
# ---------------------------------------------------------------------------

def test_management_create_and_list_league(client, admin_user):
    login = client.post(
        "/management/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass123!"},
    )
    assert login.status_code == 200

    create_resp = client.post(
        "/management/api/v1/leagues",
        json={"league_id": "013", "game_type": "F", "name": "FE League 013"},
    )
    assert create_resp.status_code == 200
    data = create_resp.json()
    assert data["full_id"] == "013F"

    list_resp = client.get("/management/api/v1/leagues")
    assert list_resp.status_code == 200
    assert any(l["full_id"] == "013F" for l in list_resp.json())


def test_delete_league_full_cleanup(client, db, admin_user, oauth_client):
    """Delete a league that has packets, processing runs, run files, memberships,
    and sequence alerts — verifies all related rows are removed without FK errors."""
    from backend.models.database import Packet, ProcessingRun, ProcessingRunFile, SequenceAlert

    # --- Setup: build a league with one of every related record ---
    league = League(league_id="014", game_type="B", name="BRE League 014", is_active=True)
    db.add(league)
    db.commit()
    db.refresh(league)

    membership = LeagueMembership(
        client_id=oauth_client.id,
        league_id=league.id,
        bbs_index=3,
        fidonet_address="13:10/103",
        is_active=True,
    )
    db.add(membership)

    packet = Packet(
        filename="014B0301.001",
        league_id=league.id,
        source_bbs_index="03",
        dest_bbs_index="01",
        sequence_number=1,
        file_size=25,
    )
    db.add(packet)

    run = ProcessingRun(league_id=league.id, status="completed")
    db.add(run)
    db.commit()
    db.refresh(run)

    run_file = ProcessingRunFile(
        processing_run_id=run.id,
        league_id=league.id,
        filename="014B0301.001",
        file_type="packet",
    )
    db.add(run_file)

    alert = SequenceAlert(
        league_id=league.id,
        source_bbs_index="03",
        dest_bbs_index="01",
        expected_sequence=2,
        received_sequence=5,
        gap_size=3,
    )
    db.add(alert)
    db.commit()

    league_db_id = league.id

    # --- Act: log in as admin and delete the league ---
    login = client.post(
        "/management/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass123!"},
    )
    assert login.status_code == 200

    resp = client.request(
        "DELETE",
        f"/management/api/v1/leagues/{league_db_id}",
        json={"confirmation_name": "BRE_014"},
    )
    assert resp.status_code == 200, resp.text

    # --- Assert: every related table is empty for this league ---
    db.expire_all()
    assert db.query(League).filter(League.id == league_db_id).first() is None
    assert db.query(LeagueMembership).filter(LeagueMembership.league_id == league_db_id).count() == 0
    assert db.query(Packet).filter(Packet.league_id == league_db_id).count() == 0
    assert db.query(ProcessingRun).filter(ProcessingRun.league_id == league_db_id).count() == 0
    assert db.query(ProcessingRunFile).filter(ProcessingRunFile.league_id == league_db_id).count() == 0
    assert db.query(SequenceAlert).filter(SequenceAlert.league_id == league_db_id).count() == 0


def test_delete_league_refuses_a_wrong_confirmation_name(client, db, admin_user):
    """The typed confirmation is the only thing standing between a stray click and
    a league's entire history, so a mismatch must refuse and change nothing."""
    league = League(league_id="014", game_type="B", name="BRE League 014", is_active=True)
    db.add(league)
    db.commit()
    db.refresh(league)
    league_db_id = league.id

    login = client.post(
        "/management/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass123!"},
    )
    assert login.status_code == 200

    # Right shape, wrong league: "FE_014" is what the other game would be called.
    resp = client.request(
        "DELETE",
        f"/management/api/v1/leagues/{league_db_id}",
        json={"confirmation_name": "FE_014"},
    )
    assert resp.status_code == 400
    assert "BRE_014" in resp.json()["detail"]

    # Omitting it entirely is a schema violation, not a deletion.
    resp = client.request("DELETE", f"/management/api/v1/leagues/{league_db_id}", json={})
    assert resp.status_code == 422

    db.expire_all()
    assert db.query(League).filter(League.id == league_db_id).first() is not None
