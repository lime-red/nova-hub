"""Packet schemas for Nova Hub APIs"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class PacketInfo(BaseModel):
    """Basic packet information"""
    filename: str
    league: str
    game_type: str
    source: str
    dest: str
    sequence: int
    received_at: datetime
    retrieved_at: Optional[datetime] = None
    file_size: int


class PacketListResponse(BaseModel):
    """List of packets"""
    packets: List[PacketInfo]


class PacketUploadResponse(BaseModel):
    """Response from packet upload"""
    status: str
    filename: str
    packet_id: int


class PacketDetail(BaseModel):
    """Detailed packet information (for management)"""
    id: int
    filename: str
    league_id: int
    league_name: str
    game_type: str
    source_bbs_index: str
    dest_bbs_index: str
    sequence_number: int
    file_size: int
    checksum: Optional[str] = None
    uploaded_at: datetime
    downloaded_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    is_processed: bool
    is_downloaded: bool
    has_error: bool
    error_message: Optional[str] = None

    class Config:
        from_attributes = True
