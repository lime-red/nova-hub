"""Management API: the audit log (admin only). See backend/services/audit.py."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.management.claim import utc
from backend.core.database import get_db
from backend.core.security import require_admin
from backend.models.database import AuditEvent, SysopUser
from backend.schemas.audit import AuditEventInfo

router = APIRouter()


@router.get("", response_model=List[AuditEventInfo], summary="Audit Log")
async def list_audit_events(
    target_type: Optional[str] = Query(None, description="client or user"),
    target_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None, description="An action, or a prefix such as 'owner.'"),
    before_id: Optional[int] = Query(None, description="Page back: events older than this id"),
    limit: int = Query(100, ge=1, le=500),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Who did what, newest first (admin only)

    **Example:** everything that happened to one BBS
    ```bash
    curl "https://hub.example.com/management/api/v1/audit?target_type=client&target_id=3" \\
      -b cookies.txt
    ```
    """
    query = db.query(AuditEvent)
    if target_type:
        query = query.filter(AuditEvent.target_type == target_type)
    if target_id is not None:
        query = query.filter(AuditEvent.target_id == target_id)
    if action:
        query = query.filter(AuditEvent.action.startswith(action, autoescape=True))
    if before_id is not None:
        query = query.filter(AuditEvent.id < before_id)
    events = query.order_by(AuditEvent.id.desc()).limit(limit).all()
    return [
        AuditEventInfo(
            id=e.id, at=utc(e.at), actor=e.actor, actor_user_id=e.actor_user_id,
            action=e.action, target_type=e.target_type, target_id=e.target_id,
            target=e.target, detail=e.detail, ip=e.ip,
        )
        for e in events
    ]
