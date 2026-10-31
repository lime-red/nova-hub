"""Read a Falcon's Eye packet: BRE's container, FE's own record types.

FE writes its packets exactly as BRE does (bre_packet): a 12-byte header,
records of type, length, ends and CRC over a zero-run-compressed payload, and a
0x0F trailer. Shown on the rig (900F, Oct 2026): bre_packet.parse reads every
FE packet the games wrote there with every CRC good.

Only the type numbers differ. FE.EXE holds its table of record names, in type
order from "Global Reset" to "Spy Report", as BRE.EXE does; the order was
checked on the rig against the sizes FE's /DETAILED transcript prints for the
records it wrote (Config Update 232 bytes, Routing List 257, Dummy Data 2,
Recon Request 2, Recon Update 1194).

The war records were mapped by playing two counties against each other on a
two-board league (31 Oct 2026): attacks of known size and objective, a spell, a
trade deal accepted and one rejected, a message to one county and one to all.
Every offset below was read against what the games showed their players, and
checked against production's 015F traffic of August 2026 (the 12 Oct backup):
all seven of its attack results join their attacks by the ID below, and the
player IDs in its attacks and spells are the ones its Recon Updates give for
the letters beside them.

Not mapped: FE's Config Update, so FE leagues show no settings and use FE's
default for lost attacks; and News, Report and Spy Report, which the war
records above did not need.
"""
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional

from backend.services.bre_packet import PacketFormatError, Record  # noqa: F401
from backend.services import bre_packet

RECORD_TYPES = {
    0x01: "Global Reset",       # 232; the Config Update's layout, as BRE's 0x10 is
    0x02: "Config Update",      # 232
    0x03: "Player List",
    0x04: "Routing List",       # 257
    0x05: "Nodelist",
    0x06: "Time Check",         # 8
    0x07: "Problem Data",
    0x08: "Dummy Data",         # 2
    0x09: "Recon Request",      # 2
    0x0A: "Recon Update",       # 1194
    0x0B: "News",               # 258
    0x0C: "Report",
    0x0D: "Message",            # 2657
    0x0E: "Trade Deal",         # 156
    0x0F: "Trade Result",       # 156
    0x10: "Attack",             # 2903
    0x11: "Attack Result",      # 2903
    0x12: "Spell",              # 21
    0x13: "Spell Result",       # 21
    0x14: "Spy Report",
}

RECON_UPDATE = 0x0A
MESSAGE = 0x0D      # player mail: never to be decoded for display, as in BRE
TRADE_DEAL, TRADE_RESULT = 0x0E, 0x0F
ATTACK, ATTACK_RESULT = 0x10, 0x11
SPELL, SPELL_RESULT = 0x12, 0x13

DEFAULT_LOST_ATTACK_DAYS = 10   # FE's editor default for Days for "Lost Attacks"


def parse(packet: bytes) -> List[Record]:
    return bre_packet.parse(packet, RECORD_TYPES)


def _i32(d: bytes, o: int) -> int:
    return struct.unpack_from("<i", d, o)[0]


def _letter(b: int) -> Optional[str]:
    return chr(b) if 0x41 <= b <= 0x59 else None     # A-Y; Z means "all of them"


# ── who holds each county ─────────────────────────────────────────────────
# As BRE's (bre_packet.realms), with FE's sizes: 25 names of up to 25
# characters, then tables of 25 i32s. Slot k is county letter chr(ord("A") + k).
# Production: board 2's Recon Updates give B "Bobo" ID 63 and board 4's give C
# "The Highwaymen" ID 80, the IDs their attack carried.
RECON_UPDATE_SIZE = 1194
_COUNTY_NAMES = 2
_COUNTY_NAME_SIZE = 26
_RECON_LAND = 652
_RECON_SCORE = 752
_RECON_WORTH = 852
_RECON_PLAYER_IDS = 952


def realms(record: Record) -> List[bre_packet.Realm]:
    """The counties a Recon Update announces for its board (`record.data[0]`).
    Empty slots are left out."""
    d = record.data
    if record.type != RECON_UPDATE or len(d) < RECON_UPDATE_SIZE:
        raise PacketFormatError(f"{record.name} is not an FE Recon Update")
    ids = struct.unpack_from(f"<{bre_packet.REALM_SLOTS}i", d, _RECON_PLAYER_IDS)
    out = []
    for k in range(bre_packet.REALM_SLOTS):
        o = _COUNTY_NAMES + k * _COUNTY_NAME_SIZE
        if not 0 < d[o] < _COUNTY_NAME_SIZE:
            continue
        name = bre_packet._pascal(d, o).strip()
        if name:
            out.append(bre_packet.Realm(chr(ord("A") + k), ids[k] or None, name))
    return out


# ── attacks ───────────────────────────────────────────────────────────────
# An Attack and its Attack Result are the same 2,903 bytes: the result is the
# attack with its losses and outcome filled in, sent back. Two tables of 25
# county slots -- attackers, then defenders -- each slot at its county's letter
# (a group attack fills several), then the tail. Board numbers stay in the
# attack's direction in the result.
ATTACK_SIZE = 2903
SLOT_SIZE = 57
_ATTACKERS = 0
_DEFENDERS = 25 * SLOT_SIZE      # 1425
# In a slot:
_SLOT_PLAYER = 0        # i32
_SLOT_SENT = 5          # 3 i32s, the race's three units, weakest first
_SLOT_LOST = 17         # 3 i32s
_SLOT_POWER = 29        # i32
# In the tail:
_FROM_BOARD = 2850
_TO_BOARD = 2851
_ATTACKER = 2852        # county letter
_TARGET = 2853          # county letter, or Z for the nation as a whole
_TARGET_PLAYER = 2858   # i32; 0 when the target is the nation
_OBJECTIVE = 2862
_OUTCOME = 2863         # 0 not yet fought, 4 failure, 5 success
_GAINED = 2865          # i32: acres, or people, by objective
_STAMP = 2889           # 6-byte Real: the attacker's clock at launch, as BRE's
_ID = 2895              # 4 bytes; with the stamp, echoed in the result

OBJECTIVES = {1: "Capture Land", 2: "Capture People", 3: "Capture Resources"}
GAINS = {1: "acres", 2: "people"}
UNITS = ("unit_1", "unit_2", "unit_3")
FAILURE, SUCCESS = 4, 5


def _check_attack(record: Record) -> bytes:
    d = record.data
    if record.type not in (ATTACK, ATTACK_RESULT) or len(d) < ATTACK_SIZE:
        raise PacketFormatError(f"{record.name} is not an FE attack")
    return d


def attack(record: Record) -> bre_packet.Attack:
    """The part of an attack that identifies it. The ID is the stamp and the
    four bytes after it: the attack's own, which no other attack shares."""
    d = _check_attack(record)
    return bre_packet.Attack(
        attack_id=d[_STAMP:_ID + 4].hex(),
        is_result=record.type == ATTACK_RESULT,
        from_planet=d[_FROM_BOARD],
        to_planet=d[_TO_BOARD],
        attacker=_letter(d[_ATTACKER]),
        target=_letter(d[_TARGET]),
        attack_type=OBJECTIVES.get(d[_OBJECTIVE], f"objective {d[_OBJECTIVE]}"),
        # A result echoes the attack's stamp rather than saying when it was
        # fought, so only the attack is dated (BRE's result is dated by the
        # defender's clock).
        stamp=None if record.type == ATTACK_RESULT
        else bre_packet.timestamp(d[_STAMP:_STAMP + 6]),
    )


def attacker_player(record: Record) -> Optional[int]:
    """The leading attacker's player ID, from its slot."""
    d = _check_attack(record)
    k = d[_ATTACKER] - 0x41
    if not 0 <= k < 25:
        return None
    return _i32(d, _ATTACKERS + k * SLOT_SIZE + _SLOT_PLAYER) or None


def target_player(record: Record) -> Optional[int]:
    return _i32(_check_attack(record), _TARGET_PLAYER) or None


@dataclass
class Forces:
    """What an attack sent and, read from its result, what it cost. Units are
    numbered: their names depend on the county's race (a Human's are Peasant,
    Swordsman and Paladin)."""
    sent: Dict[str, int]
    lost: Optional[Dict[str, int]] = None
    success: Optional[bool] = None
    objective: Optional[str] = None
    gained: Optional[int] = None
    gained_what: Optional[str] = None
    defenders_lost: Optional[int] = None


def _units(d: bytes, o: int) -> Dict[str, int]:
    return dict(zip(UNITS, struct.unpack_from("<3i", d, o)))


def forces(record: Record) -> Forces:
    """Hidden game state: only ever for an explicit admin reveal, never a listing.
    Every attacking county's units, summed (a group attack has several)."""
    d = _check_attack(record)
    sent = {u: 0 for u in UNITS}
    lost = {u: 0 for u in UNITS}
    for k in range(25):
        o = _ATTACKERS + k * SLOT_SIZE
        if not _i32(d, o + _SLOT_PLAYER):
            continue
        for u, n in _units(d, o + _SLOT_SENT).items():
            sent[u] += n
        for u, n in _units(d, o + _SLOT_LOST).items():
            lost[u] += n
    objective = d[_OBJECTIVE]
    out = Forces(sent=sent, objective=OBJECTIVES.get(objective))
    if record.type == ATTACK_RESULT and d[_OUTCOME] in (FAILURE, SUCCESS):
        out.lost = lost
        out.success = d[_OUTCOME] == SUCCESS
        out.gained = _i32(d, _GAINED)
        out.gained_what = GAINS.get(objective)
        out.defenders_lost = sum(
            sum(struct.unpack_from("<3i", d, _DEFENDERS + k * SLOT_SIZE + _SLOT_LOST))
            for k in range(25) if _i32(d, _DEFENDERS + k * SLOT_SIZE + _SLOT_PLAYER))
    return out


# ── the rest of the traffic ───────────────────────────────────────────────
# Filed under BRE's record numbers for the same kind of thing, as IB's is, so
# the hub's rules for them hold unchanged: a Message is never revealed, and a
# result pairs with the send of its chain. A spell is FE's equivalent of BRE's
# special op.
TRAFFIC_TYPES = frozenset({MESSAGE, TRADE_DEAL, TRADE_RESULT, SPELL, SPELL_RESULT})
_FILED_AS = {
    MESSAGE: bre_packet.MESSAGE,
    TRADE_DEAL: bre_packet.TRADE_DEAL,
    TRADE_RESULT: bre_packet.REPORT,
    SPELL: bre_packet.SPECIAL_OP,
    SPELL_RESULT: bre_packet.SPECIAL_RESULT,
}

# Trade Deal and Trade Result, 156 bytes: the result is the deal sent back.
TRADE_GOODS = ("silver", "food", "weapons", "runes", "stone")
_TRADE_FROM_PLAYER, _TRADE_TO_PLAYER = 0, 4
_TRADE_FROM, _TRADE_TO = 8, 9           # county letters
_TRADE_SENT, _TRADE_DEMANDED = 10, 46   # 5 i32s each, in TRADE_GOODS order
_TRADE_STAMP = 86
_TRADE_FROM_BOARD, _TRADE_TO_BOARD = 94, 95
_TRADE_ANSWER = 96      # 0 not yet answered, 5 accepted, 4 rejected
_TRADE_ID = 97          # 4 bytes, echoed in the result
ACCEPTED, REJECTED = 5, 4

# Spell and Spell Result, 21 bytes. No ID: a result pairs with the latest open
# spell between the same two counties.
SPELLS = {1: "Wizard's Eye"}
_SPELL_FROM, _SPELL_TO = 0, 1                   # county letters
_SPELL_FROM_PLAYER, _SPELL_TO_PLAYER = 2, 6
_SPELL = 10
_SPELL_FROM_BOARD, _SPELL_TO_BOARD = 17, 18
_SPELL_OUTCOME = 20     # 0 cast, 4 fizzled; success not yet seen

# Message, 2,657 bytes: who from, then up to 25 recipient player IDs (-999 for
# every nation). The text starts at 111 and is never read.
_MSG_FROM = 0
_MSG_FROM_PLAYER = 1
_MSG_RECIPIENTS = 5


def traffic(record: Record) -> Optional[bre_packet.Traffic]:
    """Who sent what to whom, filed under BRE's number; None for anything else."""
    t = record.type
    if t not in TRAFFIC_TYPES:
        return None
    d = record.data
    name = record.name
    out = bre_packet.Traffic(_FILED_AS[t], name, record.src, record.dst)
    try:
        if t in (TRADE_DEAL, TRADE_RESULT):
            # The result goes back from the recipient's board to the sender's.
            out.from_letter, out.to_letter = _letter(d[_TRADE_FROM]), _letter(d[_TRADE_TO])
            out.from_player, out.to_player = _i32(d, _TRADE_FROM_PLAYER), _i32(d, _TRADE_TO_PLAYER)
            if t == TRADE_RESULT:
                out.from_letter, out.to_letter = out.to_letter, out.from_letter
                out.from_player, out.to_player = out.to_player, out.from_player
            out.chain = f"trade:{d[_TRADE_FROM_BOARD]}:{d[_TRADE_ID:_TRADE_ID + 4].hex()}"
            out.role = bre_packet.SEND if t == TRADE_DEAL else bre_packet.RESULT
            out.stamp = bre_packet.timestamp(d[_TRADE_STAMP:_TRADE_STAMP + 6])
        elif t in (SPELL, SPELL_RESULT):
            out.from_letter, out.to_letter = _letter(d[_SPELL_FROM]), _letter(d[_SPELL_TO])
            out.from_player, out.to_player = _i32(d, _SPELL_FROM_PLAYER), _i32(d, _SPELL_TO_PLAYER)
            if t == SPELL_RESULT:
                out.from_letter, out.to_letter = out.to_letter, out.from_letter
                out.from_player, out.to_player = out.to_player, out.from_player
            out.chain = (f"spell:{d[_SPELL_FROM_BOARD]}>{d[_SPELL_TO_BOARD]}:"
                         f"{d[_SPELL_FROM_PLAYER:_SPELL_TO_PLAYER + 4].hex()}:{d[_SPELL]}")
            out.role = bre_packet.SEND if t == SPELL else bre_packet.RESULT
        elif t == MESSAGE:
            out.from_letter, out.from_player = _letter(d[_MSG_FROM]), _i32(d, _MSG_FROM_PLAYER)
            ids = []
            for o in range(_MSG_RECIPIENTS, _MSG_RECIPIENTS + 25 * 4, 4):
                v = _i32(d, o)
                if v == 0:
                    break
                ids.append(v)
                if v == bre_packet.ALL_PLANETS:
                    break
            out.recipients = ids
    except (IndexError, struct.error, OverflowError, ValueError):
        pass                # a short record: who and where is still worth having
    return out


def traffic_details(record: Record) -> Optional[dict]:
    """Hidden game state, for the admin reveal only. None for a Message, whose
    content is never decoded. PacketFormatError for a damaged record."""
    try:
        return _traffic_details(record)
    except (IndexError, struct.error, OverflowError, ValueError) as exc:
        raise PacketFormatError(f"{record.name} is damaged: {exc}") from exc


def _goods(d: bytes, o: int) -> Dict[str, int]:
    return {g: n for g, n in zip(TRADE_GOODS, struct.unpack_from("<5i", d, o)) if n}


def _traffic_details(record: Record) -> Optional[dict]:
    d, t = record.data, record.type
    if t in (TRADE_DEAL, TRADE_RESULT):
        out = {"offered": _goods(d, _TRADE_SENT), "demanded": _goods(d, _TRADE_DEMANDED)}
        if t == TRADE_RESULT:
            out["accepted"] = d[_TRADE_ANSWER] == ACCEPTED
        return out
    if t in (SPELL, SPELL_RESULT):
        out = {"spell": SPELLS.get(d[_SPELL], f"spell {d[_SPELL]}")}
        if t == SPELL_RESULT:
            # Only a fizzle has been seen; a spell that worked is not mapped.
            code = d[_SPELL_OUTCOME]
            out["outcome"] = "fizzled" if code == FAILURE else f"code {code}"
        return out
    return None
