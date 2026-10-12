"""The games the hub serves, and every name each one goes by.

A game is known by several names in different places: one letter in league
ids and the database (B), a short code in config sections, directory names and
log lines (BRE), a full name for display, the name of its nodelist file, and
the shape of its packet filenames. They all live here so that adding a game
means adding a row, not finding every place that assumed there were only two.
"""

import re
from dataclasses import dataclass
from typing import Optional

# Packet.sequence_number is an Integer column: 32-bit outside SQLite.
MAX_SEQUENCE = 2**31 - 1
# LeagueMembership.bbs_index, and the two hex digits the hub stores it as.
MAX_BBS_INDEX = 255


@dataclass(frozen=True)
class Game:
    letter: str  # "B": league ids (555B), packet filenames, League.game_type
    code: str  # "BRE": config keys and directories use it lower-cased
    name: str  # "Barren Realms Elite"
    nodelist_prefix: str  # "BRNODES": hub nodelists are BRNODES.<league>
    nodes_file: str  # "brnodes.dat": the nodes file in a game's own folder
    # Whole upper-cased packet filename, with groups league, src, dst and seq
    packet_pattern: str
    node_base: int = 16  # how src and dst are written in the filename
    seq_base: int = 10  # how seq is written in the filename

    @property
    def key(self) -> str:
        """Lower-case code, as used in config sections and directory names."""
        return self.code.lower()

    def nodelist_filename(self, league_number: str) -> str:
        return f"{self.nodelist_prefix}.{league_number}"

    def nodelist_league(self, filename: str) -> Optional[str]:
        """The league number in this game's nodelist filename, or None."""
        prefix = self.nodelist_prefix + "."
        upper = filename.upper()
        return upper[len(prefix):] if upper.startswith(prefix) else None

    def parse_packet_name(self, filename: str) -> Optional[dict]:
        """League, ends and sequence of one of this game's packets, or None.

        Board numbers come back as two upper-case hex digits whatever base
        the game writes them in, since that is how the hub stores and compares
        them.
        """
        m = re.fullmatch(self.packet_pattern, filename.upper())
        if not m:
            return None
        src = int(m["src"], self.node_base)
        dst = int(m["dst"], self.node_base)
        seq = int(m["seq"], self.seq_base)
        if src > MAX_BBS_INDEX or dst > MAX_BBS_INDEX or seq > MAX_SEQUENCE:
            return None
        return {
            "league_id": m["league"],
            "game_type": self.letter,
            "source_bbs_index": format(src, "02X"),
            "dest_bbs_index": format(dst, "02X"),
            "sequence_number": seq,
        }


def _dos_packet_pattern(letter: str) -> str:
    """BRE and FE: 555B0201.001, league + game + source + dest (hex) . seq"""
    return (rf"(?P<league>\d{{3}}){letter}(?P<src>[0-9A-F]{{2}})"
            rf"(?P<dst>[0-9A-F]{{2}})\.(?P<seq>\d{{3}})")


# Immortal Barons: L555-<src>-<seq>-<dst>.brp, nodes and a 64-bit sequence
# in base 36, the sequence zero-padded to 13 digits. The hub takes nodes up to
# MAX_BBS_INDEX. dst 0 is a broadcast to every board.
# No leading zeros, so one packet has only one name.
_IB_PACKET_PATTERN = (r"L(?P<league>\d{3})-"
                      r"(?P<src>[1-9A-Z][0-9A-Z]?)-"
                      r"(?P<seq>[0-9A-Z]{13})-"
                      r"(?P<dst>0|[1-9A-Z][0-9A-Z]?)\.BRP")


GAMES = (
    Game("B", "BRE", "Barren Realms Elite", "BRNODES", "brnodes.dat",
         _dos_packet_pattern("B")),
    Game("F", "FE", "Falcon's Eye", "FENODES", "fenodes.dat",
         _dos_packet_pattern("F")),
    Game("I", "IB", "Immortal Barons", "IBNODES", "ibnodes.dat",
         _IB_PACKET_PATTERN, node_base=36, seq_base=36),
)

_BY_LETTER = {g.letter: g for g in GAMES}
_BY_CODE = {g.code: g for g in GAMES}

# For regex character classes: "BFI"
GAME_LETTERS = "".join(g.letter for g in GAMES)
# Groups: league number, game letter
LEAGUE_ID_REGEX = rf"^(\d{{3}})([{GAME_LETTERS}])$"


def game_for_letter(letter: str) -> Game:
    """The game for a league's one-letter type. Raises KeyError if unknown."""
    return _BY_LETTER[letter.upper()]


def game_for_code(code: str) -> Game:
    """The game for a code such as "BRE" or "bre". Raises KeyError if unknown."""
    return _BY_CODE[code.upper()]


def nodelist_game(filename: str) -> Optional[Game]:
    """The game whose nodelist this filename is, or None if it is not one."""
    for g in GAMES:
        if g.nodelist_league(filename) is not None:
            return g
    return None
