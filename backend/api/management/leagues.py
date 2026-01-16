"""Management API league management endpoints"""

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user, require_admin
from backend.logging_config import get_logger
from backend.models.database import Client, League, LeagueMembership, Packet, ProcessingRun, SysopUser
from backend.schemas.leagues import (
    AddMemberRequest,
    LeagueCreate,
    LeagueDetailResponse,
    LeagueResponse,
    LeagueStats,
    LeagueUpdate,
    MemberResponse,
    UpdateBbsIndexRequest,
    UpdateFidonetRequest,
)

logger = get_logger(context="management_leagues")

router = APIRouter()


@router.get("", response_model=List[LeagueResponse], summary="List All Leagues")
async def list_leagues(
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List all leagues with member counts

    **Returns:** List of all leagues

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/leagues" \\
      -b cookies.txt
    ```
    """
    leagues = db.query(League).all()

    league_list = []
    for league in leagues:
        member_count = (
            db.query(LeagueMembership)
            .filter(
                LeagueMembership.league_id == league.id,
                LeagueMembership.is_active == True,
            )
            .count()
        )

        league_list.append(
            LeagueResponse(
                id=league.id,
                league_id=league.league_id,
                game_type=league.game_type,
                full_id=league.full_id,
                name=league.name,
                description=league.description,
                is_active=league.is_active,
                member_count=member_count,
            )
        )

    return league_list


@router.get("/{league_id}", response_model=LeagueDetailResponse, summary="Get League Details")
async def get_league(
    league_id: int,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get detailed information about a specific league

    **Path Parameters:**
    - `league_id`: Database ID of the league

    **Returns:** League details with members and stats

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/leagues/1" \\
      -b cookies.txt
    ```
    """
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    # Get all league members
    memberships = (
        db.query(LeagueMembership)
        .filter(LeagueMembership.league_id == league_id)
        .all()
    )

    members = []
    for membership in memberships:
        client = db.query(Client).filter(Client.id == membership.client_id).first()
        if client:
            members.append(
                MemberResponse(
                    membership_id=membership.id,
                    client_id=client.id,
                    bbs_name=client.bbs_name,
                    bbs_index=membership.bbs_index,
                    fidonet_address=membership.fidonet_address,
                    client_oauth_id=client.client_id,
                    joined_at=membership.joined_at.strftime("%Y-%m-%d %H:%M") if membership.joined_at else None,
                    is_active=membership.is_active,
                )
            )

    # Get available clients (not already in this league)
    member_client_ids = [m.client_id for m in memberships]
    if member_client_ids:
        available_clients = (
            db.query(Client)
            .filter(
                Client.id.notin_(member_client_ids),
                Client.is_active == True,
            )
            .all()
        )
    else:
        available_clients = db.query(Client).filter(Client.is_active == True).all()

    available_list = [
        {"id": c.id, "bbs_name": c.bbs_name, "client_id": c.client_id}
        for c in available_clients
    ]

    # Get statistics
    stats = LeagueStats(
        total_packets=db.query(Packet).filter(Packet.league_id == league_id).count(),
        processed_packets=db.query(Packet)
        .filter(
            Packet.league_id == league_id,
            Packet.is_processed == True,
        )
        .count(),
        processing_runs=db.query(ProcessingRun)
        .filter(ProcessingRun.league_id == league_id)
        .count(),
    )

    return LeagueDetailResponse(
        id=league.id,
        league_id=league.league_id,
        game_type=league.game_type,
        full_id=league.full_id,
        name=league.name,
        description=league.description,
        dosemu_path=league.dosemu_path,
        game_executable=league.game_executable,
        is_active=league.is_active,
        members=members,
        available_clients=available_list,
        stats=stats,
    )


@router.post("", response_model=LeagueResponse, summary="Create League")
async def create_league(
    request: LeagueCreate,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Create a new league (admin only)

    **Request Body:**
    - `league_id`: 3-digit league number (e.g., "555")
    - `game_type`: "B" for BRE or "F" for Falcon's Eye
    - `name`: Display name for the league
    - `description`: Optional description
    - `dosemu_path`: Path to DOSemu installation (optional)
    - `game_executable`: Game executable name (optional)
    - `is_active`: Whether league is active (default: true)

    **Returns:** Created league

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/leagues" \\
      -H "Content-Type: application/json" \\
      -d '{"league_id": "555", "game_type": "B", "name": "BRE League 555"}' \\
      -b cookies.txt
    ```
    """
    # Check if league already exists
    existing = (
        db.query(League)
        .filter(
            League.league_id == request.league_id,
            League.game_type == request.game_type,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="League with this ID and game type already exists",
        )

    league = League(
        league_id=request.league_id,
        game_type=request.game_type,
        name=request.name,
        description=request.description,
        dosemu_path=request.dosemu_path,
        game_executable=request.game_executable,
        is_active=request.is_active if request.is_active is not None else True,
    )
    db.add(league)
    db.commit()
    db.refresh(league)

    logger.info(f"Created league {league.full_id} by {current_user.username}")

    return LeagueResponse(
        id=league.id,
        league_id=league.league_id,
        game_type=league.game_type,
        full_id=league.full_id,
        name=league.name,
        description=league.description,
        is_active=league.is_active,
        member_count=0,
    )


@router.put("/{league_id}", response_model=LeagueResponse, summary="Update League")
async def update_league(
    league_id: int,
    request: LeagueUpdate,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Update a league (admin only)

    **Path Parameters:**
    - `league_id`: Database ID of the league

    **Request Body:**
    - `name`: New display name (optional)
    - `description`: New description (optional)
    - `dosemu_path`: New DOSemu path (optional)
    - `game_executable`: New game executable (optional)
    - `is_active`: New active status (optional)

    **Returns:** Updated league

    **Example:**
    ```bash
    curl -X PUT "https://hub.example.com/management/api/v1/leagues/1" \\
      -H "Content-Type: application/json" \\
      -d '{"name": "Updated Name", "is_active": false}' \\
      -b cookies.txt
    ```
    """
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    if request.name is not None:
        league.name = request.name
    if request.description is not None:
        league.description = request.description if request.description else None
    if request.dosemu_path is not None:
        league.dosemu_path = request.dosemu_path if request.dosemu_path else None
    if request.game_executable is not None:
        league.game_executable = request.game_executable if request.game_executable else None
    if request.is_active is not None:
        league.is_active = request.is_active

    db.commit()
    db.refresh(league)

    logger.info(f"Updated league {league.full_id} by {current_user.username}")

    member_count = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.league_id == league.id,
            LeagueMembership.is_active == True,
        )
        .count()
    )

    return LeagueResponse(
        id=league.id,
        league_id=league.league_id,
        game_type=league.game_type,
        full_id=league.full_id,
        name=league.name,
        description=league.description,
        is_active=league.is_active,
        member_count=member_count,
    )


@router.delete("/{league_id}", summary="Delete League")
async def delete_league(
    league_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Delete a league and all memberships (admin only)

    **Path Parameters:**
    - `league_id`: Database ID of the league

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X DELETE "https://hub.example.com/management/api/v1/leagues/1" \\
      -b cookies.txt
    ```
    """
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    league_name = league.full_id

    # Delete all memberships first
    db.query(LeagueMembership).filter(LeagueMembership.league_id == league_id).delete()
    db.delete(league)
    db.commit()

    logger.info(f"Deleted league {league_name} by {current_user.username}")

    return {"message": f"League {league_name} deleted successfully"}


# Member management endpoints


@router.post("/{league_id}/members", response_model=MemberResponse, summary="Add Member")
async def add_member(
    league_id: int,
    request: AddMemberRequest,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Add a client to a league (admin only)

    **Path Parameters:**
    - `league_id`: Database ID of the league

    **Request Body:**
    - `client_id`: Database ID of the client to add
    - `bbs_index`: BBS index (1-255)
    - `fidonet_address`: Fidonet address (zone:net/node format)

    **Returns:** Created membership

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/leagues/1/members" \\
      -H "Content-Type: application/json" \\
      -d '{"client_id": 1, "bbs_index": 2, "fidonet_address": "13:10/100"}' \\
      -b cookies.txt
    ```
    """
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    client = db.query(Client).filter(Client.id == request.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Validate BBS index range
    if not (1 <= request.bbs_index <= 255):
        raise HTTPException(status_code=400, detail="BBS ID must be between 1 and 255")

    # Check BBS index uniqueness within league
    existing_bbs = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.league_id == league_id,
            LeagueMembership.bbs_index == request.bbs_index,
            LeagueMembership.is_active == True,
        )
        .first()
    )
    if existing_bbs:
        raise HTTPException(
            status_code=400,
            detail=f"BBS ID {request.bbs_index} is already assigned in this league",
        )

    # Validate Fidonet address format
    if not re.match(r"^\d+:\d+/\d+$", request.fidonet_address):
        raise HTTPException(
            status_code=400,
            detail="Invalid Fidonet address format. Use zone:net/node (e.g., 13:10/100)",
        )

    # Check Fidonet address uniqueness within league
    existing_fidonet = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.league_id == league_id,
            LeagueMembership.fidonet_address == request.fidonet_address,
            LeagueMembership.is_active == True,
        )
        .first()
    )
    if existing_fidonet:
        raise HTTPException(
            status_code=400,
            detail=f"Fidonet address {request.fidonet_address} is already assigned in this league",
        )

    # Check if already a member
    existing = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.league_id == league_id,
            LeagueMembership.client_id == request.client_id,
        )
        .first()
    )

    if existing:
        # Reactivate if inactive and update values
        existing.is_active = True
        existing.bbs_index = request.bbs_index
        existing.fidonet_address = request.fidonet_address
        db.commit()
        membership = existing
    else:
        # Create new membership
        membership = LeagueMembership(
            league_id=league_id,
            client_id=request.client_id,
            bbs_index=request.bbs_index,
            fidonet_address=request.fidonet_address,
            is_active=True,
        )
        db.add(membership)
        db.commit()
        db.refresh(membership)

    logger.info(f"Added {client.bbs_name} to league {league.full_id} by {current_user.username}")

    return MemberResponse(
        membership_id=membership.id,
        client_id=client.id,
        bbs_name=client.bbs_name,
        bbs_index=membership.bbs_index,
        fidonet_address=membership.fidonet_address,
        client_oauth_id=client.client_id,
        joined_at=membership.joined_at.strftime("%Y-%m-%d %H:%M") if membership.joined_at else None,
        is_active=membership.is_active,
    )


@router.delete("/{league_id}/members/{member_id}", summary="Remove Member")
async def remove_member(
    league_id: int,
    member_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Remove a client from a league (admin only)

    **Path Parameters:**
    - `league_id`: Database ID of the league
    - `member_id`: Client ID to remove

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X DELETE "https://hub.example.com/management/api/v1/leagues/1/members/2" \\
      -b cookies.txt
    ```
    """
    membership = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.league_id == league_id,
            LeagueMembership.client_id == member_id,
        )
        .first()
    )

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    db.delete(membership)
    db.commit()

    logger.info(f"Removed member {member_id} from league {league_id} by {current_user.username}")

    return {"message": "Member removed successfully"}


@router.put("/{league_id}/members/{membership_id}/bbs-index", summary="Update BBS Index")
async def update_bbs_index(
    league_id: int,
    membership_id: int,
    request: UpdateBbsIndexRequest,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Update BBS index for a league membership (admin only)

    **Path Parameters:**
    - `league_id`: Database ID of the league
    - `membership_id`: Database ID of the membership

    **Request Body:**
    - `bbs_index`: New BBS index (1-255)

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X PUT "https://hub.example.com/management/api/v1/leagues/1/members/1/bbs-index" \\
      -H "Content-Type: application/json" \\
      -d '{"bbs_index": 5}' \\
      -b cookies.txt
    ```
    """
    membership = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.id == membership_id,
            LeagueMembership.league_id == league_id,
        )
        .first()
    )

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Validate range
    if not (1 <= request.bbs_index <= 255):
        raise HTTPException(status_code=400, detail="BBS ID must be between 1 and 255")

    # Check uniqueness within league (excluding current membership)
    existing = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.league_id == league_id,
            LeagueMembership.bbs_index == request.bbs_index,
            LeagueMembership.id != membership_id,
            LeagueMembership.is_active == True,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"BBS ID {request.bbs_index} is already assigned to another member in this league",
        )

    membership.bbs_index = request.bbs_index
    db.commit()

    logger.info(f"Updated BBS index for membership {membership_id} to {request.bbs_index} by {current_user.username}")

    return {"message": f"BBS index updated to {request.bbs_index}"}


@router.put("/{league_id}/members/{membership_id}/fidonet", summary="Update Fidonet Address")
async def update_fidonet(
    league_id: int,
    membership_id: int,
    request: UpdateFidonetRequest,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Update Fidonet address for a league membership (admin only)

    **Path Parameters:**
    - `league_id`: Database ID of the league
    - `membership_id`: Database ID of the membership

    **Request Body:**
    - `fidonet_address`: New Fidonet address (zone:net/node format)

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X PUT "https://hub.example.com/management/api/v1/leagues/1/members/1/fidonet" \\
      -H "Content-Type: application/json" \\
      -d '{"fidonet_address": "13:10/200"}' \\
      -b cookies.txt
    ```
    """
    membership = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.id == membership_id,
            LeagueMembership.league_id == league_id,
        )
        .first()
    )

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Validate format
    if not re.match(r"^\d+:\d+/\d+$", request.fidonet_address):
        raise HTTPException(
            status_code=400,
            detail="Invalid Fidonet address format. Use zone:net/node (e.g., 13:10/100)",
        )

    # Check uniqueness within league (excluding current membership)
    existing = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.league_id == league_id,
            LeagueMembership.fidonet_address == request.fidonet_address,
            LeagueMembership.id != membership_id,
            LeagueMembership.is_active == True,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Fidonet address {request.fidonet_address} is already assigned to another member in this league",
        )

    membership.fidonet_address = request.fidonet_address
    db.commit()

    logger.info(f"Updated Fidonet address for membership {membership_id} to {request.fidonet_address} by {current_user.username}")

    return {"message": f"Fidonet address updated to {request.fidonet_address}"}
