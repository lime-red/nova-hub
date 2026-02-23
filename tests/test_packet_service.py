# tests/test_packet_service.py - Unit tests for packet_service

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.packet_service import parse_packet_filename, PacketService


# ---------------------------------------------------------------------------
# parse_packet_filename
# ---------------------------------------------------------------------------

class TestParsePacketFilename:
    def test_valid_bre_packet(self):
        result = parse_packet_filename("555B0201.001")
        assert result is not None
        assert result["league_id"] == "555"
        assert result["game_type"] == "B"
        assert result["source_bbs_index"] == "02"
        assert result["dest_bbs_index"] == "01"
        assert result["sequence_number"] == 1

    def test_valid_fe_packet(self):
        result = parse_packet_filename("013F0A0B.999")
        assert result is not None
        assert result["league_id"] == "013"
        assert result["game_type"] == "F"
        assert result["source_bbs_index"] == "0A"
        assert result["dest_bbs_index"] == "0B"
        assert result["sequence_number"] == 999

    def test_lowercase_normalised(self):
        result = parse_packet_filename("555b0201.001")
        assert result is not None
        assert result["game_type"] == "B"

    def test_sequence_zero(self):
        result = parse_packet_filename("555B0201.000")
        assert result is not None
        assert result["sequence_number"] == 0

    def test_invalid_game_type(self):
        assert parse_packet_filename("555X0201.001") is None

    def test_too_short(self):
        assert parse_packet_filename("5B0201.001") is None

    def test_missing_extension(self):
        assert parse_packet_filename("555B020100") is None

    def test_nodelist_not_matched(self):
        assert parse_packet_filename("BRNODES.555") is None

    def test_empty_string(self):
        assert parse_packet_filename("") is None

    def test_hex_ff_indices(self):
        result = parse_packet_filename("555BFF01.000")
        assert result is not None
        assert result["source_bbs_index"] == "FF"


# ---------------------------------------------------------------------------
# PacketService.validate_packet_filename
# ---------------------------------------------------------------------------

class TestValidatePacketFilename:
    def test_valid(self, db_session):
        svc = PacketService(db_session)
        valid, err = svc.validate_packet_filename("555B0201.001")
        assert valid is True
        assert err is None

    def test_invalid(self, db_session):
        svc = PacketService(db_session)
        valid, err = svc.validate_packet_filename("garbage")
        assert valid is False
        assert err is not None


# ---------------------------------------------------------------------------
# PacketService.find_membership_by_index
# ---------------------------------------------------------------------------

class TestFindMembershipByIndex:
    def test_found(self, db_session, sample_membership, sample_league):
        svc = PacketService(db_session)
        result = svc.find_membership_by_index(sample_league.id, "02")
        assert result is not None
        assert result.bbs_index == 2

    def test_not_found_wrong_index(self, db_session, sample_membership, sample_league):
        svc = PacketService(db_session)
        result = svc.find_membership_by_index(sample_league.id, "FF")
        assert result is None

    def test_not_found_wrong_league(self, db_session, sample_membership):
        svc = PacketService(db_session)
        result = svc.find_membership_by_index(9999, "02")
        assert result is None

    def test_invalid_hex(self, db_session, sample_league):
        svc = PacketService(db_session)
        result = svc.find_membership_by_index(sample_league.id, "ZZ")
        assert result is None
