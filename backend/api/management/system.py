"""Management API system information"""

from fastapi import APIRouter, Depends

from backend.core.security import get_current_user
from backend.core.version import version_info
from backend.models.database import SysopUser

router = APIRouter()


@router.get("/version", summary="Build Information")
async def get_version(current_user: SysopUser = Depends(get_current_user)):
    """
    What build is running.

    The admin UI shows this in the sidebar, because otherwise the only place a
    version appears is the login screen and you have to log out to read it.

    **Returns:**
    - `version`: release version string
    - `revision`: short git commit, or null if the tree has no git metadata
    - `released_at`: date of that commit (YYYY-MM-DD), or null
    - `dirty`: true when the deployed tree has uncommitted changes
    """
    return version_info()
