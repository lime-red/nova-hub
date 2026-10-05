# tests/test_games.py - Unit tests for the per-game table

import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.schemas.leagues import LeagueCreate
from backend.services.games import (
    GAMES,
    LEAGUE_ID_REGEX,
    game_for_code,
    game_for_letter,
    nodelist_game,
)


class TestLookups:
    def test_letter_and_code_reach_the_same_row(self):
        for g in GAMES:
            assert game_for_letter(g.letter) is g
            assert game_for_code(g.code) is g
            assert game_for_code(g.key) is g

    def test_bre_names(self):
        g = game_for_letter("B")
        assert (g.code, g.key, g.nodes_file) == ("BRE", "bre", "brnodes.dat")
        assert g.nodelist_filename("555") == "BRNODES.555"

    def test_fe_names(self):
        g = game_for_letter("f")
        assert (g.code, g.key, g.nodes_file) == ("FE", "fe", "fenodes.dat")
        assert g.nodelist_filename("013") == "FENODES.013"

    def test_unknown_game_is_an_error_not_fe(self):
        with pytest.raises(KeyError):
            game_for_letter("X")
        with pytest.raises(KeyError):
            game_for_code("xyz")


class TestNodelistGame:
    def test_recognizes_each_game(self):
        assert nodelist_game("BRNODES.555").code == "BRE"
        assert nodelist_game("fenodes.013").code == "FE"

    def test_league_number_comes_from_the_name(self):
        assert game_for_letter("B").nodelist_league("brnodes.013") == "013"
        assert game_for_letter("F").nodelist_league("BRNODES.013") is None

    def test_packets_are_not_nodelists(self):
        assert nodelist_game("555B0201.001") is None
        assert nodelist_game("BRNODES") is None


class TestLeagueIdRegex:
    def test_matches_known_games_only(self):
        assert re.match(LEAGUE_ID_REGEX, "555B")
        assert re.match(LEAGUE_ID_REGEX, "555F")
        assert not re.match(LEAGUE_ID_REGEX, "555X")
        assert not re.match(LEAGUE_ID_REGEX, "55B")


class TestLeagueCreate:
    def test_accepts_and_uppercases_a_known_game(self):
        req = LeagueCreate(league_id="555", game_type="f", name="x")
        assert req.game_type == "F"

    def test_rejects_an_unknown_game(self):
        with pytest.raises(ValidationError):
            LeagueCreate(league_id="555", game_type="X", name="x")
