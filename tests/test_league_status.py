"""League Status: each BBS's polls, queue and the hub game's view of it, per league.

The case that asked for it (prod, Oct 2026): eots was in 015F, the hub game
wrote packets for it daily, and eots never listed 015F -- its client had only
015B configured. The game said "No Recons Recieved"; nothing in the console did.

The world here: league 555B, the hub at 01, Alpha at 02 (owned by Alice), Bravo
at 03. IB league 902I, Alpha at 02.
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, management_app, service_app
from backend.api.service import packets as packets_api
from backend.core import rate_limiter
from backend.core.clock import utcnow
from backend.core.database import get_db
from backend.core.security import get_current_user, get_password_hash
from backend.models.database import (
    Base, Client, ClientLeaguePoll, League, LeagueMembership, Packet, ProcessingRun,
    ProcessingRunFile, SysopUser,
)
from backend.services import game_status

API = "/management/api/v1"
TOKEN_URL = "/service/api/v1/auth/token"
SECRET = "the-secret"
UA = "nova-client-py/0.6.0"


def _local(when: dt.datetime) -> str:
    """A naive UTC time as the game writes it: hub-local, MM/DD/YYYY  HH:MM:SS."""
    return when.replace(tzinfo=dt.timezone.utc).astimezone().strftime("%m/%d/%Y  %H:%M:%S")


def bbsinfo(lines):
    out = ["###  BBS Name                                Last Recon        BRE Version"]
    for index, name, when, version in lines:
        if when is None:
            out.append(f"  {index}) No Recons Recieved")
        else:
            out.append(f"  {index}) {name:<34} {_local(when)}      {version}")
    return "\n".join(out) + "\n"


def routes(missing=(), boards=(1, 2, 3)):
    out = []
    for b in boards:
        out += [f"Routing Paths of BBS # {b:>2}", "-" * 25]
        out.append(f"Information missing for BBS # {b:>2}" if b in missing else f"To BBS #1: {b}->1")
        out.append("")
    return "\n".join(out)


# ── the listings ──────────────────────────────────────────────────────────

def test_bbsinfo_reads_the_lines_production_writes():
    text = ("###  BBS Name                                Last Recon        FE Version\n"
            "  1) Nova Hub                           10/31/2026  00:36:31      v1.06\n"
            "  2) Bobo's Place                       10/30/2026  23:01:02      v1.05\n"
            "  3) No Recons Recieved\n")
    boards = game_status.parse_bbsinfo(text)
    assert boards[1].name == "Nova Hub" and boards[1].version == "v1.06"
    assert boards[1].last_recon == (dt.datetime(2026, 10, 31, 0, 36, 31).astimezone(dt.timezone.utc)
                                    .replace(tzinfo=None))
    assert boards[2].name == "Bobo's Place"
    assert boards[3].last_recon is None and boards[3].version is None


def test_routes_say_which_boards_the_game_has_no_routing_for():
    assert game_status.parse_routes(routes(missing=(3,))) == {1: True, 2: True, 3: False}


# ── the world ─────────────────────────────────────────────────────────────

@pytest.fixture
def world(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    bre = League(league_id="555", game_type="B", name="BRE 555")
    ib = League(league_id="902", game_type="I", name="IB 902")
    other = League(league_id="777", game_type="F", name="FE 777")
    hub = Client(client_id="hub", client_secret="x", bbs_name="Nova Hub")
    alpha = Client(client_id="alpha", client_secret=get_password_hash(SECRET), bbs_name="Alpha",
                   last_seen=utcnow())
    bravo = Client(client_id="bravo", client_secret="x", bbs_name="Bravo", last_seen=utcnow())
    admin = SysopUser(username="admin", email="admin@hub.example", hashed_password="x",
                      is_superuser=True)
    alice = SysopUser(username="alice", email="alice@example.com", idp_subject="user_alice")
    bob = SysopUser(username="bob", email="bob@example.com", idp_subject="user_bob")
    db.add_all([bre, ib, other, hub, alpha, bravo, admin, alice, bob])
    db.flush()
    alpha.owners.append(alice)
    db.add_all([
        LeagueMembership(client=hub, league=bre, bbs_index=1),
        LeagueMembership(client=alpha, league=bre, bbs_index=2),
        LeagueMembership(client=bravo, league=bre, bbs_index=3),
        LeagueMembership(client=alpha, league=ib, bbs_index=2),
    ])
    db.commit()

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    acting = {"user": admin}
    saved = {a: dict(a.dependency_overrides) for a in (app, management_app, service_app)}
    for a in (app, management_app, service_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = lambda: Session().get(SysopUser, acting["user"].id)
    rate_limiter._attempts.clear()
    rate_limiter._lockouts.clear()

    class FakeConfig(dict):
        def get(self, section, default=None):
            return {"server": {"data_dir": str(tmp_path)},
                    "hub": {"bbs_index": "01"}}.get(section, default)

    from backend.api.management import clients as clients_api, leagues as leagues_api
    for module in (packets_api, clients_api, leagues_api):
        monkeypatch.setattr(module, "get_config", lambda: FakeConfig())

    users = {"admin": admin, "alice": alice, "bob": bob}

    class World:
        pass

    w = World()
    w.http = TestClient(app, base_url="https://testserver")
    w.db, w.bre, w.ib, w.other = db, bre, ib, other
    w.hub, w.alpha, w.bravo = hub, alpha, bravo
    w.data_dir = tmp_path
    w.act_as = lambda name: acting.update(user=users[name])
    yield w
    db.close()
    for a, overrides in saved.items():
        a.dependency_overrides.clear()
        a.dependency_overrides.update(overrides)


def _bearer(w):
    r = w.http.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": "alpha",
                                     "client_secret": SECRET}, headers={"User-Agent": UA})
    assert r.status_code == 200, r.text
    return {"Authorization": r.json()["token_type"].capitalize() + " " + r.json()["access_token"],
            "User-Agent": UA}


def _poll(w, league_id, when=None):
    w.db.add(ClientLeaguePoll(client_id=w.alpha.id, league_id=league_id,
                              first_poll_at=when or utcnow(), last_poll_at=when or utcnow()))
    w.db.commit()


def _waiting(w, name, dest, age, league=None, downloaded=False):
    w.db.add(Packet(filename=name, league_id=(league or w.bre).id, source_bbs_index="01",
                    dest_bbs_index=dest, sequence_number=1, file_size=1,
                    uploaded_at=utcnow() - age, processed_at=utcnow() - age,
                    downloaded_at=utcnow() if downloaded else None))
    w.db.commit()


def _listings(w, info, route_text=None):
    run = ProcessingRun(status="completed")
    w.db.add(run)
    w.db.flush()
    w.db.add(ProcessingRunFile(processing_run_id=run.id, league_id=w.bre.id, file_type="bbsinfo",
                               filename="bbsinfo.lst", file_data=info))
    w.db.add(ProcessingRunFile(processing_run_id=run.id, league_id=w.bre.id, file_type="routes",
                               filename="routes.lst", file_data=route_text or routes()))
    w.db.commit()


def _healthy_game(w, alpha_version="v0.988", heard=None):
    now = utcnow()
    _listings(w, bbsinfo([(1, "Nova Hub", now, "v0.988"),
                          (2, "Alpha", heard or now, alpha_version),
                          (3, "Bravo", now, "v0.988")]))


def _status(w, league="555B"):
    rows = game_status.for_client(w.db, w.alpha, 1, w.data_dir)
    return next(r for r in rows if f"{r.league.league_id}{r.league.game_type}" == league)


# ── recording polls ───────────────────────────────────────────────────────

def test_a_listing_is_recorded_with_the_clients_software(world):
    w = world
    headers = _bearer(w)
    assert w.http.get("/service/api/v1/leagues/555B/packets", headers=headers).status_code == 200
    w.db.expire_all()
    poll = w.db.get(ClientLeaguePoll, (w.alpha.id, w.bre.id))
    assert poll is not None and poll.last_user_agent == UA
    assert w.db.get(Client, w.alpha.id).last_user_agent == UA


def test_polls_are_written_at_most_once_a_minute(world):
    w = world
    headers = _bearer(w)
    w.http.get("/service/api/v1/leagues/555B/packets", headers=headers)
    w.db.expire_all()
    first = w.db.get(ClientLeaguePoll, (w.alpha.id, w.bre.id)).last_poll_at
    w.http.get("/service/api/v1/leagues/555B/packets", headers=headers)
    w.db.expire_all()
    assert w.db.get(ClientLeaguePoll, (w.alpha.id, w.bre.id)).last_poll_at == first

    w.db.get(ClientLeaguePoll, (w.alpha.id, w.bre.id)).last_poll_at = first - dt.timedelta(minutes=2)
    w.db.commit()
    w.http.get("/service/api/v1/leagues/555B/packets", headers=headers)
    w.db.expire_all()
    assert w.db.get(ClientLeaguePoll, (w.alpha.id, w.bre.id)).last_poll_at > first


def test_a_poll_of_a_league_it_is_not_in_is_recorded_and_shown(world):
    w = world
    headers = _bearer(w)
    assert w.http.get("/service/api/v1/leagues/777F/packets", headers=headers).json() == {"packets": []}
    assert w.http.get("/service/api/v1/leagues/999B/packets", headers=headers).json() == {"packets": []}
    w.db.expire_all()
    assert w.db.get(ClientLeaguePoll, (w.alpha.id, w.other.id)) is not None
    assert w.db.query(ClientLeaguePoll).count() == 1   # 999B does not exist
    assert _status(w, "777F").state == "not_member"


def test_a_failing_poll_record_does_not_break_the_listing(world, monkeypatch):
    w = world
    headers = _bearer(w)

    monkeypatch.setattr(packets_api, "ClientLeaguePoll", None)   # db.get(None, ...) raises
    assert w.http.get("/service/api/v1/leagues/555B/packets", headers=headers).status_code == 200


# ── the states ────────────────────────────────────────────────────────────

def test_the_eots_case_a_member_that_never_polls_with_packets_waiting(world):
    w = world
    _listings(w, bbsinfo([(1, "Nova Hub", utcnow(), "v0.988"), (2, None, None, None),
                          (3, "Bravo", utcnow(), "v0.988")]), routes(missing=(2,)))
    for n in range(3):
        _waiting(w, f"555B0102.00{n}", "02", dt.timedelta(days=60 - n))
    s = _status(w)
    assert s.state == "not_polling"
    assert s.waiting == 3 and s.oldest_waiting_at < utcnow() - dt.timedelta(days=59)
    assert s.game.heard is False and s.game.routing_missing is True


def test_offline_comes_before_the_league(world):
    w = world
    _poll(w, w.bre.id, utcnow() - dt.timedelta(hours=3))
    w.alpha.last_seen = utcnow() - dt.timedelta(hours=3)
    w.db.commit()
    s = _status(w)
    assert s.state == "offline" and "3 h" in s.state_detail


def test_a_client_keeping_its_token_is_online_while_it_polls(world):
    w = world
    w.alpha.last_seen = utcnow() - dt.timedelta(hours=18)    # the token it still uses
    w.db.commit()
    _poll(w, w.bre.id)
    _healthy_game(w)
    assert _status(w).state == "ok"
    assert _status(w, "902I").state == "not_polling"


def test_stopped_polling_this_league_while_polling_others(world):
    w = world
    _poll(w, w.bre.id, utcnow() - dt.timedelta(hours=5))
    assert _status(w).state == "stopped_polling"


def test_backlog_when_the_oldest_waiting_packet_is_over_a_day(world):
    w = world
    _poll(w, w.bre.id)
    _healthy_game(w)
    _waiting(w, "555B0102.001", "02", dt.timedelta(hours=30))
    _waiting(w, "555B0102.002", "02", dt.timedelta(hours=40), downloaded=True)
    s = _status(w)
    assert s.state == "backlog" and s.waiting == 1
    assert [p.filename for p in s.waiting_packets] == ["555B0102.001"]


def test_game_silent_when_the_last_recon_is_old_at_the_last_run(world):
    w = world
    _poll(w, w.bre.id)
    _healthy_game(w, heard=utcnow() - dt.timedelta(days=3))
    s = _status(w)
    assert s.state == "game_silent" and "before the last run" in s.state_detail


def test_out_of_date_when_its_game_is_not_the_hubs(world):
    w = world
    _poll(w, w.bre.id)
    _healthy_game(w, alpha_version="v0.987")
    s = _status(w)
    assert s.state == "out_of_date" and s.state_detail == "v0.987, hub v0.988"


def test_ok(world):
    w = world
    _poll(w, w.bre.id)
    _healthy_game(w)
    _waiting(w, "555B0102.001", "02", dt.timedelta(minutes=5))
    s = _status(w)
    assert (s.state, s.waiting) == ("ok", 1)
    assert s.game.line.startswith("  2) Alpha")


def test_the_hubs_own_board_has_no_queue(world):
    w = world
    _waiting(w, "555B0201.001", "01", dt.timedelta(days=5))
    rows = game_status.for_league(w.db, w.bre, 1, w.data_dir)
    hub = next(r for r in rows if r.member.bbs_index == 1)
    assert hub.waiting is None and hub.state == "ok"


# ── IB: the TimeCheck ─────────────────────────────────────────────────────

def _ib_packet(w, name, src, dst, body):
    content = json.dumps(body).encode()
    (w.data_dir / "packets" / "processed").mkdir(parents=True, exist_ok=True)
    (w.data_dir / "packets" / "processed" / name).write_bytes(content)
    w.db.add(Packet(filename=name, league_id=w.ib.id, source_bbs_index=src, dest_bbs_index=dst,
                    sequence_number=1, file_size=len(content),
                    checksum=hashlib.sha256(content).hexdigest()))
    w.db.commit()


def test_ib_is_heard_by_the_timecheck_it_answers(world):
    w = world
    _poll(w, w.ib.id)
    answered = utcnow() - dt.timedelta(hours=2)
    _ib_packet(w, "L902-1-1-0.BRP", "01", "02",
               {"FromBoard": "Nova Hub", "ToBoard": "Alpha #1", "Version": "0.2.6", "TimeChecks": []})
    _ib_packet(w, "L902-2-1-0.BRP", "02", "01", {
        "FromBoard": "Alpha #1", "ToBoard": "Nova Hub", "Version": "0.2.5",
        "TimeChecks": [
            {"From": "Nova Hub", "To": "Alpha #1", "Sent": "2099-01-01T00:00:00Z"},
            {"From": "Alpha #1", "To": "Nova Hub", "Sent": answered.strftime("%Y-%m-%dT%H:%M:%SZ")},
        ]})
    s = _status(w, "902I")
    assert s.game.source == "timecheck" and s.game.heard
    assert s.game.last_heard == answered.replace(microsecond=0)   # its answer, not the hub's ping
    assert (s.state, s.state_detail) == ("out_of_date", "0.2.5, hub 0.2.6")


def test_ib_with_no_answer_is_silent(world):
    w = world
    _poll(w, w.ib.id)
    _ib_packet(w, "L902-2-1-0.BRP", "02", "01",
               {"FromBoard": "Alpha #1", "Version": "0.2.6", "TimeChecks": []})
    assert _status(w, "902I").state == "game_silent"


# ── the API, and who sees what ────────────────────────────────────────────

def test_the_client_page_lists_its_leagues(world):
    w = world
    _poll(w, w.bre.id)
    _healthy_game(w)
    body = w.http.get(f"{API}/clients/{w.alpha.id}/league-status").json()
    assert body["tracking_since"].endswith("Z") or "+00:00" in body["tracking_since"]
    by_league = {r["full_id"]: r for r in body["rows"]}
    assert by_league["555B"]["state"] == "ok" and by_league["555B"]["bbs_index"] == 2
    assert by_league["555B"]["user_agent"] is None   # set by a real poll, not _poll
    assert by_league["902I"]["state"] == "not_polling"


def test_a_sysop_sees_their_own_client_and_no_other(world):
    w = world
    w.act_as("alice")
    assert w.http.get(f"{API}/clients/{w.alpha.id}/league-status").status_code == 200
    assert w.http.get(f"{API}/clients/{w.bravo.id}/league-status").status_code == 404
    w.act_as("bob")
    assert w.http.get(f"{API}/clients/{w.alpha.id}/league-status").status_code == 404


def test_on_the_league_page_a_sysop_gets_only_the_game_view_of_others(world):
    w = world
    _listings(w, bbsinfo([(1, "Nova Hub", utcnow(), "v0.988"), (2, "Alpha", utcnow(), "v0.988"),
                          (3, None, None, None)]))
    _waiting(w, "555B0103.001", "03", dt.timedelta(days=3))
    w.act_as("alice")
    rows = {r["bbs_name"]: r for r in w.http.get(f"{API}/leagues/{w.bre.id}/member-status").json()["rows"]}
    assert rows["Alpha"]["restricted"] is False
    bravo = rows["Bravo"]
    assert bravo["restricted"] is True
    assert bravo["waiting"] is None and bravo["last_poll_at"] is None and bravo["user_agent"] is None
    assert bravo["state"] == "game_silent"                 # the game's view, not "not_polling"
    w.act_as("bob")
    assert w.http.get(f"{API}/leagues/{w.bre.id}/member-status").status_code == 404
    w.act_as("admin")
    rows = {r["bbs_name"]: r for r in w.http.get(f"{API}/leagues/{w.bre.id}/member-status").json()["rows"]}
    assert rows["Bravo"]["state"] == "not_polling" and rows["Bravo"]["waiting"] == 1


def test_the_clients_list_marks_a_bbs_with_a_league_needing_attention(world):
    w = world
    _poll(w, w.bre.id)
    _healthy_game(w)
    by_name = {c["bbs_name"]: c for c in w.http.get(f"{API}/clients").json()}
    assert by_name["Alpha"]["league_issues"] == ["902I: Never polled this league"]
