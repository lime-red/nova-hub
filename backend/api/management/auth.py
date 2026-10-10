"""Management API authentication (session-based JWT)

Two ways in, one session. Sysops sign in through the identity provider
(/sso/start -> provider -> /sso/callback); the local username and password
(/login) is the hub operator's break-glass. Both end by setting the same
session cookie, so nothing past here knows which was used.
"""

import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.api.management.claim import public_url
from backend.core.config import get_config
from backend.core.database import get_db
from backend.core.rate_limiter import check_rate_limit, record_failed_attempt
from backend.core.security import (
    COOKIE_NAME,
    create_access_token,
    create_session_response,
    create_session_token,
    get_current_user,
    get_password_hash,
    validate_password,
    verify_password,
    verify_token,
)
from backend.logging_config import get_logger
from backend.models.database import SysopUser
from backend.schemas.auth import (
    AuthMethods,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserResponse,
)
from backend.services import sysop_accounts
from backend.services.identity_provider import (
    IdentityError,
    IdentityProvider,
    get_identity_provider,
)

logger = get_logger(context="management_auth")

router = APIRouter()


@router.post("/login", response_model=LoginResponse, summary="Login")
async def login(
    http_request: Request,
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
    # Check rate limit before processing credentials
    check_rate_limit(http_request)

    user = db.query(SysopUser).filter(SysopUser.username == request.username).first()

    # An account with no password signs in only through the provider.
    if not user or not user.hashed_password or not verify_password(request.password, user.hashed_password):
        record_failed_attempt(http_request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    config = get_config()
    # Create session token and set cookie
    token = create_session_token(user.username)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=config.security.cookie_secure,
        samesite="lax",
        max_age=config.security.jwt_expiry_hours * 3600,
    )

    logger.info(f"User {user.username} logged in")

    return LoginResponse(user=user_response(user))


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
    return user_response(current_user)


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
    if not current_user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account has no password: it signs in through the sign-in provider.",
        )

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

    # Enforce password policy
    try:
        validate_password(request.new_password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Update password
    current_user.hashed_password = get_password_hash(request.new_password)
    db.commit()

    logger.info(f"User {current_user.username} changed password")

    return {"message": "Password changed successfully"}


def user_response(user: SysopUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        is_admin=user.is_superuser,
        email=user.email,
        full_name=user.full_name,
        has_password=bool(user.hashed_password),
        sso_linked=bool(user.idp_subject),
        created_at=user.created_at.strftime("%Y-%m-%d") if user.created_at else None,
    )


# --- Sign-in through the identity provider ---

SSO_COOKIE = "nova_hub_sso"
SSO_COOKIE_PATH = "/management/api/v1/auth/sso"
SSO_STATE_TTL = timedelta(minutes=10)


def _callback_url(request: Request) -> str:
    return f"{public_url(request)}/management/api/v1/auth/sso/callback"


def _safe_next(next_path: Optional[str]) -> str:
    """Only a path on this hub: never another site, so the redirect can't be borrowed."""
    if next_path and next_path.startswith("/") and not next_path.startswith("//") \
            and "\\" not in next_path:
        return next_path
    return "/dashboard"


def _to_login(error: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?sso_error={error}", status_code=302)
    response.delete_cookie(SSO_COOKIE, path=SSO_COOKIE_PATH)
    return response


@router.get("/methods", response_model=AuthMethods, summary="Sign-in Methods")
async def methods(provider: Optional[IdentityProvider] = Depends(get_identity_provider)):
    """Which sign-in methods the login page should offer. Public."""
    return AuthMethods(password=True, sso=provider is not None)


@router.get("/sso/start", summary="Start Provider Sign-in")
async def sso_start(
    request: Request,
    next: Optional[str] = Query(None, description="Console path to land on afterwards"),
    provider: Optional[IdentityProvider] = Depends(get_identity_provider),
):
    """Send the browser to the identity provider to sign in.

    A random state goes both into the provider URL and, signed, into a short-lived
    cookie; the callback accepts only a state that matches its own browser's
    cookie, so a sign-in cannot be started in one browser and finished in another.
    """
    if provider is None:
        raise HTTPException(status_code=404, detail="Sign-in through a provider is not enabled.")
    state = secrets.token_urlsafe(24)
    cookie = create_access_token(
        {"type": "sso_state", "state": state, "next": _safe_next(next)},
        expires_delta=SSO_STATE_TTL,
    )
    response = RedirectResponse(provider.authorization_url(_callback_url(request), state),
                                status_code=302)
    response.set_cookie(
        SSO_COOKIE, cookie,
        max_age=int(SSO_STATE_TTL.total_seconds()),
        path=SSO_COOKIE_PATH,
        httponly=True,
        secure=get_config().security.cookie_secure,
        samesite="lax",  # sent on the provider's top-level redirect back
    )
    return response


@router.get("/sso/callback", summary="Finish Provider Sign-in", include_in_schema=False)
async def sso_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    provider: Optional[IdentityProvider] = Depends(get_identity_provider),
    db: Session = Depends(get_db),
):
    """Where the provider sends the browser back. Ends on a console page either way.

    On failure the browser lands on /login?sso_error=<code>, which the login page
    turns into a message.
    """
    check_rate_limit(request)
    if provider is None:
        return _to_login("disabled")
    if error:
        logger.info(f"Provider sign-in ended with error={error!r}")
        return _to_login("cancelled")

    claims = verify_token(request.cookies.get(SSO_COOKIE, ""))
    if (not claims or claims.get("type") != "sso_state" or not state or not code
            or not secrets.compare_digest(claims.get("state", ""), state)):
        record_failed_attempt(request)
        return _to_login("state")

    try:
        identity = await provider.finish(code)
    except IdentityError as e:
        logger.warning(f"Provider sign-in failed: {e}")
        return _to_login("provider")

    try:
        user = sysop_accounts.sign_in(db, identity, request)
    except sysop_accounts.SignInRefused as e:
        logger.info(f"Sign-in refused for {identity.email} ({identity.subject}): {e.code}")
        return _to_login(e.code)

    logger.info(f"User {user.username} signed in through {provider.name}")
    response = RedirectResponse(_safe_next(claims.get("next")), status_code=302)
    response.delete_cookie(SSO_COOKIE, path=SSO_COOKIE_PATH)
    return create_session_response(response, user.username)
