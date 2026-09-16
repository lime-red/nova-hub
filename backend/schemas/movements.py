"""Schemas for the item-movement view."""
from typing import List, Optional

from pydantic import BaseModel


class Movement(BaseModel):
    """One item the game moved between two nodes."""
    id: int
    processing_run_id: int
    league_id: Optional[int] = None
    league_name: Optional[str] = None
    occurred_at: Optional[str] = None
    direction: str
    item_type: str
    src_node: Optional[int] = None
    dst_node: Optional[int] = None
    size_before: Optional[int] = None
    size_after: Optional[int] = None
    phase: Optional[str] = None

    class Config:
        from_attributes = True


class TypeCount(BaseModel):
    direction: str
    item_type: str
    count: int


class PairCount(BaseModel):
    src_node: Optional[int] = None
    dst_node: Optional[int] = None
    count: int


class DayCount(BaseModel):
    day: str
    count: int


class MovementSummary(BaseModel):
    """The shape of a league's traffic, for judging by eye.

    No thresholds and no verdict: what a healthy week looks like depends on how
    many boards are playing and how active they are, which the hub has no way to
    know. A sysop reading their own league does.
    """
    since: str
    total: int
    runs: int
    by_type: List[TypeCount] = []
    by_pair: List[PairCount] = []
    by_day: List[DayCount] = []
    quiet_nodes: List[int] = []
