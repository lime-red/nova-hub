"""Game image schemas for the Management API"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ImageInfo(BaseModel):
    """A golden image the hub holds"""
    id: int
    dirname: str  # its folder in [games] images, as bre-sj1-r2
    game_type: str  # B, F or I
    version: str
    source: Optional[str] = None  # what it was taken from: upload, fetch, a file or path
    made_from: Optional[str] = None  # the media or binary it was built from, per its manifest
    recipe: Optional[int] = None  # make_golden.py's recipe version, per its manifest
    created_at: Optional[datetime] = None
    leagues: List[str] = []  # the leagues whose game was made from it, as 015I


class WaitingArchive(BaseModel):
    """An image archive dropped into [games] images, not yet imported"""
    name: str
    size: int


class ImagesResponse(BaseModel):
    """The hub's images, the archives waiting, and where it can fetch from"""
    images: List[ImageInfo]
    waiting: List[WaitingArchive]
    folder: str  # [games] images
    fetch_from: Optional[str] = None  # [games] image_url; None if the hub cannot fetch
    fetch_problems: List[str] = []  # why a fetch would be refused, if anything


class ImageName(BaseModel):
    """An image archive, by file name"""
    name: str = Field(min_length=1, max_length=120)


class ImageImported(BaseModel):
    """The result of taking an image in"""
    image: ImageInfo
    created: bool  # False: the hub already had this exact image


class ImageCheck(BaseModel):
    """An image's files against its manifest"""
    dirname: str
    problems: List[str]  # empty: every file and folder matches
