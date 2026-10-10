"""
What the signed-in user may see in the console.

Admins see everything. A sysop sees:
- the BBSes they own (client_owners),
- the leagues those BBSes are active members of, with the members' names,
  indexes and addresses (published in the nodelist anyway), nothing more,
- packets and sequence alerts to or from their BBSes, in any league they have
  been a member of, and
- processing runs that carried a packet or made a file in their leagues.

Every management read takes a Scope and applies it on the server; the console
hiding things is presentation, not enforcement. A record outside the scope is
a 404, not a 403, so that ids cannot be probed for existence.
"""

from dataclasses import dataclass, field
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy import and_, exists, false, or_
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.database import (
    ClientOwner,
    LeagueMembership,
    Packet,
    ProcessingRun,
    ProcessingRunFile,
    SequenceAlert,
    SysopUser,
)


@dataclass(frozen=True)
class Scope:
    user: SysopUser
    is_admin: bool
    client_ids: frozenset = field(default_factory=frozenset)
    # Leagues an owned BBS is an active member of.
    league_ids: frozenset = field(default_factory=frozenset)
    # (league id, BBS index as two hex digits) for every membership of an owned
    # BBS, active or not: packets and alerts are its history.
    stations: frozenset = field(default_factory=frozenset)

    def sees_client(self, client_id: int) -> bool:
        return self.is_admin or client_id in self.client_ids

    def sees_league(self, league_id: Optional[int]) -> bool:
        return self.is_admin or league_id in self.league_ids

    def require_client(self, client_id: int) -> None:
        if not self.sees_client(client_id):
            raise HTTPException(status_code=404, detail="Client not found")

    def require_league(self, league_id: Optional[int]) -> None:
        if not self.sees_league(league_id):
            raise HTTPException(status_code=404, detail="League not found")

    def _between(self, league_col, source_col, dest_col):
        if self.is_admin:
            return None
        if not self.stations:
            return false()
        return or_(*(
            and_(league_col == league_id, or_(source_col == hex_, dest_col == hex_))
            for league_id, hex_ in self.stations
        ))

    def packet_clause(self):
        """A filter for Packet queries, or None for no filter."""
        return self._between(Packet.league_id, Packet.source_bbs_index, Packet.dest_bbs_index)

    def alert_clause(self):
        """A filter for SequenceAlert queries, or None for no filter."""
        return self._between(SequenceAlert.league_id, SequenceAlert.source_bbs_index,
                             SequenceAlert.dest_bbs_index)

    def league_clause(self, league_col):
        """A filter on a league id column, or None for no filter."""
        if self.is_admin:
            return None
        return league_col.in_(self.league_ids) if self.league_ids else false()

    def run_clause(self):
        """A filter for ProcessingRun queries, or None for no filter.

        A run processes every league at once and its league_id is normally
        unset, so a run is in a sysop's leagues by what it carried or made.
        """
        if self.is_admin:
            return None
        if not self.league_ids:
            return false()
        leagues = list(self.league_ids)
        return or_(
            ProcessingRun.league_id.in_(leagues),
            exists().where(Packet.processing_run_id == ProcessingRun.id,
                           Packet.league_id.in_(leagues)),
            exists().where(ProcessingRunFile.processing_run_id == ProcessingRun.id,
                           ProcessingRunFile.league_id.in_(leagues)),
        )

    def sees_packet(self, packet: Packet) -> bool:
        return self.is_admin or any(
            packet.league_id == league_id and hex_ in (packet.source_bbs_index,
                                                       packet.dest_bbs_index)
            for league_id, hex_ in self.stations
        )


def scope_for(db: Session, user: SysopUser) -> Scope:
    if user.is_superuser:
        return Scope(user=user, is_admin=True)
    client_ids = frozenset(
        row.client_id for row in
        db.query(ClientOwner.client_id).filter(ClientOwner.sysop_user_id == user.id)
    )
    memberships = (
        db.query(LeagueMembership).filter(LeagueMembership.client_id.in_(client_ids)).all()
        if client_ids else []
    )
    return Scope(
        user=user,
        is_admin=False,
        client_ids=client_ids,
        league_ids=frozenset(m.league_id for m in memberships if m.is_active),
        stations=frozenset((m.league_id, format(m.bbs_index, "02X")) for m in memberships),
    )


async def get_scope(
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Scope:
    """FastAPI dependency: the signed-in user's Scope."""
    return scope_for(db, current_user)
