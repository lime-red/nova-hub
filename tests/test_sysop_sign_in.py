"""Sysop sign-in through the identity provider, with a fake provider.

What matters: a new identity becomes a powerless sysop account, a known one
gets its own account back, an identity never takes over an existing account by
matching email, the state check ties the callback to the browser that started
it, every refusal lands on the login page with a reason and no session, and
the redirect afterwards never leaves the hub. The real adapter is exercised
against the WorkOS emulator in test_identity_emulator.py.
"""
import sys
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
from backend.models.database import AuditEvent, Base, SysopUser
from backend.services.identity_provider import (
    FakeIdentityProvider,
    Identity,
    get_identity_provider,
)

AUTH = "/management/api/v1/auth"
ALICE = Identity(subject="user_alice", email="alice@example.com", email_verified=True,
                 name="Alice Sysop")


@pytest.fixture
def hub():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    provider = FakeIdentityProvider(ALICE)
    saved = {a: dict(a.dependency_overrides) for a in (app, management_app)}
    for a in (app, management_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_identity_provider] = lambda: provider
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()

    db = Session()
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    yield client, db, provider
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()


def sign_in(client, next_path=None):
    """Start, follow the (fake) provider back to the callback. Returns the callback response."""
    params = {"next": next_path} if next_path else {}
    start = client.get(f"{AUTH}/sso/start", params=params)
    assert start.status_code == 302
    callback = urlparse(start.headers["location"])
    return client.get(callback.path, params={k: v[0] for k, v in parse_qs(callback.query).items()})


def error_of(response) -> str:
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert location.path == "/login"
    return parse_qs(location.query)["sso_error"][0]


def test_methods_say_whether_provider_sign_in_is_on(hub):
    client, _, _ = hub
    assert client.get(f"{AUTH}/methods").json() == {"password": True, "sso": True}
    management_app.dependency_overrides[get_identity_provider] = lambda: None
    assert client.get(f"{AUTH}/methods").json() == {"password": True, "sso": False}
    assert client.get(f"{AUTH}/sso/start").status_code == 404


def test_first_sign_in_creates_a_sysop_with_no_password_and_no_power(hub):
    client, db, _ = hub
    response = sign_in(client, "/clients")
    assert response.status_code == 302
    assert response.headers["location"] == "/clients"

    me = client.get(f"{AUTH}/me").json()
    assert me["username"] == "alice"
    assert me["email"] == "alice@example.com"
    assert me["full_name"] == "Alice Sysop"
    assert me["is_admin"] is False
    assert me["has_password"] is False
    assert me["sso_linked"] is True

    user = db.query(SysopUser).one()
    assert user.idp_subject == "user_alice"
    assert user.hashed_password is None
    assert user.owned_clients == []
    event = db.query(AuditEvent).one()
    assert (event.action, event.actor, event.target) == ("account.created", "alice", "alice")


def test_signing_in_again_returns_the_same_account_and_follows_an_email_change(hub):
    client, db, provider = hub
    sign_in(client)
    client.cookies.clear()
    provider.identity = Identity(subject="user_alice", email="alice@new.example",
                                 email_verified=True)
    sign_in(client)
    assert client.get(f"{AUTH}/me").json()["email"] == "alice@new.example"
    assert db.query(SysopUser).count() == 1
    assert [e.action for e in db.query(AuditEvent).order_by(AuditEvent.id)] == \
        ["account.created", "account.email_changed"]


def test_a_new_identity_never_takes_over_an_account_by_email(hub):
    client, db, _ = hub
    db.add(SysopUser(username="admin", email="alice@example.com",
                     hashed_password=get_password_hash("Correct-Horse-9"), is_superuser=True))
    db.commit()
    assert error_of(sign_in(client)) == "email_in_use"
    assert client.get(f"{AUTH}/me").status_code == 401
    admin = db.query(SysopUser).one()
    db.refresh(admin)
    assert admin.idp_subject is None


def test_usernames_do_not_collide(hub):
    client, db, _ = hub
    db.add(SysopUser(username="alice", hashed_password="x"))
    db.commit()
    sign_in(client)
    assert client.get(f"{AUTH}/me").json()["username"] == "alice2"


@pytest.mark.parametrize("identity, code", [
    (Identity("user_bob", "bob@example.com", email_verified=False), "unverified_email"),
    (None, "provider"),
])
def test_refused_identities_get_no_session(hub, identity, code):
    client, db, provider = hub
    provider.identity = identity
    assert error_of(sign_in(client)) == code
    assert client.get(f"{AUTH}/me").status_code == 401
    assert db.query(SysopUser).count() == 0


def test_a_disabled_account_cannot_sign_in(hub):
    client, db, _ = hub
    db.add(SysopUser(username="alice", email="alice@example.com", idp_subject="user_alice",
                     is_active=False))
    db.commit()
    assert error_of(sign_in(client)) == "inactive"
    assert client.get(f"{AUTH}/me").status_code == 401


def test_the_callback_needs_the_state_this_browser_started_with(hub):
    client, db, _ = hub
    start = client.get(f"{AUTH}/sso/start")
    query = parse_qs(urlparse(start.headers["location"]).query)

    forged = client.get(f"{AUTH}/sso/callback", params={"code": query["code"][0], "state": "x"})
    assert error_of(forged) == "state"

    client.cookies.clear()  # another browser: same URL, no state cookie
    elsewhere = client.get(f"{AUTH}/sso/callback",
                           params={"code": query["code"][0], "state": query["state"][0]})
    assert error_of(elsewhere) == "state"
    assert db.query(SysopUser).count() == 0


def test_a_cancelled_sign_in_goes_back_to_login(hub):
    client, _, _ = hub
    client.get(f"{AUTH}/sso/start")
    response = client.get(f"{AUTH}/sso/callback", params={"error": "access_denied", "state": "s"})
    assert error_of(response) == "cancelled"


@pytest.mark.parametrize("next_path", ["//evil.example/x", "https://evil.example/", "/\\evil",
                                       "dashboard"])
def test_the_redirect_afterwards_stays_on_the_hub(hub, next_path):
    client, _, _ = hub
    assert sign_in(client, next_path).headers["location"] == "/dashboard"


def test_a_provider_only_account_has_no_password_to_use_or_change(hub):
    client, _, _ = hub
    sign_in(client)
    response = client.post(f"{AUTH}/change-password", json={
        "current_password": "", "new_password": "Whatever-123", "confirm_password": "Whatever-123"})
    assert response.status_code == 400
    client.cookies.clear()
    response = client.post(f"{AUTH}/login", json={"username": "alice", "password": ""})
    assert response.status_code == 401
