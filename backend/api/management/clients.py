"""Management API client management endpoints"""

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend.api.management.claim import public_url, utc
from backend.core.database import get_db
from backend.core.security import get_current_user, get_password_hash, require_admin
from backend.logging_config import get_logger
from backend.models.database import (
    ClaimLink,
    Client,
    FtnAddress,
    League,
    LeagueMembership,
    Packet,
    SysopUser,
)
from backend.schemas.clients import (
    ClientCreate,
    ClientCreatedResponse,
    ClientDetailResponse,
    ClientResponse,
    ClientSecretResponse,
    ClientStats,
    ClientUpdate,
    FtnAddressInfo,
    FtnAddressRequest,
    LeagueMembershipInfo,
    PacketHistoryItem,
)
from backend.schemas.claim import ClaimLinkIssued, ClaimLinkStatus
from backend.services import claim_links
from backend.services.stats_service import StatsService

logger = get_logger(context="management_clients")

router = APIRouter()


@router.get("", response_model=List[ClientResponse], summary="List All Clients")
async def list_clients(
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List all clients with basic stats

    **Returns:** List of all clients with 24h activity stats

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/clients" \\
      -b cookies.txt
    ```
    """
    stats_service = StatsService(db)
    clients = db.query(Client).all()

    client_list = []
    for client in clients:
        client_stats = stats_service.get_client_stats(client.id, days=1)
        client_list.append(
            ClientResponse(
                id=client.id,
                bbs_name=client.bbs_name,
                client_id=client.client_id,
                city=client.city,
                state=client.state,
                country=client.country,
                is_active=client.is_active,
                last_seen=client_stats.get("last_seen"),
                packets_sent_24h=client_stats.get("sent_24h", 0),
                packets_received_24h=client_stats.get("received_24h", 0),
            )
        )

    return client_list


@router.get("/{client_id}", response_model=ClientDetailResponse, summary="Get Client Details")
async def get_client(
    client_id: int,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get detailed information about a specific client

    **Path Parameters:**
    - `client_id`: Database ID of the client

    **Returns:** Client details with stats and recent packets

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/clients/1" \\
      -b cookies.txt
    ```
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    stats_service = StatsService(db)
    client_stats = stats_service.get_client_stats(client_id)

    # Get client memberships to determine BBS indexes per league
    memberships = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.client_id == client_id,
            LeagueMembership.is_active == True,
        )
        .all()
    )

    # Build a map of league_id -> bbs_hex
    league_bbs_map = {m.league_id: format(m.bbs_index, "02X") for m in memberships}

    # Get recent packets for any league where client is a member
    packets = []
    if league_bbs_map:
        filters = []
        for league_id, bbs_hex in league_bbs_map.items():
            filters.append(
                and_(
                    Packet.league_id == league_id,
                    or_(
                        Packet.source_bbs_index == bbs_hex,
                        Packet.dest_bbs_index == bbs_hex,
                    ),
                )
            )

        db_packets = (
            db.query(Packet)
            .filter(or_(*filters))
            .order_by(Packet.uploaded_at.desc())
            .limit(50)
            .all()
        )

        for packet in db_packets:
            league = db.query(League).filter(League.id == packet.league_id).first()
            bbs_hex = league_bbs_map.get(packet.league_id, "")
            # Direction from hub's perspective:
            # - "received" = hub received this packet from the client (client was source)
            # - "queued"   = hub has a packet ready for the client, not yet downloaded
            # - "sent"     = hub's packet has been downloaded by the client
            if packet.source_bbs_index == bbs_hex:
                direction = "received"
            elif packet.is_downloaded:
                direction = "sent"
            else:
                direction = "queued"

            packets.append(
                PacketHistoryItem(
                    filename=packet.filename,
                    direction=direction,
                    league_name=f"{league.game_type} {league.league_id}" if league else "Unknown",
                    source=packet.source_bbs_index,
                    dest=packet.dest_bbs_index,
                    timestamp=stats_service.format_timestamp(packet.uploaded_at),
                    processed_at=stats_service.format_timestamp(packet.processed_at) if packet.processed_at else None,
                    retrieved_at=stats_service.format_timestamp(packet.downloaded_at) if packet.downloaded_at else None,
                    processing_run_id=packet.processing_run_id,
                )
            )

    # Build league memberships list
    league_memberships = []
    for membership in memberships:
        league = db.query(League).filter(League.id == membership.league_id).first()
        if league:
            league_memberships.append(
                LeagueMembershipInfo(
                    league_id=league.id,
                    league_name=league.name,
                    full_id=f"{league.league_id}{league.game_type}",
                    bbs_index=membership.bbs_index,
                    fidonet_address=membership.fidonet_address,
                    nodelist_filename=_nodelist_filename(league),
                )
            )

    return ClientDetailResponse(
        id=client.id,
        bbs_name=client.bbs_name,
        client_id=client.client_id,
        city=client.city,
        state=client.state,
        country=client.country,
        is_active=client.is_active,
        created_at=client.created_at.strftime("%Y-%m-%d %H:%M") if client.created_at else None,
        stats=ClientStats(
            last_seen=client_stats.get("last_seen"),
            total_sent=client_stats.get("total_sent", 0),
            sent_24h=client_stats.get("sent_24h", 0),
            total_received=client_stats.get("total_received", 0),
            received_24h=client_stats.get("received_24h", 0),
        ),
        packets=packets,
        league_memberships=league_memberships,
        ftn_addresses=[_address_info(a) for a in client.ftn_addresses],
    )


@router.post("", response_model=ClientCreatedResponse, summary="Create Client")
async def create_client(
    request: ClientCreate,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Create a new client (admin only)

    **Request Body:**
    - `bbs_name`: Display name for the BBS
    - `client_id`: OAuth2 client ID (must be unique)
    - `city`, `state`, `country`: location lines for generated nodelists
      (optional)

    **Returns:** Created client with plain-text secret (shown only once)

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/clients" \\
      -H "Content-Type: application/json" \\
      -d '{"bbs_name": "Test BBS", "client_id": "test_client"}' \\
      -b cookies.txt
    ```
    """
    # Check if client_id already exists
    existing = db.query(Client).filter(Client.client_id == request.client_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Client ID already exists")

    # Generate random client secret
    client_secret = Client.generate_client_secret()

    client = Client(
        bbs_name=request.bbs_name,
        client_id=request.client_id,
        client_secret=get_password_hash(client_secret),
        city=request.city,
        state=request.state,
        country=request.country,
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    logger.info(f"Created client {request.client_id} by {current_user.username}")

    return ClientCreatedResponse(
        id=client.id,
        bbs_name=client.bbs_name,
        client_id=client.client_id,
        client_secret=client_secret,  # Plain text, shown only once
    )


@router.put("/{client_id}", response_model=ClientResponse, summary="Update Client")
async def update_client(
    client_id: int,
    request: ClientUpdate,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Update a client (admin only)

    **Path Parameters:**
    - `client_id`: Database ID of the client

    **Request Body:**
    - `bbs_name`: New display name (optional)
    - `city`, `state`, `country`: location lines for generated nodelists
      (optional)
    - `is_active`: Active status (optional)

    **Returns:** Updated client

    **Example:**
    ```bash
    curl -X PUT "https://hub.example.com/management/api/v1/clients/1" \\
      -H "Content-Type: application/json" \\
      -d '{"bbs_name": "Updated BBS", "is_active": false}' \\
      -b cookies.txt
    ```
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if request.bbs_name is not None:
        client.bbs_name = request.bbs_name
    if request.city is not None:
        client.city = request.city or None
    if request.state is not None:
        client.state = request.state or None
    if request.country is not None:
        client.country = request.country or None
    if request.is_active is not None:
        client.is_active = request.is_active

    db.commit()
    db.refresh(client)

    logger.info(f"Updated client {client.client_id} by {current_user.username}")

    stats_service = StatsService(db)
    client_stats = stats_service.get_client_stats(client.id, days=1)

    return ClientResponse(
        id=client.id,
        bbs_name=client.bbs_name,
        client_id=client.client_id,
        city=client.city,
        state=client.state,
        country=client.country,
        is_active=client.is_active,
        last_seen=client_stats.get("last_seen"),
        packets_sent_24h=client_stats.get("sent_24h", 0),
        packets_received_24h=client_stats.get("received_24h", 0),
    )


@router.delete("/{client_id}", summary="Delete Client")
async def delete_client(
    client_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Delete a client (admin only)

    **Path Parameters:**
    - `client_id`: Database ID of the client

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X DELETE "https://hub.example.com/management/api/v1/clients/1" \\
      -b cookies.txt
    ```
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    client_name = client.client_id
    # Its addresses go with it, so they can be assigned to another BBS.
    for address in client.ftn_addresses:
        db.delete(address)
    db.query(ClaimLink).filter(ClaimLink.client_id == client.id).delete()
    db.delete(client)
    db.commit()

    logger.info(f"Deleted client {client_name} by {current_user.username}")

    return {"message": f"Client {client_name} deleted successfully"}


@router.post("/{client_id}/claim-link", response_model=ClaimLinkIssued, summary="Issue Claim Link")
async def issue_claim_link(
    client_id: int,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Issue a single-use link that gives this BBS's sysop its credentials (admin only).

    Send the link instead of a secret. It expires after 72 hours and supersedes
    any outstanding link for this BBS. Nothing changes for the BBS until the
    link is claimed; claiming generates a new secret, which replaces the
    current one.

    **Returns:** the link (shown only here) and its expiry
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    token, link, superseded = claim_links.issue(db, client, current_user.username)
    logger.info(
        f"Claim link {link.id} issued for client {client.client_id} by {current_user.username}"
        + (f" (superseding {superseded})" if superseded else "")
    )
    return ClaimLinkIssued(
        url=f"{public_url(request)}/claim/{token}",
        expires_at=utc(link.expires_at),
        superseded=superseded,
    )


@router.get("/{client_id}/claim-link", response_model=Optional[ClaimLinkStatus], summary="Claim Link Status")
async def get_claim_link(
    client_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    The latest claim link issued for this BBS, or null (admin only).

    Never includes the link itself, which exists only in the response that
    issued it.
    """
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(status_code=404, detail="Client not found")
    link = claim_links.latest(db, client_id)
    if link is None:
        return None
    return ClaimLinkStatus(
        state=claim_links.state(link),
        issued_by=link.issued_by,
        issued_at=utc(link.issued_at),
        expires_at=utc(link.expires_at),
        used_at=utc(link.used_at),
        used_ip=link.used_ip,
    )


@router.post("/{client_id}/regenerate-secret", response_model=ClientSecretResponse, summary="Regenerate Secret")
async def regenerate_secret(
    client_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Regenerate client OAuth2 secret (admin only)

    **Path Parameters:**
    - `client_id`: Database ID of the client

    **Returns:** New client secret (shown only once)

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/clients/1/regenerate-secret" \\
      -b cookies.txt
    ```
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Generate new secret
    new_secret = Client.generate_client_secret()
    client.client_secret = get_password_hash(new_secret)
    db.commit()

    logger.info(f"Regenerated secret for client {client.client_id} by {current_user.username}")

    return ClientSecretResponse(
        client_id=client.client_id,
        client_secret=new_secret,  # Plain text, shown only once
    )


# FTN addresses. A property of the BBS, unique across the hub; memberships
# point at one of them (see FtnAddress). Admin only: a sysop renumbering
# their own address would break every league that routes to it.

def _nodelist_filename(league: League) -> Optional[str]:
    from backend.core.config import get_config
    from backend.services.nodelist_generator import find_nodelist

    path = find_nodelist(get_config().get("server", {}).get("data_dir", "./data"), league)
    return path.name if path else None


FIDONET_RE = re.compile(r"^\d+:\d+/\d+$")


def _address_info(address: FtnAddress) -> FtnAddressInfo:
    return FtnAddressInfo(
        id=address.id,
        address=address.address,
        leagues=sorted(m.league.full_id for m in address.memberships if m.league),
    )


def _check_address(db: Session, text: str, exclude_id: Optional[int] = None) -> str:
    """The normalised address, if well-formed and held by no other row; 400 otherwise."""
    address = text.strip()
    if not FIDONET_RE.match(address):
        raise HTTPException(
            status_code=400,
            detail="Invalid FTN address format. Use zone:net/node (e.g., 135:135/21)",
        )
    query = db.query(FtnAddress).filter(FtnAddress.address == address)
    if exclude_id is not None:
        query = query.filter(FtnAddress.id != exclude_id)
    holder = query.first()
    if holder:
        raise HTTPException(
            status_code=400,
            detail=f"{address} already belongs to {holder.client.bbs_name}",
        )
    hub_in = sorted(
        l.full_id for l in db.query(League).filter(League.hub_fidonet_address == address)
    )
    if hub_in:
        raise HTTPException(
            status_code=400,
            detail=f"{address} is the hub's own address in {', '.join(hub_in)}",
        )
    return address


def _client_address(db: Session, client_id: int, address_id: int) -> FtnAddress:
    address = (
        db.query(FtnAddress)
        .filter(FtnAddress.id == address_id, FtnAddress.client_id == client_id)
        .first()
    )
    if not address:
        raise HTTPException(status_code=404, detail="FTN address not found for this client")
    return address


@router.post("/{client_id}/ftn-addresses", response_model=FtnAddressInfo, summary="Assign FTN Address")
async def add_ftn_address(
    client_id: int,
    request: FtnAddressRequest,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Assign an FTN address to a BBS (admin only)

    The address must not belong to any other BBS. Use it in a league by
    choosing it on that league's membership.

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/clients/1/ftn-addresses" \\
      -H "Content-Type: application/json" \\
      -d '{"address": "135:135/21"}' \\
      -b cookies.txt
    ```
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    address = FtnAddress(client_id=client.id, address=_check_address(db, request.address))
    db.add(address)
    db.commit()
    db.refresh(address)

    logger.info(f"Assigned {address.address} to {client.bbs_name} by {current_user.username}")
    return _address_info(address)


@router.put("/{client_id}/ftn-addresses/{address_id}", response_model=FtnAddressInfo, summary="Renumber FTN Address")
async def update_ftn_address(
    client_id: int,
    address_id: int,
    request: FtnAddressRequest,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Change an FTN address (admin only)

    Every league membership using it follows, and each of those leagues'
    nodelists picks the change up at its next regeneration.
    """
    address = _client_address(db, client_id, address_id)
    old = address.address
    address.address = _check_address(db, request.address, exclude_id=address.id)
    db.commit()
    db.refresh(address)

    logger.info(
        f"Renumbered {address.client.bbs_name} {old} -> {address.address} "
        f"(used in {[m.league.full_id for m in address.memberships]}) by {current_user.username}"
    )
    return _address_info(address)


@router.delete("/{client_id}/ftn-addresses/{address_id}", summary="Remove FTN Address")
async def delete_ftn_address(
    client_id: int,
    address_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Remove an FTN address from a BBS (admin only)

    Refused while any league membership still uses it.
    """
    address = _client_address(db, client_id, address_id)
    in_use = sorted(m.league.full_id for m in address.memberships if m.league)
    if in_use:
        raise HTTPException(
            status_code=400,
            detail=f"{address.address} is still used in {', '.join(in_use)}; "
            "move those memberships to another address first",
        )
    text = address.address
    db.delete(address)
    db.commit()

    logger.info(f"Removed {text} from client {client_id} by {current_user.username}")
    return {"message": f"{text} removed"}
