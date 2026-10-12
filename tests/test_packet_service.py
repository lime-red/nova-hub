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

    def test_trailing_newline_is_refused(self):
        assert parse_packet_filename("555B0201.001\n") is None

    def test_hex_ff_indices(self):
        result = parse_packet_filename("555BFF01.000")
        assert result is not None
        assert result["source_bbs_index"] == "FF"


class TestParseIBPacketFilename:
    """Immortal Barons: L<league>-<src>-<seq>-<dst>.brp, all base 36"""

    def test_valid_ib_packet(self):
        result = parse_packet_filename("L555-a-000000000001z-1.brp")
        assert result == {
            "league_id": "555",
            "game_type": "I",
            "source_bbs_index": "0A",
            "dest_bbs_index": "01",
            "sequence_number": 71,
        }

    def test_hub_upper_cased_name_parses_the_same(self):
        assert (parse_packet_filename("L555-A-000000000001Z-1.BRP")
                == parse_packet_filename("l555-a-000000000001z-1.brp"))

    def test_broadcast_goes_to_board_zero(self):
        result = parse_packet_filename("L555-2-0000000000001-0.brp")
        assert result["dest_bbs_index"] == "00"

    def test_highest_node_the_hub_can_hold(self):
        # 255 is "73" in base 36
        result = parse_packet_filename("L555-73-0000000000001-1.brp")
        assert result["source_bbs_index"] == "FF"

    def test_node_beyond_bbs_index_range_is_refused(self):
        # IB allows nodes to 999 ("rr"); the hub's bbs_index stops at 255
        assert parse_packet_filename("L555-74-0000000000001-1.brp") is None
        assert parse_packet_filename("L555-1-0000000000001-rr.brp") is None

    def test_sequence_beyond_the_column_is_refused(self):
        # IB's counter is 64-bit; the column is 32-bit outside SQLite
        assert parse_packet_filename("L555-1-0000000zik0zk-2.brp") is None
        assert parse_packet_filename("L555-1-0000000zik0zj-2.brp")["sequence_number"] == 2**31 - 1

    def test_unpadded_sequence_is_refused(self):
        assert parse_packet_filename("L555-1-1-2.brp") is None

    def test_name_without_league_is_refused(self):
        # IB leaves the league off when it has none, and adds a digest
        assert parse_packet_filename("ABCDEFGHIJKLMNOP-1-0000000000001-2.brp") is None

    def test_content_digest_name_is_refused(self):
        assert parse_packet_filename("L555-ABCDEFGHIJKLMNOPQRSTUVWXYZ.brp") is None

    def test_leading_zero_node_is_refused(self):
        # One packet, one name: IB never pads node numbers
        assert parse_packet_filename("L555-02-0000000000001-1.brp") is None
        assert parse_packet_filename("L555-2-0000000000001-01.brp") is None
        assert parse_packet_filename("L555-2-0000000000001-00.brp") is None

    def test_source_zero_is_refused(self):
        assert parse_packet_filename("L555-0-0000000000001-1.brp") is None

    def test_trailing_newline_is_refused(self):
        assert parse_packet_filename("L555-2-0000000000001-1.brp\n") is None

    def test_dos_style_ib_name_is_refused(self):
        assert parse_packet_filename("555I0201.001") is None


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
