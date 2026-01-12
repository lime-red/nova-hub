# mock_hub.py - Simple mock hub server for testing

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Request
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

app = FastAPI(title="Nova Hub Mock Server")

# Simple in-memory storage
clients_db = {
    "test_client": {"secret": "test_secret", "bbs_index": "02", "bbs_name": "Test BBS"}
}

tokens_db = {}
packets_db = []

# Storage directories
STORAGE_DIR = Path("./mock_hub_data")
INBOUND_DIR = STORAGE_DIR / "inbound"
OUTBOUND_DIR = STORAGE_DIR / "outbound"

INBOUND_DIR.mkdir(parents=True, exist_ok=True)
OUTBOUND_DIR.mkdir(parents=True, exist_ok=True)

PACKET_REGEX = re.compile(
    r"^(\d{3})([BF])([0-9A-F]{2})([0-9A-F]{2})\.(\d{3})$", re.IGNORECASE
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


# Models
class TokenRequest(BaseModel):
    grant_type: str
    client_id: str
    client_secret: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class PacketInfo(BaseModel):
    filename: str
    league: str
    game_type: str
    source: str
    dest: str
    sequence: int
    received_at: str
    retrieved_at: Optional[str] = None
    file_size: int


# Auth
@app.post("/auth/token", response_model=TokenResponse)
async def get_token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
):
    """OAuth2 client credentials flow"""

    if grant_type != "client_credentials":
        raise HTTPException(400, "Unsupported grant_type")

    if client_id not in clients_db:
        raise HTTPException(401, "Invalid client_id")

    if clients_db[client_id]["secret"] != client_secret:
        raise HTTPException(401, "Invalid client_secret")

    # Generate token
    token = secrets.token_urlsafe(32)
    tokens_db[token] = {
        "client_id": client_id,
        "expires": datetime.now() + timedelta(hours=1),
    }

    print(f"[Auth] Token issued for {client_id}")

    return TokenResponse(access_token=token, token_type="bearer", expires_in=3600)


def verify_token(token: str = Depends(oauth2_scheme)) -> dict:
    """Verify OAuth token"""
    if token not in tokens_db:
        raise HTTPException(401, "Invalid token")

    token_data = tokens_db[token]
    if datetime.now() > token_data["expires"]:
        raise HTTPException(401, "Token expired")

    return token_data


# Packet endpoints
@app.put("/api/v1/leagues/{league_id}/packets/{filename}")
async def upload_packet(
    league_id: str = PathParam(..., regex=r'^\d{3}[BF]$'),
    filename: str = PathParam(...),
    request: Request = None,
    token_data: dict = Depends(verify_token),
):
    """Upload a packet using PUT with raw body"""

    # Parse league_id (e.g., "555B" -> "555", "B")
    match = re.match(r'^(\d{3})([BF])$', league_id.upper())
    if not match:
        raise HTTPException(400, f"Invalid league_id format: {league_id}")

    league_number, league_game_type = match.groups()

    # Normalize filename
    normalized_filename = filename.upper()

    # Parse filename
    packet_match = PACKET_REGEX.match(normalized_filename)
    if not packet_match:
        raise HTTPException(400, f"Invalid packet filename: {normalized_filename}")

    file_league, file_game, source, dest, seq = packet_match.groups()

    # Verify league_id matches filename (BOTH number AND game type)
    if file_league != league_number:
        raise HTTPException(
            400, f"League number mismatch: filename={file_league}, URL={league_number}"
        )

    if file_game.upper() != league_game_type:
        raise HTTPException(
            400, f"Game type mismatch: filename={file_game}, URL={league_game_type}"
        )

    # Verify client is authorized for this source
    client_id = token_data["client_id"]
    client_info = clients_db[client_id]

    if source.upper() != client_info["bbs_index"]:
        raise HTTPException(
            403, f"Client {client_id} cannot upload packets from BBS {source}"
        )

    # Read raw body
    content = await request.body()
    if not content:
        raise HTTPException(400, "Empty request body")

    # Save file
    filepath = INBOUND_DIR / normalized_filename
    filepath.write_bytes(content)

    # Record in database
    packet_info = {
        "filename": normalized_filename,
        "league": league_number,
        "game_type": "BRE" if file_game.upper() == "B" else "FE",
        "source": source.upper(),
        "dest": dest.upper(),
        "sequence": int(seq),
        "received_at": datetime.now().isoformat(),
        "retrieved_at": None,
        "file_size": len(content),
    }
    packets_db.append(packet_info)

    print(
        f"[Upload] {normalized_filename} from {client_info['bbs_name']} ({source} -> {dest})"
    )

    return {"status": "received", "filename": normalized_filename}


@app.get("/api/v1/leagues/{league_id}/packets")
async def list_packets(
    league_id: str, unread: bool = False, token_data: dict = Depends(verify_token)
):
    """List packets for this client"""

    client_id = token_data["client_id"]
    client_info = clients_db[client_id]
    dest_index = client_info["bbs_index"]

    # Filter packets for this client and league
    filtered = [
        p
        for p in packets_db
        if p["league"] == league_id
        and p["dest"] == dest_index
        and (not unread or p["retrieved_at"] is None)
    ]

    print(f"[List] {client_info['bbs_name']}: {len(filtered)} packet(s) available")

    return {"packets": filtered}


@app.get("/api/v1/leagues/{league_id}/packets/{filename}")
async def download_packet(
    league_id: str, filename: str, token_data: dict = Depends(verify_token)
):
    """Download a specific packet"""

    # Parse filename
    match = PACKET_REGEX.match(filename)
    if not match:
        raise HTTPException(400, "Invalid packet filename")

    league, game, source, dest, seq = match.groups()

    # Verify client owns this packet
    client_id = token_data["client_id"]
    client_info = clients_db[client_id]

    if dest.upper() != client_info["bbs_index"]:
        raise HTTPException(403, "Cannot download packets for other BBSs")

    # Check if file exists
    filepath = INBOUND_DIR / filename
    if not filepath.exists():
        raise HTTPException(404, "Packet not found")

    # Mark as retrieved
    for packet in packets_db:
        if packet["filename"] == filename:
            packet["retrieved_at"] = datetime.now().isoformat()
            break

    print(f"[Download] {filename} to {client_info['bbs_name']}")

    return FileResponse(filepath, filename=filename)


# Admin/testing endpoints
@app.post("/test/create_packet")
async def create_test_packet(
    league: str,
    game: str,  # "BRE" or "FE"
    source: str,  # hex like "01"
    dest: str,  # hex like "02"
    sequence: int,
):
    """Create a test packet (for testing download functionality)"""

    game_code = "B" if game == "BRE" else "F"
    filename = f"{league}{game_code}{source.upper()}{dest.upper()}.{sequence:03d}"

    # Create dummy packet content
    content = f"Test packet from {source} to {dest}\nSequence: {sequence}\n".encode()

    filepath = INBOUND_DIR / filename
    filepath.write_bytes(content)

    # Add to database
    packet_info = {
        "filename": filename,
        "league": league,
        "game_type": game,
        "source": source.upper(),
        "dest": dest.upper(),
        "sequence": sequence,
        "received_at": datetime.now().isoformat(),
        "retrieved_at": None,
        "file_size": len(content),
    }
    packets_db.append(packet_info)

    print(f"[Test] Created packet: {filename}")

    return {"status": "created", "filename": filename}


@app.get("/test/packets")
async def list_all_packets():
    """List all packets (for debugging)"""
    return {"packets": packets_db}


@app.get("/test/clients")
async def list_clients():
    """List all clients (for debugging)"""
    return {"clients": clients_db}


@app.get("/")
async def root():
    return {
        "service": "Nova Hub Mock Server",
        "version": "0.1.0",
        "endpoints": {
            "auth": "/auth/token",
            "packets": "/api/v1/leagues/{league_id}/packets",
        },
    }


if __name__ == "__main__":
    print("Starting Nova Hub Mock Server...")
    print("Client credentials: test_client / test_secret")
    print("BBS Index: 02")
    uvicorn.run(app, host="127.0.0.1", port=8000)
