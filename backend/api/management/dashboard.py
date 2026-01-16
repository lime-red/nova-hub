"""Management API dashboard endpoints"""

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.logging_config import get_logger
from backend.models.database import League, SequenceAlert, SysopUser
from backend.schemas.dashboard import (
    ActivityItem,
    AlertSummary,
    ChartData,
    DashboardResponse,
    DashboardStats,
)
from backend.services.stats_service import StatsService

logger = get_logger(context="management_dashboard")

router = APIRouter()


@router.get("/stats", response_model=DashboardStats, summary="Get Dashboard Stats")
async def get_dashboard_stats(
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get dashboard statistics

    **Returns:** Core statistics for dashboard cards

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/dashboard/stats" \\
      -b cookies.txt
    ```
    """
    stats_service = StatsService(db)
    stats = stats_service.get_dashboard_stats()

    return DashboardStats(
        total_packets=stats.get("total_packets", 0),
        packets_24h=stats.get("packets_24h", 0),
        active_clients=stats.get("active_clients", 0),
        active_leagues=stats.get("active_leagues", 0),
        pending_alerts=stats.get("pending_alerts", 0),
        processing_runs_24h=stats.get("processing_runs_24h", 0),
    )


@router.get("/activity", response_model=List[ActivityItem], summary="Get Recent Activity")
async def get_recent_activity(
    limit: int = Query(10, ge=1, le=100, description="Number of items to return"),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get recent activity feed

    **Query Parameters:**
    - `limit`: Number of items to return (default: 10, max: 100)

    **Returns:** List of recent activity items

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/dashboard/activity?limit=20" \\
      -b cookies.txt
    ```
    """
    stats_service = StatsService(db)
    activity = stats_service.get_recent_activity(limit=limit)

    return [
        ActivityItem(
            id=item.get("id", 0),
            type=item.get("type", "unknown"),
            description=item.get("description", ""),
            timestamp=item.get("timestamp", ""),
            client_name=item.get("client_name"),
            filename=item.get("filename"),
            league_name=item.get("league_name"),
            route=item.get("route"),
            details=item.get("details"),
        )
        for item in activity
    ]


@router.get("/alerts", response_model=List[AlertSummary], summary="Get Active Alerts")
async def get_active_alerts(
    limit: int = Query(5, ge=1, le=50, description="Number of alerts to return"),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get active (unresolved) alerts for dashboard

    **Query Parameters:**
    - `limit`: Number of alerts to return (default: 5, max: 50)

    **Returns:** List of active alerts

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/dashboard/alerts?limit=10" \\
      -b cookies.txt
    ```
    """
    stats_service = StatsService(db)

    alerts = (
        db.query(SequenceAlert)
        .filter(SequenceAlert.resolved_at == None)
        .order_by(SequenceAlert.detected_at.desc())
        .limit(limit)
        .all()
    )

    alert_list = []
    for alert in alerts:
        league = db.query(League).filter(League.id == alert.league_id).first()
        league_name = f"{league.game_type} {league.league_id}" if league else "Unknown"

        alert_list.append(
            AlertSummary(
                id=alert.id,
                league_name=league_name,
                source=alert.source_bbs_index,
                dest=alert.dest_bbs_index,
                expected_sequence=alert.expected_sequence,
                detected_at=stats_service.format_timestamp(alert.detected_at),
            )
        )

    return alert_list


@router.get("/charts/activity", response_model=ChartData, summary="Get Activity Chart Data")
async def get_activity_chart(
    hours: int = Query(24, ge=1, le=168, description="Number of hours to include"),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get activity chart data (packets per hour)

    **Query Parameters:**
    - `hours`: Number of hours to include (default: 24, max: 168 = 1 week)

    **Returns:** Chart labels and data arrays

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/dashboard/charts/activity?hours=48" \\
      -b cookies.txt
    ```
    """
    stats_service = StatsService(db)
    labels, data = stats_service.get_activity_chart_data(hours=hours)

    return ChartData(
        labels=labels,
        data=data,
    )


@router.get("/charts/leagues", response_model=ChartData, summary="Get League Distribution Chart")
async def get_league_chart(
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get league distribution chart data (packets by league)

    **Returns:** Chart labels (league names) and data (packet counts)

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/dashboard/charts/leagues" \\
      -b cookies.txt
    ```
    """
    stats_service = StatsService(db)
    labels, data = stats_service.get_league_distribution()

    return ChartData(
        labels=labels,
        data=data,
    )


@router.get("", response_model=DashboardResponse, summary="Get Full Dashboard Data")
async def get_dashboard(
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get all dashboard data in a single request

    **Returns:** Stats, activity, alerts, and chart data

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/dashboard" \\
      -b cookies.txt
    ```
    """
    stats_service = StatsService(db)

    # Get stats
    stats = stats_service.get_dashboard_stats()

    # Get activity
    activity = stats_service.get_recent_activity(limit=10)

    # Get alerts
    alerts = (
        db.query(SequenceAlert)
        .filter(SequenceAlert.resolved_at == None)
        .order_by(SequenceAlert.detected_at.desc())
        .limit(5)
        .all()
    )

    alert_list = []
    for alert in alerts:
        league = db.query(League).filter(League.id == alert.league_id).first()
        league_name = f"{league.game_type} {league.league_id}" if league else "Unknown"
        alert_list.append(
            AlertSummary(
                id=alert.id,
                league_name=league_name,
                source=alert.source_bbs_index,
                dest=alert.dest_bbs_index,
                expected_sequence=alert.expected_sequence,
                detected_at=stats_service.format_timestamp(alert.detected_at),
            )
        )

    # Get chart data
    activity_labels, activity_data = stats_service.get_activity_chart_data(hours=24)
    league_labels, league_data = stats_service.get_league_distribution()

    return DashboardResponse(
        stats=DashboardStats(
            total_packets=stats.get("total_packets", 0),
            packets_24h=stats.get("packets_24h", 0),
            active_clients=stats.get("active_clients", 0),
            active_leagues=stats.get("active_leagues", 0),
            pending_alerts=stats.get("pending_alerts", 0),
            processing_runs_24h=stats.get("processing_runs_24h", 0),
        ),
        activity=[
            ActivityItem(
                id=item.get("id", 0),
                type=item.get("type", "unknown"),
                description=item.get("description", ""),
                timestamp=item.get("timestamp", ""),
                client_name=item.get("client_name"),
                filename=item.get("filename"),
                league_name=item.get("league_name"),
                route=item.get("route"),
                details=item.get("details"),
            )
            for item in activity
        ],
        alerts=alert_list,
        activity_chart=ChartData(labels=activity_labels, data=activity_data),
        league_chart=ChartData(labels=league_labels, data=league_data),
    )
