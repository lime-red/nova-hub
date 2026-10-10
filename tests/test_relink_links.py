"""Re-link links: connecting a provider sign-in to an account that already exists.

The admin's own account and hand-made sysop accounts predate provider
sign-in, and email never links an identity to them. A re-link link does, once,
for the account it names: it binds whatever identity signs in through it,
replacing a lost one. It does nothing until that sign-in completes, cannot be
used twice, and cannot steal an identity another account holds.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app
from backend.core import rate_limiter
from backend.core.database import get_db
from backend.core.security import get_password_hash
from backend.models.database import AuditEvent, Base, RelinkLink, SysopUser
from backend.services.identity_provider import (
    FakeIdentityProvider,
    Identity,
    get_identity_provider,
)

API = "/management/api/v1"
AUTH = f"{API}/auth"
PASSWORD = "Break-Glass-2026"
ADMIN_ID = Identity("user_admin_new", "admin@hub.example", email_verified=True)


@pytest.fixture
def hub(monkeypatch):
    import importlib

    # Links are built from the request, whatever public_url the host config has.
    config_mod = importlib.import_module("backend.core.config")
    real = config_mod.get_config()
    monkeypatch.setattr(real, "_raw", {**real._raw, "server": {}})

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()
    db.add_all([
        SysopUser(username="admin", hashed_password=get_password_hash(PASSWORD),
                  is_superuser=True),
        SysopUser(username="alice", email="alice@old.example", idp_subject="user_alice_old"),
    ])
    db.commit()

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    provider = FakeIdentityProvider(ADMIN_ID)
    saved = {a: dict(a.dependency_overrides) for a in (app, management_app)}
    for a in (app, management_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_identity_provider] = lambda: provider
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()

    yield TestClient(app, base_url="https://testserver", follow_redirects=False), db, provider
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()


def as_admin(client):
    client.cookies.clear()
    assert client.post(f"{AUTH}/login", json={"username": "admin", "password": PASSWORD}) \
        .status_code == 200


def user(db, name) -> SysopUser:
    db.expire_all()
    return db.query(SysopUser).filter(SysopUser.username == name).one()


def issue(client, db, name) -> str:
    as_admin(client)
    response = client.post(f"{API}/users/{user(db, name).id}/relink-link")
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    assert url.startswith("https://testserver/relink/")
    client.cookies.clear()
    return url.rsplit("/", 1)[1]


def sign_in(client, relink=None):
    """Through the fake provider and back. Returns where the callback sends the browser."""
    start = client.get(f"{AUTH}/sso/start", params={"relink": relink} if relink else {})
    assert start.status_code == 302
    location = urlparse(start.headers["location"])
    if location.path == "/login":
        return start.headers["location"]
    back = client.get(location.path, params={k: v[0] for k, v in parse_qs(location.query).items()})
    return back.headers["location"]


def test_the_admin_connects_their_own_account(hub):
    client, db, _ = hub
    token = issue(client, db, "admin")
    page = client.get(f"{API}/relink/{token}").json()
    assert (page["username"], page["state"], page["has_sign_in"]) == ("admin", "ready", False)
    assert user(db, "admin").idp_subject is None  # opening the page changes nothing

    assert sign_in(client, token) == "/dashboard"
    me = client.get(f"{AUTH}/me").json()
    assert (me["username"], me["is_admin"], me["sso_linked"], me["has_password"]) == \
        ("admin", True, True, True)
    assert user(db, "admin").email == "admin@hub.example"
    assert client.get(f"{API}/relink/{token}").json()["state"] == "used"

    # From now on a plain provider sign-in is the admin.
    client.cookies.clear()
    assert sign_in(client) == "/dashboard"
    assert client.get(f"{AUTH}/me").json()["username"] == "admin"
    assert db.query(SysopUser).count() == 2
    actions = [e.action for e in db.query(AuditEvent).order_by(AuditEvent.id)]
    assert actions == ["relink_link.issued", "account.linked"]


def test_a_sysop_who_lost_their_email_gets_a_new_sign_in(hub):
    client, db, provider = hub
    token = issue(client, db, "alice")
    provider.identity = Identity("user_alice_new", "alice@new.example", email_verified=True)
    assert sign_in(client, token) == "/dashboard"
    alice = user(db, "alice")
    assert (alice.idp_subject, alice.email) == ("user_alice_new", "alice@new.example")
    linked = db.query(AuditEvent).filter(AuditEvent.action == "account.linked").one()
    assert linked.detail == "signs in as alice@new.example; replaced the previous sign-in"


def test_a_link_works_once(hub):
    client, db, provider = hub
    token = issue(client, db, "alice")
    provider.identity = Identity("user_alice_new", "alice@new.example", email_verified=True)
    sign_in(client, token)
    client.cookies.clear()
    provider.identity = Identity("user_mallory", "mallory@example.com", email_verified=True)
    assert sign_in(client, token) == "/login?sso_error=relink_used"
    assert user(db, "alice").idp_subject == "user_alice_new"


def test_a_newer_link_or_time_kills_an_older_one(hub):
    client, db, _ = hub
    first = issue(client, db, "admin")
    second = issue(client, db, "admin")
    assert client.get(f"{API}/relink/{first}").json()["state"] == "superseded"
    assert sign_in(client, first) == "/login?sso_error=relink_used"

    link = db.query(RelinkLink).filter(RelinkLink.superseded_at.is_(None)).one()
    link.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get(f"{API}/relink/{second}").json()["state"] == "expired"
    assert sign_in(client, second) == "/login?sso_error=relink_used"
    assert user(db, "admin").idp_subject is None


def test_an_identity_another_account_holds_is_not_taken_and_the_link_survives(hub):
    client, db, provider = hub
    token = issue(client, db, "admin")
    provider.identity = Identity("user_alice_old", "alice@old.example", email_verified=True)
    assert sign_in(client, token) == "/login?sso_error=identity_in_use"
    assert user(db, "admin").idp_subject is None
    assert user(db, "alice").idp_subject == "user_alice_old"
    assert client.get(f"{API}/relink/{token}").json()["state"] == "ready"


def test_an_unknown_link_is_a_failed_attempt(hub):
    client, _, _ = hub
    bogus = "x" * 43
    assert client.get(f"{API}/relink/{bogus}").status_code == 404
    assert sign_in(client, bogus) == "/login?sso_error=relink_invalid"


def test_unlinking_shuts_the_provider_door_until_a_new_link(hub):
    client, db, provider = hub
    as_admin(client)
    alice_id = user(db, "alice").id
    response = client.post(f"{API}/users/{alice_id}/unlink")
    assert response.status_code == 200 and response.json()["sso_linked"] is False
    assert client.post(f"{API}/users/{alice_id}/unlink").status_code == 400

    # Her old identity is now unknown, and her email is taken by her own
    # account, so signing in makes no second account: it is refused.
    client.cookies.clear()
    provider.identity = Identity("user_alice_old", "alice@old.example", email_verified=True)
    assert sign_in(client) == "/login?sso_error=email_in_use"
    assert db.query(SysopUser).count() == 2
    assert "account.unlinked" in [e.action for e in db.query(AuditEvent)]


def test_an_admin_cannot_unlink_their_only_way_in(hub):
    client, db, _ = hub
    admin = user(db, "admin")
    admin.hashed_password = None
    admin.idp_subject = "user_admin"
    db.commit()
    provider_admin = Identity("user_admin", "admin@hub.example", email_verified=True)
    client.app.dependency_overrides[get_identity_provider] = \
        lambda: FakeIdentityProvider(provider_admin)
    management_app.dependency_overrides[get_identity_provider] = \
        lambda: FakeIdentityProvider(provider_admin)
    assert sign_in(client) == "/dashboard"
    assert client.post(f"{API}/users/{admin.id}/unlink").status_code == 400


def test_link_status_for_the_admin(hub):
    client, db, _ = hub
    as_admin(client)
    alice_id = user(db, "alice").id
    assert client.get(f"{API}/users/{alice_id}/relink-link").json() is None
    issue(client, db, "alice")
    as_admin(client)
    status = client.get(f"{API}/users/{alice_id}/relink-link").json()
    assert (status["state"], status["issued_by"]) == ("ready", "admin")
    assert "token" not in status and "url" not in status
