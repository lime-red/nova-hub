"""Management API WebSocket endpoints for dashboard updates"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.logging_config import get_logger
from backend.services.websocket_service import (
    connect_dashboard,
    disconnect_dashboard,
)

logger = get_logger(context="management_websocket")

router = APIRouter()


@router.websocket("/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    """
    WebSocket endpoint for real-time dashboard updates

    **Endpoint:** `ws://hub.example.com/management/api/v1/ws/dashboard`

    **Events Received:**
    - `stats_update`: Dashboard statistics updated
    - `activity`: New activity event
    - `alert`: New or resolved alert
    - `pong`: Keepalive response

    **Events Sent:**
    - Send any text for keepalive ping

    **Example (JavaScript):**
    ```javascript
    const ws = new WebSocket('ws://hub.example.com/management/api/v1/ws/dashboard');
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'stats_update') {
            updateDashboardStats(data.stats);
        } else if (data.type === 'activity') {
            addActivityItem(data.item);
        }
    };
    ```
    """
    await connect_dashboard(websocket)

    try:
        # Keep connection alive
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        await disconnect_dashboard(websocket)
    except Exception as e:
        logger.error(f"Dashboard WebSocket error: {e}")
        await disconnect_dashboard(websocket)
