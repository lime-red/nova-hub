"""Re-link links, the sysop's side: public, no session.

GET says which account the link is for and whether it can still be used, and
changes nothing. The link is used by signing in through it: the page sends the
browser to /auth/sso/start?relink=<token>, and the callback binds the identity.
See backend/services/relink_links.py.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy.orm import Session

from backend.api.management.claim import utc
from backend.core.database import get_db
from backend.core.rate_limiter import check_rate_limit, record_failed_attempt
from backend.schemas.claim import RelinkPageStatus
from backend.services import relink_links

router = APIRouter()

TOKEN = Path(..., pattern=r"^[A-Za-z0-9_-]{20,64}$")


@router.get("/{token}", response_model=RelinkPageStatus, summary="Re-link Link Status")
async def relink_status(request: Request, token: str = TOKEN, db: Session = Depends(get_db)):
    """Which hub account this link connects a sign-in to, and whether it still can."""
    check_rate_limit(request)
    link = relink_links.find(db, token)
    if link is None:
        record_failed_attempt(request)
        raise HTTPException(status_code=404, detail="This link is not valid.")
    return RelinkPageStatus(
        username=link.user.username,
        state=relink_links.state(link),
        expires_at=utc(link.expires_at),
        has_sign_in=bool(link.user.idp_subject),
    )
