"""Management API routers for sysop administration

This API is used by the Vue.js frontend to:
- Authenticate sysop users (session-based JWT)
- View dashboard statistics and activity
- Manage clients, leagues, and memberships
- View processing runs and logs
- Manage alerts and users
- Receive real-time dashboard updates via WebSocket
"""

from fastapi import APIRouter

from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .clients import router as clients_router
from .leagues import router as leagues_router
from .processing import router as processing_router
from .alerts import router as alerts_router
from .users import router as users_router
from .websocket import router as websocket_router

management_router = APIRouter()

management_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["Management API - Authentication"]
)

management_router.include_router(
    dashboard_router,
    prefix="/dashboard",
    tags=["Management API - Dashboard"]
)

management_router.include_router(
    clients_router,
    prefix="/clients",
    tags=["Management API - Clients"]
)

management_router.include_router(
    leagues_router,
    prefix="/leagues",
    tags=["Management API - Leagues"]
)

management_router.include_router(
    processing_router,
    prefix="/processing",
    tags=["Management API - Processing"]
)

management_router.include_router(
    alerts_router,
    prefix="/alerts",
    tags=["Management API - Alerts"]
)

management_router.include_router(
    users_router,
    prefix="/users",
    tags=["Management API - Users"]
)

management_router.include_router(
    websocket_router,
    prefix="/ws",
    tags=["Management API - WebSocket"]
)
