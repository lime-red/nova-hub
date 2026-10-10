"""
backend/services/audit.py

The audit log: approvals, rejections, claim-link issue and use, owner and role
changes, secret rotations, accounts created and linked -- each with who did it
and when.

record() adds the event to the caller's session without committing, so the
event and the change it describes land in the same transaction: there is no
log line for a change that rolled back, and no change without its line.
"""

from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from backend.models.database import AuditEvent, Client, SysopUser


def _describe(target) -> tuple[Optional[str], Optional[int], Optional[str]]:
    if target is None:
        return None, None, None
    if isinstance(target, Client):
        return "client", target.id, target.bbs_name
    if isinstance(target, SysopUser):
        return "user", target.id, target.username
    raise TypeError(f"no audit description for {type(target).__name__}")


def record(
    db: Session,
    action: str,
    *,
    actor: Optional[SysopUser] = None,
    target=None,
    detail: Optional[str] = None,
    request: Optional[Request] = None,
) -> AuditEvent:
    """Add one event to the session. The caller commits."""
    from backend.core.rate_limiter import _get_client_ip

    target_type, target_id, target_name = _describe(target)
    event = AuditEvent(
        actor_user_id=actor.id if actor else None,
        actor=actor.username if actor else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target=target_name,
        detail=detail,
        ip=_get_client_ip(request) if request else None,
    )
    db.add(event)
    return event
