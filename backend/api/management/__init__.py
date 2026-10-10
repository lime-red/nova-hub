"""Management API routers for sysop administration

This API is used by the Vue.js frontend to:
- Authenticate sysop users (session-based JWT)
- View dashboard statistics and activity
- Manage clients, leagues, and memberships
- View processing runs and logs
- Manage alerts and users
"""

from fastapi import APIRouter

from .address_book import router as address_book_router
from .audit import router as audit_router
from .attacks import router as attacks_router
from .traffic import router as traffic_router
from .auth import router as auth_router
from .claim import router as claim_router
from .dashboard import router as dashboard_router
from .clients import router as clients_router
from .leagues import router as leagues_router
from .movements import router as movements_router
from .processing import router as processing_router
from .alerts import router as alerts_router
from .users import router as users_router
from .system import router as system_router

management_router = APIRouter()

management_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["Management API - Authentication"]
)

# Public: a claim link is its own credential. See claim.py.
management_router.include_router(
    claim_router,
    prefix="/claim",
    tags=["Management API - Claim Links"]
)

management_router.include_router(
    audit_router,
    prefix="/audit",
    tags=["Management API - Audit Log"]
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
    address_book_router,
    prefix="/address-book",
    tags=["Management API - Address Book"]
)

management_router.include_router(
    processing_router,
    prefix="/processing",
    tags=["Management API - Processing"]
)

management_router.include_router(
    movements_router,
    prefix="/movements",
    tags=["Management API - Movements"]
)

management_router.include_router(
    attacks_router,
    prefix="/attacks",
    tags=["Management API - Attacks"]
)

management_router.include_router(
    traffic_router,
    prefix="/traffic",
    tags=["Management API - Traffic"]
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
    system_router,
    prefix="/system",
    tags=["Management API - System"]
)
