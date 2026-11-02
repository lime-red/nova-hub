"""Sysops apply, the admin approves: the self-service join flow.

What matters: anyone signed in can apply, but only once at a time and never
as a BBS that already exists; only admins see the queue or decide; approving
a join creates the BBS, its owner, its addresses and memberships all at once
or not at all, and hands out a claim link only the applicant can use; a
rejection's note reaches the sysop; a BBS's later league requests need no new
credentials; and the Verify step follows the node from approval to active.
"""
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

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
from backend.core.security import get_current_user, get_current_user_optional
from backend.models.database import (
    Application,
    AuditEvent,
    Base,
    ClaimLink,
    Client,
    ClientOwner,
    FtnAddress,
    League,
    LeagueMembership,
    SysopUser,
)
from backend.services import applications

API = "/management/api/v1"
APPS = f"{API}/applications"


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
    bre = League(league_id="015", game_type="B", name="BRE 015", is_active=True,
                 hub_fidonet_address="135:135/1")
    fe = League(league_id="013", game_type="F", name="FE 013", is_active=True)  # no hub address
    closed = League(league_id="900", game_type="B", name="Closed", is_active=False)
    eclipse = Client(client_id="eclipse", client_secret="x", bbs_name="The Eclipse")
    db.add_all([admin, alice, bob, bre, fe, closed, eclipse])
    db.flush()
    address = FtnAddress(client_id=eclipse.id, address="135:135/20")
    db.add(address)
    db.flush()
    db.add(LeagueMembership(client_id=eclipse.id, league_id=bre.id, bbs_index=1,
                            ftn_address_id=address.id, is_active=True))
    db.commit()

    acting = {"user": admin}

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    def current_user():
        if acting["user"] is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Not authenticated")
        return Session().get(SysopUser, acting["user"].id)

    def current_user_optional():
        return Session().get(SysopUser, acting["user"].id) if acting["user"] else None

    saved = {a: dict(a.dependency_overrides) for a in (app, management_app, service_app)}
    service_app.dependency_overrides[get_db] = override_get_db
    for a in (app, management_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = current_user
        a.dependency_overrides[get_current_user_optional] = current_user_optional
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()

    class Hub:
        pass

    h = Hub()
    h.client = TestClient(app, base_url="https://testserver")
    h.db, h.admin, h.alice, h.bob = db, admin, alice, bob
    h.bre, h.fe, h.closed, h.eclipse = bre, fe, closed, eclipse
    h.act_as = lambda user: acting.__setitem__("user", user)
    yield h
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()


JOIN = {
    "kind": "join",
    "bbs_name": "Jersey Jam",
    "sysop_name": "Alice",
    "city": "St Helier",
    "state": "Jersey",
    "country": "Channel Islands",
    "address": "telnet://jj.example:23",
    "os": "linux",
    "note": "Long-time BRE player.",
}


def apply(h, user=None, **overrides):
    h.act_as(user or h.alice)
    body = {**JOIN, "league_ids": [h.bre.id, h.fe.id], **overrides}
    return h.client.post(APPS, json=body)


def actions(db) -> list[str]:
    db.expire_all()
    return [e.action for e in db.query(AuditEvent).order_by(AuditEvent.id)]


def approve_join(h, application_id, **overrides):
    h.act_as(h.admin)
    body = {
        "client_id": "jersey_jam",
        "grants": [
            {"league_id": h.bre.id, "bbs_index": 5, "ftn_address": "135:135/50"},
            {"league_id": h.fe.id, "bbs_index": 5, "ftn_address": "135:135/50"},
        ],
        **overrides,
    }
    return h.client.post(f"{APPS}/{application_id}/approve", json=body)


# --- applying ------------------------------------------------------------------

def test_a_sysop_sees_only_open_leagues(hub):
    hub.act_as(hub.alice)
    leagues = hub.client.get(f"{APPS}/leagues").json()
    assert [(lg["full_id"], lg["game"]) for lg in leagues] == [("015B", "BRE"), ("013F", "FE")]


def test_a_sysop_applies_and_sees_their_application(hub):
    response = apply(hub)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["kind"] == "join"
    assert body["email"] == "alice@example.com"  # from the account, not the form
    assert [lg["full_id"] for lg in body["leagues"]] == ["013F", "015B"]

    mine = hub.client.get(f"{APPS}/mine").json()
    assert [a["id"] for a in mine] == [body["id"]]
    hub.act_as(hub.bob)
    assert hub.client.get(f"{APPS}/mine").json() == []
    assert actions(hub.db) == ["application.submitted"]


def test_one_waiting_join_per_account(hub):
    assert apply(hub).status_code == 200
    second = apply(hub, bbs_name="Another Board")
    assert second.status_code == 409
    assert "already have an application waiting" in second.json()["detail"]


def test_an_existing_bbs_name_is_refused(hub):
    response = apply(hub, bbs_name="the eclipse")
    assert response.status_code == 409
    assert "make you its owner" in response.json()["detail"]


@pytest.mark.parametrize("league_ids", [[], "closed", [999]])
def test_leagues_must_be_open(hub, league_ids):
    if league_ids == "closed":
        league_ids = [hub.closed.id]
    assert apply(hub, league_ids=league_ids).status_code == 409


def test_applying_needs_a_session(hub):
    hub.act_as(None)
    assert hub.client.post(APPS, json={**JOIN, "league_ids": [hub.bre.id]}).status_code == 401
    assert hub.client.get(f"{APPS}/leagues").status_code == 401


def test_a_sysop_withdraws_their_own_and_no_one_elses(hub):
    application = apply(hub).json()
    hub.act_as(hub.bob)
    assert hub.client.post(f"{APPS}/{application['id']}/withdraw").status_code == 404
    hub.act_as(hub.alice)
    withdrawn = hub.client.post(f"{APPS}/{application['id']}/withdraw")
    assert withdrawn.json()["status"] == "withdrawn"
    assert hub.client.post(f"{APPS}/{application['id']}/withdraw").status_code == 409
    assert apply(hub).status_code == 200  # free to apply again


# --- the queue -----------------------------------------------------------------

def test_only_admins_see_the_queue_or_decide(hub):
    application = apply(hub).json()
    for method, path in [("get", APPS), ("get", f"{APPS}/{application['id']}"),
                         ("post", f"{APPS}/{application['id']}/approve"),
                         ("post", f"{APPS}/{application['id']}/reject")]:
        response = getattr(hub.client, method)(path, **({"json": {"grants": []}}
                                                       if method == "post" else {}))
        assert response.status_code == 403, (method, path)


def test_the_review_suggests_an_id_a_free_index_and_shows_who_holds_what(hub):
    application = apply(hub).json()
    hub.act_as(hub.admin)
    assert [a["id"] for a in hub.client.get(APPS).json()] == [application["id"]]

    review = hub.client.get(f"{APPS}/{application['id']}").json()
    assert review["client_id_suggestion"] == "jersey_jam"
    bre = next(lg for lg in review["leagues"] if lg["full_id"] == "015B")
    assert bre["hub_address"] == "135:135/1"
    assert bre["taken"] == [{"bbs_index": 1, "bbs_name": "The Eclipse",
                             "ftn_address": "135:135/20"}]
    assert bre["bbs_index"] not in (1, None)


# --- approving a join ----------------------------------------------------------

def test_approval_gives_the_new_board_to_the_hubs_game(hub, tmp_path, monkeypatch):
    """Members get a nodelist only when the hub's game sends it, which it does
    after its own nodes file changes. Approval used to write just the hub-side
    copy, so a board added this way never reached anyone."""
    import backend.api.management.applications as applications_api

    from tests.conftest import install_game

    config = applications_api.get_config()
    patched = {"hub": config.get("hub", {}), "server": {"data_dir": str(tmp_path / "data")}}
    game = install_game(hub.db, patched, hub.bre, tmp_path / "drive_c")
    (game / "BrNodes.Dat").write_bytes(b"1 HOST\r\nNova Hub\r\n")
    monkeypatch.setattr(applications_api, "get_config", lambda: patched)

    application = apply(hub).json()
    response = approve_join(hub, application["id"])
    assert response.status_code == 200, response.text

    assert sorted(p.name for p in game.iterdir() if p.is_file()) == ["BrNodes.Dat"]
    nodes = (game / "BrNodes.Dat").read_bytes()
    assert nodes.startswith(b"1 HOST 5\r\n")  # Eclipse is at the hub's index here
    assert (tmp_path / "data" / "nodelists" / "bre" / "015" / "BRNODES.015").read_bytes() == nodes
    # 013F has no hub address, so nothing was generated for it to install.
    assert not any("no game" in p for p in response.json()["nodelist_problems"])


def test_approving_a_join_creates_the_bbs_owned_by_the_applicant(hub, tmp_path, monkeypatch):
    application = apply(hub).json()
    response = approve_join(hub, application["id"], bbs_name="Jersey Jam BBS")
    assert response.status_code == 200, response.text
    body = response.json()

    db = hub.db
    db.expire_all()
    client = db.get(Client, body["client_id"])
    assert (client.client_id, client.bbs_name) == ("jersey_jam", "Jersey Jam BBS")
    assert (client.city, client.state, client.country) == ("St Helier", "Jersey", "Channel Islands")
    assert [o.username for o in client.owners] == ["alice"]
    assert [a.address for a in client.ftn_addresses] == ["135:135/50"]
    memberships = sorted((m.league.full_id, m.bbs_index, m.fidonet_address)
                         for m in client.league_memberships)
    assert memberships == [("013F", 5, "135:135/50"), ("015B", 5, "135:135/50")]

    assert body["application"]["status"] == "approved"
    assert {g["league"] for g in body["application"]["granted"]} == {"015B", "013F"}
    # 013F has no hub address, so no nodelist; the approval stands anyway.
    assert any(p.startswith("013F") for p in body["nodelist_problems"])

    link = db.query(ClaimLink).one()
    assert link.user_id == hub.alice.id
    assert urlparse(body["claim_url"]).path.startswith("/claim/")
    assert actions(db) == ["application.submitted", "client.created", "owner.added",
                           "application.approved", "claim_link.issued"]


def test_an_approval_that_cannot_be_granted_changes_nothing(hub):
    application = apply(hub).json()
    # The second grant takes The Eclipse's index; the first was fine.
    response = approve_join(hub, application["id"], grants=[
        {"league_id": hub.fe.id, "bbs_index": 5, "ftn_address": "135:135/50"},
        {"league_id": hub.bre.id, "bbs_index": 1, "ftn_address": "135:135/50"},
    ])
    assert response.status_code == 409
    assert "The Eclipse" in response.json()["detail"]

    db = hub.db
    db.expire_all()
    assert db.query(Client).count() == 1
    assert db.query(FtnAddress).count() == 1
    assert db.query(ClientOwner).count() == 0
    assert db.query(ClaimLink).count() == 0
    assert db.get(Application, application["id"]).status == "pending"


@pytest.mark.parametrize("address, reason", [
    ("135:135/20", "belongs to The Eclipse"),
    ("135:135/1", "hub's own address"),
    ("not an address", "not an FTN address"),
])
def test_an_address_must_be_free_and_well_formed(hub, address, reason):
    application = apply(hub, league_ids=[hub.bre.id]).json()
    response = approve_join(hub, application["id"], grants=[
        {"league_id": hub.bre.id, "bbs_index": 5, "ftn_address": address}])
    assert response.status_code == 409
    assert reason in response.json()["detail"]


def test_a_taken_client_id_is_refused(hub):
    application = apply(hub).json()
    response = approve_join(hub, application["id"], client_id="eclipse")
    assert response.status_code == 409
    assert "already taken" in response.json()["detail"]


def test_an_application_is_decided_once(hub):
    application = apply(hub).json()
    assert approve_join(hub, application["id"]).status_code == 200
    assert approve_join(hub, application["id"]).status_code == 409
    assert hub.client.post(f"{APPS}/{application['id']}/reject", json={}).status_code == 409


# --- the bound claim link ------------------------------------------------------

def test_only_the_applicant_signed_in_can_claim(hub):
    application = apply(hub).json()
    path = urlparse(approve_join(hub, application["id"]).json()["claim_url"]).path
    api = f"{API}{path}"

    hub.act_as(None)
    status = hub.client.get(api).json()
    assert (status["requires_sign_in"], status["signed_in_as_owner"]) == (True, False)
    assert hub.client.post(api).status_code == 401

    hub.act_as(hub.bob)
    assert hub.client.post(api).status_code == 403

    hub.act_as(hub.alice)
    assert hub.client.get(api).json()["signed_in_as_owner"] is True
    claimed = hub.client.post(api)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["client_id"] == "jersey_jam"
    assert [(lg["game"], lg["number"]) for lg in claimed.json()["leagues"]] == [
        ("BRE", "015"), ("FE", "013")]


def test_the_applicant_collects_the_link_from_the_console(hub):
    application = apply(hub).json()
    approved = approve_join(hub, application["id"]).json()
    client_id = approved["client_id"]
    original = f"{API}{urlparse(approved['claim_url']).path}"

    hub.act_as(hub.alice)
    detail = hub.client.get(f"{API}/clients/{client_id}").json()
    assert (detail["stage"], detail["claim_waiting"]) == ("approved", True)

    collected = hub.client.post(f"{API}/clients/{client_id}/claim-link/collect")
    assert collected.status_code == 200
    assert collected.json()["expires_at"] == approved["claim_expires_at"]
    fresh = f"{API}{urlparse(collected.json()['url']).path}"
    assert hub.client.get(original).json()["state"] == "superseded"
    assert hub.client.post(fresh).status_code == 200

    # Used: nothing left to collect, so no way to rotate the secret from here.
    assert hub.client.post(f"{API}/clients/{client_id}/claim-link/collect").status_code == 404
    assert hub.client.get(f"{API}/clients/{client_id}").json()["stage"] == "claimed"


def test_someone_else_cannot_collect(hub):
    application = apply(hub).json()
    client_id = approve_join(hub, application["id"]).json()["client_id"]
    hub.act_as(hub.bob)
    assert hub.client.post(f"{API}/clients/{client_id}/claim-link/collect").status_code == 404


def test_an_admin_binds_a_link_to_an_owner_only(hub):
    hub.db.add(ClientOwner(sysop_user_id=hub.alice.id, client_id=hub.eclipse.id))
    hub.db.commit()
    hub.act_as(hub.admin)
    url = f"{API}/clients/{hub.eclipse.id}/claim-link"
    assert hub.client.post(url, json={"user_id": hub.bob.id}).status_code == 400
    assert hub.client.post(url, json={"user_id": hub.alice.id}).status_code == 200
    assert hub.client.get(url).json()["bound_to"] == "alice"
    assert hub.client.post(url).status_code == 200  # no body: unbound, as before
    assert hub.client.get(url).json()["bound_to"] is None


# --- rejecting -----------------------------------------------------------------

def test_a_rejection_note_reaches_the_sysop(hub):
    application = apply(hub).json()
    hub.act_as(hub.admin)
    rejected = hub.client.post(f"{APPS}/{application['id']}/reject",
                               json={"note": "Leagues are full until November."})
    assert rejected.json()["status"] == "rejected"

    hub.act_as(hub.alice)
    mine = hub.client.get(f"{APPS}/mine").json()[0]
    assert (mine["status"], mine["decision_note"], mine["reviewed_by"]) == (
        "rejected", "Leagues are full until November.", "admin")
    assert apply(hub).status_code == 200  # may apply again
    assert "application.rejected" in actions(hub.db)


# --- more leagues for a BBS that is already here -------------------------------

def _own_eclipse(h):
    h.db.add(ClientOwner(sysop_user_id=h.alice.id, client_id=h.eclipse.id))
    h.db.commit()


def test_an_owner_asks_for_more_leagues_and_keeps_their_index(hub):
    _own_eclipse(hub)
    hub.act_as(hub.alice)
    request = hub.client.post(APPS, json={"kind": "leagues", "client_id": hub.eclipse.id,
                                          "league_ids": [hub.fe.id], "note": "FE too please"})
    assert request.status_code == 200, request.text
    application_id = request.json()["id"]

    hub.act_as(hub.admin)
    review = hub.client.get(f"{APPS}/{application_id}").json()
    assert review["client_id_suggestion"] is None
    assert review["client_addresses"] == ["135:135/20"]
    fe = review["leagues"][0]
    assert (fe["bbs_index"], fe["ftn_address"]) == (1, "135:135/20")

    approved = hub.client.post(f"{APPS}/{application_id}/approve", json={"grants": [
        {"league_id": hub.fe.id, "bbs_index": 1, "ftn_address": "135:135/20"}]}).json()
    assert approved["claim_url"] is None  # same BBS, same credentials
    hub.db.expire_all()
    assert sorted(m.league.full_id for m in hub.eclipse.league_memberships) == ["013F", "015B"]
    assert hub.db.query(ClaimLink).count() == 0


def test_league_requests_are_for_your_own_bbs_and_new_leagues(hub):
    hub.act_as(hub.alice)
    not_mine = hub.client.post(APPS, json={"kind": "leagues", "client_id": hub.eclipse.id,
                                           "league_ids": [hub.fe.id]})
    assert not_mine.status_code == 409

    _own_eclipse(hub)
    already = hub.client.post(APPS, json={"kind": "leagues", "client_id": hub.eclipse.id,
                                          "league_ids": [hub.bre.id]})
    assert already.status_code == 409
    assert "already in" in already.json()["detail"]

    ok = {"kind": "leagues", "client_id": hub.eclipse.id, "league_ids": [hub.fe.id]}
    assert hub.client.post(APPS, json=ok).status_code == 200
    assert hub.client.post(APPS, json=ok).status_code == 409  # one waiting per BBS


# --- the Verify step -----------------------------------------------------------

def test_the_stage_follows_the_node(hub):
    application = apply(hub).json()
    approved = approve_join(hub, application["id"]).json()
    db = hub.db
    db.expire_all()
    client = db.get(Client, approved["client_id"])
    assert applications.stage(db, client) == "approved"

    hub.act_as(hub.alice)
    secret = hub.client.post(f"{API}{urlparse(approved['claim_url']).path}").json()["client_secret"]
    db.expire_all()
    assert applications.stage(db, client) == "claimed"

    token = hub.client.post("/service/api/v1/auth/token", data={
        "grant_type": "client_credentials", "client_id": "jersey_jam", "client_secret": secret})
    assert token.status_code == 200, token.text
    db.expire_all()
    assert client.first_token_at is not None
    assert applications.stage(db, client) == "first_token"

    client.first_upload_at = utcnow()
    db.commit()
    assert applications.stage(db, client) == "active"
    assert applications.stage(db, client, now=utcnow() + timedelta(days=3)) == "first_upload"
