# tests/test_nodes_parser.py - Unit tests for the BRE/FE nodes.dat parser

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.nodes_parser import NodesFileParser, BBSNode


def _write_nodes(content: str) -> Path:
    """Write content to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".dat", delete=False, encoding="utf-8")
    tmp.write(content)
    tmp.flush()
    return Path(tmp.name)


VALID_TWO_NODE = """\
1
Alpha BBS
13:10/101
Springfield
IL
US

2
Beta BBS
13:10/102
Shelbyville
IL
US
"""

ROUTING_NODE = """\
1 HOST 2 3
Alpha BBS
13:10/101
Springfield
IL
US
"""


class TestNodesFileParser:
    def test_parse_valid_two_nodes(self):
        path = _write_nodes(VALID_TWO_NODE)
        parser = NodesFileParser(path)
        ok = parser.parse()
        assert ok
        assert len(parser.nodes) == 2
        assert parser.nodes[0].bbs_index == 1
        assert parser.nodes[0].bbs_name == "Alpha BBS"
        assert parser.nodes[0].fidonet_address == "13:10/101"
        assert parser.nodes[1].bbs_index == 2
        assert parser.nodes[1].bbs_name == "Beta BBS"

    def test_routing_targets_parsed(self):
        path = _write_nodes(ROUTING_NODE)
        parser = NodesFileParser(path)
        parser.parse()
        assert len(parser.nodes) == 1
        node = parser.nodes[0]
        assert node.bbs_index == 1
        assert node.routing_targets == [2, 3]

    def test_no_routing_targets_when_plain_index(self):
        path = _write_nodes(VALID_TWO_NODE)
        parser = NodesFileParser(path)
        parser.parse()
        assert parser.nodes[0].routing_targets == []

    def test_file_not_found(self):
        parser = NodesFileParser(Path("/nonexistent/path/nodes.dat"))
        ok = parser.parse()
        assert not ok
        assert parser.errors

    def test_incomplete_entry_reported(self):
        # Only 3 lines, incomplete entry
        content = "1\nAlpha BBS\n13:10/101\n"
        path = _write_nodes(content)
        parser = NodesFileParser(path)
        ok = parser.parse()
        assert not ok

    def test_invalid_bbs_index_skipped(self):
        content = "notanumber\nAlpha BBS\n13:10/101\nCity\nState\nCountry\n"
        path = _write_nodes(content)
        parser = NodesFileParser(path)
        parser.parse()
        assert any("Invalid BBS index" in e for e in parser.errors)

    def test_duplicate_index_detection(self):
        content = VALID_TWO_NODE + "1\nDup BBS\n13:10/200\nCity\nState\nCountry\n"
        path = _write_nodes(content)
        parser = NodesFileParser(path)
        parser.parse()
        dups = parser.check_duplicate_indices()
        assert len(dups) == 1
        assert "1" in dups[0]

    def test_get_node_by_index(self):
        path = _write_nodes(VALID_TWO_NODE)
        parser = NodesFileParser(path)
        parser.parse()
        node = parser.get_node_by_index(2)
        assert node is not None
        assert node.bbs_name == "Beta BBS"

    def test_get_node_by_name(self):
        path = _write_nodes(VALID_TWO_NODE)
        parser = NodesFileParser(path)
        parser.parse()
        node = parser.get_node_by_name("alpha bbs")  # Case-insensitive
        assert node is not None
        assert node.bbs_index == 1

    def test_get_node_not_found(self):
        path = _write_nodes(VALID_TWO_NODE)
        parser = NodesFileParser(path)
        parser.parse()
        assert parser.get_node_by_index(99) is None
        assert parser.get_node_by_name("Unknown BBS") is None
