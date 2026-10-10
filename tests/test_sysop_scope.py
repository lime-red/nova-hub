"""What a sysop sees in the console: their own BBS and its leagues, nothing else.

The world: two leagues. Alice owns Alpha, an active member of 555B at index
02, and formerly of 777F at index 05. Bravo is in 555B at 03. Charlie is in
777F at 02 -- the same index as Alpha in 555B, so a check on index alone
would leak. Bob owns nothing.

Alice must see Alpha, 555B, and packets/alerts to or from Alpha (including its
history in 777F); not Bravo's or Charlie's traffic, not 777F itself, and none
of the hub's internals. Records outside her scope are 404s.
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
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.database import (
    Base,
    Client,
    FtnAddress,
    League,
    LeagueMembership,
    Packet,
    ProcessingRun,
    ProcessingRunFile,
    SequenceAlert,
    SysopUser,
)

API = "/management/api/v1"


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    bre = League(league_id="555", game_type="B", name="BRE 555", dosemu_path="/srv/bre",
                 game_executable="BRE.EXE")
    fe = League(league_id="777", game_type="F", name="FE 777")
    alpha = Client(client_id="alpha-oauth", client_secret="x", bbs_name="Alpha")
    bravo = Client(client_id="bravo-oauth", client_secret="x", bbs_name="Bravo")
    charlie = Client(client_id="charlie-oauth", client_secret="x", bbs_name="Charlie")
    admin = SysopUser(username="admin", email="admin@hub.example", hashed_password="x",
                      is_superuser=True)
    alice = SysopUser(username="alice", email="alice@example.com", idp_subject="user_alice")
    bob = SysopUser(username="bob", email="bob@example.com", idp_subject="user_bob")
    db.add_all([bre, fe, alpha, bravo, charlie, admin, alice, bob])
    db.flush()
    alpha.owners.append(alice)

    def member(client, league, index, address, active=True):
        db.add(LeagueMembership(client=client, league=league, bbs_index=index, is_active=active,
                                ftn_address=FtnAddress(client_id=client.id, address=address)))

    member(alpha, bre, 2, "1:1/2")
    member(alpha, fe, 5, "1:1/5", active=False)
    member(bravo, bre, 3, "1:1/3")
    member(charlie, fe, 2, "1:7/2")
    db.flush()
    db.add(FtnAddress(client_id=bravo.id, address="9:9/9"))  # Bravo's, used in no league

    run_bre = ProcessingRun(league_id=bre.id, status="completed", dosemu_log="BRE transcript")
    run_fe = ProcessingRun(league_id=fe.id, status="completed", dosemu_log="FE transcript")
    db.add_all([run_bre, run_fe])
    db.flush()
    db.add_all([
        ProcessingRunFile(processing_run_id=run_bre.id, league_id=bre.id, file_type="routes",
                          filename="routes.lst", file_data="routes"),
        ProcessingRunFile(processing_run_id=run_bre.id, league_id=fe.id, file_type="routes",
                          filename="other.lst", file_data="not alice's"),
    ])

    def packet(name, league, src, dst, run=None):
        db.add(Packet(filename=name, league_id=league.id, source_bbs_index=src,
                      dest_bbs_index=dst, sequence_number=1, file_size=1,
                      processing_run_id=run.id if run else None))

    packet("555B0203.001", bre, "02", "03", run_bre)  # Alpha -> Bravo
    packet("555B0301.001", bre, "03", "01", run_bre)  # Bravo -> hub
    packet("777F0201.001", fe, "02", "01", run_fe)    # Charlie -> hub (index 02!)
    packet("777F0501.001", fe, "05", "01")            # Alpha's history in 777F

    def alert(league, src, dst):
        db.add(SequenceAlert(league_id=league.id, source_bbs_index=src, dest_bbs_index=dst,
                             expected_sequence=1, received_sequence=3, gap_size=2))

    alert(bre, "02", "01")
    alert(bre, "03", "01")
    alert(fe, "02", "01")
    db.commit()

    acting = {"user": alice}

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    def current_user():
        return Session().get(SysopUser, acting["user"].id)

    saved = {a: dict(a.dependency_overrides) for a in (app, management_app)}
    for a in (app, management_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = current_user

    ids = {"bre": bre.id, "fe": fe.id, "alpha": alpha.id, "bravo": bravo.id,
           "charlie": charlie.id, "run_bre": run_bre.id, "run_fe": run_fe.id}
    users = {"admin": admin, "alice": alice, "bob": bob}

    def act_as(name):
        acting["user"] = users[name]

    yield TestClient(app, base_url="https://testserver"), db, act_as, ids
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def get(client, path, **params):
    response = client.get(f"{API}{path}", params=params)
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response.json()


def status(client, path):
    return client.get(f"{API}{path}").status_code


def test_clients_are_her_own(world):
    client, _, _, ids = world
    assert [c["bbs_name"] for c in get(client, "/clients")] == ["Alpha"]
    detail = get(client, f"/clients/{ids['alpha']}")
    assert detail["owners"] == [{"user_id": detail["owners"][0]["user_id"], "username": "alice",
                                 "full_name": None, "email": None}]
    assert status(client, f"/clients/{ids['bravo']}") == 404
    assert status(client, "/clients/9999") == 404


def test_leagues_are_those_her_bbs_is_in_now(world):
    client, _, _, ids = world
    assert [l["full_id"] for l in get(client, "/leagues")] == ["555B"]
    assert status(client, f"/leagues/{ids['fe']}") == 404  # a former member
    assert status(client, f"/leagues/{ids['fe']}/nodelist") == 404

    league = get(client, f"/leagues/{ids['bre']}")
    assert sorted((m["bbs_name"], m["bbs_index"], m["fidonet_address"])
                  for m in league["members"]) == [("Alpha", 2, "1:1/2"), ("Bravo", 3, "1:1/3")]
    assert all(m["client_oauth_id"] is None and m["client_ftn_addresses"] == []
               for m in league["members"])
    assert league["dosemu_path"] is None and league["game_executable"] is None
    assert league["available_clients"] == []


def test_packets_and_alerts_are_those_to_or_from_her_bbs(world):
    client, db, _, ids = world
    stats = get(client, "/dashboard/stats")
    assert (stats["total_packets"], stats["active_clients"], stats["active_leagues"],
            stats["pending_alerts"]) == (2, 1, 1, 1)
    files = sorted(a["filename"] for a in get(client, "/dashboard/activity", limit=50))
    assert files == ["555B0203.001", "777F0501.001"]
    chart = get(client, "/dashboard/charts/leagues")
    assert dict(zip(chart["labels"], chart["data"])) == {"B 555": 1, "F 777": 1}
    assert sum(get(client, "/dashboard/charts/activity")["data"]) == 2

    alerts = get(client, "/alerts")
    assert [(a["league_name"], a["source_bbs_index"]) for a in alerts] == [("B 555", "02")]
    assert [a["source"] for a in get(client, "/dashboard/alerts")] == ["02"]
    for alert in db.query(SequenceAlert):
        mine = alert.league_id == ids["bre"] and alert.source_bbs_index == "02"
        assert status(client, f"/alerts/{alert.id}") == (200 if mine else 404)

    full = get(client, "/dashboard")
    assert full["stats"]["total_packets"] == 2 and len(full["alerts"]) == 1


def test_runs_show_her_leagues_without_the_transcript_or_others_packets(world):
    client, _, _, ids = world
    assert [r["id"] for r in get(client, "/processing/runs")] == [ids["run_bre"]]
    assert status(client, f"/processing/runs/{ids['run_fe']}") == 404
    run = get(client, f"/processing/runs/{ids['run_bre']}")
    assert [p["filename"] for p in run["packets"]] == ["555B0203.001"]
    assert run["dosemu_output"] is None and run["dosemu_output_html"] is None
    assert [f["filename"] for f in run["routes_files"]] == ["routes.lst"]


def test_the_address_book_is_her_leagues_as_their_nodelists_show_them(world):
    client, _, _, _ = world
    book = get(client, "/address-book")
    assert [l["full_id"] for l in book["leagues"]] == ["555B"]
    bbses = {b["bbs_name"]: b for b in book["bbses"]}
    assert set(bbses) == {"Alpha", "Bravo"}
    assert bbses["Bravo"]["addresses"] == ["1:1/3"]  # not 9:9/9, used nowhere she can see
    assert bbses["Alpha"]["addresses"] == ["1:1/2", "1:1/5"]  # her own, all of them


def test_game_intelligence_and_admin_pages_are_closed(world):
    client, _, _, _ = world
    for path in ["/attacks/", "/movements/", "/movements/summary", "/traffic/", "/users",
                 "/audit"]:
        assert status(client, path) == 403, path


def test_a_sysop_with_no_bbs_sees_nothing(world):
    client, _, act_as, ids = world
    act_as("bob")
    assert get(client, "/clients") == []
    assert get(client, "/leagues") == []
    assert get(client, "/processing/runs") == []
    assert get(client, "/alerts") == []
    assert get(client, "/dashboard/activity") == []
    stats = get(client, "/dashboard/stats")
    assert (stats["total_packets"], stats["active_clients"], stats["active_leagues"],
            stats["pending_alerts"]) == (0, 0, 0, 0)
    assert get(client, "/address-book") == {"leagues": [], "bbses": []}
    assert status(client, f"/leagues/{ids['bre']}") == 404


def test_an_admin_still_sees_everything(world):
    client, _, act_as, ids = world
    act_as("admin")
    assert len(get(client, "/clients")) == 3
    assert len(get(client, "/leagues")) == 2
    assert get(client, "/dashboard/stats")["total_packets"] == 4
    assert len(get(client, "/alerts")) == 3
    run = get(client, f"/processing/runs/{ids['run_bre']}")
    assert run["dosemu_output"] == "BRE transcript"
    assert len(run["packets"]) == 2
    league = get(client, f"/leagues/{ids['bre']}")
    assert league["dosemu_path"] == "/srv/bre"
    assert {m["client_oauth_id"] for m in league["members"]} == {"alpha-oauth", "bravo-oauth"}
    owner = get(client, f"/clients/{ids['alpha']}")["owners"][0]
    assert owner["email"] == "alice@example.com"
