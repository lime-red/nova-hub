# routers/web.py - Complete implementation

from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import toml
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt

from sqlalchemy.orm import Session

from app.database import (
    Client,
    League,
    LeagueMembership,
    Packet,
    ProcessingRun,
    ProcessingRunFile,
    SequenceAlert,
    SysopUser,
    get_db,
)
from app.services.stats_service import StatsService
from app.services.log_prettify import ansi_to_html, strip_multiple_blank_lines

from app.logging_config import get_logger

logger = get_logger(context="web_router")

# Load configuration
config = toml.load("config.toml")

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# JWT settings from config
SECRET_KEY = config.get("security", {}).get("jwt_secret", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = config.get("security", {}).get("jwt_expiry_hours", 24)


def create_access_token(data: dict) -> str:
    """Create JWT token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password using bcrypt directly"""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def get_password_hash(password: str) -> str:
    """Hash password using bcrypt directly"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


# def ansi_to_html(text: str) -> str:
#     """Convert ANSI escape codes to HTML for display"""
#     if not text:
#         return ""

#     try:
#         from ansi2html import Ansi2HTMLConverter
#         conv = Ansi2HTMLConverter(inline=True, scheme='solarized')
#         html = conv.convert(text, full=False)

#         # Truncate if too long for display
#         max_length = 50000
#         if len(html) > max_length:
#             html = html[:max_length] + "\n... (truncated)"

#         return html
#     except Exception as e:
#         # If conversion fails, return plain text with error message
#         return f"<pre>ANSI conversion error: {e}\n\n{text[:5000]}</pre>"


async def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Get current user from JWT cookie"""
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=302, headers={"Location": "/login"})

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=302, headers={"Location": "/login"})
    except JWTError:
        raise HTTPException(status_code=302, headers={"Location": "/login"})

    user = db.query(SysopUser).filter(SysopUser.username == username).first()
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})

    return user


async def require_admin(user: SysopUser = Depends(get_current_user)):
    """Require admin role"""
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_flash_messages(with_categories: bool = False):
    """Get flash messages from session"""
    # Simple implementation - in production use proper session management
    # Returns empty list for now - TODO: implement proper session-based flash messages
    if with_categories:
        return []  # Would return [(category, message), ...] with proper implementation
    return []


# ============================================================================
# Authentication Routes
# ============================================================================


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Process login"""
    user = db.query(SysopUser).filter(SysopUser.username == username).first()

    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid username or password"}
        )

    token = create_access_token({"sub": username})
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout():
    """Logout"""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@router.get("/change-password", response_class=HTMLResponse)
async def change_password_form(
    request: Request,
    current_user: SysopUser = Depends(get_current_user),
):
    """Change password form"""
    return templates.TemplateResponse(
        "change_password.html",
        {
            "request": request,
            "current_user": current_user,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.post("/change-password")
async def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change password"""
    # Verify current password
    if not verify_password(current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Verify new passwords match
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match")

    # Update password
    current_user.hashed_password = get_password_hash(new_password)
    db.commit()

    # Redirect back to dashboard
    return RedirectResponse(url="/dashboard?password_changed=1", status_code=302)


# ============================================================================
# Dashboard
# ============================================================================


@router.get("/", response_class=HTMLResponse)
async def root():
    """Redirect to dashboard"""
    return RedirectResponse(url="/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard page"""
    stats_service = StatsService(db)

    stats = stats_service.get_dashboard_stats()
    recent_activity = stats_service.get_recent_activity(limit=10)

    # Chart data
    activity_labels, activity_data = stats_service.get_activity_chart_data(hours=24)
    league_labels, league_data = stats_service.get_league_distribution()

    # Active alerts
    alerts = (
        db.query(SequenceAlert)
        .filter(SequenceAlert.resolved_at == None)
        .order_by(SequenceAlert.detected_at.desc())
        .limit(5)
        .all()
    )

    alert_list = []
    for alert in alerts:
        league = db.query(League).filter(League.id == alert.league_id).first()
        alert_list.append(
            {
                "id": alert.id,
                "league_name": f"{league.game_type} {league.league_id}",
                "source": alert.source_bbs_index,
                "dest": alert.dest_bbs_index,
                "expected_sequence": alert.expected_sequence,
                "detected_at": stats_service.format_timestamp(alert.detected_at),
            }
        )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "stats": stats,
            "recent_activity": recent_activity,
            "alerts": alert_list,
            "activity_chart_labels": activity_labels,
            "activity_chart_data": activity_data,
            "league_chart_labels": league_labels,
            "league_chart_data": league_data,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


# ============================================================================
# Clients
# ============================================================================


@router.get("/clients", response_class=HTMLResponse)
async def clients_list(
    request: Request,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Clients list page"""
    stats_service = StatsService(db)

    clients = db.query(Client).all()

    client_list = []
    for client in clients:
        client_stats = stats_service.get_client_stats(client.id, days=1)
        client_list.append(
            {
                "id": client.id,
                "bbs_name": client.bbs_name,
                "client_id": client.client_id,
                "is_active": client.is_active,
                "last_seen": client_stats["last_seen"],
                "packets_sent_24h": client_stats["sent_24h"],
                "packets_received_24h": client_stats["received_24h"],
            }
        )

    return templates.TemplateResponse(
        "clients/list.html",
        {
            "request": request,
            "current_user": current_user,
            "clients": client_list,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(
    request: Request,
    client_id: int,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Client detail page"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    stats_service = StatsService(db)
    client_stats = stats_service.get_client_stats(client_id)

    # Get client memberships to determine BBS indexes per league
    memberships = db.query(LeagueMembership).filter(
        LeagueMembership.client_id == client_id,
        LeagueMembership.is_active == True
    ).all()

    # Build a map of league_id -> bbs_hex
    league_bbs_map = {m.league_id: format(m.bbs_index, "02X") for m in memberships}

    # Get recent packets for any league where client is a member
    from sqlalchemy import or_, and_

    if league_bbs_map:
        filters = []
        for league_id, bbs_hex in league_bbs_map.items():
            filters.append(
                and_(
                    Packet.league_id == league_id,
                    or_(
                        Packet.source_bbs_index == bbs_hex,
                        Packet.dest_bbs_index == bbs_hex
                    )
                )
            )

        packets = (
            db.query(Packet)
            .filter(or_(*filters))
            .order_by(Packet.uploaded_at.desc())
            .limit(50)
            .all()
        )
    else:
        packets = []

    packet_list = []
    for packet in packets:
        league = db.query(League).filter(League.id == packet.league_id).first()
        bbs_hex = league_bbs_map.get(packet.league_id, "")
        direction = (
            "sent" if packet.source_bbs_index == bbs_hex else "received"
        )

        packet_list.append(
            {
                "filename": packet.filename,
                "direction": direction,
                "league_name": f"{league.game_type} {league.league_id}",
                "source": packet.source_bbs_index,
                "dest": packet.dest_bbs_index,
                "timestamp": stats_service.format_timestamp(packet.uploaded_at),
                "processed_at": packet.processed_at,
                "retrieved_at": packet.downloaded_at,
            }
        )

    return templates.TemplateResponse(
        "clients/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "client": {
                "id": client.id,
                "bbs_name": client.bbs_name,
                "client_id": client.client_id,
                "is_active": client.is_active,
                "created_at": client.created_at.strftime("%Y-%m-%d %H:%M"),
                "last_seen": client_stats["last_seen"],
                "total_sent": client_stats["total_sent"],
                "sent_24h": client_stats["sent_24h"],
                "total_received": client_stats["total_received"],
                "received_24h": client_stats["received_24h"],
            },
            "packets": packet_list,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


# ============================================================================
# Processing Runs
# ============================================================================


@router.get("/processing", response_class=HTMLResponse)
async def processing_runs(
    request: Request,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Processing runs list page"""
    runs = (
        db.query(ProcessingRun)
        .order_by(ProcessingRun.started_at.desc())
        .limit(50)
        .all()
    )

    run_list = []
    for run in runs:
        duration = "Running..."
        if run.completed_at:
            delta = run.completed_at - run.started_at
            duration = f"{delta.seconds}s"

        run_list.append(
            {
                "id": run.id,
                "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": duration,
                "packets_processed": run.packets_processed,
                "status": run.status,
            }
        )

    return templates.TemplateResponse(
        "processing/runs.html",
        {
            "request": request,
            "current_user": current_user,
            "runs": run_list,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/processing/{run_id}", response_class=HTMLResponse)
async def processing_run_detail(
    request: Request,
    run_id: int,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Processing run detail page"""
    run = db.query(ProcessingRun).filter(ProcessingRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Get packets processed in this run
    packets = db.query(Packet).filter(Packet.processing_run_id == run_id).all()

    packet_list = []
    for packet in packets:
        league = db.query(League).filter(League.id == packet.league_id).first()
        packet_list.append(
            {
                "filename": packet.filename,
                "league_name": f"{league.game_type} {league.league_id}",
                "route": f"{packet.source_bbs_index} → {packet.dest_bbs_index}",
            }
        )

    # Get files generated in this run
    files = db.query(ProcessingRunFile).filter(ProcessingRunFile.processing_run_id == run_id).all()

    # Group files by type
    score_files = [f for f in files if f.file_type == "score"]
    routes_files = [f for f in files if f.file_type == "routes"]
    bbsinfo_files = [f for f in files if f.file_type == "bbsinfo"]

    # Convert ANSI to HTML for score files
    for file in score_files:
        if file.file_data:
            file.file_data_html = await ansi_to_html(file.file_data)
        else:
            file.file_data_html = None

    duration = "Running..."
    if run.completed_at:
        delta = run.completed_at - run.started_at
        duration = f"{delta.seconds}s"

    run.dosemu_log_html = await ansi_to_html(run.dosemu_log)
    run.dosemu_log_html = await strip_multiple_blank_lines(run.dosemu_log_html)

    return templates.TemplateResponse(
        "processing/run_detail.html",
        {
            "request": request,
            "current_user": current_user,
            "run": {
                "id": run.id,
                "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                "completed_at": run.completed_at.strftime("%Y-%m-%d %H:%M:%S")
                if run.completed_at
                else None,
                "duration": duration,
                "packets_processed": run.packets_processed,
                "status": run.status,
                "error_message": run.stderr_log,
                "dosemu_output": run.dosemu_log,
                "dosemu_output_html": run.dosemu_log_html,
            },
            "packets": packet_list,
            "score_files": score_files,
            "routes_files": routes_files,
            "bbsinfo_files": bbsinfo_files,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


# ============================================================================
# Alerts
# ============================================================================


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alerts page"""
    alerts = (
        db.query(SequenceAlert)
        .order_by(
            SequenceAlert.resolved_at.asc().nullsfirst(),
            SequenceAlert.detected_at.desc(),
        )
        .all()
    )

    alert_list = []
    for alert in alerts:
        league = db.query(League).filter(League.id == alert.league_id).first()
        alert_list.append(
            {
                "id": alert.id,
                "league_name": f"{league.game_type} {league.league_id}",
                "source": alert.source_bbs_index,
                "dest": alert.dest_bbs_index,
                "expected_sequence": alert.expected_sequence,
                "detected_at": alert.detected_at.strftime("%Y-%m-%d %H:%M:%S"),
                "resolved_at": alert.resolved_at.strftime("%Y-%m-%d %H:%M:%S")
                if alert.resolved_at
                else None,
            }
        )

    return templates.TemplateResponse(
        "alerts.html",
        {
            "request": request,
            "current_user": current_user,
            "alerts": alert_list,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Mark alert as resolved"""
    alert = db.query(SequenceAlert).filter(SequenceAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.resolved_at = datetime.now()
    db.commit()

    return RedirectResponse(url="/alerts", status_code=302)


# ============================================================================
# Admin - Processing
# ============================================================================


@router.get("/admin/processing/runs", response_class=HTMLResponse)
async def admin_processing_runs(
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """View processing runs and logs"""
    runs = db.query(ProcessingRun).order_by(ProcessingRun.started_at.desc()).limit(20).all()

    # Convert ANSI to HTML for each run's logs
    for run in runs:
        if run.dosemu_log:
            #run.dosemu_log_html = run.dosemu_log.replace('\n', '\n\n')
            #run.dosemu_log_html = ansi_to_html(run.dosemu_log_html)
            try:
                run.dosemu_log_html = await ansi_to_html(run.dosemu_log)
            except Exception as e:
                logger.error(f"Failed to convert ANSI to HTML: {e}")
                run.dosemu_log_html = run.dosemu_log
            run.dosemu_log_html = await strip_multiple_blank_lines(run.dosemu_log_html)
        else:
            run.dosemu_log_html = ""

    return templates.TemplateResponse(
        "admin/processing_runs.html",
        {
            "request": request,
            "current_user": current_user,
            "runs": runs,
            "config": request.app.state.config,
        },
    )


@router.post("/admin/processing/trigger")
async def admin_trigger_processing(
    current_user: SysopUser = Depends(require_admin),
):
    """Manually trigger packet processing"""
    from app.services.processing_service import trigger_processing

    try:
        trigger_processing()
        return {"status": "success", "message": "Processing triggered successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Admin - Users
# ============================================================================


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request, current_user: SysopUser = Depends(require_admin)
):
    """Admin home - redirect to users"""
    return RedirectResponse(url="/admin/users")


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin users page"""
    users = db.query(SysopUser).all()

    user_list = []
    for user in users:
        user_list.append(
            {
                "id": user.id,
                "username": user.username,
                "is_admin": user.is_superuser,
                "created_at": user.created_at.strftime("%Y-%m-%d"),
            }
        )

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "current_user": current_user,
            "users": user_list,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/admin/users/new", response_class=HTMLResponse)
async def admin_users_new(
    request: Request, current_user: SysopUser = Depends(require_admin)
):
    """New user form"""
    return templates.TemplateResponse(
        "admin/user_form.html",
        {
            "request": request,
            "current_user": current_user,
            "user": None,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.post("/admin/users/new")
async def admin_users_create(
    username: str = Form(...),
    password: str = Form(...),
    is_admin: bool = Form(False),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create new user"""
    # Check if username exists
    existing = db.query(SysopUser).filter(SysopUser.username == username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user = SysopUser(
        username=username, hashed_password=get_password_hash(password), is_superuser=is_admin
    )
    db.add(user)
    db.commit()

    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
async def admin_users_edit(
    user_id: int,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Edit user form"""
    user = db.query(SysopUser).filter(SysopUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return templates.TemplateResponse(
        "admin/user_form.html",
        {
            "request": request,
            "current_user": current_user,
            "user": user,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.post("/admin/users/{user_id}/edit")
async def admin_users_update(
    user_id: int,
    username: str = Form(...),
    password: str = Form(""),
    is_admin: bool = Form(False),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update user"""
    user = db.query(SysopUser).filter(SysopUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if username is taken by another user
    existing = db.query(SysopUser).filter(
        SysopUser.username == username,
        SysopUser.id != user_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user.username = username
    user.is_superuser = is_admin

    # Only update password if provided
    if password:
        user.hashed_password = get_password_hash(password)

    db.commit()

    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/admin/users/{user_id}/delete")
async def admin_users_delete(
    user_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete user"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    user = db.query(SysopUser).filter(SysopUser.id == user_id).first()
    if user:
        db.delete(user)
        db.commit()

    return RedirectResponse(url="/admin/users", status_code=302)


# ============================================================================
# Admin - Clients
# ============================================================================


@router.get("/admin/clients", response_class=HTMLResponse)
async def admin_clients(
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin clients page"""
    clients = db.query(Client).all()

    client_list = []
    for client in clients:
        # Format last_seen
        if client.last_seen:
            last_seen = client.last_seen.strftime("%Y-%m-%d %H:%M")
        else:
            last_seen = None

        client_list.append(
            {
                "id": client.id,
                "bbs_name": client.bbs_name,
                "client_id": client.client_id,
                "is_active": client.is_active,
                "last_seen": last_seen,
            }
        )

    return templates.TemplateResponse(
        "admin/clients.html",
        {
            "request": request,
            "current_user": current_user,
            "clients": client_list,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/admin/clients/new", response_class=HTMLResponse)
async def admin_clients_new(
    request: Request, current_user: SysopUser = Depends(require_admin)
):
    """New client form"""
    return templates.TemplateResponse(
        "admin/client_form.html",
        {
            "request": request,
            "current_user": current_user,
            "client": None,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.post("/admin/clients/new")
async def admin_clients_create(
    request: Request,
    bbs_name: str = Form(...),
    client_id: str = Form(...),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create new client"""
    # Check if client_id already exists
    existing = db.query(Client).filter(Client.client_id == client_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Client ID already exists")

    # Generate random client secret (plain text for showing to user)
    client_secret = Client.generate_client_secret()

    client = Client(
        bbs_name=bbs_name,
        client_id=client_id,
        client_secret=get_password_hash(client_secret),
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    # Show the secret once - user must save it
    return templates.TemplateResponse(
        "admin/client_created.html",
        {
            "request": request,
            "current_user": current_user,
            "client": {
                "id": client.id,
                "bbs_name": client.bbs_name,
                "client_id": client.client_id,
                "client_secret": client_secret,  # Plain text secret shown only once
            },
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/admin/clients/{client_id}/edit", response_class=HTMLResponse)
async def admin_clients_edit(
    request: Request,
    client_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Edit client form"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    return templates.TemplateResponse(
        "admin/client_form.html",
        {
            "request": request,
            "current_user": current_user,
            "client": {
                "id": client.id,
                "bbs_name": client.bbs_name,
                "client_id": client.client_id,
                "is_active": client.is_active,
            },
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.post("/admin/clients/{client_id}/edit")
async def admin_clients_update(
    client_id: int,
    bbs_name: str = Form(...),
    is_active: bool = Form(False),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update client"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Update fields
    client.bbs_name = bbs_name
    client.is_active = is_active

    db.commit()

    return RedirectResponse(url="/admin/clients", status_code=302)


@router.get("/admin/clients/{client_id}/config")
async def admin_clients_show_config(
    request: Request,
    client_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Show client configuration"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Get base URL from request
    base_url = f"{request.url.scheme}://{request.url.netloc}"

    return templates.TemplateResponse(
        "admin/client_config.html",
        {
            "request": request,
            "current_user": current_user,
            "client": {
                "id": client.id,
                "bbs_name": client.bbs_name,
                "client_id": client.client_id,
            },
            "hub_url": base_url,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/admin/clients/{client_id}/regenerate-secret")
async def admin_clients_regenerate_secret(
    request: Request,
    client_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Regenerate client secret"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Generate new secret
    new_secret = Client.generate_client_secret()
    client.client_secret = get_password_hash(new_secret)
    db.commit()

    # Show the new secret once
    return templates.TemplateResponse(
        "admin/client_secret_regenerated.html",
        {
            "request": request,
            "current_user": current_user,
            "client": {
                "id": client.id,
                "bbs_name": client.bbs_name,
                "client_id": client.client_id,
                "client_secret": new_secret,
            },
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/admin/clients/{client_id}/delete")
async def admin_clients_delete(
    client_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete client"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        db.delete(client)
        db.commit()

    return RedirectResponse(url="/admin/clients", status_code=302)


# ============================================================================
# League Management Routes
# ============================================================================


@router.get("/admin/leagues", response_class=HTMLResponse)
async def admin_leagues_list(
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all leagues"""
    leagues = db.query(League).all()

    # Build league list with member counts
    league_list = []
    for league in leagues:
        member_count = db.query(LeagueMembership).filter(
            LeagueMembership.league_id == league.id,
            LeagueMembership.is_active == True
        ).count()

        league_list.append({
            "id": league.id,
            "league_id": league.league_id,
            "game_type": league.game_type,
            "full_id": league.full_id,
            "name": league.name,
            "description": league.description,
            "is_active": league.is_active,
            "member_count": member_count,
        })

    return templates.TemplateResponse(
        "admin/leagues.html",
        {
            "request": request,
            "current_user": current_user,
            "leagues": league_list,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/admin/leagues/new", response_class=HTMLResponse)
async def admin_leagues_new(
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """New league form"""
    return templates.TemplateResponse(
        "admin/league_form.html",
        {
            "request": request,
            "current_user": current_user,
            "league": None,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.post("/admin/leagues/new")
async def admin_leagues_create(
    request: Request,
    league_id: str = Form(...),
    game_type: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    dosemu_path: str = Form(""),
    game_executable: str = Form(""),
    is_active: bool = Form(False),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create new league"""
    # Check if league already exists
    existing = db.query(League).filter(
        League.league_id == league_id,
        League.game_type == game_type
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="League with this ID and game type already exists")

    league = League(
        league_id=league_id,
        game_type=game_type,
        name=name,
        description=description if description else None,
        dosemu_path=dosemu_path if dosemu_path else None,
        game_executable=game_executable if game_executable else None,
        is_active=is_active,
    )
    db.add(league)
    db.commit()
    db.refresh(league)

    return RedirectResponse(url=f"/admin/leagues/{league.id}", status_code=302)


@router.get("/admin/leagues/{league_id}", response_class=HTMLResponse)
async def admin_leagues_detail(
    request: Request,
    league_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """League detail with membership management"""
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    # Get all league members
    memberships = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league_id
    ).all()

    members = []
    for membership in memberships:
        client = db.query(Client).filter(Client.id == membership.client_id).first()
        if client:
            members.append({
                "membership_id": membership.id,
                "client_id": client.id,
                "bbs_name": client.bbs_name,
                "bbs_index": membership.bbs_index,
                "fidonet_address": membership.fidonet_address,
                "client_oauth_id": client.client_id,
                "joined_at": membership.joined_at,
                "membership_is_active": membership.is_active,
            })

    # Get available clients (not already in this league)
    member_client_ids = [m.client_id for m in memberships]
    available_clients = db.query(Client).filter(
        Client.id.notin_(member_client_ids) if member_client_ids else True,
        Client.is_active == True
    ).all()

    # Get statistics
    stats = {
        "total_packets": db.query(Packet).filter(Packet.league_id == league_id).count(),
        "processed_packets": db.query(Packet).filter(
            Packet.league_id == league_id,
            Packet.is_processed == True
        ).count(),
        "processing_runs": db.query(ProcessingRun).filter(
            ProcessingRun.league_id == league_id
        ).count(),
    }

    return templates.TemplateResponse(
        "admin/league_detail.html",
        {
            "request": request,
            "current_user": current_user,
            "league": league,
            "members": members,
            "available_clients": available_clients,
            "stats": stats,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.get("/admin/leagues/{league_id}/edit", response_class=HTMLResponse)
async def admin_leagues_edit(
    request: Request,
    league_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Edit league form"""
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    return templates.TemplateResponse(
        "admin/league_form.html",
        {
            "request": request,
            "current_user": current_user,
            "league": league,
            "config": {"hub": {"bbs_name": "Nova Hub", "bbs_index": "01"}},
            "get_flashed_messages": get_flash_messages,
        },
    )


@router.post("/admin/leagues/{league_id}/edit")
async def admin_leagues_update(
    league_id: int,
    name: str = Form(...),
    description: str = Form(""),
    dosemu_path: str = Form(""),
    game_executable: str = Form(""),
    is_active: bool = Form(False),
    game_type: str = Form(...),  # Hidden field for disabled select
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update league"""
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    league.name = name
    league.description = description if description else None
    league.dosemu_path = dosemu_path if dosemu_path else None
    league.game_executable = game_executable if game_executable else None
    league.is_active = is_active

    db.commit()

    return RedirectResponse(url=f"/admin/leagues/{league_id}", status_code=302)


@router.get("/admin/leagues/{league_id}/delete")
async def admin_leagues_delete(
    league_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete league"""
    league = db.query(League).filter(League.id == league_id).first()
    if league:
        # Delete all memberships first
        db.query(LeagueMembership).filter(LeagueMembership.league_id == league_id).delete()
        db.delete(league)
        db.commit()

    return RedirectResponse(url="/admin/leagues", status_code=302)


@router.post("/admin/leagues/{league_id}/members/add")
async def admin_leagues_add_member(
    league_id: int,
    client_id: int = Form(...),
    bbs_index: Optional[int] = Form(None),
    fidonet_address: str = Form(...),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Add client to league"""
    import re

    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Validate bbs_index if provided
    if bbs_index is not None:
        if not (1 <= bbs_index <= 255):
            raise HTTPException(status_code=400, detail="BBS ID must be between 1 and 255")

        # Check uniqueness within league
        existing_bbs = db.query(LeagueMembership).filter(
            LeagueMembership.league_id == league_id,
            LeagueMembership.bbs_index == bbs_index,
            LeagueMembership.is_active == True
        ).first()

        if existing_bbs:
            raise HTTPException(
                status_code=400,
                detail=f"BBS ID {bbs_index} is already assigned in this league"
            )

    # Validate Fidonet address format
    if not re.match(r'^\d+:\d+/\d+$', fidonet_address):
        raise HTTPException(
            status_code=400,
            detail="Invalid Fidonet address format. Use zone:net/node (e.g., 13:10/100)"
        )

    # Check uniqueness within league
    existing_fidonet = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league_id,
        LeagueMembership.fidonet_address == fidonet_address,
        LeagueMembership.is_active == True
    ).first()

    if existing_fidonet:
        raise HTTPException(
            status_code=400,
            detail=f"Fidonet address {fidonet_address} is already assigned in this league"
        )

    # Check if already a member
    existing = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league_id,
        LeagueMembership.client_id == client_id
    ).first()

    if existing:
        # Reactivate if inactive and update BBS index and Fidonet address
        existing.is_active = True
        if bbs_index is not None:
            existing.bbs_index = bbs_index
        existing.fidonet_address = fidonet_address
        db.commit()
    else:
        # BBS index is required for new memberships
        if bbs_index is None:
            raise HTTPException(status_code=400, detail="BBS ID is required when adding a new member")

        # Create new membership
        membership = LeagueMembership(
            league_id=league_id,
            client_id=client_id,
            bbs_index=bbs_index,
            fidonet_address=fidonet_address,
            is_active=True
        )
        db.add(membership)
        db.commit()

    return RedirectResponse(url=f"/admin/leagues/{league_id}", status_code=302)


@router.post("/admin/leagues/{league_id}/members/{client_id}/remove")
async def admin_leagues_remove_member(
    league_id: int,
    client_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove client from league"""
    membership = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league_id,
        LeagueMembership.client_id == client_id
    ).first()

    if membership:
        db.delete(membership)
        db.commit()

    return RedirectResponse(url=f"/admin/leagues/{league_id}", status_code=302)


@router.post("/admin/leagues/{league_id}/members/{membership_id}/update-bbs-index")
async def admin_leagues_update_member_bbs_index(
    league_id: int,
    membership_id: int,
    bbs_index: int = Form(...),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update BBS index for a league membership"""
    membership = db.query(LeagueMembership).filter(
        LeagueMembership.id == membership_id,
        LeagueMembership.league_id == league_id
    ).first()

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Validate range
    if not (1 <= bbs_index <= 255):
        raise HTTPException(status_code=400, detail="BBS ID must be between 1 and 255")

    # Check uniqueness within league (excluding current membership)
    existing = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league_id,
        LeagueMembership.bbs_index == bbs_index,
        LeagueMembership.id != membership_id,
        LeagueMembership.is_active == True
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"BBS ID {bbs_index} is already assigned to another member in this league"
        )

    membership.bbs_index = bbs_index
    db.commit()

    return RedirectResponse(url=f"/admin/leagues/{league_id}", status_code=302)


@router.post("/admin/leagues/{league_id}/members/{membership_id}/update-fidonet")
async def admin_leagues_update_member_fidonet(
    league_id: int,
    membership_id: int,
    fidonet_address: str = Form(...),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update Fidonet address for a league membership"""
    import re

    membership = db.query(LeagueMembership).filter(
        LeagueMembership.id == membership_id,
        LeagueMembership.league_id == league_id
    ).first()

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Validate format
    if not re.match(r'^\d+:\d+/\d+$', fidonet_address):
        raise HTTPException(
            status_code=400,
            detail="Invalid Fidonet address format. Use zone:net/node (e.g., 13:10/100)"
        )

    # Check uniqueness within league (excluding current membership)
    existing = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league_id,
        LeagueMembership.fidonet_address == fidonet_address,
        LeagueMembership.id != membership_id,
        LeagueMembership.is_active == True
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Fidonet address {fidonet_address} is already assigned to another member in this league"
        )

    membership.fidonet_address = fidonet_address
    db.commit()

    return RedirectResponse(url=f"/admin/leagues/{league_id}", status_code=302)
