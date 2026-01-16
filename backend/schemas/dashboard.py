"""Dashboard schemas for Management API"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class DashboardStats(BaseModel):
    """Dashboard statistics"""
    total_packets: int = 0
    packets_24h: int = 0
    active_clients: int = 0
    active_leagues: int = 0
    pending_alerts: int = 0
    processing_runs_24h: int = 0


class ActivityItem(BaseModel):
    """Activity feed item"""
    id: int
    type: str  # "upload", "download", "processing", etc.
    description: str
    timestamp: str
    client_name: Optional[str] = None
    filename: Optional[str] = None
    league_name: Optional[str] = None
    route: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class AlertSummary(BaseModel):
    """Alert summary for dashboard"""
    id: int
    league_name: str
    source: str
    dest: str
    expected_sequence: int
    detected_at: str


class ChartData(BaseModel):
    """Chart data response"""
    labels: List[str]
    data: List[int]


class DashboardResponse(BaseModel):
    """Complete dashboard response"""
    stats: DashboardStats
    activity: List[ActivityItem]
    alerts: List[AlertSummary]
    activity_chart: ChartData
    league_chart: ChartData
