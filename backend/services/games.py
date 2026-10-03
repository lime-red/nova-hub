"""The games the hub serves, and every name each one goes by.

A game is known by several names in different places: one letter in league
ids, packet filenames and the database (B), a short code in config sections,
directory names and log lines (BRE), a full name for display, and the name of
its nodelist file. They all live here so that adding a game means adding a row,
not finding every place that assumed there were only two.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Game:
    letter: str  # "B": league ids (555B), packet filenames, League.game_type
    code: str  # "BRE": config keys and directories use it lower-cased
    name: str  # "Barren Realms Elite"
    nodelist_prefix: str  # "BRNODES": hub nodelists are BRNODES.<league>
    nodes_file: str  # "brnodes.dat": the nodes file in a game's own folder

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


GAMES = (
    Game("B", "BRE", "Barren Realms Elite", "BRNODES", "brnodes.dat"),
    Game("F", "FE", "Falcon's Eye", "FENODES", "fenodes.dat"),
)

_BY_LETTER = {g.letter: g for g in GAMES}
_BY_CODE = {g.code: g for g in GAMES}

# For regex character classes: "BF"
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
