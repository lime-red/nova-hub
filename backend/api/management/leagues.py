"""Management API league management endpoints"""

import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.scope import Scope, get_scope
from backend.core.security import require_admin
from backend.logging_config import get_logger
from backend.services.games import game_for_letter
from backend.models.database import (
    Client,
    FtnAddress,
    League,
    LeagueMembership,
    Packet,
    ProcessingRun,
    ProcessingRunFile,
    SequenceAlert,
    SysopUser,
)
from backend.schemas.leagues import (
    AddMemberRequest,
    FtnAddressRef,
    LeagueCreate,
    LeagueDeleteRequest,
    LeagueDetailResponse,
    LeagueResponse,
    LeagueStats,
    LeagueUpdate,
    MemberResponse,
    NodelistInfo,
    UpdateMemberRequest,
)

logger = get_logger(context="management_leagues")

router = APIRouter()


@router.get("", response_model=List[LeagueResponse], summary="List All Leagues")
async def list_leagues(
    scope: Scope = Depends(get_scope),
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
    query = db.query(League)
    if not scope.is_admin:
        query = query.filter(scope.league_clause(League.id))
    leagues = query.all()

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
                hub_fidonet_address=league.hub_fidonet_address,
                hub_routes_mail=league.hub_routes_mail,
                is_active=league.is_active,
                member_count=member_count,
            )
        )

    return league_list


@router.get("/{league_id}", response_model=LeagueDetailResponse, summary="Get League Details")
async def get_league(
    league_id: int,
    scope: Scope = Depends(get_scope),
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
    scope.require_league(league_id)
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    # Get all league members
    memberships = (
        db.query(LeagueMembership)
        .filter(LeagueMembership.league_id == league_id)
        .all()
    )

    members = [_member_response(m) for m in memberships if m.client]
    if not scope.is_admin:
        # A member sees the league as its nodelist shows it: who is in it, at
        # what index and address. Not the hub's paths, other BBSes' OAuth ids,
        # or which BBSes could be added.
        members = [_public_member(m) for m in members if m.is_active]

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
        {
            "id": c.id,
            "bbs_name": c.bbs_name,
            "client_id": c.client_id,
            "ftn_addresses": [r.model_dump() for r in _address_refs(c)],
            # A BBS keeps one index across leagues; the form starts from it.
            "other_leagues": sorted(
                (
                    {"full_id": m.league.full_id, "bbs_index": m.bbs_index}
                    for m in c.league_memberships
                    if m.is_active and m.league
                ),
                key=lambda o: o["full_id"],
            ),
        }
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
        hub_fidonet_address=league.hub_fidonet_address,
        hub_routes_mail=league.hub_routes_mail,
        dosemu_path=league.dosemu_path if scope.is_admin else None,
        game_executable=league.game_executable if scope.is_admin else None,
        is_active=league.is_active,
        members=members,
        available_clients=available_list if scope.is_admin else [],
        stats=stats,
        nodelist=_nodelist_info(league),
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

    _check_hub_address_is_free(db, request.hub_fidonet_address)

    league = League(
        league_id=request.league_id,
        game_type=request.game_type,
        name=request.name,
        description=request.description,
        dosemu_path=request.dosemu_path,
        game_executable=request.game_executable,
        hub_fidonet_address=request.hub_fidonet_address,
        hub_routes_mail=(
            request.hub_routes_mail if request.hub_routes_mail is not None else True
        ),
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
        hub_fidonet_address=league.hub_fidonet_address,
        hub_routes_mail=league.hub_routes_mail,
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
    - `hub_fidonet_address`: The hub's own FidoNet address in this league
      (optional). Nodelist generation is blocked until this is set.
    - `hub_routes_mail`: whether the hub's nodelist entry carries the game's
      HOST routing directive (optional).
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
    if request.hub_fidonet_address is not None:
        _check_hub_address_is_free(db, request.hub_fidonet_address)
        league.hub_fidonet_address = request.hub_fidonet_address or None
    if request.hub_routes_mail is not None:
        league.hub_routes_mail = request.hub_routes_mail
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
        hub_fidonet_address=league.hub_fidonet_address,
        hub_routes_mail=league.hub_routes_mail,
        is_active=league.is_active,
        member_count=member_count,
    )


@router.delete("/{league_id}", summary="Delete League")
async def delete_league(
    league_id: int,
    request: LeagueDeleteRequest,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Delete a league and ALL associated data (admin only).

    Permanently removes: league record, member associations, packets, processing
    runs, sequence alerts, and all related files on disk (packet files and
    nodelist directory).

    **Path Parameters:**
    - `league_id`: Database ID of the league

    **Request Body:**
    - `confirmation_name`: Must match the league's confirmation name, e.g. `BRE_014`

    **Returns:** Success message
    """
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    # Validate confirmation name (e.g., "BRE_014" or "FE_555"). A row from
    # before game_type was validated can hold an unknown letter; it must still
    # be deletable, so fall back to the letter itself.
    try:
        game = game_for_letter(league.game_type)
    except KeyError:
        game = None
    game_name = game.code if game else league.game_type
    expected_name = f"{game_name}_{league.league_id}"
    if request.confirmation_name != expected_name:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation name does not match. Expected: {expected_name}",
        )

    league_name = league.full_id
    league_id_str = league.league_id

    # Collect packet filenames for disk cleanup using raw SQL (no ORM tracking)
    result = db.execute(
        text("SELECT filename FROM packets WHERE league_id = :lid"), {"lid": league_id}
    )
    packet_filenames = [row[0] for row in result]

    # Delete in FK dependency order using raw SQL to bypass SQLAlchemy relationship machinery
    db.execute(text("DELETE FROM sequence_alerts WHERE league_id = :lid"), {"lid": league_id})
    db.execute(text("""
        DELETE FROM processing_run_files
        WHERE processing_run_id IN (
            SELECT id FROM processing_runs WHERE league_id = :lid
        )
    """), {"lid": league_id})
    db.execute(text("DELETE FROM processing_run_files WHERE league_id = :lid"), {"lid": league_id})
    db.execute(text("DELETE FROM packets WHERE league_id = :lid"), {"lid": league_id})
    db.execute(text("DELETE FROM processing_runs WHERE league_id = :lid"), {"lid": league_id})
    db.execute(text("DELETE FROM league_memberships WHERE league_id = :lid"), {"lid": league_id})
    db.execute(text("DELETE FROM leagues WHERE id = :lid"), {"lid": league_id})
    db.commit()

    # Clean up packet files from disk
    from backend.core.config import get_config

    data_dir = Path(get_config().get("server", {}).get("data_dir", "./data"))
    for filename in packet_filenames:
        for subdir in ("inbound", "outbound", "processed"):
            filepath = data_dir / "packets" / subdir / filename
            if filepath.exists():
                filepath.unlink()
                logger.info(f"Deleted packet file {filepath}")

    # Delete nodelist directory. Skip it for an unknown game: older code filed
    # those under fe/, where the directory may belong to a real FE league.
    if game:
        nodelist_dir = data_dir / "nodelists" / game.key / league_id_str
        if nodelist_dir.exists():
            shutil.rmtree(nodelist_dir)
            logger.info(f"Deleted nodelist directory {nodelist_dir}")

    logger.info(f"Deleted league {league_name} and all associated data by {current_user.username}")

    return {"message": f"League {league_name} deleted successfully"}


def _check_hub_address_is_free(db: Session, address: Optional[str]) -> None:
    """400 if a BBS holds this address. The hub is not a client, so its
    per-league address lives on the league -- and the same address may serve
    several leagues -- but it must never be one a BBS also answers to."""
    if not address or not address.strip():
        return
    holder = db.query(FtnAddress).filter(FtnAddress.address == address.strip()).first()
    if holder:
        raise HTTPException(
            status_code=400,
            detail=f"{address.strip()} belongs to {holder.client.bbs_name}; the hub needs its own address",
        )


# Member management endpoints

def _check_bbs_index(db: Session, league_id: int, bbs_index: int,
                     exclude_membership_id: Optional[int] = None) -> None:
    """Raise 400 unless bbs_index is in range and free in this league."""
    if not (1 <= bbs_index <= 255):
        raise HTTPException(status_code=400, detail="BBS ID must be between 1 and 255")
    query = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league_id,
        LeagueMembership.bbs_index == bbs_index,
        LeagueMembership.is_active == True,
    )
    if exclude_membership_id is not None:
        query = query.filter(LeagueMembership.id != exclude_membership_id)
    holder = query.first()
    if holder:
        raise HTTPException(
            status_code=400,
            detail=f"BBS ID {bbs_index} is already assigned to {holder.client.bbs_name} in this league",
        )


def _check_ftn_address(db: Session, client: Client, ftn_address_id: int) -> FtnAddress:
    """The address, if it is one of this client's own; 400 otherwise.

    Addresses are unique across the hub and owned by a BBS, so a membership
    can only use one its BBS already holds -- which is what stops two BBSes
    sharing an address. Assigning a new one happens on the client.
    """
    address = db.query(FtnAddress).filter(FtnAddress.id == ftn_address_id).first()
    if not address:
        raise HTTPException(status_code=400, detail="FTN address not found")
    if address.client_id != client.id:
        raise HTTPException(
            status_code=400,
            detail=f"{address.address} belongs to {address.client.bbs_name}, not {client.bbs_name}",
        )
    return address


def _address_refs(client: Client) -> List[FtnAddressRef]:
    return [FtnAddressRef(id=a.id, address=a.address) for a in client.ftn_addresses]


def _public_member(member: MemberResponse) -> MemberResponse:
    return member.model_copy(update={"client_oauth_id": None, "client_ftn_addresses": [],
                                     "ftn_address_id": None})


def _member_response(membership: LeagueMembership) -> MemberResponse:
    client = membership.client
    return MemberResponse(
        membership_id=membership.id,
        client_id=client.id,
        bbs_name=client.bbs_name,
        bbs_index=membership.bbs_index,
        ftn_address_id=membership.ftn_address_id,
        fidonet_address=membership.fidonet_address,
        client_ftn_addresses=_address_refs(client),
        client_oauth_id=client.client_id,
        joined_at=membership.joined_at.strftime("%Y-%m-%d %H:%M") if membership.joined_at else None,
        is_active=membership.is_active,
    )


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
    - `ftn_address_id`: One of the client's own FTN addresses (assigned on the client)

    **Returns:** Created membership

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/leagues/1/members" \\
      -H "Content-Type: application/json" \\
      -d '{"client_id": 1, "bbs_index": 2, "ftn_address_id": 7}' \\
      -b cookies.txt
    ```
    """
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    client = db.query(Client).filter(Client.id == request.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    _check_bbs_index(db, league_id, request.bbs_index)
    address = _check_ftn_address(db, client, request.ftn_address_id)

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
        existing.ftn_address = address
        db.commit()
        membership = existing
    else:
        # Create new membership
        membership = LeagueMembership(
            league_id=league_id,
            client_id=request.client_id,
            bbs_index=request.bbs_index,
            ftn_address=address,
            is_active=True,
        )
        db.add(membership)
        db.commit()
        db.refresh(membership)

    logger.info(f"Added {client.bbs_name} to league {league.full_id} by {current_user.username}")

    return _member_response(membership)


def _data_dir() -> str:
    from backend.core.config import get_config

    return get_config().get("server", {}).get("data_dir", "./data")


def _nodelist_info(league: League) -> Optional[NodelistInfo]:
    from datetime import datetime

    from backend.services.nodelist_generator import find_nodelist

    path = find_nodelist(_data_dir(), league)
    if path is None:
        return None
    stat = path.stat()
    return NodelistInfo(
        filename=path.name,
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime),
    )


@router.get("/{league_id}/nodelist", summary="Download Nodelist")
async def download_nodelist(
    league_id: int,
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """
    Download the league's current BRNODES/FENODES nodelist.

    The same file nova_client fetches through the service API: the one the last
    processing run (or Generate Nodelist) wrote. Fetching it here does not mark
    it downloaded for any BBS.

    **Path Parameters:**
    - `league_id`: Database ID of the league

    **Returns:** the nodelist file; 404 if none has been generated yet
    """
    from backend.services.nodelist_generator import find_nodelist

    scope.require_league(league_id)
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    path = find_nodelist(_data_dir(), league)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No nodelist has been generated for {league.full_id} yet")

    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.post("/{league_id}/generate-nodelist", summary="Generate Nodelist")
async def generate_nodelist(
    league_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Generate a BRNODES/FENODES nodelist file from the current league membership (admin only).

    The generated file is written to the nodelists directory and made available
    for download by `nova_client` via the service API.

    **Path Parameters:**
    - `league_id`: Database ID of the league

    **Returns:** `{"filename": ..., "members": <count>}`
    """
    from backend.core.config import get_config
    from backend.services.nodelist_generator import NodelistGenerator

    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    try:
        game_for_letter(league.game_type)
    except KeyError:
        raise HTTPException(
            status_code=422,
            detail=f"League has unknown game type '{league.game_type}' — nodelist not generated",
        )

    config = get_config()
    data_dir = config.get("server", {}).get("data_dir", "./data")
    generator = NodelistGenerator(db, data_dir, config.get("hub", {}))
    dest = generator.generate(league_id)

    if dest is None:
        if not (league.hub_fidonet_address or "").strip():
            raise HTTPException(
                status_code=422,
                detail=(
                    "League has no hub_fidonet_address, so the hub's own node entry "
                    "and its HOST routing line cannot be written. Set it first; the "
                    "existing nodelist has been left untouched."
                ),
            )
        raise HTTPException(
            status_code=422,
            detail="No active members with a BBS index assigned — nodelist not generated",
        )

    member_count = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.league_id == league_id,
            LeagueMembership.is_active == True,
            LeagueMembership.bbs_index.isnot(None),
        )
        .count()
    )

    logger.info(f"Generated nodelist {dest.name} for league {league.full_id} by {current_user.username}")

    return {"filename": dest.name, "members": member_count}


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


@router.patch("/{league_id}/members/{membership_id}", response_model=MemberResponse, summary="Edit Member")
async def update_member(
    league_id: int,
    membership_id: int,
    request: UpdateMemberRequest,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Edit a league membership's BBS index and/or FTN address (admin only)

    Both fields are validated before either is written, so a rejected edit
    leaves the membership exactly as it was. The league's nodelist picks the
    change up at its next regeneration.

    **Path Parameters:**
    - `league_id`: Database ID of the league
    - `membership_id`: Database ID of the membership

    **Request Body** (at least one):
    - `bbs_index`: New BBS index (1-255)
    - `ftn_address_id`: One of the client's own FTN addresses

    **Returns:** The updated membership

    **Example:**
    ```bash
    curl -X PATCH "https://hub.example.com/management/api/v1/leagues/1/members/1" \\
      -H "Content-Type: application/json" \\
      -d '{"bbs_index": 5, "ftn_address_id": 7}' \\
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

    if request.bbs_index is None and request.ftn_address_id is None:
        raise HTTPException(status_code=400, detail="Nothing to change: give bbs_index and/or ftn_address_id")

    address = None
    if request.bbs_index is not None:
        _check_bbs_index(db, league_id, request.bbs_index, exclude_membership_id=membership_id)
    if request.ftn_address_id is not None:
        address = _check_ftn_address(db, membership.client, request.ftn_address_id)

    before = (membership.bbs_index, membership.fidonet_address)
    if request.bbs_index is not None:
        membership.bbs_index = request.bbs_index
    if address is not None:
        membership.ftn_address = address
    db.commit()
    db.refresh(membership)

    logger.info(
        f"Edited {membership.client.bbs_name} in league {membership.league.full_id}: "
        f"index {before[0]} -> {membership.bbs_index}, "
        f"address {before[1]} -> {membership.fidonet_address} by {current_user.username}"
    )

    return _member_response(membership)
