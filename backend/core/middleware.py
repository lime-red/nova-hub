"""Request-scoped middleware for Nova Hub"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.logging_config import current_request_user
from backend.core.security import COOKIE_NAME


class AuthContextMiddleware(BaseHTTPMiddleware):
    """
    Sets the authenticated user in the logging context variable for each request.

    Reads the JWT from the management session cookie (management API) or the
    Authorization Bearer header (service API) and populates
    `current_request_user` so all log messages emitted during the request
    automatically include the user's identity.
    """

    async def dispatch(self, request: Request, call_next):
        username = "-"

        # Try management cookie first
        token = request.cookies.get(COOKIE_NAME)

        # Fall back to Authorization header (service API)
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        if token:
            try:
                from backend.core.config import get_config
                from jose import jwt as jose_jwt

                config = get_config()
                payload = jose_jwt.decode(
                    token,
                    config.security.jwt_secret,
                    algorithms=["HS256"],
                    options={"verify_exp": False},  # expiry checked by security layer
                )
                sub = payload.get("sub")
                if sub:
                    username = sub
            except Exception:
                pass  # Token invalid — leave as "-"

        token = current_request_user.set(username)
        try:
            response = await call_next(request)
        finally:
            current_request_user.reset(token)

        return response
