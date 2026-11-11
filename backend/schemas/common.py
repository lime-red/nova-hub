"""Common schemas for Management API"""

from pydantic import BaseModel


class MessageResponse(BaseModel):
    message: str
