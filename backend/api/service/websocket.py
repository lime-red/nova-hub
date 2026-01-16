"""Service API WebSocket endpoints"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.logging_config import get_logger
from backend.services.websocket_service import connect, disconnect

logger = get_logger(context="service_websocket")

router = APIRouter()


@router.websocket("/client/{bbs_index}")
async def websocket_client(websocket: WebSocket, bbs_index: str):
    """
    WebSocket endpoint for client-specific packet notifications

    **Endpoint:** `ws://hub.example.com/service/api/v1/ws/client/{bbs_index}`

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
    const ws = new WebSocket('ws://hub.example.com/service/api/v1/ws/client/02');
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
