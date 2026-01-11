# routers/auth.py - OAuth authentication for API clients

from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import toml
from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.database import Client, get_db

# Load configuration
config = toml.load("config.toml")

router = APIRouter()

# JWT settings from config
SECRET_KEY = config.get("security", {}).get("jwt_secret", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = (
    config.get("security", {}).get("jwt_expiry_hours", 24) * 60
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def verify_client_credentials(
    client_id: str, client_secret: str, db: Session
) -> Optional[Client]:
    """Verify client credentials and return client if valid"""
    client = db.query(Client).filter(Client.client_id == client_id).first()

    if not client:
        return None

    if not client.is_active:
        return None

    # Verify secret using bcrypt directly
    if not bcrypt.checkpw(
        client_secret.encode("utf-8"), client.client_secret.encode("utf-8")
    ):
        return None

    return client


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


@router.post("/token")
async def login_for_access_token(
    grant_type: str = Form(...),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    OAuth2 token endpoint for client authentication

    Supports both grant types:
    - client_credentials: Send grant_type=client_credentials, client_id, and client_secret
    - password: Send grant_type=password, username (as client_id), and password (as client_secret)
    """
    # Validate grant type
    if grant_type not in ["client_credentials", "password"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported grant_type. Use 'client_credentials' or 'password'",
        )

    # Extract credentials based on grant type
    if grant_type == "client_credentials":
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="client_id and client_secret are required for client_credentials grant",
            )
        cred_id = client_id
        cred_secret = client_secret
    else:  # password grant
        if not username or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="username and password are required for password grant",
            )
        cred_id = username
        cred_secret = password

    # Verify credentials
    client = verify_client_credentials(cred_id, cred_secret, db)

    if not client:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Update last_seen
    client.last_seen = datetime.utcnow()
    db.commit()

    # Create token
    print(f"[Auth] Creating token for client_id: {client.client_id}")
    access_token = create_access_token(data={"sub": client.client_id})
    print(f"[Auth] Token created successfully")

    return {"access_token": access_token, "token_type": "bearer"}


async def get_current_client(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> Client:
    """Get current client from JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        client_id: str = payload.get("sub")
        print(f"[Auth] Token decoded - client_id: {client_id}")
        if client_id is None:
            print("[Auth] ERROR: No 'sub' claim in token")
            raise credentials_exception
    except JWTError as e:
        print(f"[Auth] ERROR: JWT decode failed: {e}")
        raise credentials_exception

    client = db.query(Client).filter(Client.client_id == client_id).first()
    if client is None:
        print(f"[Auth] ERROR: Client not found in database: {client_id}")
        raise credentials_exception

    if not client.is_active:
        print(f"[Auth] ERROR: Client is inactive: {client_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Client is inactive"
        )

    print(f"[Auth] SUCCESS: Client authenticated: {client_id}")
    return client


@router.get("/verify")
async def verify_token(current_client: Client = Depends(get_current_client)):
    """
    Verify token and return client information

    Useful for testing authentication
    """
    return {
        "client_id": current_client.client_id,
        "bbs_name": current_client.bbs_name,
        "is_active": current_client.is_active,
    }
