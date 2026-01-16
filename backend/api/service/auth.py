"""Service API authentication (OAuth2 client credentials)"""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import (
    create_access_token,
    get_current_client,
    verify_client_credentials,
)
from backend.logging_config import get_logger
from backend.models.database import Client
from backend.schemas.auth import TokenResponse, ClientVerifyResponse

logger = get_logger(context="service_auth")

router = APIRouter()


@router.post("/token", response_model=TokenResponse, summary="Get Access Token")
async def login_for_access_token(
    grant_type: str = Form(..., description="Must be 'client_credentials'"),
    client_id: str = Form(..., description="Your client ID provided by the hub administrator"),
    client_secret: str = Form(..., description="Your client secret provided by the hub administrator"),
    db: Session = Depends(get_db),
):
    """
    OAuth2 token endpoint for client authentication

    **Grant Type:** client_credentials

    **Form Parameters:**
    - `grant_type`: Must be "client_credentials"
    - `client_id`: Your client ID
    - `client_secret`: Your client secret

    **Returns:** Access token valid for 24 hours

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/service/api/v1/auth/token" \\
      -d "grant_type=client_credentials" \\
      -d "client_id=your_client_id" \\
      -d "client_secret=your_secret"
    ```
    """
    # Validate grant type
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported grant_type. Only 'client_credentials' is supported",
        )

    # Verify credentials
    client = verify_client_credentials(client_id, client_secret, db)

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
    logger.debug(f"Creating token for client_id: {client.client_id}")
    access_token = create_access_token(data={"sub": client.client_id})
    logger.info(f"Token created successfully for {client.client_id}")

    return TokenResponse(access_token=access_token, token_type="bearer")


@router.get("/verify", response_model=ClientVerifyResponse, summary="Verify Access Token")
async def verify_token(current_client: Client = Depends(get_current_client)):
    """
    Verify your access token and get client information

    **Requires:** Valid Bearer token in Authorization header

    **Returns:** Client details if token is valid

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/service/api/v1/auth/verify" \\
      -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
    ```
    """
    return ClientVerifyResponse(
        client_id=current_client.client_id,
        bbs_name=current_client.bbs_name,
        is_active=current_client.is_active,
    )
