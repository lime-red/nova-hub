"""The Processing Runs page's list: filters, keyset paging, and the tiles (#125).

The world: two leagues, 555B and 902I, and eight runs.
- IB runs carry packets but make no files. The league filter has to find a run
  by its packets, not only by its files.
- One run failed, one left packets unconsumed, one quarantined some, and one
  is still running.
- Two are older than a week.
"""
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app
from backend.core.clock import utcnow
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.database import (
    Base, Client, FtnAddress, League, LeagueMembership, Packet, ProcessingRun,
    ProcessingRunFile, SysopUser,
)

API = "/management/api/v1/processing"


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    bre = League(league_id="555", game_type="B", name="BRE 555")
    ib = League(league_id="902", game_type="I", name="IB 902")
    alpha = Client(client_id="alpha-oauth", client_secret="x", bbs_name="Alpha")
    admin = SysopUser(username="admin", email="admin@hub.example", hashed_password="x",
                      is_superuser=True)
    alice = SysopUser(username="alice", email="alice@example.com", idp_subject="user_alice")
    db.add_all([bre, ib, alpha, admin, alice])
    db.flush()
    alpha.owners.append(alice)
    db.add(LeagueMembership(client=alpha, league=bre, bbs_index=2, is_active=True,
                            ftn_address=FtnAddress(client_id=alpha.id, address="1:1/2")))

    now = utcnow()
    runs = {}

    def run(name, league, *, age_days=0, status="completed", unconsumed=0, quarantined=0,
            files=False):
        r = ProcessingRun(status=status, started_at=now - timedelta(days=age_days, minutes=len(runs)),
                          packets_unconsumed=unconsumed, packets_quarantined=quarantined)
        db.add(r)
        db.flush()
        db.add(Packet(filename=f"{league.league_id}{league.game_type}0201.{r.id:03d}",
                      league_id=league.id, source_bbs_index="02", dest_bbs_index="01",
                      sequence_number=r.id, file_size=1, processing_run_id=r.id))
        if files:
            db.add(ProcessingRunFile(processing_run_id=r.id, league_id=league.id,
                                     file_type="score", filename="PLYSCORE.ANS", file_data="x"))
        runs[name] = r.id

    # Oldest first, so ids grow with time as they do on a hub.
    run("bre_old", bre, age_days=20, files=True)
    run("ib_old", ib, age_days=10)
    run("bre_failed", bre, age_days=3, status="failed", files=True)
    run("ib_unconsumed", ib, age_days=2, unconsumed=1)
    run("ib_quarantined", ib, age_days=1, quarantined=2)
    run("bre_ok", bre, files=True)
    run("ib_ok", ib)
    run("ib_running", ib, status="running")
    db.commit()

    acting = {"user": admin}

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

    def act_as(user):
        acting["user"] = {"admin": admin, "alice": alice}[user]

    yield (TestClient(app, base_url="https://testserver"), act_as, runs,
           {"bre": bre.id, "ib": ib.id})
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def ids(client, path="/runs", **params):
    response = client.get(f"{API}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def names(runs, listed):
    by_id = {v: k for k, v in runs.items()}
    return [by_id[r["id"]] for r in listed]


def test_newest_first_with_every_league_named(world):
    client, _, runs, leagues = world
    listed = ids(client)
    assert names(runs, listed) == ["ib_running", "ib_ok", "bre_ok", "ib_quarantined",
                                   "ib_unconsumed", "bre_failed", "ib_old", "bre_old"]
    # An IB run makes no files; its league still comes from its packets.
    ib_ok = listed[1]
    assert ib_ok["leagues"] == ["902I"] and ib_ok["league_ids"] == [leagues["ib"]]
    assert listed[3]["packets_quarantined"] == 2


def test_pages_follow_on_by_id_and_end_short(world):
    client, _, runs, _ = world
    first = ids(client, limit=3)
    assert names(runs, first) == ["ib_running", "ib_ok", "bre_ok"]
    second = ids(client, limit=3, before_id=first[-1]["id"])
    assert names(runs, second) == ["ib_quarantined", "ib_unconsumed", "bre_failed"]
    third = ids(client, limit=3, before_id=second[-1]["id"])
    assert names(runs, third) == ["ib_old", "bre_old"]  # fewer than limit: the end


def test_a_league_filter_finds_runs_by_their_packets(world):
    client, _, runs, leagues = world
    assert names(runs, ids(client, league_id=leagues["ib"])) == [
        "ib_running", "ib_ok", "ib_quarantined", "ib_unconsumed", "ib_old"]
    assert names(runs, ids(client, league_id=leagues["bre"])) == [
        "bre_ok", "bre_failed", "bre_old"]


def test_status_problems_and_window_filters(world):
    client, _, runs, leagues = world
    assert names(runs, ids(client, status="failed")) == ["bre_failed"]
    assert names(runs, ids(client, status="running")) == ["ib_running"]
    assert names(runs, ids(client, problems=True)) == [
        "ib_quarantined", "ib_unconsumed", "bre_failed"]
    assert names(runs, ids(client, days=7, league_id=leagues["ib"])) == [
        "ib_running", "ib_ok", "ib_quarantined", "ib_unconsumed"]
    assert client.get(f"{API}/runs", params={"status": "bogus"}).status_code == 422


def test_the_tiles_count_what_the_filters_would_show(world):
    client, _, _, leagues = world
    assert ids(client, "/runs/summary") == {
        "total": 8, "completed": 6, "failed": 1, "running": 1, "problems": 3}
    assert ids(client, "/runs/summary", days=7, league_id=leagues["ib"]) == {
        "total": 4, "completed": 3, "failed": 0, "running": 1, "problems": 2}


def test_a_sysop_filters_only_within_their_leagues(world):
    client, act_as, runs, leagues = world
    act_as("alice")
    assert names(runs, ids(client)) == ["bre_ok", "bre_failed", "bre_old"]
    assert ids(client, "/runs/summary")["total"] == 3
    # Not her league: the same 404 as asking for it directly.
    assert client.get(f"{API}/runs", params={"league_id": leagues["ib"]}).status_code == 404
    assert client.get(f"{API}/runs/summary",
                      params={"league_id": leagues["ib"]}).status_code == 404
