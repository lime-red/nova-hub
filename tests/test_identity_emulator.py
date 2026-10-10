"""The real WorkOS adapter, against the WorkOS CLI's local emulator.

test_sysop_sign_in.py covers the hub's decisions with a fake provider; this
covers the adapter itself: the authorize URL WorkOS is sent to, the redirect
back with a code, and the code exchange, over HTTP, with no WorkOS account.
The emulator serves the same endpoints as api.workos.com.

Needs npx (Node.js) and the npm registry. Without npx the tests skip, unless
NOVA_REQUIRE_EMULATOR=1 is set, as CI sets it, so that a runner losing Node
fails the build instead of quietly passing.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app
from backend.core import rate_limiter
from backend.core.database import get_db
from backend.models.database import Base, SysopUser
from backend.services.identity_provider import WorkOSProvider, get_identity_provider

WORKOS_CLI = "workos@0.23.0"
API_KEY = "sk_test_default"  # the emulator's fixed key
CLIENT_ID = "client_emulator"
AUTH = "/management/api/v1/auth"

if not shutil.which("npx"):
    if os.environ.get("NOVA_REQUIRE_EMULATOR") == "1":
        raise RuntimeError("NOVA_REQUIRE_EMULATOR=1 but npx is not on PATH")
    pytest.skip("npx not available for the WorkOS emulator", allow_module_level=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def emulator():
    port = _free_port()
    process = subprocess.Popen(
        ["npx", "-y", WORKOS_CLI, "emulate", "--port", str(port), "--json"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 120  # first run downloads the CLI
        while True:
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                output = process.stdout.read().decode(errors="replace") if process.stdout else ""
                raise RuntimeError(f"WorkOS emulator did not start:\n{output[-2000:]}")
            time.sleep(0.5)
        yield base
    finally:
        # npx runs the CLI as a child; take the whole group down.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def make_user(emulator: str, email: str, verified: bool = True) -> str:
    response = httpx.post(f"{emulator}/user_management/users",
                          headers={"Authorization": f"Bearer {API_KEY}"},
                          json={"email": email, "email_verified": verified,
                                "first_name": "Emu", "last_name": "Sysop"})
    response.raise_for_status()
    return response.json()["id"]


@pytest.fixture
def hub(emulator):
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

    provider = WorkOSProvider(client_id=CLIENT_ID, api_key=API_KEY, api_base=emulator)
    saved = {a: dict(a.dependency_overrides) for a in (app, management_app)}
    for a in (app, management_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_identity_provider] = lambda: provider
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()

    db = Session()
    # localhost: the emulator redirects nowhere else.
    yield TestClient(app, base_url="https://localhost", follow_redirects=False), db
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def through_workos(client: TestClient, email: str) -> tuple[str, dict]:
    """Start sign-in at the hub and let the emulator answer. Returns the callback path and query."""
    start = client.get(f"{AUTH}/sso/start", params={"next": "/clients"})
    assert start.status_code == 302
    authorize = start.headers["location"]
    query = parse_qs(urlparse(authorize).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["provider"] == ["authkit"]
    assert query["redirect_uri"] == ["https://localhost/management/api/v1/auth/sso/callback"]

    # The emulator signs in the user login_hint names (else its first user)
    # and redirects back, as AuthKit would once they had signed in.
    back = httpx.get(authorize, params={"login_hint": email}, follow_redirects=False)
    assert back.status_code == 302
    callback = urlparse(back.headers["location"])
    assert callback.path == "/management/api/v1/auth/sso/callback"
    return callback.path, {k: v[0] for k, v in parse_qs(callback.query).items()}


def test_sign_in_through_the_emulator(hub, emulator):
    client, db = hub
    subject = make_user(emulator, "emu.sysop@example.com")

    path, query = through_workos(client, "emu.sysop@example.com")
    assert "code" in query
    state_cookie = client.cookies.get("nova_hub_sso")
    landed = client.get(path, params=query)
    assert landed.status_code == 302
    assert landed.headers["location"] == "/clients"

    me = client.get(f"{AUTH}/me").json()
    assert (me["email"], me["full_name"], me["is_admin"]) == \
        ("emu.sysop@example.com", "Emu Sysop", False)
    assert db.query(SysopUser).one().idp_subject == subject

    # A code works once. Replay the whole callback, state cookie included, so
    # the only thing wrong with it is the spent code: WorkOS refuses it.
    client.cookies.clear()
    client.cookies.set("nova_hub_sso", state_cookie, domain="localhost.local",
                       path="/management/api/v1/auth/sso")
    replay = client.get(path, params=query)
    assert replay.headers["location"] == "/login?sso_error=provider"
    assert client.get(f"{AUTH}/me").status_code == 401


def test_an_unverified_email_is_refused(hub, emulator):
    client, db = hub
    make_user(emulator, "unverified@example.com", verified=False)
    path, query = through_workos(client, "unverified@example.com")
    landed = client.get(path, params=query)
    assert landed.headers["location"] == "/login?sso_error=unverified_email"
    assert db.query(SysopUser).count() == 0


def test_a_bad_code_is_a_provider_error(hub, emulator):
    client, _ = hub
    start = client.get(f"{AUTH}/sso/start")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    landed = client.get(f"{AUTH}/sso/callback", params={"code": "auth_code_bogus", "state": state})
    assert landed.headers["location"] == "/login?sso_error=provider"
