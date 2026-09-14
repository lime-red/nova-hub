# tests/test_validator_nodes.py - ROLLOUT_PLAN card F-2a
#
# The validator reported the hub's own entry as "not in the database" on every
# run, for both production leagues, because the hub has no membership row and
# never will -- it is written from [hub] config. Two permanent false warnings
# train people to ignore the warning list, which is where the real ones go.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import Client, League, LeagueMembership
from backend.services.validator import HubValidator

NODES = "\r\n".join([
    "1 HOST 2",
    "Nova Hub",
    "135:1/1",
    "Brisbane",
    "QLD",
    "AUS",
    "",
    "2",
    "Starship Junkyard",
    "135:135/8",
    "Brisbane",
    "QLD",
    "AUS",
    "",
]) + "\r\n"


@pytest.fixture
def game_folder(tmp_path):
    folder = tmp_path / "bre_900"
    folder.mkdir()
    (folder / "brnodes.dat").write_bytes(NODES.encode())
    return folder


@pytest.fixture
def seeded_db(db_session):
    league = League(league_id="900", game_type="B", name="Test 900", is_active=True)
    db_session.add(league)
    db_session.commit()
    client = Client(
        client_id="node02",
        client_secret="x",
        bbs_name="Starship Junkyard",
        is_active=True,
    )
    db_session.add(client)
    db_session.commit()
    db_session.add(
        LeagueMembership(
            client_id=client.id,
            league_id=league.id,
            bbs_index=2,
            fidonet_address="135:135/8",
            is_active=True,
        )
    )
    db_session.commit()
    return db_session


def _validate(db, game_folder, hub_index="01"):
    validator = HubValidator(db_session=db)
    validator.config = {"hub": {"bbs_name": "Nova Hub", "bbs_index": hub_index}}
    validator.validate_nodes_file_against_db(
        "bre", "900", {"game_folder": str(game_folder)}
    )
    return validator


def test_the_hub_is_not_reported_as_missing_from_the_database(seeded_db, game_folder):
    validator = _validate(seeded_db, game_folder)
    assert validator.errors == []
    assert [w.message for w in validator.warnings] == []


def test_a_genuinely_unknown_node_is_still_reported(seeded_db, game_folder):
    """Suppressing the hub must not suppress everything else."""
    extra = NODES + "\r\n".join(["9", "Ghost BBS", "135:135/99", "", "", "", ""]) + "\r\n"
    (game_folder / "brnodes.dat").write_bytes(extra.encode())

    validator = _validate(seeded_db, game_folder)
    messages = " ".join(w.message for w in validator.warnings)
    assert "BBS index 9" in messages
    assert "Nova Hub" not in messages


def test_an_unparseable_hub_index_does_not_suppress_anything(seeded_db, game_folder):
    validator = _validate(seeded_db, game_folder, hub_index="not-a-number")
    messages = " ".join(w.message for w in validator.warnings)
    assert "BBS index 1" in messages
