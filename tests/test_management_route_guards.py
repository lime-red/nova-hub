"""Every Management API route says who may call it.

With sysop sign-up, any provider user can hold a session. So a route that
takes only get_current_user would show a fresh sysop everything. Each route
must depend on require_admin, or on get_scope (and filter by it), or be on
one of the short lists below, which say why.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.routing import APIRoute

from main import management_app
from backend.core.scope import get_scope
from backend.core.security import get_current_user, require_admin

PREFIX = "/api/v1"

# Anyone signed in, about themselves or the hub as a whole.
SELF = {
    ("GET", "/auth/me"),
    ("POST", "/auth/change-password"),
    ("GET", "/system/version"),
}

# No session at all: signing in, and claim and re-link links (the link is the credential).
PUBLIC = {
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/methods"),
    ("GET", "/auth/sso/start"),
    ("GET", "/auth/sso/callback"),
    ("GET", "/claim/{token}"),
    ("POST", "/claim/{token}"),
    ("GET", "/relink/{token}"),
}


def _calls(dependant):
    yield dependant.call
    for sub in dependant.dependencies:
        yield from _calls(sub)


def _routes():
    for route in management_app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                yield method, route.path.removeprefix(PREFIX), set(_calls(route.dependant))


def test_every_route_is_admin_scoped_self_or_public():
    unguarded = []
    for method, path, calls in _routes():
        key = (method, path)
        if key in PUBLIC:
            assert get_current_user not in calls, f"{key} is listed public but needs a session"
        elif key in SELF:
            assert get_current_user in calls, f"{key} is listed self but takes no session"
        elif require_admin not in calls and get_scope not in calls:
            unguarded.append(key)
    assert not unguarded, f"routes with neither require_admin nor get_scope: {sorted(unguarded)}"


def test_the_lists_name_real_routes():
    known = {(m, p) for m, p, _ in _routes()}
    assert (SELF | PUBLIC) <= known, sorted((SELF | PUBLIC) - known)
