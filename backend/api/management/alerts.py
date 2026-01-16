"""Management API alert management endpoints"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user, require_admin
from backend.logging_config import get_logger
from backend.models.database import League, SequenceAlert, SysopUser
from backend.schemas.alerts import AlertResponse

logger = get_logger(context="management_alerts")

router = APIRouter()


@router.get("", response_model=List[AlertResponse], summary="List All Alerts")
async def list_alerts(
    resolved: bool = Query(None, description="Filter by resolved status (true/false/none for all)"),
    limit: int = Query(100, ge=1, le=500, description="Number of alerts to return"),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List all alerts (sequence gaps)

    **Query Parameters:**
    - `resolved`: Filter by resolved status (omit for all, true/false for filtered)
    - `limit`: Number of alerts to return (default: 100, max: 500)

    **Returns:** List of alerts ordered by unresolved first, then by date

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/alerts?resolved=false" \\
      -b cookies.txt
    ```
    """
    query = db.query(SequenceAlert)

    # Filter by resolved status if specified
    if resolved is not None:
        if resolved:
            query = query.filter(SequenceAlert.resolved_at != None)
        else:
            query = query.filter(SequenceAlert.resolved_at == None)

    # Order by unresolved first, then by date
    alerts = (
        query.order_by(
            SequenceAlert.resolved_at.asc().nullsfirst(),
            SequenceAlert.detected_at.desc(),
        )
        .limit(limit)
        .all()
    )

    alert_list = []
    for alert in alerts:
        league = db.query(League).filter(League.id == alert.league_id).first()
        league_name = f"{league.game_type} {league.league_id}" if league else "Unknown"

        alert_list.append(
            AlertResponse(
                id=alert.id,
                league_id=alert.league_id,
                league_name=league_name,
                source_bbs_index=alert.source_bbs_index,
                dest_bbs_index=alert.dest_bbs_index,
                expected_sequence=alert.expected_sequence,
                received_sequence=alert.received_sequence,
                gap_size=alert.gap_size,
                is_resolved=alert.resolved_at is not None,
                detected_at=alert.detected_at,
                resolved_at=alert.resolved_at,
            )
        )

    return alert_list


@router.get("/{alert_id}", response_model=AlertResponse, summary="Get Alert Details")
async def get_alert(
    alert_id: int,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get details about a specific alert

    **Path Parameters:**
    - `alert_id`: Database ID of the alert

    **Returns:** Alert details

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/alerts/1" \\
      -b cookies.txt
    ```
    """
    alert = db.query(SequenceAlert).filter(SequenceAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    league = db.query(League).filter(League.id == alert.league_id).first()
    league_name = f"{league.game_type} {league.league_id}" if league else "Unknown"

    return AlertResponse(
        id=alert.id,
        league_id=alert.league_id,
        league_name=league_name,
        source_bbs_index=alert.source_bbs_index,
        dest_bbs_index=alert.dest_bbs_index,
        expected_sequence=alert.expected_sequence,
        received_sequence=alert.received_sequence,
        gap_size=alert.gap_size,
        is_resolved=alert.resolved_at is not None,
        detected_at=alert.detected_at,
        resolved_at=alert.resolved_at,
    )


@router.post("/{alert_id}/resolve", summary="Resolve Alert")
async def resolve_alert(
    alert_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Mark an alert as resolved (admin only)

    **Path Parameters:**
    - `alert_id`: Database ID of the alert

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/alerts/1/resolve" \\
      -b cookies.txt
    ```
    """
    alert = db.query(SequenceAlert).filter(SequenceAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.resolved_at:
        raise HTTPException(status_code=400, detail="Alert is already resolved")

    alert.resolved_at = datetime.now()
    db.commit()

    logger.info(f"Alert {alert_id} resolved by {current_user.username}")

    return {"message": "Alert resolved successfully"}


@router.post("/{alert_id}/unresolve", summary="Unresolve Alert")
async def unresolve_alert(
    alert_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Mark an alert as unresolved (admin only)

    **Path Parameters:**
    - `alert_id`: Database ID of the alert

    **Returns:** Success message

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/alerts/1/unresolve" \\
      -b cookies.txt
    ```
    """
    alert = db.query(SequenceAlert).filter(SequenceAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if not alert.resolved_at:
        raise HTTPException(status_code=400, detail="Alert is not resolved")

    alert.resolved_at = None
    db.commit()

    logger.info(f"Alert {alert_id} unresolved by {current_user.username}")

    return {"message": "Alert marked as unresolved"}
