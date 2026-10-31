"""Falcon's Eye attacks and traffic through the hub, as BRE's are followed.

The packets are a war played on a two-board rig league (31 Oct 2026; see
test_fe_packet). Here board 1 stands in for the hub's own game, which is what
board 1 is in a real league. In order:

  war_900f0201.003      Raider Two attacks twice, casts Wizard's Eye, offers a
                        trade and writes to Target One
  results_900f0102.003  both attacks fail; the spell fizzles
  war_900f0102.004      Target One accepts the trade, attacks, offers a trade of
                        its own, writes to everyone
  results_900f0201.004  that attack fails; Raider Two rejects the trade
  war_900f0201.005      Raider Two attacks while Target One's army is away
  results_900f0102.005  ... and takes 15 acres; Target One attacks again
  results_900f0201.006  ... and takes 17 people
"""
from datetime import datetime
from pathlib import Path

import pytest

from backend.models.database import AttackSighting, League, Packet, TrafficSighting
from backend.services import attack_trace, traffic_trace
from tests.test_attack_trace import api, db  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "fe_packets"
HUB = "01"
ORDER = ("war_900f0201.003", "results_900f0102.003", "war_900f0102.004",
         "results_900f0201.004", "war_900f0201.005", "results_900f0102.005",
         "results_900f0201.006")


@pytest.fixture
def league(db):
    lg = League(league_id="900", game_type="F", name="Rig 900F")
    db.add(lg)
    db.commit()
    return lg


def store(db, league, name, at):
    content = (FIXTURES / name).read_bytes()
    filename = name.split("_", 1)[1].upper()
    src, dst = filename[4:6], filename[6:8]
    row = Packet(filename=filename, league_id=league.id, source_bbs_index=src,
                 dest_bbs_index=dst, sequence_number=int(filename[-3:]),
                 file_size=len(content), file_data=content, uploaded_at=at)
    if src == HUB:
        row.processed_at = at
    else:
        row.downloaded_at, row.is_downloaded = at, True
    db.add(row)
    db.commit()
    return attack_trace.record(db, row, content)


@pytest.fixture
def war(db, league):
    for minute, name in enumerate(ORDER):
        store(db, league, name, datetime(2026, 10, 31, 1, minute))


def journeys(db):
    return attack_trace.journeys(db, HUB)


def test_every_attack_meets_its_result(db, war):
    js = journeys(db)
    assert len(js) == 5
    assert all(j.result_at_hub for j in js)
    land = next(j for j in js if j.attack_type == "Capture Land" and j.from_planet == 2)
    assert (land.attacker, land.target) == ("A", "A")
    assert (land.attacker_name, land.target_name) == ("Raider Two", "Target One")


def test_names_are_the_counties_the_recon_updates_announced(db, war):
    names = {(s.from_planet, s.attacker_name) for s in db.query(AttackSighting)}
    assert names == {(2, "Raider Two"), (1, "Target One")}


def test_lost_attacks_use_fes_default(db, war):
    assert {j.lost_attack_days for j in journeys(db)} == {10}
    assert {j.game_type for j in journeys(db)} == {"F"}


def test_trades_and_spells_pair_and_messages_stand_alone(db, war):
    found = traffic_trace.journeys(traffic_trace.events(db, HUB, since=datetime(2026, 10, 1)))
    shape = sorted((j.kind, len(j.results)) for j in found)
    assert shape == [("Message", 0), ("Message", 0), ("Spell", 1),
                     ("Trade Deal", 1), ("Trade Deal", 1)]


def test_a_message_holds_no_text(db, war):
    for s in db.query(TrafficSighting).filter(TrafficSighting.kind == "Message"):
        row = [getattr(s, c) for c in TrafficSighting.__table__.columns.keys()]
        assert not any("HELLO" in str(v) for v in row)


def test_a_member_cannot_speak_for_another_board(db, league):
    """board 2's packet relabelled as board 3's: none of it is board 3's."""
    content = (FIXTURES / "war_900f0201.003").read_bytes()
    row = Packet(filename="900F0301.003", league_id=league.id, source_bbs_index="03",
                 dest_bbs_index="01", sequence_number=3, file_size=len(content))
    db.add(row)
    db.commit()
    assert attack_trace.record(db, row, content) == 0


# ── the reveals ───────────────────────────────────────────────────────────
def test_an_admin_reveals_an_fe_attacks_forces(api, db, war):
    land = next(j for j in journeys(db) if j.attack_type == "Capture Land" and j.from_planet == 2)
    got = api.get(f"/management/api/v1/attacks/{land.attack_id}/forces").json()
    assert got["sent"] == {"unit_1": 30, "unit_2": 0, "unit_3": 0}
    assert (got["success"], got["objective"], got["gained"]) == (True, "Capture Land", "15 acres")
    assert got["regions_captured"] == 15
    assert got["returned"] == {"unit_1": 30, "unit_2": 0, "unit_3": 0}


def test_the_listing_finds_an_fe_attack_by_id(api, db, war):
    j = journeys(db)[0]
    got = api.get("/management/api/v1/attacks/", params={"attack_id": j.attack_id}).json()
    assert [g["attack_id"] for g in got] == [j.attack_id]


def test_an_admin_reveals_a_trade_but_never_a_message(api, db, war):
    keys = {s.kind: s.event_key for s in db.query(TrafficSighting)}
    got = api.get(f"/management/api/v1/traffic/{keys['Trade Result']}/details").json()
    assert "accepted" in got["details"]
    assert api.get(f"/management/api/v1/traffic/{keys['Message']}/details").status_code == 403
