"""Service API routers for nova_client

This API is used by nova_client to:
- Authenticate with OAuth2 client credentials
- Upload and download game packets
- Download nodelists
- Receive real-time notifications via WebSocket
"""

from fastapi import APIRouter

from .auth import router as auth_router
from .packets import router as packets_router
from .leagues import router as leagues_router
from .websocket import router as websocket_router

service_router = APIRouter()

service_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["Service API - Authentication"]
)

service_router.include_router(
    packets_router,
    prefix="/leagues/{league_id}/packets",
    tags=["Service API - Packets"]
)

service_router.include_router(
    leagues_router,
    prefix="/leagues",
    tags=["Service API - Leagues"]
)

service_router.include_router(
    websocket_router,
    prefix="/ws",
    tags=["Service API - WebSocket"]
)
