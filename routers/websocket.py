# routers/websocket.py

from app.services.stats_service import StatsService
from app.services.websocket_service import connect, disconnect
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket, db: Session = Depends(get_db)):
    """WebSocket endpoint for dashboard real-time updates"""
    await connect(websocket)

    try:
        # Send initial stats
        stats_service = StatsService(db)
        stats = stats_service.get_dashboard_stats()

        await websocket.send_json({"type": "initial_stats", "stats": stats})

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_text()
            # Echo back for keepalive
            await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        await disconnect(websocket)
    except Exception as e:
        print(f"[WebSocket] Error: {e}")
        await disconnect(websocket)


@router.websocket("/ws/client/{bbs_index}")
async def websocket_client(websocket: WebSocket, bbs_index: str):
    """WebSocket endpoint for specific client notifications"""
    await connect(websocket, bbs_index)

    try:
        # Keep connection alive
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        await disconnect(websocket, bbs_index)
    except Exception as e:
        print(f"[WebSocket] Error: {e}")
        await disconnect(websocket, bbs_index)
