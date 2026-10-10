"""Who owns a BBS, and the record of who changed what.

Ownership decides what a sysop sees, so only admins change it, and each change
is in the audit log. The log itself: written in the same transaction as the
change, readable only by admins, filterable by target and action.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app
from backend.core import rate_limiter
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.database import AuditEvent, Base, Client, ClientOwner, SysopUser

API = "/management/api/v1"


@pytest.fixture
def hub():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = Session()
    admin = SysopUser(username="admin", email="admin@hub.example", hashed_password="x",
                      is_superuser=True)
    alice = SysopUser(username="alice", email="alice@example.com", idp_subject="user_alice")
    bob = SysopUser(username="bob", email="bob@example.com", idp_subject="user_bob")
    board = Client(client_id="board", client_secret="x", bbs_name="The Board")
    db.add_all([admin, alice, bob, board])
    db.commit()

    acting = {"user": admin}

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    def current_user():
        session = Session()
        return session.get(SysopUser, acting["user"].id)

    saved = {a: dict(a.dependency_overrides) for a in (app, management_app)}
    for a in (app, management_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = current_user
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()

    def act_as(user):
        acting["user"] = user

    yield TestClient(app, base_url="https://testserver"), db, act_as, admin, alice, bob, board
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def actions(db) -> list[str]:
    db.expire_all()
    return [e.action for e in db.query(AuditEvent).order_by(AuditEvent.id)]


def test_an_admin_makes_a_sysop_an_owner(hub):
    client, db, _, _, alice, _, board = hub
    owners = client.post(f"{API}/clients/{board.id}/owners", json={"user_id": alice.id})
    assert owners.status_code == 200
    assert owners.json() == [{"user_id": alice.id, "username": "alice", "full_name": None,
                              "email": "alice@example.com"}]
    assert client.get(f"{API}/clients/{board.id}").json()["owners"][0]["username"] == "alice"
    users = {u["username"]: u for u in client.get(f"{API}/users").json()}
    assert users["alice"]["owned_clients"] == [{"id": board.id, "bbs_name": "The Board"}]
    assert users["alice"]["sso_linked"] is True and users["alice"]["has_password"] is False

    # Again: nothing changes and nothing more is logged.
    client.post(f"{API}/clients/{board.id}/owners", json={"user_id": alice.id})
    assert db.query(ClientOwner).count() == 1
    assert actions(db) == ["owner.added"]
    event = db.query(AuditEvent).one()
    assert (event.actor, event.target_type, event.target_id, event.target) == \
        ("admin", "client", board.id, "The Board")


def test_a_bbs_can_have_several_owners_and_lose_one(hub):
    client, db, _, _, alice, bob, board = hub
    client.post(f"{API}/clients/{board.id}/owners", json={"user_id": alice.id})
    client.post(f"{API}/clients/{board.id}/owners", json={"user_id": bob.id})
    left = client.delete(f"{API}/clients/{board.id}/owners/{alice.id}")
    assert [o["username"] for o in left.json()] == ["bob"]
    assert client.delete(f"{API}/clients/{board.id}/owners/{alice.id}").status_code == 404
    assert actions(db) == ["owner.added", "owner.added", "owner.removed"]


def test_unknown_bbs_or_user_is_404(hub):
    client, _, _, _, alice, _, board = hub
    assert client.post(f"{API}/clients/999/owners", json={"user_id": alice.id}).status_code == 404
    assert client.post(f"{API}/clients/{board.id}/owners", json={"user_id": 999}).status_code == 404


def test_only_admins_change_ownership_or_read_the_log(hub):
    client, db, act_as, _, alice, _, board = hub
    act_as(alice)
    assert client.post(f"{API}/clients/{board.id}/owners",
                       json={"user_id": alice.id}).status_code == 403
    assert client.delete(f"{API}/clients/{board.id}/owners/{alice.id}").status_code == 403
    assert client.get(f"{API}/audit").status_code == 403
    assert db.query(ClientOwner).count() == 0


def test_deleting_a_user_or_a_bbs_drops_their_ownerships(hub):
    client, db, _, _, alice, bob, board = hub
    other = Client(client_id="other", client_secret="x", bbs_name="Other")
    db.add(other)
    db.commit()
    client.post(f"{API}/clients/{board.id}/owners", json={"user_id": alice.id})
    client.post(f"{API}/clients/{other.id}/owners", json={"user_id": bob.id})

    assert client.delete(f"{API}/users/{alice.id}").status_code == 200
    assert client.delete(f"{API}/clients/{other.id}").status_code == 200
    assert db.query(ClientOwner).count() == 0
    db.expire_all()
    deleted = db.query(AuditEvent).filter(AuditEvent.action == "user.deleted").one()
    assert deleted.detail == "owned The Board"


def test_changes_to_a_bbs_are_logged_with_what_changed(hub):
    client, db, _, _, _, _, board = hub
    created = client.post(f"{API}/clients", json={"bbs_name": "New BBS", "client_id": "newbbs"})
    new_id = created.json()["id"]
    client.put(f"{API}/clients/{new_id}", json={"city": "Perth", "bbs_name": "New BBS"})
    client.put(f"{API}/clients/{new_id}", json={"city": "Perth"})  # no change, no entry
    client.post(f"{API}/clients/{new_id}/regenerate-secret")
    client.post(f"{API}/clients/{new_id}/ftn-addresses", json={"address": "1:2/3"})
    link = client.post(f"{API}/clients/{new_id}/claim-link").json()["url"]
    token = link.rsplit("/", 1)[1]
    assert client.post(f"{API}/claim/{token}").status_code == 200

    assert actions(db) == ["client.created", "client.updated", "client.secret_regenerated",
                           "ftn_address.assigned", "claim_link.issued", "claim_link.used"]
    updated = db.query(AuditEvent).filter(AuditEvent.action == "client.updated").one()
    assert updated.detail == "city: None -> 'Perth'"
    used = db.query(AuditEvent).filter(AuditEvent.action == "claim_link.used").one()
    assert used.actor is None and used.target == "New BBS" and used.ip


def test_role_changes_are_logged_and_an_admin_cannot_demote_themselves(hub):
    client, db, _, admin, alice, _, _ = hub
    assert client.put(f"{API}/users/{alice.id}", json={"is_admin": True}).status_code == 200
    assert client.put(f"{API}/users/{alice.id}", json={"is_admin": True}).status_code == 200
    assert client.put(f"{API}/users/{admin.id}", json={"is_admin": False}).status_code == 400
    assert actions(db) == ["user.role_changed"]
    assert db.query(AuditEvent).one().detail == "now admin"


def test_the_log_reads_newest_first_and_filters(hub):
    client, _, _, _, alice, bob, board = hub
    client.post(f"{API}/clients/{board.id}/owners", json={"user_id": alice.id})
    client.put(f"{API}/users/{bob.id}", json={"is_admin": True})
    client.delete(f"{API}/clients/{board.id}/owners/{alice.id}")

    everything = client.get(f"{API}/audit").json()
    assert [e["action"] for e in everything] == ["owner.removed", "user.role_changed",
                                                 "owner.added"]
    assert everything[0]["at"].endswith("Z") or everything[0]["at"].endswith("+00:00")

    on_board = client.get(f"{API}/audit", params={"target_type": "client",
                                                   "target_id": board.id}).json()
    assert [e["action"] for e in on_board] == ["owner.removed", "owner.added"]
    owners_only = client.get(f"{API}/audit", params={"action": "owner."}).json()
    assert len(owners_only) == 2
    older = client.get(f"{API}/audit", params={"before_id": everything[0]["id"],
                                                "limit": 1}).json()
    assert [e["action"] for e in older] == ["user.role_changed"]
