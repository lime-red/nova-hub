# routers/websocket.py

from app.services.stats_service import StatsService
from app.services.websocket_service import connect, disconnect
from app.logging_config import get_logger
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import get_db

logger = get_logger(context="websocket")

router = APIRouter()


@router.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket, db: Session = Depends(get_db)):
    """
    WebSocket endpoint for dashboard real-time updates

    **Endpoint:** `ws://hub.example.com/ws/dashboard` (or wss:// for secure)

    **Events Received:**
    - `initial_stats`: Dashboard statistics sent on connection
    - `stats_update`: Updated statistics when changes occur
    - `packet_received`: New packet uploaded notification
    - `processing_started`: Batch processing started
    - `processing_complete`: Processing finished
    - `alert_created`: New sequence gap detected
    - `pong`: Keepalive response

    **Events Sent:**
    - Send any text for keepalive ping

    **Example (JavaScript):**
    ```javascript
    const ws = new WebSocket('ws://hub.example.com/ws/dashboard');
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log('Received:', data.type);
    };
    ```
    """
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
        logger.error(f"Error: {e}")
        await disconnect(websocket)


@router.websocket("/ws/client/{bbs_index}")
async def websocket_client(websocket: WebSocket, bbs_index: str):
    """
    WebSocket endpoint for client-specific packet notifications

    **Endpoint:** `ws://hub.example.com/ws/client/{bbs_index}`

    **Path Parameters:**
    - `bbs_index`: Your BBS index in hex (e.g., "02")

    **Events Received:**
    - `packet_available`: Notification when a packet for your BBS is ready
    - `nodelist_available`: Notification when nodelist is updated
    - `pong`: Keepalive response

    **Events Sent:**
    - Send any text for keepalive ping

    **Example (JavaScript):**
    ```javascript
    const ws = new WebSocket('ws://hub.example.com/ws/client/02');
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'packet_available') {
            console.log('New packet:', data.filename);
        }
    };
    ```
    """
    await connect(websocket, bbs_index)

    try:
        # Keep connection alive
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        await disconnect(websocket, bbs_index)
    except Exception as e:
        logger.error(f"Error: {e}")
        await disconnect(websocket, bbs_index)
