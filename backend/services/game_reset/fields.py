"""Every setting a league reset sets: one table per game.

The table drives draft validation, the management API, the console form and
(later) the driver that types the answers into the editor, so none of them can
drift from the others. Labels, order, defaults and maximums were read off the
editors on the test rig (14 Oct 2026); see the design doc "Nova Hub: Game Reset
from the Console".

Things the editor does that the table encodes:

- Neither game has any minimum: every number field takes 0, and so does the
  form. `advised_min` is our judgement of where a smaller value stops a normal
  game, and `advised_reason` says why. It only warns: a 0 can be a real league
  choice later on, say a free-for-all stage where nobody can buy anything.
- Over the maximum, the editor puts the maximum on the prompt and waits for a
  second Enter. A driver typing one Enter falls out of step, so nothing past
  validation may carry an over-max value.
- A choice takes one letter. A letter the field does not allow is ignored, so
  `choices` lists only what the editor accepts (no None on Attack Damage).
- Sabre Constant asks a second question, a dial from 0 to 10; that is the
  dependent field `sabre_dial`.
- Dates are a date only. A typed date starts at 00:00; RESET itself stamps
  Start Date with the moment of the reset, so a blank one is left alone.

Immortal Barons has no editor to drive: its settings are the Coordinator's
config.json, read off IB v0.2.6's own config editor (internal/menu), limits
and all. IB does have minimums (a turn a day, BRE's floor on interest and
investment), and a blank date is its "none": start now, or no join cut-off.
"""
from dataclasses import dataclass, replace
from typing import Optional

from backend.services.game_reset import help_text

NUMBER = "number"
CHOICE = "choice"
TOGGLE = "toggle"
DATE = "date"
TEXT = "text"                       # IB's required version, the one free-text field

PAGE_GENERAL = "General"
PAGE_GAMEPLAY = "GamePlay"
PAGE_INTERBBS = "InterBBS"

# Join Date's "no cut-off": the editor's own default.
NO_JOIN_CUTOFF = "2999-01-01"

HML = (("H", "High"), ("M", "Medium"), ("L", "Low"))
HMLN = HML + (("N", "None"),)


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    page: int                       # editor page, 1 or 2
    group: str                      # how the form groups it
    kind: str
    default: object                 # None for Start Date: the day of the reset
    min: Optional[int] = None       # the game's own: 0 for every BRE and FE number
    max: Optional[int] = None
    advised_min: Optional[int] = None
    choices: tuple = ()             # ((letter, name), ...) in the editor's order
    # The editor marks these with a *. The form doesn't: the hub only runs
    # InterBBS games, so every setting applies.
    interbbs: bool = False
    advised_reason: Optional[str] = None
    note: Optional[str] = None      # ours, where the game and its help disagree
    admin_note: Optional[str] = None
    # Only asked when another field has this value: (field id, value)
    depends_on: Optional[tuple] = None
    help_title: str = ""
    help: str = ""


def _number(id, label, page, group, default, max, min=0, **kw):
    return Field(id, label, page, group, NUMBER, default, min=min, max=max, **kw)


def _choice(id, label, page, group, default, choices, **kw):
    return Field(id, label, page, group, CHOICE, default, choices=choices, **kw)


def _toggle(id, label, page, group, default, **kw):
    return Field(id, label, page, group, TOGGLE, default, **kw)


_START = dict(
    note="A date only: the editor asks for no time. Leave it blank to start at "
         "the moment of the reset. A later date starts at midnight that day, and "
         "lets players join now but not play until then.",
)
_JOIN = dict(note="Leave at 01/01/2999 for no cut-off.")
_TURNS = dict(advised_min=1, advised_reason="0 turns means nobody can play.")
_PLAYERS = dict(advised_min=1, advised_reason="0 means nobody can join.")
_LOST = dict(
    advised_min=1, advised_reason="0 would void every attack as soon as it was sent.",
    note="Missing In Transit (MIT) is calculated at midnight maintenance. "
         "A setting of '1' can cause attacks sent close to midnight to be "
         "marked MIT very quickly. "
         "A board's own RESOURCE.DAT can override this locally, so the league "
         "setting is not the final word on any one board. The hub can't see or "
         "change that file."
    )
_STEADY_INVESTMENT = dict(note="Variable investment rates are calculated locally to each board, "
         "meaning different boards' investment rate can drift considerably from eachother over time."
    )
_LAND_PER_DAY = dict(note="If max purchasable regions is >= land created per day, "
         "the first player of the day can buy all of the available land. "
    )
_ATTACK_DAMAGE = dict(note="Only effective if InterBBS -> Allow Local Attacks is enabled. ")
_ATTACK_REWARDS = dict(note="Only effective if InterBBS -> Allow Local Attacks is enabled. ")

_BRE = (
    Field("start_date", "Game Start Date", 1, PAGE_GENERAL, DATE, None, **_START),
    Field("join_date", "Game Join Date", 1, PAGE_GENERAL, DATE, NO_JOIN_CUTOFF, **_JOIN),
    _number("turns_per_day", "Turns Per Day", 1, PAGE_GENERAL, 8, 20, **_TURNS),
    _number("protection_turns", "Turns of Protection", 1, PAGE_GENERAL, 20, 200),
    _number("initial_land", "Initial Market Land", 1, PAGE_GENERAL, 0, 50000),
    _number("land_per_day", "Land Created / Day", 1, PAGE_GENERAL, 1000, 5000, **_LAND_PER_DAY),
    _number("interest_rate", "Interest Rate", 1, PAGE_GENERAL, 50, 200),
    _number("investment_rate", "Standard Investment Rate", 1, PAGE_GENERAL, 35, 100),
    _toggle("steady_investment", "Steady Investment Rate", 1, PAGE_GENERAL, False, **_STEADY_INVESTMENT),
    _number("tax_rate", "Tax Rate", 1, PAGE_GENERAL, 50, 200),
    _number("max_region_purchase", "Max Purchasable Regions", 1, PAGE_GENERAL, 500, 10000,
            advised_min=1, advised_reason="0 stops all region buying."),
    _number("max_players", "Max Players Per BBS", 1, PAGE_GENERAL, 25, 25, **_PLAYERS),
    _choice("buy_military", "Buy Military", 1, PAGE_GAMEPLAY, "Y",
            (("Y", "Yes"), ("N", "No"), ("L", "Limited"))),
    _choice("maintenance_costs", "Maintenance Costs", 1, PAGE_GAMEPLAY, "M", HMLN),
    _choice("trade_costs", "Trade Deal Costs", 1, PAGE_GAMEPLAY, "M", HMLN),
    _choice("region_costs", "Region Costs", 1, PAGE_GAMEPLAY, "M", HMLN),
    _choice("attack_damage", "Attack Damage", 1, PAGE_GAMEPLAY, "M", HML, **_ATTACK_DAMAGE),
    _choice("attack_rewards", "Attack Rewards", 2, PAGE_GAMEPLAY, "M", HML, **_ATTACK_REWARDS),
    _choice("sabre", "Sabre Handling", 2, PAGE_GAMEPLAY, "U",
            (("N", "None/Disabled"), ("U", "User Select/Original"), ("R", "Random"),
             ("C", "Constant")),
            note="The help names none of the settings. With Constant, every S3-Sabre "
                 "does the same thing, set by a dial from 0 to 10."),
    _number("sabre_dial", "Sabre Constant Setting", 2, PAGE_GAMEPLAY, 5, 10,
            depends_on=("sabre", "C"),
            admin_note="Players are meant to find these out by trial and error. "
                       "From Immortal Barons (Andy's BRE clone), not yet checked "
                       "against BRE: 0-1 Intelligence HQ (covert agents); 2-3 "
                       "residential zones (people); 4 military bases (troops, jets, "
                       "turrets, tanks); 5-6 airbases (jets only); 7-8 regions, "
                       "destroyed outright; 9-10 the food supply."),
    _choice("attack_costs", "Attack Costs", 2, PAGE_INTERBBS, "M", HMLN, interbbs=True),
    _choice("terrorist_costs", "Terrorist Costs", 2, PAGE_INTERBBS, "M", HMLN, interbbs=True),
    _number("indiv_attacks", "Individual Attacks / Day", 2, PAGE_INTERBBS, 1, 10,
            interbbs=True),
    _number("group_attacks", "Group Attacks / Day", 2, PAGE_INTERBBS, 1, 10, interbbs=True),
    _number("terrorist_ops", "Terrorist Attacks / Day", 2, PAGE_INTERBBS, 10, 10000,
            interbbs=True),
    _number("bombings", "Bombings / Day", 2, PAGE_INTERBBS, 5, 10000, interbbs=True),
    _number("lost_attack_days", 'Days for "Lost Attacks"', 2, PAGE_INTERBBS, 7, 100,
            interbbs=True, **_LOST),
    _toggle("gooie", "Gooie Kablooies", 2, PAGE_INTERBBS, True, interbbs=True),
    _toggle("bombing_ops", "Bombing Operations", 2, PAGE_INTERBBS, True, interbbs=True),
    _toggle("missile_ops", "Missile Operations", 2, PAGE_INTERBBS, True, interbbs=True),
    _toggle("local_attacks", "Local Attacks", 2, PAGE_INTERBBS, True, interbbs=True),
    _toggle("local_scoring", "Local Attack Scoring", 2, PAGE_INTERBBS, False, interbbs=True),
    _toggle("dupe_checking", "Dupe Checking", 2, PAGE_INTERBBS, True, interbbs=True),
)

_FE = (
    Field("start_date", "Game Start Date", 1, PAGE_GENERAL, DATE, None, **_START),
    Field("join_date", "Game Join Date", 1, PAGE_GENERAL, DATE, NO_JOIN_CUTOFF, **_JOIN),
    _number("turns_per_day", "Turns Per Day", 1, PAGE_GENERAL, 8, 15,
            note='The help mentions "the maximum amount" without giving it; the '
                 'editor caps it at 15.', **_TURNS),
    _number("protection_turns", "Turns of Protection", 1, PAGE_GENERAL, 20, 50),
    _number("initial_land", "Initial Market Land", 1, PAGE_GENERAL, 20000, 50000),
    _number("land_per_day", "Land Created / Day", 1, PAGE_GENERAL, 2000, 5000),
    _number("wages", "Standard Wages", 1, PAGE_GENERAL, 15, 200,
            advised_min=1, advised_reason="0 makes workers free."),
    _number("start_land", "Starting Land", 1, PAGE_GENERAL, 100, 2000),
    _number("start_people", "Starting People", 1, PAGE_GENERAL, 100, 2000),
    _number("start_silver", "Starting Silver", 1, PAGE_GENERAL, 15000, 100000),
    _number("start_food", "Starting Food", 1, PAGE_GENERAL, 2000, 25000),
    _number("start_stone", "Starting Stone Blocks", 1, PAGE_GENERAL, 200, 500),
    _number("start_weapons", "Starting Weapons", 1, PAGE_GENERAL, 400, 2000),
    _number("start_runes", "Starting Runes", 1, PAGE_GENERAL, 25, 1000),
    _number("max_attacks", "Max Attacks", 1, PAGE_GENERAL, 7, 12,
            advised_min=4, advised_reason="The help: some races have attack penalties."),
    _number("max_spells", "Max Spells", 1, PAGE_GENERAL, 15, 40,
            advised_min=5, advised_reason="The help: some races have spell penalties."),
    _number("max_players", "Max Players", 1, PAGE_GENERAL, 25, 25, **_PLAYERS),
    _number("lost_attack_days", 'Days for "Lost Attacks"', 2, PAGE_INTERBBS, 10, 30,
            interbbs=True, **_LOST),
    _toggle("local_attacks", "Local Attacks", 2, PAGE_INTERBBS, True, interbbs=True),
    _toggle("local_scoring", "Local Attack Scoring", 2, PAGE_INTERBBS, False, interbbs=True),
    _toggle("dupe_checking", "Dupe Checking", 2, PAGE_INTERBBS, False, interbbs=True),
)

# IB has no pages and asks nothing in order, so every field is on "page 1".
_IB_DAY_CAP = dict(note="0 means no limit.")
_IB = (
    Field("start_date", "Game Start Date", 1, PAGE_GENERAL, DATE, None,
          note="Blank starts the new game on the day of the reset. A later date "
               "resets every board now; players can join, but no game day begins "
               "until then."),
    Field("join_date", "Game Join Date", 1, PAGE_GENERAL, DATE, None,
          note="Blank leaves joining open."),
    _number("turns_per_day", "Turns Per Day", 1, PAGE_GENERAL, 10, 20, min=1),
    _number("protection_turns", "Turns of Protection", 1, PAGE_GENERAL, 15, 200),
    _number("game_length", "Game Length (days)", 1, PAGE_GENERAL, 0, 100000,
            note="0 means the game never ends."),
    _number("idle_days_remove", "Days Idle Before Removal", 1, PAGE_GENERAL, 10, 365,
            note="0 never removes anyone."),
    _number("initial_land", "Initial Market Land", 1, PAGE_GENERAL, 0, 50000),
    _number("land_per_day", "Land Created / Day", 1, PAGE_GENERAL, 1000, 5000),
    _number("interest_rate", "Interest Rate", 1, PAGE_GENERAL, 50, 200, min=50),
    _number("investment_rate", "Standard Investment Rate", 1, PAGE_GENERAL, 35, 100, min=35),
    _toggle("steady_investment", "Steady Investment Rate", 1, PAGE_GENERAL, False),
    _number("tax_rate", "Max Tax Rate", 1, PAGE_GENERAL, 50, 50),
    _number("planetary_tax_rate", "Planetary Tax Rate", 1, PAGE_GENERAL, 5, 20),
    _number("max_region_purchase", "Max Purchasable Regions", 1, PAGE_GENERAL, 500, 10000,
            note="0 means no limit."),
    _number("max_players", "Max Players Per BBS", 1, PAGE_GENERAL, 25, 25,
            note="0 means as many as the planet has room for (25)."),
    _toggle("food_unlimited", "Food Unlimited", 1, PAGE_GENERAL, False),
    _choice("buy_military", "Buy Military", 1, PAGE_GAMEPLAY, "Y",
            (("Y", "Yes"), ("N", "No"), ("L", "Limited"))),
    _choice("maintenance_costs", "Maintenance Costs", 1, PAGE_GAMEPLAY, "M", HMLN),
    _choice("trade_costs", "Trade Deal Costs", 1, PAGE_GAMEPLAY, "M", HMLN),
    _choice("region_costs", "Region Costs", 1, PAGE_GAMEPLAY, "M", HMLN),
    _choice("attack_damage", "Attack Damage", 1, PAGE_GAMEPLAY, "M", HML),
    _choice("attack_rewards", "Attack Rewards", 1, PAGE_GAMEPLAY, "M", HML),
    _choice("sabre", "Sabre Handling", 1, PAGE_GAMEPLAY, "U",
            (("N", "None/Disabled"), ("U", "User Select/Original"), ("R", "Random"),
             ("C", "Constant"))),
    _number("sabre_dial", "Sabre Constant Setting", 1, PAGE_GAMEPLAY, 5, 10,
            depends_on=("sabre", "C"),
            admin_note="Players are meant to find these out by trial and error: "
                       "0-1 Intelligence HQ (covert agents); 2-3 residential zones "
                       "(people); 4 military bases (troops, jets, turrets, tanks); "
                       "5-6 airbases (jets only); 7-8 regions, destroyed outright; "
                       "9-10 the food supply."),
    _number("local_attacks_per_day", "Max Local Attacks / Day", 1, PAGE_GAMEPLAY, 0, 100,
            **_IB_DAY_CAP),
    _toggle("pirates", "Pirates", 1, PAGE_GAMEPLAY, True),
    _choice("attack_costs", "Attack Costs", 1, PAGE_INTERBBS, "M", HMLN),
    _choice("terrorist_costs", "Terrorist Costs", 1, PAGE_INTERBBS, "M", HMLN),
    _number("indiv_attacks", "Individual Attacks / Day", 1, PAGE_INTERBBS, 1, 100,
            **_IB_DAY_CAP),
    _number("group_attacks", "Group Attacks / Day", 1, PAGE_INTERBBS, 1, 100, **_IB_DAY_CAP),
    _number("terrorist_ops", "Terrorist Ops / Day", 1, PAGE_INTERBBS, 10, 100, **_IB_DAY_CAP),
    _number("bombings", "Bombing Ops / Day", 1, PAGE_INTERBBS, 5, 100, **_IB_DAY_CAP),
    _number("lost_attack_days", 'Days for "Lost Forces"', 1, PAGE_INTERBBS, 3, 30,
            note="0 means never give up."),
    _toggle("gooie", "Gooie Kablooie", 1, PAGE_INTERBBS, True),
    _toggle("bombing_ops", "Bombing Operations", 1, PAGE_INTERBBS, True),
    _toggle("missile_ops", "Missile Operations", 1, PAGE_INTERBBS, True),
    _toggle("local_attacks", "Local Attacks", 1, PAGE_INTERBBS, True),
    _toggle("local_scoring", "Local Attack Scoring", 1, PAGE_INTERBBS, False),
    _toggle("dupe_checking", "Dupe Checking", 1, PAGE_INTERBBS, True),
    _toggle("ip_trading", "InterPlanetary Trading", 1, PAGE_INTERBBS, True),
    Field("min_board_version", "Minimum Game Version", 1, PAGE_INTERBBS, TEXT, "",
          note="Blank for no requirement. A board running an older version has "
               "its packets refused."),
)


def _with_help(fields, helps):
    out = []
    for f in fields:
        # The dial has no topic of its own: the editor shows Sabre Handling's.
        title, text = helps.get(f.id, ("", ""))
        out.append(replace(f, help_title=title, help=text))
    return tuple(out)


CATALOGUE = {
    "B": _with_help(_BRE, help_text.BRE),
    "F": _with_help(_FE, help_text.FE),
    "I": _with_help(_IB, help_text.IB),
}


def fields_for(game_type: str) -> tuple:
    try:
        return CATALOGUE[game_type]
    except KeyError:
        raise ValueError(f"no reset catalogue for game type {game_type!r}") from None


def defaults(game_type: str) -> dict:
    return {f.id: f.default for f in fields_for(game_type)}
