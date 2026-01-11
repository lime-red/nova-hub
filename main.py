# main.py - Enhanced with OpenAPI documentation

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import toml
from pathlib import Path

from app.database import init_database
from routers import web, packets, auth, websocket

# Load config
config = toml.load("config.toml")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Initialize database
    db_path = config.get("database", {}).get("path", "/home/lime/nova-data/nova-hub.db")
    database_url = f"sqlite:///{db_path}"
    database = init_database(database_url)
    app.state.database = database
    app.state.config = config

    # Note: Run migrations manually with: alembic upgrade head
    # Automatic migrations disabled to prevent startup hangs

    # Start packet watcher for hub-generated outbound packets
    from app.database import get_db
    from app.services.packet_service import PacketService
    from app.services.packet_watcher import WatcherService
    import asyncio

    db_session = next(get_db())
    watcher = None
    try:
        # Get the current event loop
        loop = asyncio.get_running_loop()

        packet_service = PacketService(db_session)
        watcher = WatcherService(packet_service, config, loop)
        watcher.start()
        app.state.watcher = watcher

        # Process any existing files in watched directories
        # (files that were there before the watcher started)
        await watcher.process_pending_scans()

    except Exception as e:
        print(f"[Startup] Failed to start packet watcher: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db_session.close()

    print("Nova Hub started successfully")

    yield

    # Shutdown
    if hasattr(app.state, 'watcher') and app.state.watcher:
        app.state.watcher.stop()

    print("Nova Hub shutting down...")


app = FastAPI(
    title="Nova Hub",
    description="""
# Nova Hub - BBS Inter-League Routing System

Modern routing hub for Solar Realms games: Barren Realms Elite (BRE) and Falcons Eye (FE).

## Features

- **Packet Routing**: Hub-and-spoke routing for BBS door game leagues
- **OAuth Authentication**: Secure client authentication for packet transfer
- **Real-time Updates**: WebSocket support for live dashboard updates
- **Sequence Validation**: Automatic detection of missing packets
- **Web Interface**: Comprehensive sysop dashboard

## Authentication

API endpoints require OAuth 2.0 client credentials flow.

1. Obtain client credentials from hub administrator
2. Request token: `POST /auth/token` with `client_id` and `client_secret`
3. Use token in `Authorization: Bearer <token>` header

## Packet Naming

Packets follow the format: `<league><game><source><dest>.<seq>`

- **league**: 3-digit league number (e.g., 555)
- **game**: B (BRE) or F (FE)
- **source**: 2-digit hex BBS index (e.g., 02)
- **dest**: 2-digit hex BBS index (e.g., 01)
- **seq**: 3-digit sequence number (000-999, wraps)

Example: `555B0201.001` = BRE League 555, from BBS 02 to BBS 01, sequence 1

## WebSocket Events

Connect to `/ws/dashboard` for real-time updates:

- `packet_received` - New packet uploaded
- `processing_started` - Batch processing started
- `processing_complete` - Processing finished
- `stats_update` - Dashboard statistics updated
- `alert_created` - New sequence gap detected
    """,
    version="0.1.0",
    contact={
        "name": "Nova Hub",
        "url": "https://github.com/yourusername/nova-hub"
    },
    license_info={
        "name": "MIT"
    },
    lifespan=lifespan,
    docs_url=None,  # Disable default docs
    redoc_url=None  # Disable default redoc
)

# Mount static files
Path("static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"]
)
app.include_router(
    packets.router,
    prefix="/api/v1",
    tags=["Packets"]
)
app.include_router(
    websocket.router,
    tags=["WebSocket"]
)
app.include_router(
    web.router,
    tags=["Web Interface"]
)


# Custom documentation endpoints
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - API Documentation",
        swagger_favicon_url="/static/favicon.ico"
    )


@app.get("/redoc", include_in_schema=False)
async def redoc_html():
    return get_redoc_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - API Documentation",
        redoc_favicon_url="/static/favicon.ico"
    )


# Health check endpoint
@app.get("/health", tags=["System"])
async def health_check():
    """
    Health check endpoint for monitoring
    
    Returns system status and statistics
    """
    from app.database import get_db
    from sqlalchemy import text
    
    db = next(get_db())
    try:
        # Test database connection
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    finally:
        db.close()
    
    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "database": db_status,
        "version": app.version
    }