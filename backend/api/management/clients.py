"""Management API client management endpoints"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user, get_password_hash, require_admin
from backend.logging_config import get_logger
from backend.models.database import Client, League, LeagueMembership, Packet, SysopUser
from backend.schemas.clients import (
    ClientCreate,
    ClientCreatedResponse,
    ClientDetailResponse,
    ClientResponse,
    ClientSecretResponse,
    ClientStats,
    ClientUpdate,
    LeagueMembershipInfo,
    PacketHistoryItem,
)
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
            # - "sent" = hub sent this packet to the client (client was dest)
            direction = "received" if packet.source_bbs_index == bbs_hex else "sent"

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
                )
            )

    return ClientDetailResponse(
        id=client.id,
        bbs_name=client.bbs_name,
        client_id=client.client_id,
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
    db.delete(client)
    db.commit()

    logger.info(f"Deleted client {client_name} by {current_user.username}")

    return {"message": f"Client {client_name} deleted successfully"}


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
