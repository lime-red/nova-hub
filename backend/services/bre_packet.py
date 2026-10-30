"""Read a BRE v0.988 InterBBS packet, and pull the attack traffic out of it.

The packets were described as encrypted and tamper-proofed. They are neither,
beyond a checksum: every record is plain structured data behind a zero-run
compressor, and "Test Realm" or "v0.988" can be read straight off a hex dump.
Everything below was established by differential capture on the test rig --
attacks sent with 37, 38 and 25 troopers, diffed byte for byte -- and checked
against the game's own `/DETAILED` transcript, whose per-record `Old:`/`Size:`
figures match the lengths decoded here exactly.

    file    = header(12)  record*  0x0F...
    header  = u32, rising over time  +  8 bytes not yet identified
    record  = type u8 | length u16le | src u8 | dst u8 | crc u32le | payload[length]
    crc     = zlib CRC-32 without the final xor, over the *compressed* payload
    payload = 0xFD n -> n zero bytes; 0xFD 0x00, or a final 0xFD, is a literal 0xFD;
              any other byte is literal

The 8 unidentified header bytes are not a CRC over any span of the file. They do
not stand between us and the records, so they are left for the memory-capture
route rather than guessed at.

**Why this exists: Missing In Transit.** An individual attack (0x07) carries an
8-byte attack ID. The defender's game answers with an Attack Result (0x08) that
is the original 875-byte record echoed back verbatim with 28 bytes of outcome
appended -- so the ID survives the round trip and an attack can be joined to its
result exactly, however many are in flight. Correlating by timing breaks down in
exactly the busy free-for-all stage when MITs matter most; the ID does not.

The attacker's game declares MIT when no result has arrived within the league's
"Days for Lost Attacks" (default 7). Forces come home, the attack achieves
nothing, and `PROBLEMS.LOG` says `Missing-In-Transit Attack Found to BBS #n`. A
result arriving after that is thrown away -- `Duplicate or Late Attack Return
Recieved - Packet Deleted` -- even though the defender has already taken the hit.
Both reproduced on the rig.

The timestamp in an attack record is also what the attacker's MIT report prints
as `Date:`, to the second. That is the join from what a player saw to the record
the hub carried.

Do not surface unit counts to anyone who also plays. Planets, realm letters, the
ID and the timestamps identify an attack; its strength is hidden game state.
`forces()` reads it for the one exception: an admin who asks, per attack, by
clicking to reveal it -- so an admin who plays never sees it by accident.
"""
import datetime
import hashlib
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Optional

HEADER_SIZE = 12
RECORD_HEADER_SIZE = 9
TRAILER = 0x0F

INDIV_ATTACK = 0x07
ATTACK_RESULT = 0x08

# Names as the game's /DETAILED transcript prints them, matched to type numbers
# by decompressed size (every type has exactly one across production's 10,322
# BRE packets) and by which answers which in traffic. The hub keeps those names
# in processing_run_items. BRE.EXE holds its own table of the names, in type
# order from RECON_REQUEST (1) to TIME_CHECK (0x1A), which confirmed every one
# of these and named 0x10, 0x16 and 0x19 (Oct 2026).
#
# The ops are not traceable as attacks are: they carry realm letters, player IDs
# and planets but no unique ID, and a result echoes its op's first ten bytes
# behind a timestamp -- two identical Bombing Ops are byte for byte the same.
# They also have no forces to go missing. A Message is player mail; it is never
# to be decoded for display.
RECORD_TYPES = {
    0x01: "Recon Request",          # 2
    0x02: "Recon Update",           # 1448
    0x03: "Terrorist Op",           # 18
    0x04: "Terrorist Result",       # 22
    0x05: "Bombing Op",             # 10
    0x06: "Bombing Result",         # 10
    INDIV_ATTACK: "Indiv Attack",   # 875
    ATTACK_RESULT: "Attack Results",  # 903
    0x09: "Message",                # 2659
    0x0A: "Configupdate",           # 240
    0x0B: "Gooie Attack",           # 12
    0x0C: "Gooie Results",          # 7
    0x0D: "Player List",            # 783
    0x0E: "Special Op",             # 15
    0x0F: "Special Result",         # 15
    # 240, Configupdate layout, only in the coordinator's first packet of each
    # game (013B, 014B, and 015B's 8 Jul restart): the game's GLOBALRESET. The
    # hub calls what it starts a Game Start (GAME_START, game_reset.history).
    0x10: "Global Reset",
    0x11: "Dummy Data",             # 2
    0x12: "Routing List",           # 257
    0x13: "Spy Report",             # 74
    0x14: "News Data",              # 258
    0x15: "Report",                 # 738, game-written text, e.g. "Trade Deal arrived at ..."
    0x16: "Packet Data",            # never seen; named by BRE.EXE's table
    0x17: "Spy Guy",                # 3
    0x18: "Trade Deal",             # 52; named by the rig's /DETAILED transcript
    0x19: "Nodelist",               # never seen; named by BRE.EXE's table
    0x1A: "Time Check",             # 8
}

# Fitted, not assumed: three known wall-clock times on the rig decode to the
# second against this epoch. It is not Turbo Pascal's or Delphi's.
_EPOCH = datetime.datetime(1989, 12, 30)

# Offsets into a decompressed 0x07 record (0x08 shares them for its first 875).
ATTACK_SIZE = 875
_ATTACKER = 0          # realm letter on the sending planet
_FROM_PLANET = 1
_TO_PLANET = 2
_TARGET = 7            # realm letter on the target planet
_TROOPERS = 12         # i32
_JETS = 16             # i32; jets need carriers to fly, one carrier per 100
_TANKS = 20            # i32
_BOMBERS = 24          # i32
_CARRIERS = 28         # i32; they ride along and always come home, so never "lost"
_STAMP = 859           # 6-byte Real; attack: sender's session start, result: resolved
_NORMAL = 866          # 1 = Normal attack, 0 = Quick Strike (Extended not yet seen)
_ID = 867              # 8 bytes, unique per attack, echoed in the result


class PacketFormatError(ValueError):
    pass


# Two bytes of RLE can stand for 255 zeros, so a packet decompresses to far
# more than it uploads: a 10 MB packet of nothing but runs would be 1.3 GB.
# Real records are at most 2,659 bytes (every BRE packet on dev, Oct 2026) and
# real packets at most 12.6 KB decompressed, so these leave room to spare.
MAX_RECORD_BODY = 16 * 1024
MAX_PACKET_BODY = 1024 * 1024


def unrle(payload: bytes, limit: int = MAX_RECORD_BODY) -> bytes:
    """0xFD n is n zero bytes; 0xFD 0x00 is a literal 0xFD.

    A 0xFD as the very last byte is also literal: the encoder writes a trailing
    0xFD bare. Both cases were found in production, where 0xFD falls inside an
    attack ID often enough to matter -- decoding it as an escape shifts the ID.

    PacketFormatError past `limit` bytes, before allocating them.
    """
    out, i = bytearray(), 0
    while i < len(payload):
        b = payload[i]
        if b == 0xFD and i + 1 < len(payload):
            n = payload[i + 1]
            out += bytes(n) if n else b"\xfd"
            i += 2
        else:
            out.append(b)
            i += 1
        if len(out) > limit:
            raise PacketFormatError(f"a record decompresses past {limit} bytes")
    return bytes(out)


def real48(raw: bytes) -> float:
    """Turbo Pascal's 6-byte Real: exponent byte, then 39-bit mantissa and sign."""
    if len(raw) != 6:
        raise PacketFormatError(f"a Real is 6 bytes, not {len(raw)}")
    if raw[0] == 0:
        return 0.0
    mantissa = int.from_bytes(raw[1:6], "little")
    sign, mantissa = mantissa >> 39, mantissa & ((1 << 39) - 1)
    return (-1) ** sign * (1 + mantissa / 2 ** 39) * 2.0 ** (raw[0] - 129)


def timestamp(raw: bytes) -> Optional[datetime.datetime]:
    """None for no date, and for one no calendar holds (a damaged record)."""
    days = real48(raw)
    if not days:
        return None
    try:
        return _EPOCH + datetime.timedelta(days=days)
    except (OverflowError, ValueError):
        return None


def record_crc(payload: bytes) -> bytes:
    return (zlib.crc32(payload) ^ 0xFFFFFFFF).to_bytes(4, "little")


@dataclass
class Record:
    type: int
    src: int
    dst: int
    crc_ok: bool
    data: bytes          # decompressed

    @property
    def name(self) -> str:
        return RECORD_TYPES.get(self.type, f"type {self.type:#04x}")


def parse(packet: bytes) -> List[Record]:
    if len(packet) < HEADER_SIZE:
        raise PacketFormatError(f"{len(packet)} bytes is shorter than the header")
    records, i, budget = [], HEADER_SIZE, MAX_PACKET_BODY
    while i < len(packet):
        if all(b == TRAILER for b in packet[i:]):
            break
        if i + RECORD_HEADER_SIZE > len(packet):
            raise PacketFormatError(f"truncated record header at offset {i}")
        rtype = packet[i]
        (length,) = struct.unpack_from("<H", packet, i + 1)
        src, dst = packet[i + 3], packet[i + 4]
        crc = packet[i + 5:i + 9]
        payload = packet[i + 9:i + 9 + length]
        if len(payload) != length:
            raise PacketFormatError(f"record at offset {i} runs past the end")
        data = unrle(payload, min(MAX_RECORD_BODY, budget))
        budget -= len(data)
        records.append(Record(rtype, src, dst, record_crc(payload) == crc, data))
        i += RECORD_HEADER_SIZE + length
    return records


@dataclass
class Attack:
    """The part of an attack that identifies it -- deliberately not its strength."""
    attack_id: str
    is_result: bool
    from_planet: int
    to_planet: int
    attacker: str
    target: str
    attack_type: str
    stamp: Optional[datetime.datetime]


def attack(record: Record) -> Attack:
    d = record.data
    if record.type not in (INDIV_ATTACK, ATTACK_RESULT) or len(d) < ATTACK_SIZE:
        raise PacketFormatError(f"{record.name} is not an individual attack")
    return Attack(
        attack_id=d[_ID:_ID + 8].hex(),
        is_result=record.type == ATTACK_RESULT,
        from_planet=d[_FROM_PLANET],
        to_planet=d[_TO_PLANET],
        attacker=chr(d[_ATTACKER]),
        target=chr(d[_TARGET]),
        attack_type="Normal" if d[_NORMAL] else "Quick Strike",
        stamp=timestamp(d[_STAMP:_STAMP + 6]),
    )


def troopers(record: Record) -> int:
    """Kept apart from `attack()` on purpose: this is hidden game state."""
    return struct.unpack_from("<i", record.data, _TROOPERS)[0]


UNITS = ("troopers", "jets", "tanks", "bombers")


@dataclass
class Forces:
    """What an attack sent, and -- read from its result -- what it cost."""
    troopers: int
    jets: int
    tanks: int
    bombers: int
    carriers: int
    success: Optional[bool] = None
    loss_fraction: Optional[float] = None
    defenders_destroyed: Optional[int] = None
    regions_captured: Optional[int] = None

    def lost(self) -> Optional[dict]:
        """The game loses the same fraction of every unit type, rounded half up.

        Checked against the attacker's report for six attacks, e.g. 10 troopers
        + 23 jets + 5 tanks + 3 bombers lost 2, 4, 1, 1. Carriers are not in
        the report and came home in full.
        """
        if self.loss_fraction is None:
            return None
        return {u: int(getattr(self, u) * self.loss_fraction + 0.5) for u in UNITS}


# Offsets into the 28 bytes a result appends to the echoed attack.
_FAILED = 0            # 1 = FAILURE, 0 = SUCCESS
_DESTROYED = 1         # i32, defending troopers killed ("You destroyed 8 Troopers!")
_CAPTURED = 17         # i32, regions captured ("captured 10 regions!")
_LOSS = 21             # 6-byte Real, fraction of each unit type the attacker lost


def forces(record: Record) -> Forces:
    """Hidden game state: only ever for an explicit admin reveal, never a listing."""
    d = record.data
    if record.type not in (INDIV_ATTACK, ATTACK_RESULT) or len(d) < ATTACK_SIZE:
        raise PacketFormatError(f"{record.name} is not an individual attack")
    out = Forces(*(struct.unpack_from("<i", d, o)[0]
                   for o in (_TROOPERS, _JETS, _TANKS, _BOMBERS, _CARRIERS)))
    if record.type == ATTACK_RESULT and len(d) >= ATTACK_SIZE + 28:
        tail = d[ATTACK_SIZE:]
        out.success = tail[_FAILED] == 0
        out.defenders_destroyed = struct.unpack_from("<i", tail, _DESTROYED)[0]
        out.regions_captured = struct.unpack_from("<i", tail, _CAPTURED)[0]
        loss = real48(tail[_LOSS:_LOSS + 6])
        # A fraction; anything else is a damaged record, and unknown beats invented.
        out.loss_fraction = loss if 0.0 <= loss <= 1.0 else None
    return out


def attacks(packet: bytes) -> List[Attack]:
    return [attack(r) for r in parse(packet)
            if r.type in (INDIV_ATTACK, ATTACK_RESULT)]


# ── who holds each realm ──────────────────────────────────────────────────
# A Recon Update is one planet telling the league about its realms. Slot k is
# realm letter chr(ord("A") + k): its name, and at RECON_PLAYER_IDS its player
# ID, the number ops, trade deals and messages carry. Checked against
# production (12 Oct 2026 backup): every player ID an op or trade deal carried
# was in its planet's Recon Updates at the slot of the letter beside it.
RECON_UPDATE = 0x02
RECON_UPDATE_SIZE = 1448
REALM_SLOTS = 25
_REALM_NAMES = 2            # 25 Pascal strings of 30 characters
_REALM_NAME_SIZE = 31
_RECON_PLAYER_IDS = 1077    # 25 i32s


@dataclass
class Realm:
    letter: str
    player_id: Optional[int]
    name: str


def realms(record: Record) -> List[Realm]:
    """The realms a Recon Update announces for its planet (`record.data[0]`).
    Empty slots are left out."""
    d = record.data
    if record.type != RECON_UPDATE or len(d) < RECON_UPDATE_SIZE:
        raise PacketFormatError(f"{record.name} is not a Recon Update")
    out = []
    ids = struct.unpack_from(f"<{REALM_SLOTS}i", d, _RECON_PLAYER_IDS)
    for k in range(REALM_SLOTS):
        o = _REALM_NAMES + k * _REALM_NAME_SIZE
        if not 0 < d[o] < _REALM_NAME_SIZE:
            continue
        name = _pascal(d, o).strip()
        if name:
            out.append(Realm(chr(ord("A") + k), ids[k] or None, name))
    return out


# ── league settings ───────────────────────────────────────────────────────
# The League Coordinator's editor settings travel as a Configupdate. A Game
# Start (the first run after a league-wide reset) carries the same 240 bytes.
# Mapped on the rig by resetting 900B from the console with distinctive values
# in every field and diffing what the hub sent (16 Oct 2026; the captures are
# tests/fixtures/bre_configupdates.json). Bytes 12, 38, 42 and 58-240 are not
# settings the editor shows: 12 changes from game to game, 66-73 is the same
# 8 bytes twice, the rest stayed zero.
CONFIG_UPDATE = 0x0A
GAME_START = 0x10

# u16 words, by offset, as catalogue field ids (game_reset/fields.py).
_WORDS = {14: "turns_per_day", 16: "protection_turns", 18: "land_per_day",
          20: "interest_rate", 22: "tax_rate", 24: "indiv_attacks",
          26: "group_attacks", 28: "terrorist_ops", 30: "bombings",
          32: "lost_attack_days", 34: "investment_rate", 36: "max_region_purchase",
          40: "initial_land"}
# Single bytes. The cost and damage choices share one code; Buy Military has its own.
_CHOICES = {45: "maintenance_costs", 46: "attack_damage", 47: "attack_costs",
            48: "attack_rewards", 49: "terrorist_costs", 50: "region_costs",
            51: "trade_costs"}
_HMLN = {0: "M", 1: "N", 2: "L", 3: "H"}
_BUY_MILITARY = {0: "Y", 1: "N", 2: "L"}
_TOGGLES = {75: "gooie", 76: "dupe_checking", 77: "local_attacks",
            78: "local_scoring", 79: "bombing_ops", 80: "missile_ops",
            81: "steady_investment"}
# Sabre: 0 None, 1 User Select, 2 Random, 200 + the dial for Constant.
_SABRE = {0: "N", 1: "U", 2: "R"}


@dataclass
class LeagueSettings:
    game_started_at: Optional[datetime.datetime]
    protection_turns: int
    indiv_attacks_per_day: int
    lost_attack_days: int      # the MIT window: no result by then, forces come home
    # Every setting of BRE's reset editor but the start date, by catalogue
    # field id, in the catalogue's values (letters for choices, bools for toggles,
    # ISO dates). A value the map doesn't know is left out, not guessed.
    settings: dict = field(default_factory=dict)


def _date(d: bytes, o: int) -> Optional[str]:
    y, m, day = struct.unpack_from("<3H", d, o)
    try:
        return datetime.date(y, m, day).isoformat()
    except ValueError:
        return None


def league_settings(record: Record) -> LeagueSettings:
    """The settings in a Configupdate, or in a Game Start, which shares its layout."""
    d = record.data
    if record.type not in (CONFIG_UPDATE, GAME_START) or len(d) < 83:
        raise PacketFormatError(f"{record.name} is not a Configupdate")
    w = struct.unpack_from("<6H", d, 0)
    try:
        started = datetime.datetime(*w)
    except ValueError:
        started = None
    out = {fid: struct.unpack_from("<H", d, o)[0] for o, fid in _WORDS.items()}
    out["max_players"] = d[44]
    for o, fid in _CHOICES.items():
        if d[o] in _HMLN:
            out[fid] = _HMLN[d[o]]
    if d[74] in _BUY_MILITARY:
        out["buy_military"] = _BUY_MILITARY[d[74]]
    for o, fid in _TOGGLES.items():
        if d[o] in (0, 1):
            out[fid] = bool(d[o])
    if d[82] in _SABRE:
        out["sabre"] = _SABRE[d[82]]
    elif 200 <= d[82] <= 210:
        out["sabre"], out["sabre_dial"] = "C", d[82] - 200
    joined = _date(d, 52)
    if joined:
        out["join_date"] = joined
    return LeagueSettings(started, out["protection_turns"], out["indiv_attacks"],
                          out["lost_attack_days"], out)


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        print(path)
        for rec in parse(open(path, "rb").read()):
            line = (f"  {rec.name:15} {rec.src:>3}->{rec.dst:<3} {len(rec.data):5}B "
                    f"crc={'ok' if rec.crc_ok else 'BAD'}")
            if rec.type in (INDIV_ATTACK, ATTACK_RESULT):
                a = attack(rec)
                line += (f"  id={a.attack_id} {a.from_planet}{a.attacker}->"
                         f"{a.to_planet}{a.target} {a.attack_type} {a.stamp}")
            print(line)


# ── the rest of the InterBBS traffic ──────────────────────────────────────
# Everything a player does to another planet, other than an individual attack.
# Mapped on the rig against what the game told the player, and the Gooie and
# Special/Bombing Op records from production's own traffic. As with attacks,
# `traffic()` reads only who did what kind of thing to whom; what was in it is
# `traffic_details()`, for the admin reveal. A Message's text is never read: its
# header ends at MESSAGE_TEXT and nothing here looks past the recipients.
#
# None of these carry an ID, but each result echoes enough of what it answers
# to be joined to it: `chain` is that shared part, the same string on a send
# and on its result (see traffic_trace for how they are matched up).
TERRORIST_OP, TERRORIST_RESULT = 0x03, 0x04
BOMBING_OP, BOMBING_RESULT = 0x05, 0x06
MESSAGE = 0x09
GOOIE_ATTACK, GOOIE_RESULTS = 0x0B, 0x0C
SPECIAL_OP, SPECIAL_RESULT = 0x0E, 0x0F
SPY_REPORT, NEWS, REPORT, SPY_GUY, TRADE_DEAL = 0x13, 0x14, 0x15, 0x17, 0x18
TRAFFIC_TYPES = frozenset({
    TERRORIST_OP, TERRORIST_RESULT, BOMBING_OP, BOMBING_RESULT, MESSAGE,
    GOOIE_ATTACK, GOOIE_RESULTS, SPECIAL_OP, SPECIAL_RESULT,
    SPY_REPORT, NEWS, REPORT, SPY_GUY, TRADE_DEAL,
})

# Terrorist Ops, by their number on the game's menu, which is what the op carries.
OPERATIONS = {
    1: "Send Spy", 2: "Bomb Intelligence", 3: "Demoralize", 4: "Cause Dissensions",
    5: "Bomb AirBases", 6: "Stir Emigrations", 7: "Spread Propaganda",
    8: "Bomb Food Stores", 9: "Sabotage HQ",
}
SEND_SPY = 1
# A Trade Deal's goods: nine i32s from offset 16, in the order of its menu.
TRADE_GOODS = ("troopers", "jets", "turrets", "bombers", "food", "gold",
               "agents", "tanks", "carriers")
MESSAGE_RECIPIENTS = 7      # i32 player IDs, zero-terminated, up to 25 of them
MESSAGE_TEXT = 114          # where the player's text begins -- never read
ALL_PLANETS = -999          # the recipient ID of a message to every planet

# The Gooie's life is told to the target in News items, [from][to][text]; the
# only News the hub follows. The kill is the target telling the sender.
GOOIE_NEWS = (
    ("funding completed", "Gooie Funded"),
    ("construction started", "Gooie Construction"),
    ("arrives from", "Gooie Warning"),
    ("destroyed on", "Gooie Destroyed"),
)
SEND, RESULT, NOTICE = "send", "result", "notice"


@dataclass
class Traffic:
    """Who sent what to whom. Letters and player IDs are on the record's
    from/to sides (the board that wrote it, and the board it is for); None
    where the record does not say."""
    type: int
    name: str
    from_planet: int
    to_planet: int
    from_letter: Optional[str] = None
    to_letter: Optional[str] = None
    from_player: Optional[int] = None
    to_player: Optional[int] = None
    recipients: Optional[List[int]] = None     # Message only
    chain: Optional[str] = None                # shared by a send and its result
    role: Optional[str] = None                 # SEND, RESULT or NOTICE
    stamp: Optional[datetime.datetime] = None  # when the far side resolved it


def event_key(record: Record) -> str:
    """The same record relayed by the hub is the same bytes; that is its identity.
    (Two ops identical to the byte -- same realms, count, type, agents owned --
    would be one event. Ops carry no ID of their own.)"""
    return hashlib.sha1(bytes([record.type]) + record.data).hexdigest()[:16]


def _i32(d: bytes, o: int) -> int:
    return struct.unpack_from("<i", d, o)[0]


def _pascal(d: bytes, o: int) -> str:
    text = d[o + 1:o + 1 + d[o]].decode("cp437", "replace")
    return re.sub(r"\^\\[0-9A-Fa-f]{2}", "", text)     # the game's colour codes


def traffic(record: Record) -> Optional[Traffic]:
    """None for anything the hub does not follow -- including any News item
    that is not about a Gooie."""
    if record.type not in TRAFFIC_TYPES:
        return None
    d, t = record.data, record.type
    out = Traffic(t, record.name, record.src, record.dst)
    try:
        _read(out, d, t)
    except (IndexError, struct.error, OverflowError, ValueError):
        pass                # a short record: who and where is still worth having
    if t == NEWS and out.role is None:
        return None
    return out


def _read(out: Traffic, d: bytes, t: int) -> None:
    if t == TERRORIST_OP:
        out.from_letter, out.to_letter = chr(d[0]), chr(d[1])
        out.from_player, out.to_player = _i32(d, 2), _i32(d, 6)
        out.chain = f"op:{d[2:10].hex()}:{'spy' if d[17] == SEND_SPY else 'op'}"
        out.role = SEND
    elif t == TERRORIST_RESULT:
        # The result travels back: from the target's board to the operative's.
        out.stamp = timestamp(d[0:6])
        out.from_letter, out.to_letter = chr(d[7]), chr(d[6])
        out.from_player, out.to_player = _i32(d, 12), _i32(d, 8)
        out.chain = f"op:{d[8:16].hex()}:{'spy' if d[20] == SEND_SPY else 'op'}"
        out.role = RESULT
    elif t == SPY_REPORT:
        # What a successful Send Spy brings home instead of a Terrorist Result:
        # the target's player ID, then the spy's.
        out.from_player, out.to_player = _i32(d, 2), _i32(d, 6)
        out.stamp = timestamp(d[68:74])
        out.chain = f"op:{(d[6:10] + d[2:6]).hex()}:spy"
        out.role = RESULT
    elif t == TRADE_DEAL:
        out.from_letter, out.to_letter = chr(d[0]), chr(d[1])
        out.from_player, out.to_player = _i32(d, 2), _i32(d, 6)
        out.chain = f"trade:{d[2:6].hex()}>{d[11]}"
        out.role = SEND
    elif t == REPORT:
        out.to_player = _i32(d, 2)
        if _pascal(d, 6).startswith("Trade Deal arrived"):
            out.chain = f"trade:{d[2:6].hex()}>{d[0]}"
            out.role = RESULT
    elif t == BOMBING_OP:
        # A planet-wide op: the operative's letter and ID, no target realm.
        out.from_letter, out.from_player = chr(d[0]), _i32(d, 1)
        out.chain = f"bomb:{d[0:5].hex()}:{d[5]}>{d[6]}:{d[7]}"
        out.role = SEND
    elif t == BOMBING_RESULT:
        out.to_letter, out.to_player = chr(d[0]), _i32(d, 1)
        out.chain = f"bomb:{d[0:5].hex()}:{d[6]}>{d[5]}:{d[7]}"
        out.role = RESULT
    elif t == SPECIAL_OP:
        out.from_letter, out.to_letter = chr(d[0]), chr(d[1])
        out.from_player, out.to_player = _i32(d, 2), _i32(d, 6)
        out.chain = f"sop:{d[2:10].hex()}:{d[12]}"
        out.role = SEND
    elif t == SPECIAL_RESULT:
        out.from_letter, out.to_letter = chr(d[1]), chr(d[0])
        out.from_player, out.to_player = _i32(d, 6), _i32(d, 2)
        out.chain = f"sop:{d[2:10].hex()}:{d[12]}"
        out.role = RESULT
    elif t in (GOOIE_ATTACK, GOOIE_RESULTS):
        # [target planet][sending planet]; one Gooie per planet at a time.
        out.chain = f"gooie:{d[1]}>{d[0]}"
        out.role = SEND if t == GOOIE_ATTACK else RESULT
    elif t == NEWS:
        text = _pascal(d, 2)
        if "Gooie Kablooie" in text:
            for phrase, name in GOOIE_NEWS:
                if phrase in text:
                    out.name = name
                    sender, target = (d[1], d[0]) if name == "Gooie Destroyed" else (d[0], d[1])
                    out.chain = f"gooie:{sender}>{target}"
                    out.role = NOTICE
                    break
    elif t == MESSAGE:
        out.from_letter, out.from_player = chr(d[2]), _i32(d, 3)
        ids = []
        for o in range(MESSAGE_RECIPIENTS, MESSAGE_RECIPIENTS + 25 * 4, 4):
            v = _i32(d, o)
            if v == 0:
                break
            ids.append(v)
            if v == ALL_PLANETS:
                break
        out.recipients = ids


def traffic_details(record: Record) -> Optional[dict]:
    """Hidden game state, for the admin reveal only. None for a Message: its
    content is never decoded, and for types not yet mapped. PacketFormatError
    for a record too short to hold what its type says."""
    try:
        return _traffic_details(record)
    except (IndexError, struct.error, OverflowError, ValueError) as exc:
        raise PacketFormatError(f"{record.name} is damaged: {exc}") from exc


def _traffic_details(record: Record) -> Optional[dict]:
    d, t = record.data, record.type
    if t == TERRORIST_OP:
        return {"operation": OPERATIONS.get(d[17], f"op {d[17]}"), "sent": d[11],
                "agents_owned": _i32(d, 13)}
    if t == TERRORIST_RESULT:
        return {"operation": OPERATIONS.get(d[20], f"op {d[20]}"), "sent": d[17],
                "succeeded": d[19]}
    if t == TRADE_DEAL:
        goods = struct.unpack_from("<9i", d, 16)
        return {"goods": {g: n for g, n in zip(TRADE_GOODS, goods) if n}}
    if t == SPY_REPORT:
        return {"realm": _pascal(d, 12), "troopers": _i32(d, 47)}
    if t == REPORT:
        return {"text": _pascal(d, 6)}
    if t == NEWS:
        return {"text": _pascal(d, 2)}
    if t == SPY_GUY:
        return {"days": d[2]}
    return None
