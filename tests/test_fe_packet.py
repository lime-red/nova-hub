"""Falcon's Eye packets: BRE's container, FE's type table.

The fixtures are two packets node 2 of the rig's 900F wrote (Oct 2026). What is
expected of each is what FE itself printed while writing it, in its /DETAILED
transcript: every record's type name, decompressed size and ends, in order.
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


def test_an_fe_league_records_nothing_yet(db):
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
