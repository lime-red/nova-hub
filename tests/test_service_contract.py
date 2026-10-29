"""The Service API's contract with clients written in other languages (#89).

Every time the hub stores is naive UTC, whatever the host's zone, and goes out
as RFC 3339 with its "Z". The token endpoint speaks enough RFC 6749 for a
stock OAuth2 library: HTTP Basic credentials, expires_in, no-store. And the
spec says what the binary endpoints really take and return.
"""
import ast
import base64
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app, service_app
from backend.core import rate_limiter
from backend.core.clock import utcnow
from backend.core.database import get_db
from backend.core.security import get_current_user, get_password_hash
from backend.models.database import (
    Base, Client, League, LeagueMembership, Packet, ProcessingRun, SysopUser,
)

ROOT = Path(__file__).parent.parent
TOKEN_URL = "/service/api/v1/auth/token"
PACKETS = "/service/api/v1/leagues/555B/packets"
SECRET = "the-secret"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = Session()
    admin = SysopUser(username="admin", hashed_password="x", is_superuser=True)
    board = Client(client_id="board", client_secret=get_password_hash(SECRET), bbs_name="The Board")
    league = League(league_id="555", game_type="B", name="BRE 555")
    db.add_all([admin, board, league])
    db.commit()
    db.add(LeagueMembership(client_id=board.id, league_id=league.id, bbs_index=2))
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
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()

    from backend.api.service import packets as packets_api

    class FakeConfig:
        def get(self, section, default=None):
            return {"data_dir": str(tmp_path)} if section == "server" else default

    monkeypatch.setattr(packets_api, "get_config", lambda: FakeConfig())

    yield TestClient(app, base_url="https://testserver"), db, league, tmp_path
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def _bearer(client):
    r = client.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                       "client_id": "board", "client_secret": SECRET})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def far_from_utc():
    """The host's zone fourteen hours east of UTC, where local and UTC differ by most."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"
    time.tzset()
    assert datetime.now() - utcnow() > timedelta(hours=13)
    yield
    if old is None:
        os.environ.pop("TZ")
    else:
        os.environ["TZ"] = old
    time.tzset()


# -- one clock ------------------------------------------------------------------

# Local wall time, on purpose: a filename and a log line read by a person.
LOCAL_TIME_OK = {
    "backend/services/dosemu_runner.py",
    "backend/services/game_reset/perform.py",
    "backend/api/management/game_reset.py",
}


def test_nothing_stored_or_compared_reads_the_hosts_local_clock():
    found = []
    for path in (ROOT / "backend").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in LOCAL_TIME_OK:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "now" and not node.args and not node.keywords
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "datetime"):
                found.append(f"{rel}:{node.lineno}")
    assert not found, f"datetime.now() is the host's local time; use utcnow(): {found}"


def test_a_download_is_stamped_in_utc_and_said_so(hub, far_from_utc):
    client, db, league, data_dir = hub
    (data_dir / "packets" / "outbound").mkdir(parents=True)
    (data_dir / "packets" / "outbound" / "555B0102.001").write_bytes(b"packet")
    db.add(Packet(filename="555B0102.001", league_id=league.id, source_bbs_index="01",
                  dest_bbs_index="02", sequence_number=1, file_size=6,
                  uploaded_at=utcnow() - timedelta(minutes=5),
                  processed_at=utcnow() - timedelta(minutes=4)))
    db.commit()
    headers = _bearer(client)

    assert client.get(f"{PACKETS}/555B0102.001", headers=headers).status_code == 200

    db.expire_all()
    stored = db.query(Packet).one().downloaded_at
    assert abs(stored - utcnow()) < timedelta(minutes=1)

    listed = client.get(PACKETS, headers=headers).json()["packets"][0]
    assert listed["received_at"].endswith("Z") and listed["retrieved_at"].endswith("Z")
    assert datetime.fromisoformat(listed["retrieved_at"]).replace(tzinfo=None) == stored
    assert listed["retrieved_at"] > listed["received_at"]


def test_a_run_never_lasts_negative_time(hub):
    client, db, _, _ = hub
    now = utcnow()
    db.add_all([ProcessingRun(started_at=now, completed_at=now - timedelta(seconds=3), status="completed"),
                ProcessingRun(started_at=now - timedelta(days=1, seconds=5), completed_at=now,
                              status="completed")])
    db.commit()

    runs = client.get("/management/api/v1/processing/runs").json()
    assert sorted(r["duration"] for r in runs) == ["0s", "86405s"]


# -- the token endpoint ---------------------------------------------------------

def test_a_token_says_how_long_it_lasts_and_is_not_cached(hub):
    client, *_ = hub
    r = client.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                       "client_id": "board", "client_secret": SECRET})
    assert r.status_code == 200
    assert r.json()["expires_in"] == 24 * 3600
    assert r.headers["cache-control"] == "no-store"


def test_credentials_may_come_as_http_basic(hub):
    client, *_ = hub
    r = client.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                    headers=_basic("board", SECRET))
    assert r.status_code == 200, r.text
    me = client.get("/service/api/v1/auth/verify",
                    headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["client_id"] == "board"


def test_basic_credentials_are_form_decoded_as_rfc_6749_says(hub):
    client, db, *_ = hub
    db.add(Client(client_id="a b", client_secret=get_password_hash("p:w%"), bbs_name="Spaced"))
    db.commit()
    r = client.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                    headers=_basic("a+b", "p%3Aw%25"))
    assert r.status_code == 200, r.text


def test_a_wrong_basic_secret_is_refused_like_a_wrong_form_one(hub):
    client, *_ = hub
    r = client.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                    headers=_basic("board", "wrong"))
    assert r.status_code == 401


@pytest.mark.parametrize("form, headers", [
    ({"client_id": "board", "client_secret": SECRET}, _basic("board", SECRET)),
    ({}, {}),
    ({"client_id": "board"}, {}),
])
def test_credentials_come_one_way_and_complete(hub, form, headers):
    client, *_ = hub
    r = client.post(TOKEN_URL, data={"grant_type": "client_credentials", **form}, headers=headers)
    assert r.status_code == 400


# -- the spec and the paths -----------------------------------------------------

def test_a_lowercase_league_id_is_the_same_league(hub):
    client, *_ = hub
    r = client.get("/service/api/v1/leagues/555b/packets", headers=_bearer(client))
    assert r.status_code == 200, r.text


def test_the_spec_says_the_packet_endpoints_carry_bytes():
    paths = service_app.openapi()["paths"]
    one = paths["/api/v1/leagues/{league_id}/packets/{filename}"]
    binary = {"type": "string", "format": "binary"}
    assert one["put"]["requestBody"]["content"]["application/octet-stream"]["schema"] == binary
    assert one["get"]["responses"]["200"]["content"] == {"application/octet-stream": {"schema": binary}}

    nodelist = paths["/api/v1/leagues/{league_id}/nodelist"]["get"]
    assert "application/octet-stream" in nodelist["responses"]["200"]["content"]
    assert "304" in nodelist["responses"]
    assert {"If-None-Match", "If-Modified-Since"} <= {p["name"] for p in nodelist["parameters"]}


def test_a_packet_is_listed_only_once_it_can_be_downloaded(hub):
    """Uploads land in hub inbound; processing puts them in outbound. A row
    listed before that is a download that 404s."""
    client, db, league, data_dir = hub
    db.add(Packet(filename="555B0302.001", league_id=league.id, source_bbs_index="03",
                  dest_bbs_index="02", sequence_number=1, file_size=6))
    db.commit()
    headers = _bearer(client)

    assert client.get(PACKETS, headers=headers).json()["packets"] == []

    (data_dir / "packets" / "outbound").mkdir(parents=True)
    (data_dir / "packets" / "outbound" / "555B0302.001").write_bytes(b"packet")
    db.query(Packet).one().processed_at = utcnow()
    db.commit()

    listed = client.get(PACKETS, headers=headers).json()["packets"]
    assert [p["filename"] for p in listed] == ["555B0302.001"]
    assert client.get(f"{PACKETS}/555B0302.001", headers=headers).status_code == 200
