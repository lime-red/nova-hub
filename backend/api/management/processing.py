"""Management API processing run endpoints"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user, require_admin
from backend.logging_config import get_logger
from backend.models.database import League, Packet, ProcessingRun, ProcessingRunFile, SysopUser
from backend.schemas.processing import (
    ProcessingRunDetail,
    ProcessingRunFile as ProcessingRunFileSchema,
    ProcessingRunPacket,
    ProcessingRunResponse,
)
from backend.services.log_prettify import ansi_to_html, strip_multiple_blank_lines

logger = get_logger(context="management_processing")

router = APIRouter()


@router.get("/runs", response_model=List[ProcessingRunResponse], summary="List Processing Runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=200, description="Number of runs to return"),
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List processing runs

    **Query Parameters:**
    - `limit`: Number of runs to return (default: 50, max: 200)

    **Returns:** List of processing runs ordered by most recent first

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/processing/runs?limit=20" \\
      -b cookies.txt
    ```
    """
    runs = (
        db.query(ProcessingRun)
        .order_by(ProcessingRun.started_at.desc())
        .limit(limit)
        .all()
    )

    run_list = []
    for run in runs:
        duration = None
        if run.completed_at:
            delta = run.completed_at - run.started_at
            duration = f"{delta.seconds}s"

        # Get league info if available
        league_name = None
        if run.league_id:
            league = db.query(League).filter(League.id == run.league_id).first()
            if league:
                league_name = f"{league.game_type} {league.league_id}"

        run_list.append(
            ProcessingRunResponse(
                id=run.id,
                started_at=run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                completed_at=run.completed_at.strftime("%Y-%m-%d %H:%M:%S") if run.completed_at else None,
                duration=duration,
                packets_processed=run.packets_processed,
                status=run.status,
                league_name=league_name,
            )
        )

    return run_list


@router.get("/runs/{run_id}", response_model=ProcessingRunDetail, summary="Get Processing Run Details")
async def get_run(
    run_id: int,
    current_user: SysopUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get detailed information about a specific processing run

    **Path Parameters:**
    - `run_id`: Database ID of the processing run

    **Returns:** Run details including packets processed, generated files, and logs

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/processing/runs/1" \\
      -b cookies.txt
    ```
    """
    run = db.query(ProcessingRun).filter(ProcessingRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Calculate duration
    duration = None
    if run.completed_at:
        delta = run.completed_at - run.started_at
        duration = f"{delta.seconds}s"

    # Get league info
    league_name = None
    if run.league_id:
        league = db.query(League).filter(League.id == run.league_id).first()
        if league:
            league_name = f"{league.game_type} {league.league_id}"

    # Get packets processed in this run
    packets = db.query(Packet).filter(Packet.processing_run_id == run_id).all()

    packet_list = []
    for packet in packets:
        p_league = db.query(League).filter(League.id == packet.league_id).first()
        packet_list.append(
            ProcessingRunPacket(
                filename=packet.filename,
                league_name=f"{p_league.game_type} {p_league.league_id}" if p_league else "Unknown",
                route=f"{packet.source_bbs_index} -> {packet.dest_bbs_index}",
            )
        )

    # Get files generated in this run
    files = db.query(ProcessingRunFile).filter(ProcessingRunFile.processing_run_id == run_id).all()

    # Group files by type and convert ANSI to HTML for score files
    score_files = []
    routes_files = []
    bbsinfo_files = []

    for file in files:
        file_data = None
        file_data_html = None

        if file.file_data:
            file_data = file.file_data
            if file.file_type == "score":
                try:
                    file_data_html = await ansi_to_html(file.file_data)
                except Exception:
                    file_data_html = file.file_data

        file_schema = ProcessingRunFileSchema(
            id=file.id,
            filename=file.filename,
            file_type=file.file_type,
            file_data=file_data,
            file_data_html=file_data_html,
        )

        if file.file_type == "score":
            score_files.append(file_schema)
        elif file.file_type == "routes":
            routes_files.append(file_schema)
        elif file.file_type == "bbsinfo":
            bbsinfo_files.append(file_schema)

    # Convert ANSI to HTML for dosemu log
    dosemu_log_html = None
    if run.dosemu_log:
        try:
            dosemu_log_html = await ansi_to_html(run.dosemu_log)
            dosemu_log_html = await strip_multiple_blank_lines(dosemu_log_html)
        except Exception:
            dosemu_log_html = run.dosemu_log

    return ProcessingRunDetail(
        id=run.id,
        started_at=run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
        completed_at=run.completed_at.strftime("%Y-%m-%d %H:%M:%S") if run.completed_at else None,
        duration=duration,
        packets_processed=run.packets_processed,
        status=run.status,
        league_name=league_name,
        error_message=run.stderr_log,
        dosemu_output=run.dosemu_log,
        dosemu_output_html=dosemu_log_html,
        packets=packet_list,
        score_files=score_files,
        routes_files=routes_files,
        bbsinfo_files=bbsinfo_files,
    )


@router.post("/trigger", summary="Trigger Processing")
async def trigger_processing(
    current_user: SysopUser = Depends(require_admin),
):
    """
    Manually trigger packet processing (admin only)

    **Returns:** Success/error status

    **Example:**
    ```bash
    curl -X POST "https://hub.example.com/management/api/v1/processing/trigger" \\
      -b cookies.txt
    ```
    """
    from backend.services.processing_service import trigger_processing as do_trigger

    try:
        do_trigger()
        logger.info(f"Processing triggered manually by {current_user.username}")
        return {"status": "success", "message": "Processing triggered successfully"}
    except Exception as e:
        logger.error(f"Failed to trigger processing: {e}")
        return {"status": "error", "message": str(e)}
