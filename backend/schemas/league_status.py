"""League Status: each BBS's polls, queue and the hub game's view, per league"""

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel

from backend.services import game_status


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    """The database holds naive UTC; say so on the way out."""
    return value.replace(tzinfo=timezone.utc) if value else None


class WaitingPacketInfo(BaseModel):
    filename: str
    processed_at: Optional[datetime] = None


class GameViewInfo(BaseModel):
    """What the hub's copy of the game says about the board, as its players see it"""
    source: str  # "bbsinfo" (BRE, FE) or "timecheck" (IB)
    as_of: Optional[datetime] = None
    heard: bool
    last_heard: Optional[datetime] = None
    version: Optional[str] = None
    hub_version: Optional[str] = None
    routing_missing: Optional[bool] = None
    line: Optional[str] = None  # the BBSINFO line, verbatim


class LeagueStatusRow(BaseModel):
    """One BBS in one league.

    `state` is the first of game_status.STATES that applies; `state_label` says
    it in words and `state_detail` says why. On a league page a sysop gets
    only the game's view of BBSes they do not own: `restricted` is then true
    and the poll and queue fields are empty.
    """
    league_id: int
    full_id: str  # e.g. "015F"
    league_name: str
    game_type: str
    client_id: int
    bbs_name: str
    member: bool
    bbs_index: Optional[int] = None
    fidonet_address: Optional[str] = None
    restricted: bool = False
    first_poll_at: Optional[datetime] = None
    last_poll_at: Optional[datetime] = None
    user_agent: Optional[str] = None
    last_token_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None  # last token or last poll of any league
    waiting: Optional[int] = None  # None: the hub's own board, or restricted
    oldest_waiting_at: Optional[datetime] = None
    waiting_packets: List[WaitingPacketInfo] = []
    last_downloaded_at: Optional[datetime] = None
    last_uploaded_at: Optional[datetime] = None
    game: Optional[GameViewInfo] = None
    state: str
    state_label: str
    state_detail: Optional[str] = None

    @classmethod
    def of(cls, s: "game_status.LeagueStatus", restricted: bool = False) -> "LeagueStatusRow":
        g = s.game
        row = cls(
            league_id=s.league.id,
            full_id=f"{s.league.league_id}{s.league.game_type}",
            league_name=s.league.name,
            game_type=s.league.game_type,
            client_id=s.client.id,
            bbs_name=s.client.bbs_name,
            member=s.member is not None,
            bbs_index=s.member.bbs_index if s.member else None,
            fidonet_address=s.member.fidonet_address if s.member else None,
            game=GameViewInfo(
                source=g.source, as_of=_utc(g.as_of), heard=g.heard,
                last_heard=_utc(g.last_heard), version=g.version, hub_version=g.hub_version,
                routing_missing=g.routing_missing, line=g.line,
            ) if g else None,
            state=s.state,
            state_label=game_status.STATES[s.state],
            state_detail=s.state_detail,
        )
        if restricted:
            # Only the game's view, which the league's players see in-game anyway.
            row.state, row.state_detail = game_status.game_state(s, s.is_hub)
            row.state_label = game_status.STATES[row.state]
            row.restricted = True
            return row
        row.first_poll_at = _utc(s.poll.first_poll_at) if s.poll else None
        row.last_poll_at = _utc(s.poll.last_poll_at) if s.poll else None
        row.user_agent = (s.poll.last_user_agent if s.poll else None) or s.client.last_user_agent
        row.last_token_at = _utc(s.client.last_seen)
        row.last_active_at = _utc(s.last_active)
        row.waiting = s.waiting
        row.oldest_waiting_at = _utc(s.oldest_waiting_at)
        row.waiting_packets = [WaitingPacketInfo(filename=w.filename, processed_at=_utc(w.processed_at))
                               for w in s.waiting_packets]
        row.last_downloaded_at = _utc(s.last_downloaded_at)
        row.last_uploaded_at = _utc(s.last_uploaded_at)
        return row


class LeagueStatusResponse(BaseModel):
    tracking_since: Optional[datetime] = None  # polls before this went unrecorded
    rows: List[LeagueStatusRow]
