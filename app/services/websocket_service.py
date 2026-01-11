# app/services/websocket_service.py

import asyncio
import json
from typing import Dict, Set

from fastapi import WebSocket

# Connected clients
_connections: Set[WebSocket] = set()
_client_connections: Dict[str, Set[WebSocket]] = {}  # bbs_index -> websockets


async def connect(websocket: WebSocket, client_bbs_index: str = None):
    """Register a new WebSocket connection"""
    await websocket.accept()
    _connections.add(websocket)

    if client_bbs_index:
        if client_bbs_index not in _client_connections:
            _client_connections[client_bbs_index] = set()
        _client_connections[client_bbs_index].add(websocket)

    print(f"[WebSocket] Client connected (total: {len(_connections)})")


async def disconnect(websocket: WebSocket, client_bbs_index: str = None):
    """Remove a WebSocket connection"""
    _connections.discard(websocket)

    if client_bbs_index and client_bbs_index in _client_connections:
        _client_connections[client_bbs_index].discard(websocket)
        if not _client_connections[client_bbs_index]:
            del _client_connections[client_bbs_index]

    print(f"[WebSocket] Client disconnected (total: {len(_connections)})")


async def broadcast(message: dict):
    """Broadcast message to all connected clients"""
    if not _connections:
        return

    message_json = json.dumps(message)

    # Send to all clients, remove disconnected ones
    disconnected = set()
    for ws in _connections:
        try:
            await ws.send_text(message_json)
        except Exception as e:
            print(f"[WebSocket] Error sending to client: {e}")
            disconnected.add(ws)

    # Clean up disconnected clients
    for ws in disconnected:
        await disconnect(ws)


async def send_to_client(bbs_index: str, message: dict):
    """Send message to specific client BBS"""
    if bbs_index not in _client_connections:
        return

    message_json = json.dumps(message)

    disconnected = set()
    for ws in _client_connections[bbs_index]:
        try:
            await ws.send_text(message_json)
        except Exception as e:
            print(f"[WebSocket] Error sending to client {bbs_index}: {e}")
            disconnected.add(ws)

    # Clean up disconnected clients
    for ws in disconnected:
        await disconnect(ws, bbs_index)


# Convenience functions for specific events


async def broadcast_packet_received(filename: str, source: str, dest: str):
    """Broadcast that a packet was received"""
    await broadcast(
        {
            "type": "packet_received",
            "filename": filename,
            "source": source,
            "dest": dest,
            "timestamp": asyncio.get_event_loop().time(),
        }
    )


async def broadcast_packet_available(filename: str, dest: str):
    """Notify specific client that a packet is available"""
    await send_to_client(
        dest,
        {
            "type": "packet_available",
            "filename": filename,
            "timestamp": asyncio.get_event_loop().time(),
        },
    )


async def broadcast_processing_started():
    """Broadcast that processing started"""
    await broadcast(
        {"type": "processing_started", "timestamp": asyncio.get_event_loop().time()}
    )


async def broadcast_processing_complete(run_id: int):
    """Broadcast that processing completed"""
    await broadcast(
        {
            "type": "processing_complete",
            "run_id": run_id,
            "timestamp": asyncio.get_event_loop().time(),
        }
    )


async def broadcast_stats_update(stats: dict):
    """Broadcast updated statistics"""
    await broadcast(
        {
            "type": "stats_update",
            "stats": stats,
            "timestamp": asyncio.get_event_loop().time(),
        }
    )


async def broadcast_alert_created(alert_id: int):
    """Broadcast that a new alert was created"""
    await broadcast(
        {
            "type": "alert_created",
            "alert_id": alert_id,
            "timestamp": asyncio.get_event_loop().time(),
        }
    )


async def broadcast_nodelist_available(league_id: str, game_type: str):
    """Broadcast that a new nodelist is available for download"""
    await broadcast(
        {
            "type": "nodelist_available",
            "league_id": league_id,
            "game_type": game_type,
            "timestamp": asyncio.get_event_loop().time(),
        }
    )
