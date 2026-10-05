"""Claim links: how a BBS gets its credentials without a secret going over chat.

The link is the credential, so what matters is what it can and cannot do: it
works once, it expires, a newer link kills an older one, opening it changes
nothing, the secret it produces is the one the service API then accepts (and
the old one stops working), and the token itself is never stored.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import toml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, service_app, management_app
from backend.core.database import get_db
from backend.core.security import get_current_user, get_password_hash, verify_password
from backend.models.database import (
    Base,
    ClaimLink,
    Client,
    FtnAddress,
    League,
    LeagueMembership,
    SysopUser,
)

CLIENTS = "/management/api/v1/clients"
CLAIM = "/management/api/v1/claim"
TOKEN_URL = "/service/api/v1/auth/token"


def _admin():
    return SysopUser(id=1, username="admin", hashed_password="x", is_superuser=True)


def _sysop():
    return SysopUser(id=2, username="sysop", hashed_password="x", is_superuser=False)


@pytest.fixture
def api():
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
        a.dependency_overrides[get_current_user] = _admin

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


@pytest.fixture
def bbs(api):
    """Jersey Jam: #5 in 015B and 015F, with an old secret it is currently using."""
    _, db = api
    client = Client(client_id="jersey", client_secret=get_password_hash("old-secret"),
                    bbs_name="Jersey Jam", is_active=True)
    bre = League(league_id="015", game_type="B", name="015B", is_active=True)
    fe = League(league_id="015", game_type="F", name="015F", is_active=True)
    db.add_all([client, bre, fe])
    db.commit()
    address = FtnAddress(client_id=client.id, address="135:135/5")
    db.add(address)
    db.commit()
    for league in (fe, bre):
        db.add(LeagueMembership(league_id=league.id, client_id=client.id, bbs_index=5,
                                ftn_address_id=address.id, is_active=True))
    db.commit()
    return client.id


def _issue(client, client_id):
    response = client.post(f"{CLIENTS}/{client_id}/claim-link")
    assert response.status_code == 200, response.text
    body = response.json()
    return body["url"].rsplit("/", 1)[1], body


def _secret_works(client, client_id, secret):
    response = client.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                              "client_id": client_id, "client_secret": secret})
    return response.status_code == 200


# -- issuing ------------------------------------------------------------------

def test_issue_gives_a_link_and_stores_only_its_hash(api, bbs):
    client, db = api

    token, body = _issue(client, bbs)

    assert body["url"] == f"https://testserver/claim/{token}"
    assert body["superseded"] == 0
    link = db.query(ClaimLink).one()
    assert token not in link.token_hash
    assert link.issued_by == "admin"
    expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    assert timedelta(hours=71) < expires.replace(tzinfo=None) - datetime.utcnow() <= timedelta(hours=72)


def test_issuing_changes_nothing_for_the_bbs(api, bbs):
    client, _ = api
    _issue(client, bbs)

    assert _secret_works(client, "jersey", "old-secret")


def test_a_new_link_supersedes_the_outstanding_one(api, bbs):
    client, _ = api
    first, _ = _issue(client, bbs)

    second, body = _issue(client, bbs)

    assert body["superseded"] == 1
    assert client.get(f"{CLAIM}/{first}").json()["state"] == "superseded"
    assert client.post(f"{CLAIM}/{first}").status_code == 409
    assert client.get(f"{CLAIM}/{second}").json()["state"] == "ready"


def test_admin_status_shows_the_latest_link_but_never_the_token(api, bbs):
    client, _ = api
    assert client.get(f"{CLIENTS}/{bbs}/claim-link").json() is None

    token, _ = _issue(client, bbs)
    status = client.get(f"{CLIENTS}/{bbs}/claim-link")

    assert status.json()["state"] == "ready"
    assert status.json()["issued_by"] == "admin"
    assert token not in status.text


@pytest.mark.parametrize("method", ["post", "get"])
def test_only_admins_issue_or_inspect_links(api, bbs, method):
    client, _ = api
    for a in (app, service_app, management_app):
        a.dependency_overrides[get_current_user] = _sysop

    response = getattr(client, method)(f"{CLIENTS}/{bbs}/claim-link")

    assert response.status_code == 403


# -- opening and claiming -----------------------------------------------------

def test_opening_the_link_changes_nothing(api, bbs):
    client, _ = api
    token, _ = _issue(client, bbs)

    for _ in range(3):  # a chat app's preview fetch, then the sysop, then again
        page = client.get(f"{CLAIM}/{token}")
        assert page.status_code == 200
        assert page.json()["state"] == "ready"
        assert page.json()["bbs_name"] == "Jersey Jam"
    assert _secret_works(client, "jersey", "old-secret")


def test_claiming_hands_over_a_secret_the_hub_then_accepts(api, bbs):
    client, db = api
    token, _ = _issue(client, bbs)

    result = client.post(f"{CLAIM}/{token}")

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["client_id"] == "jersey"
    assert _secret_works(client, "jersey", body["client_secret"])
    assert not _secret_works(client, "jersey", "old-secret")
    db.expire_all()
    stored = db.get(Client, bbs).client_secret
    assert body["client_secret"] not in stored and verify_password(body["client_secret"], stored)


def test_a_link_works_once_and_then_says_when_and_from_where(api, bbs):
    client, _ = api
    token, _ = _issue(client, bbs)
    secret = client.post(f"{CLAIM}/{token}").json()["client_secret"]

    again = client.post(f"{CLAIM}/{token}")
    page = client.get(f"{CLAIM}/{token}").json()

    assert again.status_code == 409
    assert "used" in again.json()["detail"]
    assert page["state"] == "used"
    assert page["used_at"] and page["used_ip"]
    assert _secret_works(client, "jersey", secret)  # the second attempt changed nothing


def test_an_expired_link_cannot_be_claimed(api, bbs):
    client, db = api
    token, _ = _issue(client, bbs)
    link = db.query(ClaimLink).one()
    link.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    assert client.get(f"{CLAIM}/{token}").json()["state"] == "expired"
    assert client.post(f"{CLAIM}/{token}").status_code == 409
    assert _secret_works(client, "jersey", "old-secret")


@pytest.mark.parametrize("token, status", [
    ("A" * 43, 404),  # well formed, never issued
    ("short", 422),
    ("has spaces in it and is long enough", 422),
])
def test_unknown_or_malformed_tokens(api, bbs, token, status):
    client, _ = api

    assert client.get(f"{CLAIM}/{token}").status_code == status
    assert client.post(f"{CLAIM}/{token}").status_code == status


def test_deleting_the_bbs_deletes_its_links(api, bbs):
    client, db = api
    _issue(client, bbs)
    # Deleting a BBS that is still in a league fails on its own account (a
    # pre-existing gap in delete_client); that is not what this is about.
    db.query(LeagueMembership).delete()
    db.commit()

    assert client.delete(f"{CLIENTS}/{bbs}").status_code == 200
    assert db.query(ClaimLink).count() == 0


# -- the config files ---------------------------------------------------------

def _claim(client, bbs):
    token, _ = _issue(client, bbs)
    return client.post(f"{CLAIM}/{token}").json()


def test_linux_config_is_the_python_clients_toml(api, bbs):
    client, _ = api
    body = _claim(client, bbs)

    config = toml.loads(body["config_toml"])

    assert config["hub"] == {"url": "https://testserver", "client_id": "jersey",
                             "client_secret": body["client_secret"]}
    assert config["bbs"]["name"] == "Jersey Jam"
    assert set(config["leagues"]) == {"BRE", "FE"}
    bre = config["leagues"]["BRE"]["015"]
    assert bre["bbs_index"] == 5
    assert bre["game_command"] == "BRE"
    assert "CHANGE-ME" in bre["inbound_dir"] and "CHANGE-ME" in bre["game_dos_path"]
    assert [l["game"] + l["number"] for l in body["leagues"]] == ["BRE015", "FE015"]


def test_windows_config_is_the_powershell_clients_psd1(api, bbs):
    client, _ = api
    body = _claim(client, bbs)

    psd1 = body["config_psd1"]

    assert "\r\n" in psd1
    assert f"ClientSecret = '{body['client_secret']}'" in psd1
    assert "Game            = 'FE'" in psd1
    assert "BbsIndex        = 5" in psd1
    assert "C:\\CHANGE-ME\\DOORS\\BRE015\\INBOUND" in psd1


def test_names_with_quotes_survive_both_formats(api, bbs):
    client, db = api
    db.get(Client, bbs).bbs_name = "Joe's \"Best\" BBS"
    db.commit()

    body = _claim(client, bbs)

    assert toml.loads(body["config_toml"])["bbs"]["name"] == "Joe's \"Best\" BBS"
    assert "Name = 'Joe''s \"Best\" BBS'" in body["config_psd1"]


def test_a_bbs_with_no_leagues_gets_a_config_without_any(api, bbs):
    client, db = api
    db.query(LeagueMembership).update({"is_active": False})
    db.commit()

    body = _claim(client, bbs)

    assert "leagues" not in toml.loads(body["config_toml"])
    assert body["leagues"] == []


def test_configured_public_url_is_used(api, bbs, monkeypatch):
    import importlib

    # backend.core.config the attribute is the Config object, not the module
    config_mod = importlib.import_module("backend.core.config")
    monkeypatch.setattr(config_mod, "get_config",
                        lambda: {"server": {"public_url": "https://hub.example.com/"}})
    client, _ = api

    token, body = _issue(client, bbs)
    result = client.post(f"{CLAIM}/{token}").json()

    assert body["url"] == f"https://hub.example.com/claim/{token}"
    assert result["hub_url"] == "https://hub.example.com"
