"""Management API authentication (session-based JWT)"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import (
    COOKIE_NAME,
    create_session_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from backend.logging_config import get_logger
from backend.models.database import SysopUser
from backend.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserResponse,
)

logger = get_logger(context="management_auth")

router = APIRouter()


@router.post("/login", response_model=LoginResponse, summary="Login")
async def login(
    request: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Authenticate user and set session cookie

    **Request Body:**
    - `username`: Sysop username
    - `password`: Sysop password

    **Returns:** User info and sets httpOnly session cookie

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/auth/login" \\
      -H "Content-Type: application/json" \\
      -d '{"username": "admin", "password": "admin"}' \\
      -c cookies.txt
    ```
    """
    user = db.query(SysopUser).filter(SysopUser.username == request.username).first()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Create session token and set cookie
    token = create_session_token(user.username)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=24 * 3600,  # 24 hours
    )

    logger.info(f"User {user.username} logged in")

    return LoginResponse(
        user=UserResponse(
            id=user.id,
            username=user.username,
            is_admin=user.is_superuser,
        )
    )


@router.post("/logout", summary="Logout")
async def logout(response: Response):
    """
    Clear session cookie

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/auth/logout" \\
      -b cookies.txt
    ```
    """
    response.delete_cookie(key=COOKIE_NAME)
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse, summary="Get Current User")
async def get_me(current_user: SysopUser = Depends(get_current_user)):
    """
    Get current authenticated user info

    **Requires:** Valid session cookie

    **Returns:** Current user details

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/auth/me" \\
      -b cookies.txt
    ```
    """
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        is_admin=current_user.is_superuser,
    )


@router.post("/change-password", summary="Change Password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Change current user's password

    **Request Body:**
    - `current_password`: Current password
    - `new_password`: New password
    - `confirm_password`: Confirm new password

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/auth/change-password" \\
      -H "Content-Type: application/json" \\
      -d '{"current_password": "old", "new_password": "new", "confirm_password": "new"}' \\
      -b cookies.txt
    ```
    """
    # Verify current password
    if not verify_password(request.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Verify new passwords match
    if request.new_password != request.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New passwords do not match",
        )

    # Update password
    current_user.hashed_password = get_password_hash(request.new_password)
    db.commit()

    logger.info(f"User {current_user.username} changed password")

    return {"message": "Password changed successfully"}
