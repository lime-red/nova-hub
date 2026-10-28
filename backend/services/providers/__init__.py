"""The provider for each game the hub serves. See base.py."""

from backend.services.games import GAMES, game_for_letter
from backend.services.providers.base import GameProvider
from backend.services.providers.immortal_barons import ImmortalBaronsProvider
from backend.services.providers.sr_games import SRGamesProvider

_SR = SRGamesProvider()
_IB = ImmortalBaronsProvider()

# By League.game_type
PROVIDERS: dict[str, GameProvider] = {"B": _SR, "F": _SR, "I": _IB}

assert set(PROVIDERS) == {g.letter for g in GAMES}, "every game needs a provider"


def provider_for(game_type: str) -> GameProvider:
    """The provider for a league's one-letter game type. Raises KeyError if unknown."""
    return PROVIDERS[game_for_letter(game_type).letter]


def letters_where(flag: str) -> tuple[str, ...]:
    """The game letters whose provider has `flag` set, e.g. "checks_sequences"."""
    return tuple(letter for letter, p in PROVIDERS.items() if getattr(p, flag))
