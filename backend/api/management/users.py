"""Management API user management endpoints (admin only)"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.api.management.auth import user_response
from backend.api.management.claim import public_url, utc
from backend.core.database import get_db
from backend.core.security import get_password_hash, require_admin, validate_password
from backend.logging_config import get_logger
from backend.models.database import RelinkLink, SysopUser
from backend.schemas.auth import UserCreate, UserResponse, UserUpdate
from backend.schemas.claim import ClaimLinkIssued, ClaimLinkStatus
from backend.services import audit, relink_links

logger = get_logger(context="management_users")

router = APIRouter()


@router.get("", response_model=List[UserResponse], summary="List All Users")
async def list_users(
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    List all sysop users (admin only)

    **Returns:** List of all users

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/users" \\
      -b cookies.txt
    ```
    """
    users = db.query(SysopUser).order_by(SysopUser.username).all()
    return [user_response(user) for user in users]


@router.get("/{user_id}", response_model=UserResponse, summary="Get User Details")
async def get_user(
    user_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Get details about a specific user (admin only)

    **Path Parameters:**
    - `user_id`: Database ID of the user

    **Returns:** User details

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/users/1" \\
      -b cookies.txt
    ```
    """
    user = db.query(SysopUser).filter(SysopUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user_response(user)


@router.post("", response_model=UserResponse, summary="Create User")
async def create_user(
    request: UserCreate,
    http_request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Create a new sysop user (admin only)

    **Request Body:**
    - `username`: Username (must be unique)
    - `password`: Password
    - `is_admin`: Whether user should have admin privileges (default: false)

    **Returns:** Created user

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/users" \\
      -H "Content-Type: application/json" \\
      -d '{"username": "newuser", "password": "secret", "is_admin": false}' \\
      -b cookies.txt
    ```
    """
    # Check if username exists
    existing = db.query(SysopUser).filter(SysopUser.username == request.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Enforce password policy
    try:
        validate_password(request.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user = SysopUser(
        username=request.username,
        hashed_password=get_password_hash(request.password),
        is_superuser=request.is_admin if request.is_admin is not None else False,
    )
    db.add(user)
    db.flush()
    audit.record(db, "user.created", actor=current_user, target=user,
                 detail="admin" if user.is_superuser else "sysop", request=http_request)
    db.commit()
    db.refresh(user)

    logger.info(f"Created user {request.username} by {current_user.username}")

    return user_response(user)


@router.put("/{user_id}", response_model=UserResponse, summary="Update User")
async def update_user(
    user_id: int,
    request: UserUpdate,
    http_request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Update a sysop user (admin only)

    **Path Parameters:**
    - `user_id`: Database ID of the user

    **Request Body:**
    - `username`: New username (optional)
    - `password`: New password (optional)
    - `is_admin`: New admin status (optional)

    **Returns:** Updated user

    **Example:**
    ```bash
    curl -X PUT "https://hub.example.com/management/api/v1/users/1" \\
      -H "Content-Type: application/json" \\
      -d '{"username": "updateduser", "is_admin": true}' \\
      -b cookies.txt
    ```
    """
    user = db.query(SysopUser).filter(SysopUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if username is taken by another user
    if request.username:
        existing = (
            db.query(SysopUser)
            .filter(
                SysopUser.username == request.username,
                SysopUser.id != user_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        if request.username != user.username:
            audit.record(db, "user.renamed", actor=current_user, target=user,
                         detail=f"{user.username} -> {request.username}", request=http_request)
        user.username = request.username

    if request.password:
        try:
            validate_password(request.password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        user.hashed_password = get_password_hash(request.password)
        audit.record(db, "user.password_set", actor=current_user, target=user,
                     request=http_request)

    if request.is_admin is not None and request.is_admin != user.is_superuser:
        if user.id == current_user.id and not request.is_admin:
            raise HTTPException(status_code=400, detail="Cannot remove your own admin role")
        user.is_superuser = request.is_admin
        audit.record(db, "user.role_changed", actor=current_user, target=user,
                     detail="now admin" if request.is_admin else "now sysop",
                     request=http_request)

    db.commit()
    db.refresh(user)

    logger.info(f"Updated user {user.username} by {current_user.username}")

    return user_response(user)


@router.delete("/{user_id}", summary="Delete User")
async def delete_user(
    user_id: int,
    http_request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Delete a sysop user (admin only)

    **Path Parameters:**
    - `user_id`: Database ID of the user

    **Returns:** Success message

    **Note:** Cannot delete yourself

    **Example:**
    ```bash
    curl -X DELETE "https://hub.example.com/management/api/v1/users/1" \\
      -b cookies.txt
    ```
    """
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    user = db.query(SysopUser).filter(SysopUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    username = user.username
    audit.record(db, "user.deleted", actor=current_user, target=user,
                 detail=f"owned {', '.join(c.bbs_name for c in user.owned_clients) or 'no BBS'}",
                 request=http_request)
    db.query(RelinkLink).filter(RelinkLink.user_id == user.id).delete()
    db.delete(user)
    db.commit()

    logger.info(f"Deleted user {username} by {current_user.username}")

    return {"message": f"User {username} deleted successfully"}


# Re-link links: connecting a provider sign-in to an existing account. See
# backend/services/relink_links.py.

def _user_or_404(db: Session, user_id: int) -> SysopUser:
    user = db.query(SysopUser).filter(SysopUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/{user_id}/relink-link", response_model=ClaimLinkIssued, summary="Issue Re-link Link")
async def issue_relink_link(
    user_id: int,
    http_request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Issue a single-use link that connects a sign-in to this account (admin only).

    Whoever opens it and signs in through the identity provider has that sign-in
    bound to this account, replacing any it had. Use it for an account that
    predates provider sign-in, or a sysop who has lost access to their email.
    Expires after 72 hours; supersedes any outstanding link for the account.

    **Returns:** the link (shown only here) and its expiry
    """
    user = _user_or_404(db, user_id)
    token, link, superseded = relink_links.issue(db, user, current_user.username)
    audit.record(db, "relink_link.issued", actor=current_user, target=user,
                 detail=f"link {link.id}, expires {link.expires_at:%Y-%m-%d %H:%M} UTC"
                 + (f", superseding {superseded}" if superseded else ""),
                 request=http_request)
    db.commit()
    logger.info(f"Re-link link {link.id} issued for {user.username} by {current_user.username}")
    return ClaimLinkIssued(
        url=f"{public_url(http_request)}/relink/{token}",
        expires_at=utc(link.expires_at),
        superseded=superseded,
    )


@router.get("/{user_id}/relink-link", response_model=Optional[ClaimLinkStatus], summary="Re-link Link Status")
async def get_relink_link(
    user_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The latest re-link link issued for this account, or null (admin only). Never the link itself."""
    _user_or_404(db, user_id)
    link = relink_links.latest(db, user_id)
    if link is None:
        return None
    return ClaimLinkStatus(
        state=relink_links.state(link),
        issued_by=link.issued_by,
        issued_at=utc(link.issued_at),
        expires_at=utc(link.expires_at),
        used_at=utc(link.used_at),
        used_ip=link.used_ip,
    )


@router.post("/{user_id}/unlink", response_model=UserResponse, summary="Remove Sign-in")
async def unlink_identity(
    user_id: int,
    http_request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Disconnect this account's provider sign-in (admin only).

    It can no longer sign in through the provider until a re-link link is used.
    Sessions already open last until they expire. Refused for an account that
    would then have no way in at all, unless it is not your own.
    """
    user = _user_or_404(db, user_id)
    if not user.idp_subject:
        raise HTTPException(status_code=400, detail="This account has no provider sign-in")
    if user.id == current_user.id and not user.hashed_password:
        raise HTTPException(status_code=400,
                            detail="That would leave your own account with no way to sign in")
    user.idp_subject = None
    audit.record(db, "account.unlinked", actor=current_user, target=user, request=http_request)
    db.commit()
    db.refresh(user)
    logger.info(f"Provider sign-in removed from {user.username} by {current_user.username}")
    return user_response(user)
