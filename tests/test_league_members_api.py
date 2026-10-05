"""League memberships and the FTN addresses they use.

An FTN address belongs to a BBS and is unique across the hub; a membership
points at one of its own BBS's addresses. That is what stops one address
going to two BBSes in two leagues -- which happened in production while the
address was free text on each membership. Edits validate everything before
writing anything, so a rejected edit leaves the row untouched.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, service_app, management_app
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.database import (
    Base,
    Client,
    FtnAddress,
    League,
    LeagueMembership,
    SysopUser,
)

LEAGUES = "/management/api/v1/leagues"
CLIENTS = "/management/api/v1/clients"


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


def _as_sysop():
    for a in (app, service_app, management_app):
        a.dependency_overrides[get_current_user] = _sysop


@pytest.fixture
def world(api):
    """League 900B: Alpha #2 at 135:135/20 (also holds unused /8), Beta #5 at /21.

    Returns a dict of ids.
    """
    _, db = api
    league = League(league_id="900", game_type="B", name="Test 900B", is_active=True)
    alpha = Client(client_id="alpha", client_secret="x", bbs_name="Alpha")
    beta = Client(client_id="beta", client_secret="x", bbs_name="Beta")
    db.add_all([league, alpha, beta])
    db.commit()
    a20 = FtnAddress(client_id=alpha.id, address="135:135/20")
    a8 = FtnAddress(client_id=alpha.id, address="135:135/8")
    b21 = FtnAddress(client_id=beta.id, address="135:135/21")
    db.add_all([a20, a8, b21])
    db.commit()
    ma = LeagueMembership(league_id=league.id, client_id=alpha.id, bbs_index=2,
                          ftn_address_id=a20.id, is_active=True)
    mb = LeagueMembership(league_id=league.id, client_id=beta.id, bbs_index=5,
                          ftn_address_id=b21.id, is_active=True)
    db.add_all([ma, mb])
    db.commit()
    return dict(league=league.id, alpha=alpha.id, beta=beta.id,
                a20=a20.id, a8=a8.id, b21=b21.id, ma=ma.id, mb=mb.id)


def _member(db, membership_id):
    db.expire_all()
    m = db.get(LeagueMembership, membership_id)
    return m.bbs_index, m.fidonet_address


# -- memberships ------------------------------------------------------------

def test_edit_index_and_address(api, world):
    client, db = api

    r = client.patch(f"{LEAGUES}/{world['league']}/members/{world['ma']}",
                     json={"bbs_index": 3, "ftn_address_id": world["a8"]})

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["bbs_index"], body["fidonet_address"]) == (3, "135:135/8")
    assert [a["address"] for a in body["client_ftn_addresses"]] == ["135:135/20", "135:135/8"]
    assert _member(db, world["ma"]) == (3, "135:135/8")


def test_edit_one_field_leaves_the_other(api, world):
    client, db = api

    r = client.patch(f"{LEAGUES}/{world['league']}/members/{world['ma']}",
                     json={"ftn_address_id": world["a8"]})

    assert r.status_code == 200, r.text
    assert _member(db, world["ma"]) == (2, "135:135/8")


def test_resaving_own_values_is_not_a_clash(api, world):
    client, _ = api

    r = client.patch(f"{LEAGUES}/{world['league']}/members/{world['ma']}",
                     json={"bbs_index": 2, "ftn_address_id": world["a20"]})

    assert r.status_code == 200, r.text


def test_cannot_use_another_bbss_address(api, world):
    """The structural fix: Beta's address is not Alpha's to use."""
    client, db = api

    r = client.patch(f"{LEAGUES}/{world['league']}/members/{world['ma']}",
                     json={"ftn_address_id": world["b21"]})

    assert r.status_code == 400
    assert r.json()["detail"] == "135:135/21 belongs to Beta, not Alpha"
    assert _member(db, world["ma"]) == (2, "135:135/20")


def test_index_clash_names_the_holder(api, world):
    client, db = api

    r = client.patch(f"{LEAGUES}/{world['league']}/members/{world['ma']}",
                     json={"bbs_index": 5})

    assert r.status_code == 400
    assert "BBS ID 5 is already assigned to Beta" in r.json()["detail"]
    assert _member(db, world["ma"]) == (2, "135:135/20")


def test_rejected_edit_writes_nothing(api, world):
    """A valid address with a clashing index must not half-apply."""
    client, db = api

    r = client.patch(f"{LEAGUES}/{world['league']}/members/{world['ma']}",
                     json={"bbs_index": 5, "ftn_address_id": world["a8"]})

    assert r.status_code == 400
    assert _member(db, world["ma"]) == (2, "135:135/20")


@pytest.mark.parametrize("payload", [{"bbs_index": 0}, {"bbs_index": 256},
                                     {"ftn_address_id": 9999}, {}])
def test_invalid_edit_is_refused(api, world, payload):
    client, db = api

    r = client.patch(f"{LEAGUES}/{world['league']}/members/{world['ma']}", json=payload)

    assert r.status_code == 400
    assert _member(db, world["ma"]) == (2, "135:135/20")


def test_inactive_member_does_not_hold_its_index(api, world):
    client, db = api
    db.get(LeagueMembership, world["mb"]).is_active = False
    db.commit()

    r = client.patch(f"{LEAGUES}/{world['league']}/members/{world['ma']}", json={"bbs_index": 5})

    assert r.status_code == 200, r.text


def test_membership_must_belong_to_league(api, world):
    client, _ = api

    r = client.patch(f"{LEAGUES}/{world['league'] + 1}/members/{world['ma']}",
                     json={"bbs_index": 9})

    assert r.status_code == 404


def test_add_member_uses_one_of_its_own_addresses(api, world):
    client, db = api
    gamma = Client(client_id="gamma", client_secret="x", bbs_name="Gamma")
    db.add(gamma)
    db.commit()
    g30 = FtnAddress(client_id=gamma.id, address="135:135/30")
    db.add(g30)
    db.commit()

    refused = client.post(f"{LEAGUES}/{world['league']}/members",
                          json={"client_id": gamma.id, "bbs_index": 7, "ftn_address_id": world["a20"]})
    added = client.post(f"{LEAGUES}/{world['league']}/members",
                        json={"client_id": gamma.id, "bbs_index": 7, "ftn_address_id": g30.id})

    assert refused.status_code == 400
    assert "belongs to Alpha, not Gamma" in refused.json()["detail"]
    assert added.status_code == 200, added.text
    assert added.json()["fidonet_address"] == "135:135/30"


def test_league_detail_offers_each_clients_addresses(api, world):
    client, db = api
    gamma = Client(client_id="gamma", client_secret="x", bbs_name="Gamma", is_active=True)
    db.add(gamma)
    db.commit()
    db.add(FtnAddress(client_id=gamma.id, address="135:135/30"))
    db.commit()

    body = client.get(f"{LEAGUES}/{world['league']}").json()

    alpha = next(m for m in body["members"] if m["bbs_name"] == "Alpha")
    assert alpha["fidonet_address"] == "135:135/20"
    assert [a["address"] for a in alpha["client_ftn_addresses"]] == ["135:135/20", "135:135/8"]
    offered = next(c for c in body["available_clients"] if c["bbs_name"] == "Gamma")
    assert [a["address"] for a in offered["ftn_addresses"]] == ["135:135/30"]


# -- addresses on the BBS ---------------------------------------------------

def test_assign_address(api, world):
    client, _ = api

    r = client.post(f"{CLIENTS}/{world['beta']}/ftn-addresses", json={"address": " 13:10/105 "})

    assert r.status_code == 200, r.text
    assert r.json() == {"id": r.json()["id"], "address": "13:10/105", "leagues": []}


@pytest.mark.parametrize("address, detail", [
    ("135:135/20", "135:135/20 already belongs to Alpha"),
    ("135/20", "Invalid FTN address format"),
])
def test_assign_refuses_taken_or_malformed(api, world, address, detail):
    client, _ = api

    r = client.post(f"{CLIENTS}/{world['beta']}/ftn-addresses", json={"address": address})

    assert r.status_code == 400
    assert detail in r.json()["detail"]


def test_renumber_carries_its_memberships(api, world):
    client, db = api

    r = client.put(f"{CLIENTS}/{world['alpha']}/ftn-addresses/{world['a20']}",
                   json={"address": "135:135/22"})

    assert r.status_code == 200, r.text
    assert r.json()["leagues"] == ["900B"]
    assert _member(db, world["ma"]) == (2, "135:135/22")


def test_renumber_onto_a_taken_address_is_refused(api, world):
    client, db = api

    r = client.put(f"{CLIENTS}/{world['alpha']}/ftn-addresses/{world['a20']}",
                   json={"address": "135:135/21"})

    assert r.status_code == 400
    assert _member(db, world["ma"]) == (2, "135:135/20")


def test_address_in_use_cannot_be_removed(api, world):
    client, _ = api

    in_use = client.delete(f"{CLIENTS}/{world['alpha']}/ftn-addresses/{world['a20']}")
    unused = client.delete(f"{CLIENTS}/{world['alpha']}/ftn-addresses/{world['a8']}")

    assert in_use.status_code == 400
    assert "still used in 900B" in in_use.json()["detail"]
    assert unused.status_code == 200, unused.text


def test_address_must_be_this_clients(api, world):
    client, _ = api

    r = client.delete(f"{CLIENTS}/{world['beta']}/ftn-addresses/{world['a8']}")

    assert r.status_code == 404


def test_client_detail_lists_addresses_and_their_leagues(api, world):
    client, _ = api

    body = client.get(f"{CLIENTS}/{world['alpha']}").json()

    assert body["ftn_addresses"] == [
        {"id": world["a20"], "address": "135:135/20", "leagues": ["900B"]},
        {"id": world["a8"], "address": "135:135/8", "leagues": []},
    ]


# -- only admins assign -----------------------------------------------------

@pytest.mark.parametrize("method, path, payload", [
    ("patch", "LEAGUES/{league}/members/{ma}", {"bbs_index": 9}),
    ("post", "CLIENTS/{alpha}/ftn-addresses", {"address": "135:135/99"}),
    ("put", "CLIENTS/{alpha}/ftn-addresses/{a20}", {"address": "135:135/99"}),
    ("delete", "CLIENTS/{alpha}/ftn-addresses/{a8}", None),
])
def test_sysops_cannot_change_addresses(api, world, method, path, payload):
    """A sysop renumbering their own address would break every league using it."""
    client, db = api
    _as_sysop()
    url = path.replace("LEAGUES", LEAGUES).replace("CLIENTS", CLIENTS).format(**world)

    kwargs = {"json": payload} if payload is not None else {}
    r = getattr(client, method)(url, **kwargs)

    assert r.status_code == 403
    assert _member(db, world["ma"]) == (2, "135:135/20")


# -- the hub's own address --------------------------------------------------

def test_bbs_cannot_take_the_hubs_address(api, world):
    client, db = api
    league = db.get(League, world["league"])
    league.hub_fidonet_address = "135:1/1"
    db.commit()

    r = client.post(f"{CLIENTS}/{world['beta']}/ftn-addresses", json={"address": "135:1/1"})

    assert r.status_code == 400
    assert r.json()["detail"] == "135:1/1 is the hub's own address in 900B"


def test_hub_cannot_take_a_bbss_address(api, world):
    client, _ = api

    r = client.put(f"{LEAGUES}/{world['league']}", json={"hub_fidonet_address": "135:135/21"})

    assert r.status_code == 400
    assert "belongs to Beta" in r.json()["detail"]
