"""Each BBS's standing in each league: its polls, its queue, and the game's view of it.

Built for the client page's League Status card and the league page's members
table. The case that asked for it (prod, Oct 2026): eots was a member of 015F,
the hub game wrote packets for it every day, and eots never once asked for
them -- its client had only 015B configured. Nothing showed it: clients.last_seen
moves with a token for any league, and the hub's queue is a count nobody reads.

Three sources, none of them new:

- **Polls**, client_league_polls (written by the service API's packet listing):
  when this client last listed this league's packets.
- **The queue**: rows addressed to the board, processed and not downloaded.
  Rows addressed to the hub's own index never get a downloaded_at (the hub's
  game reads them from disk), so the hub's own membership has no queue.
- **The hub game's view**, which is what the board's players see in-game:
  for BRE and FE, the BBSINFO and ROUTEINFO listings the last processing run
  stored (bbsinfo.lst, routes.lst); for IB, which writes neither, the newest
  TimeCheck the board answered in a packet to the hub, and the Version it
  stamped on that packet.

The state is the first that applies, most actionable first (STATES).
"""
import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.models.database import (
    Client, ClientLeaguePoll, League, LeagueMembership, Packet, ProcessingRunFile,
)
from backend.services import ib_packet

OFFLINE_AFTER = dt.timedelta(hours=1)        # no token at all
STOPPED_AFTER = dt.timedelta(hours=1)        # no listing of this league
BACKLOG_AFTER = dt.timedelta(days=1)         # oldest waiting packet
SILENT_AFTER = dt.timedelta(days=2)          # the game's last recon from the board
WAITING_LISTED = 20                          # packets named in the expandable list
IB_PACKETS_READ = 5                          # newest board->hub packets searched

# In order: the first that applies is the row's state.
STATES = {
    "not_polling": "Never polled this league",
    "offline": "Client offline",
    "stopped_polling": "Stopped polling this league",
    "backlog": "Packets piling up",
    "game_silent": "Hub game has not heard from it",
    "out_of_date": "Game version differs from the hub's",
    "ok": "OK",
    "not_member": "Polls a league it is not a member of",
}

# "  1) Nova Hub                           10/31/2026  00:36:31      v1.06"
_BBSINFO_SEEN = re.compile(
    r"^\s*(\d+)\)\s+(.*?)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})\s+(\S+)\s*$")
# "  3) No Recons Recieved" (sic)
_BBSINFO_NONE = re.compile(r"^\s*(\d+)\)\s+No Recons? Rec(?:ie|ei)ved", re.I)
_ROUTES_HEAD = re.compile(r"Routing Paths of BBS #\s*(\d+)", re.I)
_ROUTES_MISSING = re.compile(r"Information missing for BBS #\s*(\d+)", re.I)


@dataclass
class BoardLine:
    """One BBSINFO line: the game's last recon from the board, in hub-local time."""
    index: int
    name: Optional[str] = None
    last_recon: Optional[dt.datetime] = None      # naive UTC
    version: Optional[str] = None


def _local_to_utc(text: str) -> Optional[dt.datetime]:
    """The game writes hub-local time; the database is naive UTC."""
    try:
        local = dt.datetime.strptime(text, "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None
    return local.astimezone(dt.timezone.utc).replace(tzinfo=None)


def parse_bbsinfo(text: str) -> Dict[int, BoardLine]:
    out: Dict[int, BoardLine] = {}
    for line in (text or "").splitlines():
        m = _BBSINFO_SEEN.match(line)
        if m:
            idx = int(m.group(1))
            out[idx] = BoardLine(idx, m.group(2).strip() or None,
                                 _local_to_utc(f"{m.group(3)} {m.group(4)}"), m.group(5))
            continue
        m = _BBSINFO_NONE.match(line)
        if m:
            out[int(m.group(1))] = BoardLine(int(m.group(1)))
    return out


def parse_routes(text: str) -> Dict[int, bool]:
    """Board index -> whether the game has its routing (False: "Information missing")."""
    out: Dict[int, bool] = {}
    for line in (text or "").splitlines():
        m = _ROUTES_HEAD.search(line)
        if m:
            out.setdefault(int(m.group(1)), True)
            continue
        m = _ROUTES_MISSING.search(line)
        if m:
            out[int(m.group(1))] = False
    return out


@dataclass
class GameView:
    """What the hub's copy of the game says about the board."""
    source: str                                   # "bbsinfo" or "timecheck"
    as_of: Optional[dt.datetime] = None           # when the hub learned it
    heard: bool = False                           # any recon / TimeCheck at all
    last_heard: Optional[dt.datetime] = None
    version: Optional[str] = None
    hub_version: Optional[str] = None
    routing_missing: Optional[bool] = None        # None: no routes listing (IB)
    line: Optional[str] = None                    # the BBSINFO line, verbatim


@dataclass
class WaitingPacket:
    filename: str
    processed_at: Optional[dt.datetime]


@dataclass
class LeagueStatus:
    league: League
    member: Optional[LeagueMembership]
    client: Client
    poll: Optional[ClientLeaguePoll] = None
    waiting: Optional[int] = None                 # None: the hub's own board
    oldest_waiting_at: Optional[dt.datetime] = None
    waiting_packets: List[WaitingPacket] = field(default_factory=list)
    last_downloaded_at: Optional[dt.datetime] = None
    last_uploaded_at: Optional[dt.datetime] = None
    game: Optional[GameView] = None
    # Its last token or its last poll of any league, whichever is later: a
    # client keeps its token for hours (SJ and Eclipse on prod, Oct 2026), so
    # clients.last_seen alone calls a board polling every two minutes offline.
    last_active: Optional[dt.datetime] = None
    is_hub: bool = False
    state: str = "ok"
    state_detail: Optional[str] = None


def _hex(index: Optional[int]) -> Optional[str]:
    return format(index, "02X") if index is not None else None


def hub_index_from(config: dict) -> int:
    """[hub] bbs_index, written as the two hex digits of a packet name."""
    try:
        return int(str(config.get("hub", {}).get("bbs_index", "01")), 16)
    except ValueError:
        return 1


def tracking_since(db: Session) -> Optional[dt.datetime]:
    """The first poll ever recorded: polls before it went unrecorded."""
    return db.query(func.min(ClientLeaguePoll.first_poll_at)).scalar()


# ── the hub game's view ───────────────────────────────────────────────────

def _latest_listings(db: Session, league_ids: List[int]) -> Dict[Tuple[int, str], ProcessingRunFile]:
    if not league_ids:
        return {}
    newest = (
        db.query(func.max(ProcessingRunFile.id))
        .filter(ProcessingRunFile.league_id.in_(league_ids),
                ProcessingRunFile.file_type.in_(("bbsinfo", "routes")),
                ProcessingRunFile.file_data != None)
        .group_by(ProcessingRunFile.league_id, ProcessingRunFile.file_type)
    )
    rows = db.query(ProcessingRunFile).filter(ProcessingRunFile.id.in_(newest)).all()
    return {(r.league_id, r.file_type): r for r in rows}


def _sr_view(listings, league: League, index: int, hub_index: int) -> Optional[GameView]:
    info = listings.get((league.id, "bbsinfo"))
    routes = listings.get((league.id, "routes"))
    if info is None and routes is None:
        return None
    view = GameView(source="bbsinfo", as_of=(info or routes).created_at)
    if info is not None:
        boards = parse_bbsinfo(info.file_data)
        hub = boards.get(hub_index)
        view.hub_version = hub.version if hub else None
        board = boards.get(index)
        if board is not None:
            view.heard = board.last_recon is not None
            view.last_heard = board.last_recon
            view.version = board.version
        view.line = next((ln.rstrip() for ln in info.file_data.splitlines()
                          if re.match(rf"^\s*{index}\)", ln)), None)
    if routes is not None:
        known = parse_routes(routes.file_data)
        if index in known:
            view.routing_missing = not known[index]
    return view


def _stored_json(packet: Packet, data_dir: Optional[Path]) -> Optional[dict]:
    """The packet as the hub holds it, checked against the row's checksum."""
    copies = [packet.file_data] if packet.file_data else []
    if data_dir:
        for sub in ("processed", "outbound", "inbound"):
            f = data_dir / "packets" / sub / packet.filename
            try:
                copies.append(f.read_bytes())
            except OSError:
                continue
    for content in copies:
        if isinstance(content, str):
            content = content.encode()
        if packet.checksum and hashlib.sha256(content).hexdigest() != packet.checksum:
            continue
        try:
            return ib_packet.parse(content)
        except ib_packet.PacketFormatError:
            continue
    return None


def _sent(item: dict) -> Optional[dt.datetime]:
    try:
        return (dt.datetime.fromisoformat(str(item.get("Sent")).replace("Z", "+00:00"))
                .astimezone(dt.timezone.utc).replace(tzinfo=None))
    except ValueError:
        return None


def _ib_view(db: Session, league: League, board: str, hub: str,
             data_dir: Optional[Path]) -> GameView:
    view = GameView(source="timecheck")
    from_board = (db.query(Packet)
                  .filter(Packet.league_id == league.id, Packet.source_bbs_index == board,
                          Packet.dest_bbs_index == hub)
                  .order_by(Packet.id.desc()).limit(IB_PACKETS_READ).all())
    for packet in from_board:
        p = _stored_json(packet, data_dir)
        if p is None:
            continue
        name = p.get("FromBoard")
        replies = [s for s in (_sent(i) for i in ib_packet._items(p, "TimeChecks")
                               if i.get("From") == name) if s]
        if view.version is None:
            view.version = ib_packet._str(p.get("Version"))
            view.as_of = packet.uploaded_at
        if replies:
            view.heard = True
            view.last_heard = max(replies)
            view.as_of = packet.uploaded_at
            break
    to_board = (db.query(Packet)
                .filter(Packet.league_id == league.id, Packet.source_bbs_index == hub)
                .order_by(Packet.id.desc()).limit(IB_PACKETS_READ).all())
    for packet in to_board:
        p = _stored_json(packet, data_dir)
        if p is not None and p.get("Version"):
            view.hub_version = ib_packet._str(p.get("Version"))
            break
    return view


# ── the queue ─────────────────────────────────────────────────────────────

def _fill_queue(db: Session, s: LeagueStatus, board: str) -> None:
    waiting = (db.query(Packet)
               .filter(Packet.league_id == s.league.id, Packet.dest_bbs_index == board,
                       Packet.processed_at != None, Packet.downloaded_at == None))
    s.waiting = waiting.count()
    if s.waiting:
        rows = waiting.order_by(Packet.processed_at.asc()).limit(WAITING_LISTED).all()
        s.oldest_waiting_at = rows[0].processed_at
        s.waiting_packets = [WaitingPacket(r.filename, r.processed_at) for r in rows]
    s.last_downloaded_at = (db.query(func.max(Packet.downloaded_at))
                            .filter(Packet.league_id == s.league.id,
                                    Packet.dest_bbs_index == board).scalar())


def _fill_uploaded(db: Session, s: LeagueStatus, board: str) -> None:
    s.last_uploaded_at = (db.query(func.max(Packet.uploaded_at))
                          .filter(Packet.league_id == s.league.id,
                                  Packet.source_bbs_index == board).scalar())


# ── the state ─────────────────────────────────────────────────────────────

def _ago(when: dt.datetime, now: dt.datetime) -> str:
    secs = int((now - when).total_seconds())
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 2 * 86400:
        return f"{secs // 3600} h"
    return f"{secs // 86400} days"


def _state(s: LeagueStatus, now: dt.datetime, since: Optional[dt.datetime],
           is_hub: bool) -> Tuple[str, Optional[str]]:
    if s.member is None:
        return "not_member", None
    if not is_hub:
        if s.poll is None:
            return "not_polling", (f"no poll since tracking began {since:%Y-%m-%d %H:%M} UTC"
                                   if since else "no poll recorded yet")
        last_seen = s.last_active
        if last_seen is None or now - last_seen > OFFLINE_AFTER:
            return "offline", (f"last heard from {_ago(last_seen, now)} ago" if last_seen
                               else "never signed in")
        if now - s.poll.last_poll_at > STOPPED_AFTER:
            return "stopped_polling", f"last poll {_ago(s.poll.last_poll_at, now)} ago"
        if s.oldest_waiting_at and now - s.oldest_waiting_at > BACKLOG_AFTER:
            return "backlog", (f"{s.waiting} waiting, oldest "
                               f"{_ago(s.oldest_waiting_at, now)}")
    return game_state(s, is_hub)


def game_state(s: LeagueStatus, is_hub: bool) -> Tuple[str, Optional[str]]:
    """The state from the game's view alone: what anyone in the league may see."""
    g = s.game
    if g is not None and not is_hub:
        if not g.heard:
            return "game_silent", ("no recons received" if g.source == "bbsinfo"
                                   else "no TimeCheck answered")
        # BBSINFO is as of the last run, so measure from it: a hub that has not
        # run is not the board's fault. A TimeCheck answer is as of the packet
        # that carried it, so measure from now.
        ref = g.as_of if g.source == "bbsinfo" else utcnow()
        if g.last_heard and ref and ref - g.last_heard > SILENT_AFTER:
            return "game_silent", (f"last heard {_ago(g.last_heard, ref)} "
                                   + ("before the last run" if g.source == "bbsinfo" else "ago"))
        if g.routing_missing:
            return "game_silent", "routing information missing"
    if g is not None and g.version and g.hub_version and g.version != g.hub_version:
        return "out_of_date", f"{g.version}, hub {g.hub_version}"
    return "ok", None


# ── assembly ──────────────────────────────────────────────────────────────

def statuses(db: Session, pairs: List[Tuple[Client, League, Optional[LeagueMembership]]],
             hub_index: int, data_dir: Optional[Path] = None) -> List[LeagueStatus]:
    """A LeagueStatus per (client, league, membership-or-None)."""
    now = utcnow()
    since = tracking_since(db)
    hub = _hex(hub_index)
    listings = _latest_listings(db, sorted({l.id for _, l, _ in pairs if l.game_type != "I"}))
    last_polls = dict(db.query(ClientLeaguePoll.client_id, func.max(ClientLeaguePoll.last_poll_at))
                      .filter(ClientLeaguePoll.client_id.in_({c.id for c, _, _ in pairs}))
                      .group_by(ClientLeaguePoll.client_id).all())
    out = []
    for client, league, member in pairs:
        s = LeagueStatus(league=league, member=member, client=client,
                         poll=db.get(ClientLeaguePoll, (client.id, league.id)),
                         last_active=max(filter(None, (client.last_seen, last_polls.get(client.id))),
                                         default=None))
        index = member.bbs_index if member else None
        board = _hex(index)
        is_hub = s.is_hub = board == hub
        if board is not None:
            if not is_hub:
                _fill_queue(db, s, board)
            _fill_uploaded(db, s, board)
            if league.game_type == "I":
                s.game = _ib_view(db, league, board, hub, data_dir)
            else:
                s.game = _sr_view(listings, league, index, hub_index)
        s.state, s.state_detail = _state(s, now, since, is_hub)
        out.append(s)
    return out


def for_client(db: Session, client: Client, hub_index: int,
               data_dir: Optional[Path] = None) -> List[LeagueStatus]:
    """Every league the client is an active member of, then any it polls without being one."""
    members = (db.query(LeagueMembership)
               .filter(LeagueMembership.client_id == client.id, LeagueMembership.is_active == True)
               .all())
    pairs = [(client, m.league, m) for m in members if m.league is not None]
    seen = {m.league_id for m in members}
    for poll in db.query(ClientLeaguePoll).filter(ClientLeaguePoll.client_id == client.id):
        if poll.league_id not in seen:
            league = db.get(League, poll.league_id)
            if league is not None:
                pairs.append((client, league, None))
    pairs.sort(key=lambda p: (p[2] is None, p[1].league_id, p[1].game_type))
    return statuses(db, pairs, hub_index, data_dir)


def for_league(db: Session, league: League, hub_index: int,
               data_dir: Optional[Path] = None) -> List[LeagueStatus]:
    """Every active member of the league, by BBS index."""
    members = (db.query(LeagueMembership)
               .filter(LeagueMembership.league_id == league.id, LeagueMembership.is_active == True)
               .all())
    pairs = [(m.client, league, m) for m in members if m.client is not None]
    pairs.sort(key=lambda p: (p[2].bbs_index is None, p[2].bbs_index or 0))
    return statuses(db, pairs, hub_index, data_dir)
