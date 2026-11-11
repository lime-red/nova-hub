"""Processing run schemas for Management API"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


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
    leagues: List[str] = Field(
        [], description="The leagues the run processed, e.g. ['015B', '015F']: those whose "
                        "packets it moved or whose score and listing files it made")
    league_name: Optional[str] = Field(None, description="The same leagues, comma-separated")
    league_ids: List[int] = Field(
        [], description="The same leagues' database ids, in the same order, for links")
    packets_quarantined: int = 0

    model_config = ConfigDict(from_attributes=True)


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
    league_name: Optional[str] = None  # the league it came from, e.g. "555B"
    file_data: Optional[str] = None
    file_data_html: Optional[str] = None  # Converted ANSI to HTML

    model_config = ConfigDict(from_attributes=True)


class ProcessingRunDetail(BaseModel):
    """Detailed processing run with logs and files"""
    id: int
    started_at: str
    completed_at: Optional[str] = None
    duration: Optional[str] = None
    packets_processed: int = 0
    packets_unconsumed: int = 0
    status: str
    leagues: List[str] = []
    league_name: Optional[str] = None
    error_message: Optional[str] = None
    dosemu_output: Optional[str] = None
    dosemu_output_html: Optional[str] = None
    packets: List[ProcessingRunPacket] = []
    score_files: List[ProcessingRunFile] = []
    routes_files: List[ProcessingRunFile] = []
    bbsinfo_files: List[ProcessingRunFile] = []

    model_config = ConfigDict(from_attributes=True)


class ProcessingRunSummary(BaseModel):
    """How many runs in a window, by outcome, for the console's tiles. Counts
    honour the league and window filters, not the status ones, so a tile says
    what clicking it would show."""
    total: int
    completed: int
    failed: int
    running: int
    problems: int = Field(..., description="Failed, or left packets unconsumed or quarantined")



class ProcessingTriggerResponse(BaseModel):
    """Response from manually triggering packet processing"""
    status: str
    message: str
