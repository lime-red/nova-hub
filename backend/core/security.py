"""
Security utilities for Nova Hub

Provides authentication and authorization for both Service API (OAuth2)
and Management API (session-based JWT).
"""

from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.core.config import get_config
from backend.core.database import get_db


# OAuth2 scheme for Service API
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/service/api/v1/auth/token")


def get_jwt_settings():
    """Get JWT settings from config"""
    config = get_config()
    return {
        "secret_key": config.security.jwt_secret,
        "algorithm": "HS256",
        "access_token_expire_minutes": config.security.jwt_expiry_hours * 60,
    }


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token

    Args:
        data: Claims to include in the token
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string
    """
    settings = get_jwt_settings()
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings["access_token_expire_minutes"])

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings["secret_key"], algorithm=settings["algorithm"])
    return encoded_jwt


def verify_token(token: str) -> Optional[dict]:
    """
    Verify and decode a JWT token

    Args:
        token: JWT token string

    Returns:
        Decoded token payload or None if invalid
    """
    settings = get_jwt_settings()
    try:
        payload = jwt.decode(token, settings["secret_key"], algorithms=[settings["algorithm"]])
        return payload
    except JWTError:
        return None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash using bcrypt"""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# Alias for consistency with naming conventions
get_password_hash = hash_password


# --- Service API Authentication (OAuth2) ---

async def get_current_client(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    """
    Get the current authenticated client from OAuth2 token

    For use in Service API endpoints.

    Raises:
        HTTPException: If token is invalid or client not found
    """
    from backend.models.database import Client

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = verify_token(token)
    if payload is None:
        raise credentials_exception

    client_id: str = payload.get("sub")
    if client_id is None:
        raise credentials_exception

    client = db.query(Client).filter(Client.client_id == client_id).first()
    if client is None:
        raise credentials_exception

    if not client.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client is inactive"
        )

    return client


def verify_client_credentials(client_id: str, client_secret: str, db: Session):
    """
    Verify OAuth2 client credentials

    Args:
        client_id: Client identifier
        client_secret: Client secret (plaintext)
        db: Database session

    Returns:
        Client instance if valid, None otherwise
    """
    from backend.models.database import Client

    client = db.query(Client).filter(Client.client_id == client_id).first()

    if not client:
        return None

    if not client.is_active:
        return None

    if not verify_password(client_secret, client.client_secret):
        return None

    return client


# --- Management API Authentication (Session JWT) ---

COOKIE_NAME = "nova_hub_session"


async def get_current_user(request: Request, db: Session = Depends(get_db)):
    """
    Get the current authenticated user from session cookie

    For use in Management API endpoints.

    Raises:
        HTTPException: If not authenticated or user not found
    """
    from backend.models.database import SysopUser

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session"
        )

    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session"
        )

    user = db.query(SysopUser).filter(SysopUser.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive"
        )

    return user


async def get_current_user_optional(request: Request, db: Session = Depends(get_db)):
    """
    Get current user if authenticated, None otherwise

    For endpoints that work both authenticated and unauthenticated.
    """
    from backend.models.database import SysopUser

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    payload = verify_token(token)
    if payload is None:
        return None

    username: str = payload.get("sub")
    if username is None:
        return None

    user = db.query(SysopUser).filter(SysopUser.username == username).first()
    if user is None or not user.is_active:
        return None

    return user


def create_session_response(response, username: str):
    """
    Add session cookie to response

    Args:
        response: FastAPI response object
        username: Username to store in session
    """
    token = create_access_token(data={"sub": username, "type": "session"})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=get_config().security.jwt_expiry_hours * 3600
    )
    return response


def clear_session_response(response):
    """Remove session cookie from response"""
    response.delete_cookie(key=COOKIE_NAME)
    return response


def create_session_token(username: str) -> str:
    """
    Create a session token for management UI authentication

    Args:
        username: Username to encode in the token

    Returns:
        JWT token string
    """
    return create_access_token(data={"sub": username, "type": "session"})


async def require_admin(current_user=Depends(get_current_user)):
    """
    Dependency that requires the current user to be an admin

    For use in Management API endpoints that need admin privileges.

    Raises:
        HTTPException: If user is not an admin
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user
