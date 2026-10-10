"""
backend/services/identity_provider.py

Who a sysop is, according to someone else.

The hub does not keep sysop passwords or send email. A sysop signs in through
an identity provider (WorkOS AuthKit: email code, password, Discord, GitHub),
which hands back a stable subject ID and a verified email. The hub then sets
its own session cookie, so nothing past the callback knows a provider exists.

The interface is two steps: where to send the browser to sign in, and turning
the code it comes back with into an Identity. Moving to another provider means
another class here and a config section, nothing else.

WorkOSProvider talks to the AuthKit HTTP API directly with httpx rather than
through the WorkOS SDK: these two calls are all the hub needs, and the SDK has
had four major versions in 2026 and brings its own HTTP stack. The same code
runs against the local emulator (`npx workos emulate`), which serves the same
endpoints; see tests/test_identity_emulator.py.
"""

from dataclasses import dataclass
from typing import Optional, Protocol
from urllib.parse import urlencode

import httpx


@dataclass(frozen=True)
class Identity:
    subject: str  # the provider's user ID; never changes for a person
    email: str
    email_verified: bool
    name: Optional[str] = None


class IdentityError(Exception):
    """The provider refused the sign-in, or could not be reached."""


class IdentityProvider(Protocol):
    name: str

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """Where to send the browser to sign in."""

    async def finish(self, code: str) -> Identity:
        """The identity behind the code the browser came back with."""


class WorkOSProvider:
    name = "workos"

    def __init__(self, client_id: str, api_key: str, api_base: str = "https://api.workos.com"):
        self.client_id = client_id
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        query = urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "provider": "authkit",
            "state": state,
        })
        return f"{self.api_base}/user_management/authorize?{query}"

    async def finish(self, code: str) -> Identity:
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                response = await http.post(
                    f"{self.api_base}/user_management/authenticate",
                    json={
                        "client_id": self.client_id,
                        "client_secret": self.api_key,
                        "grant_type": "authorization_code",
                        "code": code,
                    },
                )
        except httpx.HTTPError as e:
            raise IdentityError(f"WorkOS could not be reached: {e}") from e
        if response.status_code != 200:
            raise IdentityError(f"WorkOS refused the sign-in code ({response.status_code}): "
                                f"{response.text[:200]}")
        user = response.json()["user"]
        name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)
        return Identity(
            subject=user["id"],
            email=user["email"],
            email_verified=bool(user.get("email_verified")),
            name=name or None,
        )


class FakeIdentityProvider:
    """For tests: signs in whoever `identity` says, with no network.

    The authorization URL points straight back at the callback with a code, as
    a provider would after a successful sign-in. A code other than the one it
    handed out is refused, as is any code when `identity` is None.
    """

    name = "fake"
    CODE = "fake-code"

    def __init__(self, identity: Optional[Identity] = None):
        self.identity = identity

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        return f"{redirect_uri}?{urlencode({'code': self.CODE, 'state': state})}"

    async def finish(self, code: str) -> Identity:
        if code != self.CODE or self.identity is None:
            raise IdentityError("fake provider: sign-in refused")
        return self.identity


def get_identity_provider() -> Optional[IdentityProvider]:
    """The configured provider, or None when sign-in through one is off.

    A FastAPI dependency, so tests override it with a FakeIdentityProvider.
    """
    from backend.core.config import get_config

    settings = get_config().get("identity", {}) or {}
    provider = settings.get("provider", "")
    if not provider:
        return None
    if provider == "workos":
        if not settings.get("client_id") or not settings.get("api_key"):
            raise RuntimeError("[identity] provider = 'workos' needs client_id and api_key")
        return WorkOSProvider(
            client_id=settings["client_id"],
            api_key=settings["api_key"],
            api_base=settings.get("api_base", "https://api.workos.com"),
        )
    raise RuntimeError(f"[identity] provider {provider!r} is not one the hub knows")
