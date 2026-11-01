"""Read an Immortal Barons packet, and pull the attack traffic out of it.

IB packets are JSON: readable, not encrypted. They are signed (ed25519): the
Coordinator signs the league orders (config, roster, reset, bulletins, freeze)
and each board signs its whole packet (BoardSig). The games check those; the
hub reads what they say. The shape is frozen by IB's own test
(internal/game/testdata/packet-shape.txt, copied to
tests/fixtures/ib_packet_shape.txt), so the fields read here cannot move without
IB's test failing first.

IB is a rebuild of BRE, and the hub follows it the way it follows BRE
(bre_packet): who did what kind of thing to whom, never with what. What differs:

- **Every action has an ID.** One counter per board numbers all of its
  interplanetary actions -- attacks, terror and special ops, recon, trades --
  and the answer echoes it, so a result is joined to its send exactly, not by
  what it echoes as BRE's ops are. The counter is the board's own and restarts
  with a new game, so an action is (game, origin board, ID): see `action_id`.
- **Realms are named**, not lettered. Sightings carry the names and no letters.
- **The result of a terror or special op is an attack result** (Results[] with
  Kind "terror", or the op's name). They are filed as BRE files them -- ops as
  traffic, attacks as attacks -- so the two games read the same in the console.
- **A Gooie is an Annihilator**, whose status is resent while it is built and
  while it flies. Each status is a News-like notice on its chain.

Traffic is filed under BRE's record numbers for the same thing (a terror op is
0x03, a message 0x09, ...), so what the console does with a kind of record --
never reveal a message's text, say -- holds for both games. A message's Body is
never read.
"""
import datetime
import hashlib
import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from backend.services import bre_packet
from backend.services.bre_packet import (
    MESSAGE, NEWS, NOTICE, REPORT, RESULT, SEND, SPECIAL_OP,
    SPECIAL_RESULT, SPY_GUY, SPY_REPORT, TERRORIST_OP, TERRORIST_RESULT, TRADE_DEAL,
)

# Bulletins carry whole files; the largest dev packet is a few KB.
MAX_PACKET_SIZE = 8 * 1024 * 1024

RECON_REQUEST = 0x01    # BRE's number; IB's Recon[]
DEFAULT_LOST_FORCES_DAYS = 3    # IB's default "Days before lost forces returned"

# RemoteAttack.Kind (AttackKind, an int on the wire), named as BRE names them.
ATTACK_KINDS = {0: "Quick Strike", 1: "Normal Attack", 2: "Extended Battle"}
GROUP_ATTACK = "Group Attack"
# AttackResult.Kind for the two kinds of op; any other is an attack's.
TERROR_KIND = "terror"
SPECIAL_OPS = {
    "bomb-food": "Bomb Food Stores", "bomb-market": "Bomb Market",
    "bomb-routes": "Bomb Trade Routes", "undermine": "Undermine",
    "nuclear": "Nuclear Missile", "chemical": "Chemical Missile",
    "slappenheimer": "S3-Sabre",
}
TERROR_OPS = bre_packet.OPERATIONS          # the same nine, numbered the same
SEND_SPY = bre_packet.SEND_SPY


class PacketFormatError(ValueError):
    pass


def parse(content: bytes) -> dict:
    if len(content) > MAX_PACKET_SIZE:
        raise PacketFormatError(f"{len(content)} bytes is larger than any IB packet")
    try:
        p = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PacketFormatError(f"not an IB packet: {exc}") from exc
    if not isinstance(p, dict):
        raise PacketFormatError("not an IB packet: not a JSON object")
    return p


def _items(p: dict, key: str) -> List[dict]:
    v = p.get(key)
    return [i for i in v if isinstance(i, dict)] if isinstance(v, list) else []


def _int(v) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _str(v) -> Optional[str]:
    return v.strip()[:64] or None if isinstance(v, str) else None


def day(value) -> Optional[datetime.datetime]:
    """An ISO date (the packet's Date, a reset's OnDate) as midnight; None if not one."""
    try:
        return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def action_id(game: str, origin: int, ident: int) -> str:
    """An action's ID across the league's history: the game it was sent in (its
    start date, as yymmdd, or 0 when the hub has not seen one), the board that
    sent it, and that board's number for it."""
    return f"{game}.{origin}.{ident}"


def game_of(started: Optional[datetime.datetime]) -> str:
    return started.strftime("%y%m%d") if started else "0"


# ── attacks ───────────────────────────────────────────────────────────────
@dataclass
class Strike:
    """An attack or its result: who and where, never its forces."""
    origin: int             # the attacker's board, whose counter numbered it
    ident: int
    is_result: bool
    from_planet: int        # the attacker's
    to_planet: int          # the target's
    attacker_name: Optional[str]
    target_name: Optional[str]
    attack_type: Optional[str]
    stamp: Optional[datetime.datetime]


def strikes(p: dict, src: int, dst: int) -> List[Strike]:
    """Attacks this packet carries from src to dst, and results going home."""
    stamp = day(p.get("Date"))
    out = []
    for a in _items(p, "Attacks"):
        ident = _int(a.get("ID"))
        if not ident:
            continue
        kind = GROUP_ATTACK if a.get("Group") else ATTACK_KINDS.get(_int(a.get("Kind")))
        out.append(Strike(src, ident, False, src, dst, _str(a.get("FromEmpire")),
                          _str(a.get("TargetEmpire")), kind, stamp))
    for r in _items(p, "Results"):
        ident, kind = _int(r.get("ID")), r.get("Kind")
        if not ident or kind == TERROR_KIND or kind in SPECIAL_OPS:
            continue
        # Back from the target's board (src) to the attacker's (dst).
        out.append(Strike(dst, ident, True, dst, src, None, _str(r.get("TargetEmpire")),
                          _str(kind), stamp))
    return out


@dataclass
class Forces:
    """What an attack sent and, from its result, what came home. Hidden game
    state, for the admin reveal only."""
    sent: dict
    won: Optional[bool] = None
    land_taken: Optional[int] = None
    returned: Optional[dict] = None
    outcome: Optional[str] = None


UNITS = ("Troopers", "Jets", "Tanks", "Bombers")


def _force_total(contributors) -> dict:
    total = {u.lower(): 0 for u in UNITS}
    for c in contributors if isinstance(contributors, list) else []:
        if not isinstance(c, dict):
            continue
        force = c.get("AttackForce") if isinstance(c.get("AttackForce"), dict) else c
        for u in UNITS:
            total[u.lower()] += _int(force.get(u)) or 0
    return total


def forces(attack: Optional[dict], result: Optional[dict]) -> Optional[Forces]:
    if attack is None and result is None:
        return None
    sent = _force_total(attack.get("Contributors")) if attack else {}
    out = Forces(sent=sent)
    if result is not None:
        out.won = bool(result.get("Won"))
        out.land_taken = _int(result.get("LandTaken"))
        out.returned = _force_total(result.get("Survivors"))
        out.outcome = _str(result.get("Outcome"))
    return out


def find_strike(p: dict, ident: int, is_result: bool) -> Optional[dict]:
    for item in _items(p, "Results" if is_result else "Attacks"):
        if _int(item.get("ID")) == ident:
            return item
    return None


# ── the rest of the traffic ───────────────────────────────────────────────
@dataclass
class Traffic:
    record_type: int        # BRE's number for the same kind of record
    kind: str
    from_planet: int        # the board that wrote it
    to_planet: int          # the board it is for
    key: str                # the event: see event_key
    item: dict = field(repr=False, default_factory=dict)
    from_name: Optional[str] = None
    to_name: Optional[str] = None
    chain: Optional[str] = None
    role: Optional[str] = None
    stamp: Optional[datetime.datetime] = None


def event_key(record_type: int, src: int, dst: int, item: dict) -> str:
    """One item between two boards. The same status sent again unchanged (a
    Gooie's, while nothing happens to it) is the same event."""
    raw = json.dumps([record_type, src, dst, item], sort_keys=True).encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def traffic(p: dict, src: int, dst: int, game: str) -> List[Traffic]:
    stamp = day(p.get("Date"))
    out: List[Traffic] = []

    def add(rtype, kind, item, **kw):
        out.append(Traffic(rtype, kind, src, dst, event_key(rtype, src, dst, item),
                           item=item, stamp=stamp, **kw))

    def by_id(origin, item):
        ident = _int(item.get("ID"))
        return f"id:{action_id(game, origin, ident)}" if ident else None

    for t in _items(p, "Terrors"):
        add(TERRORIST_OP, "Terrorist Op", t, from_name=_str(t.get("FromEmpire")),
            to_name=_str(t.get("TargetEmpire")), chain=by_id(src, t), role=SEND)
    for o in _items(p, "SpecialOps"):
        add(SPECIAL_OP, "Special Op", o, from_name=_str(o.get("FromEmpire")),
            to_name=_str(o.get("TargetEmpire")), chain=by_id(src, o), role=SEND)
    spied = set()
    for r in _items(p, "Results"):
        kind = r.get("Kind")
        if kind == TERROR_KIND:
            rtype, name = TERRORIST_RESULT, "Terrorist Result"
            if r.get("Won"):
                spied.add((_str(r.get("TargetEmpire")), by_id(dst, r)))
        elif kind in SPECIAL_OPS:
            rtype, name = SPECIAL_RESULT, "Special Result"
        else:
            continue            # an attack's: see strikes()
        # Back from the target (src) to the board that sent the op (dst).
        add(rtype, name, r, from_name=_str(r.get("TargetEmpire")),
            chain=by_id(dst, r), role=RESULT)
    for d in _items(p, "TradeDeals"):
        add(TRADE_DEAL, "Trade Deal", d, from_name=_str(d.get("FromEmpire")),
            to_name=_str(d.get("ToEmpire")), chain=by_id(src, d), role=SEND)
    for r in _items(p, "TradeReceipts"):
        # Addressed back to the sender: FromEmpire is the sender, on dst.
        add(REPORT, "Trade Receipt", r, from_name=_str(r.get("ToEmpire")),
            to_name=_str(r.get("FromEmpire")), chain=by_id(dst, r), role=RESULT)
    for q in _items(p, "Recon"):
        add(RECON_REQUEST, "Recon Request", q, to_name=_str(q.get("TargetEmpire")),
            chain=f"recon:{src}>{dst}", role=SEND)
    for s in _items(p, "ReconReports"):
        realm = _str(s.get("Empire"))
        # A Send Spy that got in brings a report home beside its result; any
        # other report answers a recon request (which carries no ID to echo).
        spy = next((c for e, c in spied if e == realm and c), None)
        add(SPY_REPORT, "Spy Report", s, from_name=realm,
            chain=spy or f"recon:{dst}>{src}", role=RESULT)
    for m in _items(p, "IPMessages"):
        # Who it is from and to; never the Body.
        header = {k: m.get(k) for k in ("FromBoard", "FromEmpire", "ToCoordinator",
                                        "ToEmpire", "ToEmpires", "When")}
        add(MESSAGE, "Message", header, from_name=_str(m.get("FromEmpire")),
            to_name=_str(m.get("ToEmpire")))
    for g in _items(p, "SpyGuys"):
        add(SPY_GUY, "Spy Guy", g)
    a = p.get("Annihilator")
    if isinstance(a, dict):
        if a.get("Dismantled"):
            kind = "Gooie Destroyed"
        elif a.get("Launched"):
            kind = "Gooie Warning"
        else:
            kind = "Gooie Funded"
        add(NEWS, kind, a, chain=f"gooie:{src}>{dst}", role=NOTICE)
    return out


def traffic_details(t: Traffic) -> Optional[dict]:
    """Hidden game state, for the admin reveal only. None for a message."""
    i = t.item
    if t.record_type == MESSAGE:
        return None
    if t.record_type == TERRORIST_OP:
        op = _int(i.get("Op")) or 0
        return {"operation": TERROR_OPS.get(op, f"op {op}"), "sent": _int(i.get("Agents"))}
    if t.record_type == SPECIAL_OP:
        op = i.get("Op")
        out = {"operation": SPECIAL_OPS.get(op, op)}
        if _int(i.get("Dial")):
            out["dial"] = i["Dial"]
        return out
    if t.record_type in (TERRORIST_RESULT, SPECIAL_RESULT):
        out = {"won": bool(i.get("Won"))}
        if i.get("Outcome"):
            out["outcome"] = i["Outcome"]
        if i.get("Backfired"):
            out["backfired"] = True
        if t.record_type == TERRORIST_RESULT:
            out["troopers_destroyed"] = _int(i.get("LandTaken"))
        return out
    if t.record_type in (TRADE_DEAL, REPORT):
        goods = i.get("Goods") if isinstance(i.get("Goods"), dict) else {}
        out = {"goods": {k.lower(): v for k, v in goods.items() if _int(v)}}
        if t.record_type == REPORT:
            out["delivered"] = bool(i.get("Delivered"))
        return out
    if t.record_type == SPY_REPORT:
        return {k.lower(): i.get(k) for k in ("Empire", "Date", "Land", "Troopers", "Jets",
                                              "Turrets", "Tanks", "Morale") if k in i}
    if t.record_type == SPY_GUY:
        return {"days": _int(i.get("Days"))}
    if t.record_type == NEWS:
        return {k.lower(): i.get(k) for k in ("Funded", "Launched", "ArrivesAt", "Intact",
                                              "Dismantled") if k in i}
    if t.record_type == RECON_REQUEST:
        return {"realm": _str(i.get("TargetEmpire")) or "every realm"}
    return None


# ── league orders ─────────────────────────────────────────────────────────
# LeagueConfig's fields by the reset catalogue's ids (game_reset/fields.py):
# BRE's id where IB keeps BRE's setting, an id of its own where it doesn't.
# Level is Medium 0, Low 1, High 2, None 3; BuyMode Yes 0, No 1, Limited 2;
# SabreMode None 0, User Select 1, Random 2, Constant 3. MoneyCapBillions is
# left out: IB fixed it at 2 (#205) and no longer lets it be set.
_NUMBERS = {"TurnsPerDay": "turns_per_day", "ProtectionTurns": "protection_turns",
            "LandPerDay": "land_per_day", "InterestRate": "interest_rate",
            "MaxTaxRate": "tax_rate", "MaxIndividualAttacks": "indiv_attacks",
            "MaxGroupAttacks": "group_attacks", "MaxTerrorOps": "terrorist_ops",
            "MaxBombingOps": "bombings", "LostForcesDays": "lost_attack_days",
            "StdInvestRate": "investment_rate", "MaxRegions": "max_region_purchase",
            "InitialMarketLand": "initial_land", "MaxPlayers": "max_players"}
_LEVELS = {"MaintCosts": "maintenance_costs", "AttackDamage": "attack_damage",
           "AttackCosts": "attack_costs", "AttackRewards": "attack_rewards",
           "TerrorCosts": "terrorist_costs", "RegionCosts": "region_costs",
           "TradeCosts": "trade_costs"}
_LEVEL = {0: "M", 1: "L", 2: "H", 3: "N"}
_TOGGLES = {"ClingyAnnihilator": "gooie", "DupeChecking": "dupe_checking",
            "LocalAttacks": "local_attacks", "LocalAttackScoring": "local_scoring",
            "BombingOps": "bombing_ops", "MissileOps": "missile_ops",
            "SteadyInvest": "steady_investment"}
_BUY = {0: "Y", 1: "N", 2: "L"}
_SABRE = {0: "N", 1: "U", 2: "R", 3: "C"}
# IB's own settings.
_OWN_NUMBERS = {"GameLength": "game_length", "IdleDaysRemove": "idle_days_remove",
                "PlanetaryTaxRate": "planetary_tax_rate",
                "MaxLocalAttacks": "local_attacks_per_day"}
_OWN_TOGGLES = {"FoodUnlimited": "food_unlimited", "IPTrading": "ip_trading",
                "Pirates": "pirates"}


def league_settings(c: dict) -> bre_packet.LeagueSettings:
    out = {}
    for k, fid in _NUMBERS.items():
        if _int(c.get(k)) is not None:
            out[fid] = c[k]
    for k, fid in _LEVELS.items():
        if _int(c.get(k)) in _LEVEL:
            out[fid] = _LEVEL[c[k]]
    for k, fid in _TOGGLES.items():
        if isinstance(c.get(k), bool):
            out[fid] = c[k]
    if _int(c.get("BuyMilitary")) in _BUY:
        out["buy_military"] = _BUY[c["BuyMilitary"]]
    sabre = _int(c.get("SlappenheimerHandling"))
    if sabre in _SABRE:
        out["sabre"] = _SABRE[sabre]
        if sabre == 3:
            out["sabre_dial"] = _int(c.get("SabreConstantDial")) or 0
    joined = day(c.get("JoinDate"))
    if joined:
        out["join_date"] = joined.date().isoformat()
    for k, fid in _OWN_NUMBERS.items():
        if _int(c.get(k)) is not None:
            out[fid] = c[k]
    for k, fid in _OWN_TOGGLES.items():
        if isinstance(c.get(k), bool):
            out[fid] = c[k]
    if isinstance(c.get("MinBoardVersion"), str):
        out["min_board_version"] = c["MinBoardVersion"]
    return bre_packet.LeagueSettings(
        day(c.get("GameStartDate")), out.get("protection_turns"),
        out.get("indiv_attacks"), out.get("lost_attack_days"), out)


def config_json(settings: dict) -> dict:
    """The keys of the Coordinator's config.json that a reset's settings set:
    league_settings() the other way round. config.json names Sabre Handling
    SabreHandling where the packet says SlappenheimerHandling, and the dial is
    left as it is unless Sabre is Constant, which is the only time the form has
    one. A blank date is IB's own "none": start now, or no join cut-off."""
    out = {}
    for tables in ((_NUMBERS, _OWN_NUMBERS), (_TOGGLES, _OWN_TOGGLES)):
        for table in tables:
            for k, fid in table.items():
                if fid in settings:
                    out[k] = settings[fid]
    level = {v: k for k, v in _LEVEL.items()}
    for k, fid in _LEVELS.items():
        if fid in settings:
            out[k] = level[settings[fid]]
    if "buy_military" in settings:
        out["BuyMilitary"] = {v: k for k, v in _BUY.items()}[settings["buy_military"]]
    if "sabre" in settings:
        out["SabreHandling"] = {v: k for k, v in _SABRE.items()}[settings["sabre"]]
    if settings.get("sabre_dial") is not None:
        out["SabreConstantDial"] = settings["sabre_dial"]
    if "start_date" in settings:
        out["GameStartDate"] = settings["start_date"] or ""
    if "join_date" in settings:
        out["JoinDate"] = settings["join_date"] or ""
    if "min_board_version" in settings:
        out["MinBoardVersion"] = settings["min_board_version"] or ""
    return out


def orders(p: dict) -> Tuple[Optional[bre_packet.LeagueSettings], Optional[datetime.datetime]]:
    """The Coordinator's settings, and the date a reset it ordered starts the
    new game, where this packet carries them."""
    config = p.get("LeagueConfig")
    settings = league_settings(config) if isinstance(config, dict) else None
    reset = p.get("Reset")
    starts = day(reset.get("OnDate")) if isinstance(reset, dict) else None
    return settings, starts

