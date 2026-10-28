"""Service API packet operations"""

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.core.config import get_config
from backend.core.database import get_db
from backend.core.security import get_current_client
from backend.logging_config import get_logger
from backend.models.database import Client, League, LeagueMembership, Packet
from backend.schemas.packets import BINARY, PacketInfo, PacketListResponse, PacketUploadResponse
from backend.services.games import LEAGUE_ID_REGEX, game_for_letter, nodelist_game
from backend.services.league_utils import board_hex, parse_league_id
from backend.services.packet_service import parse_packet_filename
from backend.services.processing_service import find_file_case_insensitive
from backend.services.providers import provider_for
from backend.services.providers.base import index_hex

logger = get_logger(context="service_packets")

router = APIRouter()

# How long an identical re-upload counts as a retry of the last one. A route's
# numbering takes months to come back round to the same name; the same name and
# the same bytes within a day is one packet sent twice.
RETRY_WINDOW = timedelta(days=1)


async def _read_body(request: Request, max_size: int) -> bytes:
    """The body, refused as soon as it passes max_size: before reading at all
    when Content-Length says so, and while reading when it lies or is absent
    (chunked), so no upload is ever held in memory past the limit."""
    too_big = HTTPException(status_code=413,
                            detail=f"Packet exceeds maximum allowed size of {max_size} bytes")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared > max_size:
            raise too_big
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > max_size:
            raise too_big
    return bytes(body)


@router.put("/{filename}", response_model=PacketUploadResponse, summary="Upload Packet",
            openapi_extra={"requestBody": {"required": True, "content": BINARY}})
async def upload_packet(
    league_id: str = PathParam(..., pattern=LEAGUE_ID_REGEX),
    filename: str = PathParam(...),
    request: Request = None,
    client: Client = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """
    Upload a game packet to the hub using PUT with raw body

    **Path Parameters:**
    - `league_id`: League identifier with game type (e.g., "555B" for BRE, "555F" for FE)
    - `filename`: Packet filename (e.g., "555B0201.001")

    **Request Body:** Raw file data (application/octet-stream)

    **Packet Filename Format, BRE and FE:** `<league><game><source><dest>.<seq>`
    - League: 3 digits (e.g., 555)
    - Game: B (BRE) or F (FE)
    - Source: 2-digit hex BBS index (e.g., 02)
    - Dest: 2-digit hex BBS index (e.g., 01)
    - Seq: 3-digit sequence (000-999)

    **Packet Filename Format, Immortal Barons:** `L<league>-<source>-<seq>-<dest>.brp`
    - League: 3 digits (e.g., 555), in a league with game I (555I)
    - Source, Dest: BBS index in base 36 (e.g., 2, 1z); dest 0 is every board
    - Seq: 13-digit zero-padded base-36 sequence

    **Example:** `555B0201.001` = BRE League 555, from BBS 02 to BBS 01, seq 1

    **Authentication:** Requires Bearer token
    """

    # Parse league_id from URL (e.g., "555B" -> ("555", "B"))
    league_number, league_game_type = parse_league_id(league_id)

    # Normalize filename to uppercase (server-side normalization)
    normalized_filename = filename.upper()

    # Block nodelist uploads from clients
    if nodelist_game(normalized_filename):
        raise HTTPException(
            status_code=403,
            detail="Nodelist files cannot be uploaded by clients - they are hub-generated only"
        )

    # Parse and validate filename
    packet_info = parse_packet_filename(normalized_filename)
    if not packet_info:
        raise HTTPException(status_code=400, detail="Invalid filename format")

    # Verify league_id matches filename (BOTH number AND game type)
    if packet_info["league_id"] != league_number:
        raise HTTPException(
            status_code=400,
            detail=f"League number mismatch: filename={packet_info['league_id']}, URL={league_number}",
        )

    if packet_info["game_type"] != league_game_type:
        raise HTTPException(
            status_code=400,
            detail=f"Game type mismatch: filename={packet_info['game_type']}, URL={league_game_type}",
        )

    content = await _read_body(request, get_config().security.max_upload_size_bytes)
    if not content:
        raise HTTPException(status_code=400, detail="Empty request body")

    # Query league by BOTH league_id AND game_type
    league = (
        db.query(League)
        .filter(
            League.league_id == league_number,
            League.game_type == league_game_type,
        )
        .first()
    )

    # Leagues are made in the console. Making one here, for any number a token
    # holder names, only to refuse them as no member of it, left the row behind.
    if not league:
        raise HTTPException(status_code=404, detail=f"League {league_id} not found")
    if not league.is_active:
        # Never processed while disabled, so it would only sit in inbound.
        raise HTTPException(status_code=403, detail=f"League {league_id} is disabled")

    # Check client membership in this league
    membership = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.client_id == client.id,
            LeagueMembership.league_id == league.id,
            LeagueMembership.is_active == True,
        )
        .first()
    )

    if not membership:
        raise HTTPException(
            status_code=403,
            detail=f"Client {client.client_id} is not a member of league {league_id}",
        )

    membership_bbs_hex = board_hex(membership)

    # Verify client is authorized for this source BBS
    if packet_info["source_bbs_index"].upper() != membership_bbs_hex:
        raise HTTPException(
            status_code=403,
            detail=f"Client {client.client_id} BBS ID {membership.bbs_index} (0x{membership_bbs_hex}) cannot upload packets from BBS {packet_info['source_bbs_index']}",
        )

    # Calculate file hash
    file_hash = hashlib.sha256(content).hexdigest()

    # A retry: the connection dropped after the hub had the packet but before
    # the client heard. Writing it again would add a second row (and second
    # sightings) and could rewrite the inbound file mid-run.
    previous = (
        db.query(Packet)
        .filter(Packet.league_id == league.id, Packet.filename == normalized_filename)
        .order_by(Packet.id.desc())
        .first()
    )
    if (previous is not None and previous.checksum == file_hash
            and previous.uploaded_at and previous.uploaded_at >= utcnow() - RETRY_WINDOW):
        logger.info(f"{normalized_filename} from {client.client_id} is a retry of packet {previous.id}")
        return PacketUploadResponse(status="received", filename=normalized_filename,
                                    packet_id=previous.id)

    provider = provider_for(league.game_type)
    if previous is not None and not provider.reuses_names:
        # A name that is one packet for ever (Immortal Barons): the same bytes
        # again is that packet, already here; different bytes are a collision,
        # and neither copy replaces the other.
        if previous.checksum == file_hash:
            return PacketUploadResponse(status="received", filename=normalized_filename,
                                        packet_id=previous.id)
        logger.warning(f"{normalized_filename} from {client.client_id} collides with packet "
                       f"{previous.id}: same name, different bytes")
        raise HTTPException(
            status_code=409,
            detail=f"The hub already has a different packet named {normalized_filename}",
        )

    # Save file to inbound directory
    data_dir = get_config().get("server", {}).get("data_dir", "./data")
    inbound_dir = Path(data_dir) / "packets" / "inbound"
    inbound_dir.mkdir(parents=True, exist_ok=True)

    filepath = inbound_dir / normalized_filename
    filepath.write_bytes(content)

    # One row per board it goes to (file data stored on disk, not in DB): one
    # for an SR packet, one per board for an Immortal Barons broadcast.
    hub = index_hex(get_config().get("hub.bbs_index") or "01")
    rows = [
        Packet(
            filename=normalized_filename,
            league_id=league.id,
            source_bbs_index=packet_info["source_bbs_index"],
            dest_bbs_index=dest,
            sequence_number=packet_info["sequence_number"],
            file_size=len(content),
            checksum=file_hash,
            source_client_id=client.id,
        )
        for dest in provider.recipients(db, league, packet_info, hub)
    ]
    # The newest row stands for the upload, as a retry finds it (`previous`).
    packet = rows[-1]
    db.add_all(rows)
    if client.first_upload_at is None:
        # The onboarding Verify step: this node is really exchanging packets
        client.first_upload_at = utcnow()
    db.commit()

    # Best-effort: an attack that cannot be traced must not fail the upload.
    from backend.services.attack_trace import record_safely
    record_safely(db, packet, content)

    # Trigger processing (fire and forget)
    from backend.services.processing_service import trigger_processing
    trigger_processing()

    return PacketUploadResponse(
        status="received",
        filename=normalized_filename,
        packet_id=packet.id
    )


@router.get("", response_model=PacketListResponse, summary="List Available Packets")
async def list_packets(
    league_id: str = PathParam(..., pattern=LEAGUE_ID_REGEX),
    unread: bool = Query(False, description="Filter to only unread (not downloaded) packets"),
    client: Client = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """
    List packets available for download by this client

    **Path Parameters:**
    - `league_id`: League identifier with game type (e.g., "555B" for BRE, "555F" for FE)

    **Query Parameters:**
    - `unread`: Set to `true` to only show packets not yet downloaded (default: false)

    **Returns:** List of packets destined for this client's BBS

    **Authentication:** Requires Bearer token
    """

    # Parse league_id from URL
    league_number, league_game_type = parse_league_id(league_id)

    # Get league
    league = (
        db.query(League)
        .filter(
            League.league_id == league_number,
            League.game_type == league_game_type
        )
        .first()
    )

    if not league:
        return PacketListResponse(packets=[])

    # Get client memberships
    memberships = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.client_id == client.id,
            LeagueMembership.league_id == league.id,
            LeagueMembership.is_active == True,
        )
        .all()
    )

    if not memberships:
        return PacketListResponse(packets=[])

    # Build list of (league_id, bbs_hex) tuples for filtering
    bbs_filters = [(m.league_id, format(m.bbs_index, "02X"))
                   for m in memberships if m.bbs_index is not None]
    if not bbs_filters:
        return PacketListResponse(packets=[])

    # Query packets
    filters = [
        and_(Packet.league_id == lid, Packet.dest_bbs_index == bbs_hex)
        for lid, bbs_hex in bbs_filters
    ]

    query = db.query(Packet).filter(or_(*filters))

    if unread:
        query = query.filter(Packet.downloaded_at == None)

    packets = query.order_by(Packet.uploaded_at.desc()).all()

    packet_list = []
    for packet in packets:
        packet_league = db.query(League).filter(League.id == packet.league_id).first()
        packet_list.append(
            PacketInfo(
                filename=packet.filename,
                league=packet_league.league_id,
                game_type=packet_league.game_type,
                source=packet.source_bbs_index,
                dest=packet.dest_bbs_index,
                sequence=packet.sequence_number,
                received_at=packet.uploaded_at,
                retrieved_at=packet.downloaded_at,
                file_size=packet.file_size,
            )
        )

    return PacketListResponse(packets=packet_list)


@router.get("/{filename}", summary="Download Packet", response_class=Response,
            responses={200: {"description": "The packet", "content": BINARY}})
async def download_packet(
    league_id: str = PathParam(..., pattern=LEAGUE_ID_REGEX),
    filename: str = PathParam(...),
    request: Request = None,
    client: Client = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """
    Download a specific packet file

    **Path Parameters:**
    - `league_id`: League identifier with game type (e.g., "555B" for BRE, "555F" for FE)
    - `filename`: Packet filename (e.g., "555B0102.001")

    **Returns:** Binary packet file

    **Authentication:** Requires Bearer token and authorization for destination BBS
    """

    # Parse league_id from URL
    league_number, league_game_type = parse_league_id(league_id)

    # Normalize filename to uppercase
    normalized_filename = filename.upper()

    # Check if this is a nodelist file
    is_nodelist = nodelist_game(normalized_filename) is not None

    if is_nodelist:
        return await _download_nodelist(
            league_number, league_game_type, normalized_filename,
            client, db, request
        )
    else:
        return await _download_packet(
            league_number, league_game_type, normalized_filename,
            client, db, request
        )


async def _download_nodelist(
    league_number: str, league_game_type: str, filename: str,
    client: Client, db: Session, request: Request
):
    """Handle nodelist download"""
    # Get league
    league = (
        db.query(League)
        .filter(
            League.league_id == league_number,
            League.game_type == league_game_type
        )
        .first()
    )

    if not league:
        raise HTTPException(status_code=404, detail=f"League not found")

    # Check membership
    membership = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.client_id == client.id,
            LeagueMembership.league_id == league.id,
            LeagueMembership.is_active == True,
        )
        .first()
    )

    if not membership:
        raise HTTPException(
            status_code=403,
            detail=f"Client {client.client_id} is not a member of this league",
        )

    # Find nodelist file
    data_dir = get_config().get("server", {}).get("data_dir", "./data")
    game_key = game_for_letter(league_game_type).key
    nodelists_dir = Path(data_dir) / "nodelists" / game_key / league_number

    nodelist_path = find_file_case_insensitive(nodelists_dir, filename)

    if not nodelist_path or not nodelist_path.exists():
        raise HTTPException(status_code=404, detail="Nodelist file not found")

    # Mark as downloaded
    dest_bbs_hex = board_hex(membership)
    nodelist_packet = (
        db.query(Packet)
        .filter(
            Packet.filename == filename,
            Packet.league_id == league.id,
            Packet.dest_bbs_index == dest_bbs_hex,
        )
        .first()
    )

    if nodelist_packet:
        nodelist_packet.downloaded_at = utcnow()
        nodelist_packet.is_downloaded = True
        db.commit()

    logger.info(f"Client {client.client_id} downloading nodelist: {nodelist_path.name}")

    return FileResponse(
        nodelist_path,
        filename=nodelist_path.name,
        media_type="application/octet-stream",
    )


async def _download_packet(
    league_number: str, league_game_type: str, filename: str,
    client: Client, db: Session, request: Request
):
    """Handle regular packet download"""
    # Parse filename
    packet_info = parse_packet_filename(filename)
    if not packet_info:
        raise HTTPException(status_code=400, detail="Invalid filename format")

    # Verify league matches
    if packet_info["league_id"] != league_number:
        raise HTTPException(status_code=400, detail="League number mismatch")

    if packet_info["game_type"] != league_game_type:
        raise HTTPException(status_code=400, detail="Game type mismatch")

    league = (
        db.query(League)
        .filter(League.league_id == league_number, League.game_type == league_game_type)
        .first()
    )
    if not league:
        raise HTTPException(status_code=404, detail="Packet not found")

    # Check membership
    membership = (
        db.query(LeagueMembership)
        .filter(
            LeagueMembership.client_id == client.id,
            LeagueMembership.league_id == league.id,
            LeagueMembership.is_active == True,
        )
        .first()
    )

    if not membership:
        if not db.query(Packet.id).filter(Packet.league_id == league.id,
                                          Packet.filename == filename).first():
            raise HTTPException(status_code=404, detail="Packet not found")
        raise HTTPException(status_code=403, detail="Client is not a member of this league")

    # This board's row for the packet (prioritize undownloaded, newest first).
    # The row says who a packet is for, not the name: an Immortal Barons
    # broadcast is one file, named for destination 0, with a row per board.
    membership_bbs_hex = board_hex(membership)
    packet = (
        db.query(Packet)
        .filter(Packet.league_id == league.id, Packet.filename == filename,
                Packet.dest_bbs_index == membership_bbs_hex)
        .order_by(Packet.is_downloaded.asc(), Packet.uploaded_at.desc())
        .first()
    )

    if not packet:
        if packet_info["dest_bbs_index"].upper() != membership_bbs_hex and db.query(Packet.id).filter(
                Packet.league_id == league.id, Packet.filename == filename).first():
            raise HTTPException(
                status_code=403,
                detail=f"Cannot download packets for BBS {packet_info['dest_bbs_index']} (client BBS ID is {membership.bbs_index}/0x{membership_bbs_hex})",
            )
        raise HTTPException(status_code=404, detail="Packet not found")

    # Find file
    data_dir = get_config().get("server", {}).get("data_dir", "./data")
    filepath = Path(data_dir) / "packets" / "outbound" / filename

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Packet file not found")

    # Mark as downloaded
    packet.downloaded_at = utcnow()
    packet.is_downloaded = True
    db.commit()

    return FileResponse(
        filepath,
        filename=filename,
        media_type="application/octet-stream"
    )
