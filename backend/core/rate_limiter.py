"""
In-memory auth rate limiter for Nova Hub

Tracks failed authentication attempts per IP address and enforces
a temporary lockout after exceeding the configured threshold.
"""

import time
from collections import defaultdict
from threading import Lock
from typing import Dict, List

from fastapi import HTTPException, Request, status

from backend.core.config import get_config
from backend.logging_config import get_logger

logger = get_logger(context="rate_limiter")

# Per-IP tracking: IP -> list of attempt timestamps
_attempts: Dict[str, List[float]] = defaultdict(list)
# Per-IP lockout expiry: IP -> lockout_until timestamp
_lockouts: Dict[str, float] = {}
_lock = Lock()


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For if set by trusted proxy."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def record_failed_attempt(request: Request) -> None:
    """Record a failed auth attempt for the requesting IP."""
    config = get_config()
    if not config.rate_limiting.enabled:
        return

    ip = _get_client_ip(request)
    now = time.monotonic()
    window = 60.0  # 1 minute sliding window
    max_attempts = config.rate_limiting.auth_attempts_per_minute
    lockout_duration = config.rate_limiting.auth_lockout_seconds

    with _lock:
        # Prune old attempts outside the window
        _attempts[ip] = [t for t in _attempts[ip] if now - t < window]
        _attempts[ip].append(now)

        if len(_attempts[ip]) >= max_attempts:
            _lockouts[ip] = now + lockout_duration
            _attempts[ip] = []
            logger.warning(
                f"Auth lockout applied for IP {ip} "
                f"({max_attempts} attempts in {window}s, locked for {lockout_duration}s)"
            )


def check_rate_limit(request: Request) -> None:
    """
    Raise HTTP 429 if the requesting IP is currently locked out.

    Call this at the start of auth endpoints before processing credentials.
    """
    config = get_config()
    if not config.rate_limiting.enabled:
        return

    ip = _get_client_ip(request)
    now = time.monotonic()

    with _lock:
        lockout_until = _lockouts.get(ip)
        if lockout_until and now < lockout_until:
            retry_after = int(lockout_until - now)
            logger.info(f"Blocked auth attempt from locked-out IP {ip} (retry after {retry_after}s)")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed authentication attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        elif lockout_until and now >= lockout_until:
            # Lockout expired
            del _lockouts[ip]
