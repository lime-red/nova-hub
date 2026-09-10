"""Processing run schemas for Management API"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class ProcessingRunResponse(BaseModel):
    """Processing run response"""
    id: int
    started_at: str
    completed_at: Optional[str] = None
    duration: Optional[str] = None
    packets_processed: int = 0
    # Non-zero means the game was handed packets it never ingested -- almost always
    # a bad inbound path in its BBS.CFG. They stay put and retry next run.
    packets_unconsumed: int = 0
    status: str  # "running", "completed", "failed"
    error_message: Optional[str] = None
    league_name: Optional[str] = None

    class Config:
        from_attributes = True


class ProcessingRunPacket(BaseModel):
    """Packet processed in a run"""
    filename: str
    league_name: str
    route: str


class ProcessingRunFile(BaseModel):
    """File from processing run"""
    id: int
    filename: str
    file_type: str  # "score", "routes", "bbsinfo"
    league_name: Optional[str] = None  # e.g. "B 555" — identifies source league
    file_data: Optional[str] = None
    file_data_html: Optional[str] = None  # Converted ANSI to HTML

    class Config:
        from_attributes = True


class ProcessingRunDetail(BaseModel):
    """Detailed processing run with logs and files"""
    id: int
    started_at: str
    completed_at: Optional[str] = None
    duration: Optional[str] = None
    packets_processed: int = 0
    packets_unconsumed: int = 0
    status: str
    league_name: Optional[str] = None
    error_message: Optional[str] = None
    dosemu_output: Optional[str] = None
    dosemu_output_html: Optional[str] = None
    packets: List[ProcessingRunPacket] = []
    score_files: List[ProcessingRunFile] = []
    routes_files: List[ProcessingRunFile] = []
    bbsinfo_files: List[ProcessingRunFile] = []

    class Config:
        from_attributes = True
