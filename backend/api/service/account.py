"""Service API: what the hub knows about the calling BBS.

The client compares this with its own config, so a sysop finds out that a
league or BBS index is wrong when they test the connection, not when the hub
starts refusing their uploads.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_client
from backend.models.database import Client, League, LeagueMembership
from backend.services.games import game_for_letter

router = APIRouter()


class AccountLeague(BaseModel):
    league_id: str
    game: str
    number: str
    name: str
    bbs_index: int
    fidonet_address: Optional[str] = None


class Account(BaseModel):
    client_id: str
    bbs_name: str
    leagues: List[AccountLeague]


@router.get("/me", response_model=Account, summary="Who Am I")
async def who_am_i(client: Client = Depends(get_current_client), db: Session = Depends(get_db)):
    """
    The authenticated BBS and the leagues it belongs to

    Lists the active leagues this BBS is an active member of, with the BBS
    index the hub has for it in each. The client's connection test compares
    these with its config.

    **Authentication:** Requires Bearer token
    """
    rows = (
        db.query(LeagueMembership, League)
        .join(League, LeagueMembership.league_id == League.id)
        .filter(
            LeagueMembership.client_id == client.id,
            LeagueMembership.is_active == True,  # noqa: E712
            League.is_active == True,  # noqa: E712
        )
        .all()
    )
    leagues = []
    for membership, league in rows:
        try:
            game = game_for_letter(league.game_type)
        except KeyError:
            continue
        leagues.append(AccountLeague(
            league_id=f"{league.league_id}{league.game_type}",
            game=game.code,
            number=league.league_id,
            name=league.name,
            bbs_index=membership.bbs_index,
            fidonet_address=membership.fidonet_address,
        ))
    leagues.sort(key=lambda l: l.league_id)
    return Account(client_id=client.client_id, bbs_name=client.bbs_name, leagues=leagues)
