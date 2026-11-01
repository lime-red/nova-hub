"""What the hub knows about an Immortal Barons league, as its Coordinator.

The hub runs a copy of the game as node 1, the League Coordinator. Nobody
plays on it: it signs the league's rules, roster and orders, and it is the
other end of every board's packets. So its world holds the answers the
console wants about the boards: when each one's packets were last accepted
and which version and rules they stated, whose packets it is holding and why,
the round trip to each, and the league freeze with each board's quiet report.
Andy's sysop panel (ib-sysop) reads the same fields; this is the hub's
reading of them. A sysop panel also lists the board's own strikes and trades
in flight; the hub's board has none, so they are not read.

Read straight from the data dir, never through the game: IB writes world.json
by rename, so a read sees one save or the next, and none of this needs the
world lock. Held packets are plain JSON files in held/. Board names are joined
to node numbers through the roster the hub wrote, ibnodes.dat.

The game decides why it holds a packet (store.holdReason); nothing records it,
so it is asked again here, of the same fields, as ib-sysop does:

  protocol   a packet format the hub's build cannot read (game.Protocol)
  rules      states rules that are neither the league's nor, within the grace
             after a change, the ones before
  signature  anything else: the sender's roster key did not verify it (the
             hub does not check signatures; the next run applies a packet
             no check holds, so one still here is all but certainly this)
  unreadable not a packet at all
"""
import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from backend.models.database import League

HELD_DIR = "held"
DEFAULT_HELD_PACKET_DAYS = 14          # game.DefaultHeldPacketDays
RULESET_GRACE = dt.timedelta(days=7)   # game.RulesetGraceDays
PROTOCOL = 5                           # game.Protocol in v0.2.6; the hub's own
                                       # packets say which it speaks now


def _stamp(text) -> Optional[dt.datetime]:
    """A game stamp ("01/02/2006  15:04:05 UTC", or the one-space report form),
    as naive UTC. Only UTC is written by the versions the hub runs; a stamp
    without a zone was on some board's clock and is not placed (ParseStamp)."""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"\s*(\d\d/\d\d/\d{4})\s+(\d\d:\d\d:\d\d) UTC\s*", text)
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None


def _instant(text) -> Optional[dt.datetime]:
    """An RFC 3339 time (Go's time.Time in JSON), as naive UTC; zero is None."""
    if not isinstance(text, str) or not text or text.startswith("0001-01-01"):
        return None
    try:
        t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t


def _utc_mtime(path: Path) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).replace(tzinfo=None)
    except OSError:
        return None


def roster(data_dir: Path) -> Dict[str, int]:
    """Board name -> node number, from ibnodes.dat: six or seven lines a board,
    a blank line between."""
    try:
        text = (data_dir / "ibnodes.dat").read_text(errors="replace")
    except OSError:
        return {}
    out: Dict[str, int] = {}
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = [ln.strip() for ln in block.strip().splitlines()]
        if len(lines) >= 2 and lines[0].isdigit():
            out[lines[1]] = int(lines[0])
    return out


_TYPES = (  # game.Packet.PacketType, first match wins
    ("LeagueConfig", "league config"), ("LeagueNodes", "roster"), ("Reset", "reset"),
    ("Freeze", "freeze"), ("Quiet", "quiet report"), ("Bulletins", "bulletins"),
    ("Attacks", "attacks"), ("Terrors", "terror ops"), ("SpecialOps", "special ops"),
    ("Results", "results"), ("Scores", "scores"), ("Recon", "recon"),
    ("ReconReports", "recon reports"), ("Annihilator", "annihilator"),
    ("TimeChecks", "time checks"), ("IPMessages", "ip messages"), ("SpyGuys", "spyguys"),
    ("News", "news"), ("Threats", "threats"), ("TradeBids", "trade"),
    ("TradeFills", "trade"), ("TradeDeals", "trade deals"), ("TradeReceipts", "trade deals"),
)


def packet_type(p: dict) -> str:
    if p.get("Attacks") and p.get("Results"):
        return "attacks, results"
    for key, name in _TYPES:
        if p.get(key):
            return name
    return "notice" if p.get("Notice") else "empty"


def version_key(v: Optional[str]) -> tuple:
    """"0.2.10" > "0.2.9"; a pre-release ("0.2.7-rc1") sorts below its release."""
    if not v:
        return ()
    core, _, pre = str(v).lstrip("v").partition("-")
    nums = tuple(int(n) if n.isdigit() else 0 for n in core.split("."))
    return nums + ((0, pre) if pre else (1, ""))


def below(version: Optional[str], minimum: Optional[str]) -> bool:
    return bool(version and minimum) and version_key(version) < version_key(minimum)


@dataclass
class HeldPacket:
    file: str
    reason: str
    arrived: Optional[dt.datetime]
    expires: Optional[dt.datetime]
    from_board: Optional[str] = None
    from_index: Optional[int] = None
    type: Optional[str] = None
    protocol: Optional[int] = None
    ruleset: Optional[str] = None
    version: Optional[str] = None


@dataclass
class Board:
    """One board as the Coordinator knows it."""
    name: str
    index: Optional[int]
    last_packet: Optional[dt.datetime] = None   # last ACCEPTED, a bounce included
    version: Optional[str] = None
    ruleset: Optional[str] = None               # the rules it said it plays by
    held_at: Optional[dt.datetime] = None       # last set aside for its protocol
    # Held for its protocol and not talked past since (ProtocolHoldCurrent): a
    # board on the hub's build holds its packets too, which stops the lost-
    # forces timer of any attack sent to it (attack_trace's `paused`).
    protocol_held: bool = False
    held: Dict[str, int] = field(default_factory=dict)   # reason -> files
    round_trip_days: Optional[float] = None
    round_trip_seen: Optional[dt.datetime] = None
    quiet_since: Optional[dt.datetime] = None   # its report for the freeze in force


@dataclass
class HubGame:
    data_dir: Path
    saved: Optional[dt.datetime]                # world.json's mtime
    started: Optional[str] = None
    ruleset: Optional[str] = None               # the league's, as the hub states it
    prev_ruleset: Optional[str] = None
    prev_ruleset_at: Optional[dt.datetime] = None
    min_board_version: Optional[str] = None
    protocol: int = PROTOCOL
    frozen: bool = False
    freeze_serial: int = 0
    freeze_message: Optional[str] = None
    frozen_at: Optional[dt.datetime] = None
    thawed_at: Optional[dt.datetime] = None
    boards: Dict[str, Board] = field(default_factory=dict)
    held: List[HeldPacket] = field(default_factory=list)

    def board_at(self, index: int) -> Optional[Board]:
        return next((b for b in self.boards.values() if b.index == index), None)

    def ruleset_divergent(self, p: dict, now: dt.datetime) -> bool:
        """game.World.RulesetDivergent, from the Coordinator's chair."""
        fp = p.get("Ruleset")
        if not fp or p.get("Notice") or p.get("FromNode") == 1:
            return False
        if not self.ruleset or fp == self.ruleset:
            return False
        return not (fp == self.prev_ruleset and self.prev_ruleset_at is not None
                    and now - self.prev_ruleset_at <= RULESET_GRACE)


def _board(game: HubGame, names: Dict[str, int], name: str) -> Board:
    b = game.boards.get(name)
    if b is None:
        b = game.boards[name] = Board(name, names.get(name))
    return b


def _read_json(path: Path) -> Optional[dict]:
    try:
        v = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _held_days(data_dir: Path) -> int:
    """bbs.cfg's HeldPacketDays, else the game's default."""
    try:
        text = (data_dir / "bbs.cfg").read_text(errors="replace")
    except OSError:
        return DEFAULT_HELD_PACKET_DAYS
    m = re.search(r"^\s*HeldPacketDays\s*[=:]?\s*(\d+)", text, re.M | re.I)
    days = int(m.group(1)) if m else 0
    return days if days > 0 else DEFAULT_HELD_PACKET_DAYS


def read(data_dir: Path, hub_ruleset: Optional[str] = None, hub_protocol: Optional[int] = None,
         now: Optional[dt.datetime] = None) -> Optional[HubGame]:
    """The hub game in `data_dir`, or None when it has no world yet.

    hub_ruleset and hub_protocol are what the hub's newest packet stated: the
    world keeps the league's fingerprint only as of the last maintenance, and
    no field at all for the protocol, which is a constant of the build."""
    from backend.core.clock import utcnow
    now = now or utcnow()
    world = _read_json(data_dir / "world.json")
    if world is None:
        return None
    rules = _read_json(data_dir / "config.json") or {}
    names = roster(data_dir)
    game = HubGame(
        data_dir=data_dir, saved=_utc_mtime(data_dir / "world.json"),
        started=world.get("StartedDate") or None,
        ruleset=hub_ruleset or world.get("LeagueRuleset") or None,
        prev_ruleset=world.get("PrevLeagueRuleset") or None,
        prev_ruleset_at=_stamp(world.get("PrevRulesetAt")),
        min_board_version=(rules.get("MinBoardVersion") or None),
        protocol=hub_protocol or PROTOCOL,
        frozen=bool(world.get("Frozen")), freeze_serial=int(world.get("FreezeSerial") or 0),
        freeze_message=world.get("FreezeMessage") or None,
        frozen_at=_instant(world.get("FrozenAt")), thawed_at=_instant(world.get("ThawedAt")),
    )
    for name, stamp in (world.get("LastPacketFrom") or {}).items():
        _board(game, names, name).last_packet = _stamp(stamp)
    for name, v in (world.get("BoardVersion") or {}).items():
        _board(game, names, name).version = v or None
    for name, fp in (world.get("BoardRuleset") or {}).items():
        _board(game, names, name).ruleset = fp or None
    for name, stamp in (world.get("ProtocolHeldAt") or {}).items():
        _board(game, names, name).held_at = _stamp(stamp)
    for name, days in (world.get("TravelTimes") or {}).items():
        if isinstance(days, (int, float)):
            _board(game, names, name).round_trip_days = float(days)
    for name, seen in (world.get("TravelSeen") or {}).items():
        _board(game, names, name).round_trip_seen = _instant(seen)
    if game.frozen:
        for name, stamp in (world.get("QuietBoards") or {}).items():
            _board(game, names, name).quiet_since = _stamp(stamp)
    _read_held(game, names, now)
    return game


def _protocol_hold_current(b: Board, arrived: Optional[dt.datetime]) -> bool:
    """game.World.ProtocolHoldCurrent: the hold is no older than the board's last
    accepted packet, so the link is still stalled, not a leftover."""
    held_at = b.held_at or arrived
    if held_at is None:
        return True
    return b.last_packet is None or held_at >= b.last_packet


def _read_held(game: HubGame, names: Dict[str, int], now: dt.datetime) -> None:
    folder = game.data_dir / HELD_DIR
    if not folder.is_dir():
        return
    keep = dt.timedelta(days=_held_days(game.data_dir))
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".brp" or path.name.startswith("."):
            continue
        arrived = _utc_mtime(path)
        h = HeldPacket(path.name, "unreadable", arrived, arrived + keep if arrived else None)
        p = _read_json(path)
        if p is not None:
            h.from_board = p.get("FromBoard") or None
            h.from_index = p.get("FromNode") or (names.get(h.from_board) if h.from_board else None)
            h.type, h.protocol = packet_type(p), p.get("Protocol")
            h.ruleset, h.version = p.get("Ruleset") or None, p.get("Version") or None
            if h.protocol != game.protocol:
                h.reason = "protocol"
            elif game.ruleset_divergent(p, now):
                h.reason = "rules"
            else:
                h.reason = "signature"
            if h.from_board:
                b = _board(game, names, h.from_board)
                b.held[h.reason] = b.held.get(h.reason, 0) + 1
                if h.reason == "protocol" and _protocol_hold_current(b, arrived):
                    b.protocol_held = True
        game.held.append(h)


def for_league(config: dict, league: League, hub_ruleset: Optional[str] = None,
               hub_protocol: Optional[int] = None) -> Optional[HubGame]:
    """The hub's Coordinator game for an IB league, from [ib.<league>] data_dir."""
    if league.game_type != "I":
        return None
    from backend.services.providers import provider_for
    paths = provider_for("I")._paths(config, league)
    if paths is None:
        return None
    return read(paths[0], hub_ruleset, hub_protocol)
