# tests/test_nodelist_generator.py - ROLLOUT_PLAN card F-2
#
# The generator shipped writing a nodelist with no hub in it. Because it runs
# after every processing batch, it overwrote the good hand-made BRNODES.015 on
# production and kept overwriting it; the file was never distributed, which is
# the only reason it did not break routing. These tests pin down the two things
# that were missing -- the hub's own entry and the HOST routing line -- and the
# refusal that stops a partial nodelist being written over a good one.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.models.database import Client, FtnAddress, League, LeagueMembership
from backend.services.nodes_parser import NodesFileParser
from backend.services.nodelist_generator import NodelistGenerator

HUB = {
    "bbs_name": "Nova Hub",
    "bbs_index": "01",
    "city": "Brisbane",
    "state": "QLD",
    "country": "AUS",
}


@pytest.fixture
def league(db_session):
    league = League(
        league_id="900",
        game_type="B",
        name="Test League 900",
        is_active=True,
        hub_fidonet_address="135:1/1",
    )
    db_session.add(league)
    db_session.commit()
    return league


def _member(db, league, index: int, name: str, fidonet: str, **location) -> Client:
    client = Client(
        client_id=f"node{index:02d}",
        client_secret="x",
        bbs_name=name,
        is_active=True,
        **location,
    )
    db.add(client)
    db.commit()
    db.add(
        LeagueMembership(
            client_id=client.id,
            league_id=league.id,
            bbs_index=index,
            ftn_address=FtnAddress(client_id=client.id, address=fidonet),
            is_active=True,
        )
    )
    db.commit()
    return client


def _generate(db, tmp_path, league, hub=HUB):
    return NodelistGenerator(db, str(tmp_path), hub).generate(league.id)


def test_hub_is_node_one_with_a_host_line(db_session, tmp_path, league):
    """The bug: memberships alone produce a nodelist with no hub and no routing."""
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8",
            city="Brisbane", state="QLD", country="AUS")
    _member(db_session, league, 3, "Eye of the Storm", "135:135/5")
    _member(db_session, league, 4, "The Eclipse", "135:135/20")

    dest = _generate(db_session, tmp_path, league)
    assert dest is not None

    lines = dest.read_text().splitlines()
    assert lines[0] == "1 HOST 2 3 4"
    assert lines[1] == "Nova Hub"
    assert lines[2] == "135:1/1"
    assert lines[3:6] == ["Brisbane", "QLD", "AUS"]


def test_parser_round_trips_the_generated_file(db_session, tmp_path, league):
    """Whatever we emit has to survive the parser the validator uses."""
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8",
            city="Brisbane", state="QLD", country="AUS")
    _member(db_session, league, 3, "Eye of the Storm", "135:135/5")

    dest = _generate(db_session, tmp_path, league)
    parser = NodesFileParser(dest)
    assert parser.parse(), parser.errors
    assert parser.errors == []

    by_index = {n.bbs_index: n for n in parser.nodes}
    assert set(by_index) == {1, 2, 3}
    assert by_index[1].routing_targets == [2, 3]
    assert by_index[2].bbs_name == "Starship Junkyard"
    assert by_index[2].city == "Brisbane"
    # A member with no location still yields a well-formed 6-line entry.
    assert by_index[3].city == ""
    assert by_index[3].fidonet_address == "135:135/5"


def test_refuses_to_write_without_a_hub_address(db_session, tmp_path, league):
    """A nodelist missing the hub is worse than yesterday's nodelist."""
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    league.hub_fidonet_address = None
    db_session.commit()

    assert _generate(db_session, tmp_path, league) is None
    assert not (tmp_path / "nodelists").exists()


def test_refusal_leaves_the_previous_file_untouched(db_session, tmp_path, league):
    """The production failure mode: a good file quietly replaced by a bad one."""
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    dest = _generate(db_session, tmp_path, league)
    good = dest.read_text()

    league.hub_fidonet_address = "   "  # whitespace is not an address
    db_session.commit()

    assert _generate(db_session, tmp_path, league) is None
    assert dest.read_text() == good


def test_no_members_is_the_hub_alone(db_session, tmp_path, league):
    """A new league's game starts with no members: its nodes file is the hub's
    entry, HOSTing no one, which RESET and PLANETARY both take."""
    lines = _generate(db_session, tmp_path, league).read_text().splitlines()
    assert lines[0] == "1 HOST"
    assert len([l for l in lines if l == ""]) == 1  # one entry


def test_inactive_members_are_excluded_from_the_host_line(db_session, tmp_path, league):
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    _member(db_session, league, 3, "Eye of the Storm", "135:135/5")
    membership = (
        db_session.query(LeagueMembership)
        .filter(LeagueMembership.bbs_index == 3)
        .first()
    )
    membership.is_active = False
    db_session.commit()

    dest = _generate(db_session, tmp_path, league)
    lines = dest.read_text().splitlines()
    assert lines[0] == "1 HOST 2"
    assert "Eye of the Storm" not in lines


def test_a_membership_at_the_hub_index_does_not_duplicate_node_one(
    db_session, tmp_path, league
):
    """Config wins, so the hub cannot appear twice with conflicting details."""
    _member(db_session, league, 1, "Impostor Hub", "135:1/99")
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")

    dest = _generate(db_session, tmp_path, league)
    parser = NodesFileParser(dest)
    assert parser.parse(), parser.errors
    indices = [n.bbs_index for n in parser.nodes]
    assert indices == [1, 2]
    assert parser.nodes[0].bbs_name == "Nova Hub"
    assert parser.nodes[0].routing_targets == [2]


def test_newlines_in_a_name_cannot_shift_the_file(db_session, tmp_path, league):
    """A stray newline would re-point every field below it."""
    _member(db_session, league, 2, "Bad\nName", "135:135/8")

    dest = _generate(db_session, tmp_path, league)
    parser = NodesFileParser(dest)
    assert parser.parse(), parser.errors
    assert parser.nodes[1].bbs_name == "Bad Name"
    assert parser.nodes[1].fidonet_address == "135:135/8"


def test_fe_league_uses_the_fenodes_prefix(db_session, tmp_path):
    league = League(
        league_id="900",
        game_type="F",
        name="FE 900",
        is_active=True,
        hub_fidonet_address="135:1/1",
    )
    db_session.add(league)
    db_session.commit()
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")

    dest = _generate(db_session, tmp_path, league)
    assert dest.name == "FENODES.900"
    assert dest.parent == tmp_path / "nodelists" / "fe" / "900"


def test_a_league_that_does_not_route_through_the_hub_gets_a_bare_index(
    db_session, tmp_path, league
):
    """013's nodes.dat has a bare "1"; generating HOST over it would re-route it."""
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    league.hub_routes_mail = False
    db_session.commit()

    dest = _generate(db_session, tmp_path, league)
    lines = dest.read_text().splitlines()
    assert lines[0] == "1"

    parser = NodesFileParser(dest)
    assert parser.parse(), parser.errors
    assert parser.nodes[0].routing_targets == []


def test_the_file_is_written_with_dos_line_endings(db_session, tmp_path, league):
    """Every nodes.dat the games use is CRLF; this generator emitted LF."""
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")

    dest = _generate(db_session, tmp_path, league)
    raw = dest.read_bytes()
    assert b"\r\n" in raw
    # No bare LF anywhere: every newline in the file is a full CRLF pair.
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0
    assert raw.endswith(b"\r\n")


def test_no_temporary_file_is_left_behind(db_session, tmp_path, league):
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    dest = _generate(db_session, tmp_path, league)
    assert list(dest.parent.iterdir()) == [dest]


# -- the hub's own game gets the nodelist, so it sends it to the members ------
#
# Generating only wrote the hub-side copy. Members receive a nodelist when the
# hub's game sends it after its own nodes file changes, so a board added through
# self-service never reached anyone (prod, Oct 2026: Tentacle BBS in 015).

def _game(tmp_path, league, name=None, content=None):
    """The league's hub game, and the config that finds it."""
    from sqlalchemy.orm import object_session
    from tests.conftest import install_game

    config = {"server": {"data_dir": str(tmp_path / "data")}}
    folder = install_game(object_session(league), config, league, tmp_path / "drive_c")
    if name:
        (folder / name).write_bytes(content if content is not None else b"1 HOST 2\r\nold\r\n")
    return folder, config


def _files(folder):
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def _generator(db, tmp_path, config):
    return NodelistGenerator(db, str(tmp_path / "data"), HUB, config)


@pytest.mark.parametrize("name", ["BRNODES.DAT", "brnodes.dat", "BrNodes.Dat"])
def test_the_game_file_is_rewritten_under_its_own_name(db_session, tmp_path, league, name):
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    folder, dosemu = _game(tmp_path, league, name)

    generator = _generator(db_session, tmp_path, dosemu)
    dest = generator.generate(league.id)

    assert _files(folder) == [name]
    assert (folder / name).read_bytes() == dest.read_bytes()
    assert generator.problems == []


def test_a_game_with_no_nodes_file_gets_one_in_upper_case(db_session, tmp_path, league):
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    folder, dosemu = _game(tmp_path, league)

    dest = _generator(db_session, tmp_path, dosemu).generate(league.id)

    assert (folder / "BRNODES.DAT").read_bytes() == dest.read_bytes()


def test_an_unchanged_nodelist_leaves_the_game_file_alone(db_session, tmp_path, league):
    """Processing regenerates after every batch; rewriting each time would make
    the game resend the same nodelist to everyone on every run."""
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    folder, dosemu = _game(tmp_path, league)
    _generator(db_session, tmp_path, dosemu).generate(league.id)
    game_file = folder / "BRNODES.DAT"
    old = game_file.stat().st_mtime_ns - 10_000_000_000
    import os
    os.utime(game_file, ns=(old, old))

    _generator(db_session, tmp_path, dosemu).generate(league.id)

    assert game_file.stat().st_mtime_ns == old


def test_an_unchanged_nodelist_is_not_rewritten_or_logged(db_session, tmp_path, league, caplog):
    """Regenerating after every batch logged "Generated nodelist" each run on
    prod, though nothing had changed."""
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    dest = _generate(db_session, tmp_path, league)
    old = dest.stat().st_mtime_ns - 10_000_000_000
    import os
    os.utime(dest, ns=(old, old))

    caplog.clear()
    with caplog.at_level("INFO"):
        assert _generate(db_session, tmp_path, league) == dest

    assert dest.stat().st_mtime_ns == old
    assert "Generated nodelist" not in caplog.text


def test_a_new_member_reaches_the_game_file(db_session, tmp_path, league):
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    folder, dosemu = _game(tmp_path, league)
    _generator(db_session, tmp_path, dosemu).generate(league.id)

    _member(db_session, league, 6, "Tentacle BBS", "135:135/6")
    _generator(db_session, tmp_path, dosemu).generate(league.id)

    nodes = NodesFileParser(folder / "BRNODES.DAT")
    nodes.parse()
    assert sorted(n.bbs_index for n in nodes.nodes) == [1, 2, 6]
    assert (folder / "BRNODES.DAT").read_bytes().startswith(b"1 HOST 2 6\r\n")


def test_no_game_folder_is_reported_not_fatal(db_session, tmp_path, league):
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")

    generator = _generator(db_session, tmp_path, {})
    dest = generator.generate(league.id)

    assert dest.exists()
    assert len(generator.problems) == 1
    assert "the hub has no game for this league" in generator.problems[0]


def test_two_nodes_files_differing_by_case_are_left_alone(db_session, tmp_path, league):
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    folder, dosemu = _game(tmp_path, league, "BRNODES.DAT", b"upper")
    (folder / "brnodes.dat").write_bytes(b"lower")

    generator = _generator(db_session, tmp_path, dosemu)
    generator.generate(league.id)

    assert (folder / "BRNODES.DAT").read_bytes() == b"upper"
    assert (folder / "brnodes.dat").read_bytes() == b"lower"
    assert "neither was changed" in generator.problems[0]


def test_without_config_no_game_is_touched(db_session, tmp_path, league):
    _member(db_session, league, 2, "Starship Junkyard", "135:135/8")
    folder, _ = _game(tmp_path, league, "BRNODES.DAT", b"untouched")

    generator = NodelistGenerator(db_session, str(tmp_path / "data"), HUB)
    generator.generate(league.id)

    assert (folder / "BRNODES.DAT").read_bytes() == b"untouched"
    assert generator.problems == []
