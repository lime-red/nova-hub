"""Service API authentication (OAuth2 client credentials)"""

from typing import Optional
from urllib.parse import unquote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from backend.core.clock import utcnow
from backend.core.database import get_db
from backend.core.rate_limiter import check_rate_limit, record_failed_attempt
from backend.core.security import (
    create_service_token,
    get_current_client,
    get_jwt_settings,
    verify_client_credentials,
)
from backend.logging_config import get_logger
from backend.models.database import Client
from backend.schemas.auth import TokenResponse, ClientVerifyResponse

logger = get_logger(context="service_auth")

router = APIRouter()

# RFC 6749 2.3.1: a client may send its credentials as HTTP Basic instead of in
# the form. Not required, so the form alone still works.
basic = HTTPBasic(auto_error=False, description="client_id and client_secret, as RFC 6749 2.3.1")


@router.post("/token", response_model=TokenResponse, summary="Get Access Token")
async def login_for_access_token(
    request: Request,
    response: Response,
    grant_type: str = Form(..., description="Must be 'client_credentials'"),
    client_id: Optional[str] = Form(None, description="Your client ID provided by the hub administrator"),
    client_secret: Optional[str] = Form(None, description="Your client secret provided by the hub administrator"),
    credentials: Optional[HTTPBasicCredentials] = Depends(basic),
    db: Session = Depends(get_db),
):
    """
    OAuth2 token endpoint for client authentication

    **Grant Type:** client_credentials

    **Form Parameters:**
    - `grant_type`: Must be "client_credentials"
    - `client_id`: Your client ID
    - `client_secret`: Your client secret

    Or send `client_id` and `client_secret` as HTTP Basic credentials
    (RFC 6749 2.3.1) and only `grant_type` in the form. Not both.

    **Returns:** An access token, and in `expires_in` how many seconds it lasts

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/service/api/v1/auth/token" \\
      -d "grant_type=client_credentials" \\
      -d "client_id=your_client_id" \\
      -d "client_secret=your_secret"
    ```
    """
    # Check rate limit before processing credentials
    check_rate_limit(request)

    # Validate grant type
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported grant_type. Only 'client_credentials' is supported",
        )

    if credentials is not None:
        if client_id is not None or client_secret is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Send the client credentials one way: HTTP Basic or the form, not both",
            )
        # Form-encoded before they were base64'd (RFC 6749 2.3.1).
        client_id, client_secret = unquote_plus(credentials.username), unquote_plus(credentials.password)
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_id and client_secret are required",
        )

    # Verify credentials
    client = verify_client_credentials(client_id, client_secret, db)

    if not client:
        record_failed_attempt(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Update last_seen, and the onboarding Verify step's first sign-in
    client.last_seen = utcnow()
    client.last_user_agent = (request.headers.get("user-agent") or "")[:100] or None
    if client.first_token_at is None:
        client.first_token_at = client.last_seen
    db.commit()

    # Create token
    logger.debug(f"Creating token for client_id: {client.client_id}")
    access_token = create_service_token(client.client_id)
    logger.info(f"Token created successfully for {client.client_id}")

    response.headers["Cache-Control"] = "no-store"     # RFC 6749 5.1
    return TokenResponse(access_token=access_token, token_type="bearer",
                         expires_in=get_jwt_settings()["access_token_expire_minutes"] * 60)


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
