"""The address book: every BBS in every league, with its index and address.

A BBS is meant to keep the same index in every league it joins, and its FTN
addresses are its own. This puts all of it on one page so a misallocation shows
at a glance instead of being found league by league -- the way 135:135/20 went
to two BBSes before addresses belonged to the BBS.
"""

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from backend.core.database import get_db
from backend.core.scope import Scope, get_scope
from backend.models.database import Client, League, LeagueMembership

router = APIRouter()


class AddressBookLeague(BaseModel):
    id: int
    full_id: str
    name: str


class AddressBookEntry(BaseModel):
    league_id: int  # database id, matching AddressBookLeague.id
    bbs_index: int
    fidonet_address: str | None = None


class AddressBookBbs(BaseModel):
    id: int
    bbs_name: str
    is_active: bool
    addresses: List[str]
    memberships: List[AddressBookEntry]
    index_differs: bool  # its active memberships do not all use one index


class AddressBook(BaseModel):
    leagues: List[AddressBookLeague]
    bbses: List[AddressBookBbs]


@router.get("", response_model=AddressBook, summary="Address Book")
async def address_book(
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """
    Every BBS against every active league: its index and FTN address in each,
    and whether its index differs between leagues.

    Only active memberships are listed. BBSes in no league are included, so a
    newly added one shows up here before it joins anything.

    A sysop sees only their own leagues, the BBSes in them, and the addresses
    those BBSes use there -- what the leagues' nodelists publish.
    """
    query = db.query(League).filter(League.is_active == True)  # noqa: E712
    if not scope.is_admin:
        query = query.filter(scope.league_clause(League.id))
    leagues = query.order_by(League.league_id, League.game_type).all()
    league_ids = {league.id for league in leagues}

    clients = (
        db.query(Client)
        .options(
            selectinload(Client.ftn_addresses),
            selectinload(Client.league_memberships).selectinload(LeagueMembership.ftn_address),
        )
        .order_by(Client.bbs_name)
        .all()
    )

    bbses = []
    for client in clients:
        own = scope.sees_client(client.id)
        memberships = [
            AddressBookEntry(
                league_id=m.league_id,
                bbs_index=m.bbs_index,
                fidonet_address=m.fidonet_address,
            )
            for m in client.league_memberships
            if m.is_active and m.league_id in league_ids
        ]
        if not scope.is_admin and not memberships and not own:
            continue  # in none of this sysop's leagues
        addresses = [a.address for a in client.ftn_addresses]
        if not scope.is_admin and not own:
            addresses = sorted({m.fidonet_address for m in memberships if m.fidonet_address})
        bbses.append(
            AddressBookBbs(
                id=client.id,
                bbs_name=client.bbs_name,
                is_active=bool(client.is_active),
                addresses=addresses,
                memberships=memberships,
                index_differs=len({m.bbs_index for m in memberships}) > 1,
            )
        )

    return AddressBook(
        leagues=[AddressBookLeague(id=l.id, full_id=l.full_id, name=l.name) for l in leagues],
        bbses=bbses,
    )
