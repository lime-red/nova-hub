"""Falcon's Eye packets: BRE's container, FE's type table, FE's war records.

day1_ and request_ are two packets node 2 of the rig's 900F wrote (Oct 2026).
What is expected of each is what FE itself printed while writing it, in its
/DETAILED transcript: every record's type name, decompressed size and ends, in
order. war_ and results_ are a two-board war played on 31 Oct 2026.
"""
from pathlib import Path

from backend.models.database import AttackSighting, League, Packet, TrafficSighting
from backend.services import attack_trace, bre_packet, fe_packet
from tests.test_attack_trace import db  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "fe_packets"

TRANSCRIPTS = {
    "day1_900f0201.001": [
        ("Recon Update", 1194, 2, 1), ("Routing List", 257, 2, 1),
        ("Recon Request", 2, 2, 1), ("Dummy Data", 2, 2, 3), ("Recon Request", 2, 2, 3),
        ("Dummy Data", 2, 2, 4), ("Recon Request", 2, 2, 4),
    ],
    # Node 2 relaying the hub's Config Update on to boards 3 and 4.
    "request_900f0201.002": [
        ("Recon Update", 1194, 2, 1), ("Dummy Data", 2, 2, 3), ("Config Update", 232, 1, 3),
        ("Recon Request", 2, 1, 3), ("Dummy Data", 2, 2, 4), ("Config Update", 232, 1, 4),
        ("Recon Request", 2, 1, 4), ("Recon Request", 2, 2, 1), ("Recon Request", 2, 2, 3),
        ("Recon Request", 2, 2, 4),
    ],
}


def test_fe_packets_read_as_fe_wrote_them():
    for name, expected in TRANSCRIPTS.items():
        records = fe_packet.parse((FIXTURES / name).read_bytes())
        assert all(r.crc_ok for r in records)
        assert [(r.name, len(r.data), r.src, r.dst) for r in records] == expected


def test_the_same_numbers_mean_other_things_in_bre():
    rec = fe_packet.parse((FIXTURES / "day1_900f0201.001").read_bytes())[0]
    assert rec.name == "Recon Update"
    assert bre_packet.Record(rec.type, 2, 1, True, rec.data).name == "Configupdate"


def test_a_packet_with_no_war_in_it_records_nothing(db):
    lg = League(league_id="900", game_type="F", name="Rig 900F")
    db.add(lg)
    db.commit()
    content = (FIXTURES / "request_900f0201.002").read_bytes()
    p = Packet(filename="900F0201.002", league_id=lg.id, source_bbs_index="02",
               dest_bbs_index="01", sequence_number=2, file_size=len(content))
    db.add(p)
    db.commit()
    # Read with BRE's table, FE's Config Update (0x02) would be a Recon Update.
    assert attack_trace.record(db, p, content) == 0
    assert db.query(AttackSighting).count() == db.query(TrafficSighting).count() == 0


# ── the war records ───────────────────────────────────────────────────────
# Played on a two-board rig league (31 Oct 2026): Raider Two (board 2, county A,
# player 68) against Target One (board 1, county A, player 66). What each test
# expects is what the games told their players.
def records(name, kind):
    return [r for r in fe_packet.parse((FIXTURES / name).read_bytes()) if r.name == kind]


def test_a_recon_update_names_its_board_s_counties():
    rec, = records("war_900f0201.003", "Recon Update")
    assert [(r.letter, r.player_id, r.name) for r in fe_packet.realms(rec)] == \
        [("A", 68, "Raider Two")]


def test_an_attack_and_its_result_share_an_id():
    sent = [fe_packet.attack(r) for r in records("war_900f0201.003", "Attack")]
    back = [fe_packet.attack(r) for r in records("results_900f0102.003", "Attack Result")]
    assert [a.attack_id for a in sent] == [a.attack_id for a in back]
    assert len({a.attack_id for a in sent}) == 2
    a = sent[0]
    assert (a.from_planet, a.to_planet, a.attacker, a.target) == (2, 1, "A", "A")
    assert a.attack_type == "Capture People" and sent[1].attack_type == "Capture Resources"
    # "Date: 10/31/2026 00:57:09" on the attacker's report.
    assert a.stamp.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-31 00:57:09"
    assert not a.is_result and back[0].is_result and back[0].stamp is None


def test_forces_and_a_failure():
    """"You lost 11 Peasants / 26 Peasants have returned", and the defender
    "lost 4 Peasants"."""
    sent, = records("war_900f0201.003", "Attack")[:1]
    f = fe_packet.forces(sent)
    assert f.sent == {"unit_1": 37, "unit_2": 0, "unit_3": 0}
    assert (f.success, f.lost) == (None, None)
    r = fe_packet.forces(records("results_900f0102.003", "Attack Result")[0])
    assert (r.success, r.lost["unit_1"], r.defenders_lost) == (False, 11, 4)


def test_what_a_win_takes():
    land = fe_packet.forces(records("results_900f0102.005", "Attack Result")[0])
    assert (land.success, land.objective, land.gained, land.gained_what) == \
        (True, "Capture Land", 15, "acres")
    people = fe_packet.forces(records("results_900f0201.006", "Attack Result")[0])
    assert (people.success, people.gained, people.gained_what) == (True, 17, "people")


def test_a_trade_and_its_answer():
    deal, = records("war_900f0201.003", "Trade Deal")
    accepted, = records("war_900f0102.004", "Trade Result")
    t, r = fe_packet.traffic(deal), fe_packet.traffic(accepted)
    assert t.chain == r.chain and (t.role, r.role) == ("send", "result")
    assert (t.from_letter, t.from_player, t.to_player) == ("A", 68, 66)
    assert (r.from_player, r.to_player) == (66, 68)       # back to the sender
    assert fe_packet.traffic_details(deal) == {
        "offered": {"silver": 123, "food": 45, "stone": 6},
        "demanded": {"weapons": 7, "runes": 2}}
    assert fe_packet.traffic_details(accepted)["accepted"] is True
    rejected, = records("results_900f0201.004", "Trade Result")
    assert fe_packet.traffic_details(rejected) == {
        "offered": {"food": 9}, "demanded": {"silver": 8}, "accepted": False}


def test_a_spell_and_its_fizzle():
    spell, = records("war_900f0201.003", "Spell")
    fizzle, = records("results_900f0102.003", "Spell Result")
    s, f = fe_packet.traffic(spell), fe_packet.traffic(fizzle)
    assert s.chain == f.chain and s.name == "Spell"
    assert (s.from_player, s.to_player, f.from_player, f.to_player) == (68, 66, 66, 68)
    assert fe_packet.traffic_details(spell) == {"spell": "Wizard's Eye"}
    assert fe_packet.traffic_details(fizzle) == {"spell": "Wizard's Eye", "outcome": "fizzled"}


def test_messages_say_who_they_are_for_and_nothing_else():
    one, = records("war_900f0201.003", "Message")
    everyone, = records("war_900f0102.004", "Message")
    assert fe_packet.traffic(one).recipients == [66]
    assert fe_packet.traffic(everyone).recipients == [bre_packet.ALL_PLANETS]
    assert fe_packet.traffic(one).type == bre_packet.MESSAGE
    assert fe_packet.traffic_details(one) is None
