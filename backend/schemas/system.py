"""System schemas for Management API"""

from typing import Optional
from pydantic import BaseModel


class VersionResponse(BaseModel):
    version: str
    revision: Optional[str] = None
    released_at: Optional[str] = None
    dirty: bool = False
