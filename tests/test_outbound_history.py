"""A packet the hub's game writes keeps its own row, even after its name recurs.

Until this was changed the collector reused the row whenever a filename came
round again, so a hub-originated route was a table of 1,000 slots: after a wrap
the row said the new packet had arrived months ago, and the old packet -- when
it was written, when its board took it, the relayed attack it carried -- was
gone. In production that erased the relay hop of every attack older than about
a thousand packets on its route, which the Attacks view then showed as a result
never delivered.

A name that recurs while its row is still waiting to be downloaded is the same
packet rewritten, and still updates in place.
"""
import asyncio
import hashlib
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import AttackSighting, Base, League, Packet
from backend.services.processing_service import ProcessingService

FIXTURES = Path(__file__).parent / "fixtures" / "bre_packets"
NAME = "901B0102.002"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(League(league_id="901", game_type="B", name="Rig 901B"))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def hub(db, tmp_path):
    game_out = tmp_path / "game_outbound"
    game_out.mkdir()
    service = ProcessingService(db, {"hub": {"bbs_index": "01"},
                                     "server": {"data_dir": str(tmp_path / "data")},
                                     "dosemu": {"dosemu_path": "/usr/bin/dosemu", "timeout": 60}})

    def emit(fixture):
        (game_out / NAME).write_bytes((FIXTURES / fixture).read_bytes())
        league = db.query(League).filter(League.league_id == "901").one()
        asyncio.run(service.collect_outbound_packets(league, run_id=None, outbound_dir=game_out))
        return db.query(Packet).filter(Packet.filename == NAME).order_by(Packet.id).all()

    return emit


def test_a_wrapped_name_gets_a_new_row_and_the_old_one_keeps_its_history(db, hub):
    first, = hub("results_901b0102.002")
    first.is_downloaded = True
    db.commit()

    old, new = hub("results_late_901b0102.004")
    assert old.id == first.id and old.is_downloaded
    assert old.checksum == hashlib.sha256((FIXTURES / "results_901b0102.002").read_bytes()).hexdigest()
    assert not new.is_downloaded
    # Both packets' attacks remain traceable, each against its own row.
    assert {s.packet_id for s in db.query(AttackSighting)} == {old.id, new.id}


def test_a_name_that_recurs_before_it_was_taken_is_the_same_packet_rewritten(db, hub):
    hub("results_901b0102.002")
    rows = hub("results_late_901b0102.004")
    assert len(rows) == 1
    assert rows[0].checksum == hashlib.sha256(
        (FIXTURES / "results_late_901b0102.004").read_bytes()).hexdigest()
    assert rows[0].file_data is None


def test_a_row_with_a_download_time_is_taken_even_without_the_flag(db, hub):
    """Rows from January 2026 carry downloaded_at with is_downloaded false. A
    reset's Game Start reused one, and showed as downloaded months before it
    was written."""
    from datetime import datetime
    first, = hub("results_901b0102.002")
    first.downloaded_at = datetime(2026, 1, 11, 3, 33)
    db.commit()

    old, new = hub("results_late_901b0102.004")
    assert old.id == first.id and old.downloaded_at == datetime(2026, 1, 11, 3, 33)
    assert new.downloaded_at is None
