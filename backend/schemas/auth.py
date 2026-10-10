"""Authentication schemas for Nova Hub APIs"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


# --- Service API (OAuth2) ---

class TokenResponse(BaseModel):
    """OAuth2 token response"""
    access_token: str
    token_type: str = "bearer"


class ClientVerifyResponse(BaseModel):
    """Response for token verification"""
    client_id: str
    bbs_name: str
    is_active: bool


# --- Management API (Session) ---

class LoginRequest(BaseModel):
    """Login request for management UI"""
    username: str
    password: str


class OwnedClient(BaseModel):
    """A BBS the user owns"""
    id: int
    bbs_name: str


class UserResponse(BaseModel):
    """User information response"""
    id: int
    username: str
    is_admin: bool = False
    email: Optional[str] = None
    full_name: Optional[str] = None
    has_password: bool = False  # can use the local login
    sso_linked: bool = False  # can sign in through the identity provider
    owned_clients: List[OwnedClient] = []
    created_at: Optional[str] = None
    last_login: Optional[str] = None

    class Config:
        from_attributes = True


class AuthMethods(BaseModel):
    """What the login page offers"""
    password: bool
    sso: bool


class LoginResponse(BaseModel):
    """Login response"""
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    """Password change request"""
    current_password: str
    new_password: str
    confirm_password: str


class UserCreate(BaseModel):
    """User creation request"""
    username: str
    password: str
    is_admin: Optional[bool] = False


class UserUpdate(BaseModel):
    """User update request"""
    username: Optional[str] = None
    password: Optional[str] = None
    is_admin: Optional[bool] = None
