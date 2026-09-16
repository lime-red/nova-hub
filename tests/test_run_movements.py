"""Recording what the games said moved between nodes.

The transcript is the only record of this, and retention deletes it after 30
days, so the run's items are pulled out and stored while the transcript is still
there. These tests cover the storing, not the parsing -- the parser has its own
file.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import League, ProcessingRun, ProcessingRunItem
from backend.services.processing_service import ProcessingService

TRANSCRIPT = (
    "■  Processing Incoming Data (May take a while)\n"
    "DeCompress:  Old:  56  New:1448  %: 96.1%  Type: Recon Update   2-> 1\n"
    "■  Updating Local Recon Info\n"
    "Compress: Old: 1448  Size:   36  %: 97.5%  Type: Configupdate   1-> 2\n"
    "■  Planetary Maintenance Complete\n"
)


@pytest.fixture
def leagues(db_session):
    made = {}
    for number, name in (("900", "Test 900B"), ("901", "Test 901B")):
        lg = League(league_id=number, game_type="B", name=name, is_active=True)
        db_session.add(lg)
        made[number] = lg
    db_session.commit()
    for lg in made.values():
        db_session.refresh(lg)
    return made


@pytest.fixture
def run(db_session):
    r = ProcessingRun(status="running")
    db_session.add(r)
    db_session.commit()
    db_session.refresh(r)
    return r


@pytest.fixture
def service(db_session, tmp_path):
    # DosemuRunner reads [dosemu] at construction; nothing here runs a game.
    return ProcessingService(db_session, {
        "hub": {"bbs_index": "01"},
        "dosemu": {"dosemu_path": "/usr/bin/dosemu", "timeout": 30},
        "server": {"data_dir": str(tmp_path)},
    })


def test_each_item_is_stored_with_its_direction_and_nodes(db_session, service, run, leagues):
    service._record_movements(
        run,
        {"BRE_900": {"output": TRANSCRIPT}},
        {"BRE_900": leagues["900"].id},
    )
    db_session.commit()

    rows = db_session.query(ProcessingRunItem).all()
    assert {(r.direction, r.item_type, r.src_node, r.dst_node) for r in rows} == {
        ("in", "Recon Update", 2, 1),
        ("out", "Configupdate", 1, 2),
    }
    assert all(r.processing_run_id == run.id for r in rows)
    assert all(r.occurred_at == run.started_at for r in rows)


def test_items_are_attributed_to_the_league_whose_game_emitted_them(
    db_session, service, run, leagues
):
    """The reason this reads per group rather than from run.dosemu_log.

    One run covers every league with traffic, and their transcripts are
    concatenated into a single log. Attribution has to happen while the outputs
    are still separate, or every item in a multi-league run gets filed under
    whichever league happens to be first.
    """
    other = (
        "DeCompress:  Old:  10  New:  20  %: 50.0%  Type: Message        3-> 1\n"
    )
    service._record_movements(
        run,
        {"BRE_900": {"output": TRANSCRIPT}, "BRE_901": {"output": other}},
        {"BRE_900": leagues["900"].id, "BRE_901": leagues["901"].id},
    )
    db_session.commit()

    by_league = {}
    for row in db_session.query(ProcessingRunItem).all():
        by_league.setdefault(row.league_id, set()).add(row.item_type)

    assert by_league[leagues["900"].id] == {"Recon Update", "Configupdate"}
    assert by_league[leagues["901"].id] == {"Message"}


def test_a_transcript_that_cannot_be_read_does_not_fail_the_run(
    db_session, service, run, leagues, monkeypatch
):
    """Movement records are a nice-to-have; the packets are not.

    A parser that throws must cost the run its movement history and nothing
    else, or a formatting surprise in a transcript takes down packet delivery.
    """
    def explode(_raw):
        raise ValueError("unreadable")

    monkeypatch.setattr(
        "backend.services.transcript_service.parse", explode
    )

    service._record_movements(
        run, {"BRE_900": {"output": TRANSCRIPT}}, {"BRE_900": leagues["900"].id}
    )
    db_session.commit()

    assert db_session.query(ProcessingRunItem).count() == 0


def test_a_run_with_no_transcript_records_nothing_and_says_nothing(
    db_session, service, run, leagues
):
    """Failed runs and empty groups are ordinary, not exceptional."""
    service._record_movements(
        run,
        {"BRE_900": {"output": ""}, "BRE_901": None},
        {"BRE_900": leagues["900"].id, "BRE_901": leagues["901"].id},
    )
    db_session.commit()

    assert db_session.query(ProcessingRunItem).count() == 0


def test_repeated_screen_paints_do_not_inflate_the_stored_counts(
    db_session, service, run, leagues
):
    """dosemu repaints the screen; the database must not grow a copy each time."""
    service._record_movements(
        run, {"BRE_900": {"output": TRANSCRIPT * 8}}, {"BRE_900": leagues["900"].id}
    )
    db_session.commit()

    assert db_session.query(ProcessingRunItem).count() == 2


def test_deleting_a_run_takes_its_items_with_it(db_session, service, run, leagues):
    """Retention deletes old runs. Items must not outlive their run as orphans."""
    service._record_movements(
        run, {"BRE_900": {"output": TRANSCRIPT}}, {"BRE_900": leagues["900"].id}
    )
    db_session.commit()
    assert db_session.query(ProcessingRunItem).count() == 2

    db_session.delete(run)
    db_session.commit()

    assert db_session.query(ProcessingRunItem).count() == 0
