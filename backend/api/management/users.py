"""Management API user management endpoints (admin only)"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.api.management.auth import user_response
from backend.core.database import get_db
from backend.core.security import get_password_hash, require_admin, validate_password
from backend.logging_config import get_logger
from backend.models.database import SysopUser
from backend.schemas.auth import UserCreate, UserResponse, UserUpdate
from backend.services import audit

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
    db.delete(user)
    db.commit()

    logger.info(f"Deleted user {username} by {current_user.username}")

    return {"message": f"User {username} deleted successfully"}
