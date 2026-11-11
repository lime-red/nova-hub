"""Management API processing run endpoints"""

import html
from datetime import timedelta
from typing import Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.scope import Scope, get_scope, runs_touching
from backend.core.security import require_admin
from backend.logging_config import get_logger
from backend.core.clock import utcnow
from backend.models.database import League, Packet, ProcessingRun, ProcessingRunFile, SysopUser
from backend.schemas.processing import (
    ProcessingTriggerResponse,
    ProcessingRunSummary,
    ProcessingRunDetail,
    ProcessingRunFile as ProcessingRunFileSchema,
    ProcessingRunPacket,
    ProcessingRunResponse,
)
from backend.services.log_prettify import ansi_to_html, strip_multiple_blank_lines

logger = get_logger(context="management_processing")

router = APIRouter()


def _run_leagues(db: Session, run_ids: List[int],
                 scope: Scope) -> Dict[int, List[Tuple[str, int]]]:
    """The leagues each run processed, as "015B", sorted, in one pass for any
    number of runs. A run takes every league with work in one go, so its own
    league_id has never been set; what it did is the record: the leagues whose
    packets it moved and whose score and listing files it made (the same test
    Scope.run_clause uses). A sysop sees only their own leagues among them."""
    if not run_ids:
        return {}
    pairs = set(
        db.query(Packet.processing_run_id, Packet.league_id)
        .filter(Packet.processing_run_id.in_(run_ids)).distinct().all()
    ) | set(
        db.query(ProcessingRunFile.processing_run_id, ProcessingRunFile.league_id)
        .filter(ProcessingRunFile.processing_run_id.in_(run_ids)).distinct().all()
    )
    league_ids = {lid for _, lid in pairs if lid is not None and scope.sees_league(lid)}
    names = {l.id: l.full_id for l in db.query(League).filter(League.id.in_(league_ids))}
    out: Dict[int, List[Tuple[str, int]]] = {rid: [] for rid in run_ids}
    for rid, lid in pairs:
        if lid in names:
            out[rid].append((names[lid], lid))
    return {rid: sorted(v) for rid, v in out.items()}


RunStatus = Literal["completed", "failed", "running"]


def _runs_query(db: Session, scope: Scope, league_id: Optional[int], days: Optional[int]):
    """The runs a page may show: the caller's scope, then the league and window
    filters. Shared by the list and its summary so the tiles count what the
    list shows."""
    query = db.query(ProcessingRun)
    if not scope.is_admin:
        query = query.filter(scope.run_clause())
    if league_id is not None:
        scope.require_league(league_id)
        query = query.filter(runs_touching([league_id]))
    if days:
        query = query.filter(ProcessingRun.started_at >= utcnow() - timedelta(days=days))
    return query


def _problem_clause():
    return or_(ProcessingRun.status == "failed",
               ProcessingRun.packets_unconsumed > 0,
               ProcessingRun.packets_quarantined > 0)


@router.get("/runs", response_model=List[ProcessingRunResponse], summary="List Processing Runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=200, description="Number of runs to return"),
    before_id: Optional[int] = Query(None, description="Only runs older than this one: "
                                     "the last id of the previous page"),
    league_id: Optional[int] = Query(None, description="Only runs that carried a packet "
                                     "or made a file in this league"),
    status: Optional[RunStatus] = Query(None, description="Only runs with this outcome"),
    problems: bool = Query(False, description="Only runs that failed or left packets "
                           "unconsumed or quarantined"),
    days: Optional[int] = Query(None, ge=1, le=3650, description="Only runs started in "
                                "the last this many days; all when omitted"),
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """
    List processing runs, newest first, a page at a time

    **Query Parameters:**
    - `limit`: Number of runs to return (default: 50, max: 200)
    - `before_id`: For the next page, the last id of this one (keyset paging:
      runs arriving meanwhile do not shift the pages)
    - `league_id`, `status`, `problems`, `days`: filters, all optional

    **Returns:** Processing runs, most recent first. Fewer than `limit` means
    there are no more.

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/processing/runs?league_id=3&problems=true" \\
      -b cookies.txt
    ```
    """
    query = _runs_query(db, scope, league_id, days)
    if before_id is not None:
        query = query.filter(ProcessingRun.id < before_id)
    if status is not None:
        query = query.filter(ProcessingRun.status == status)
    if problems:
        query = query.filter(_problem_clause())
    # By id, not started_at: ids only grow, which is what keyset paging needs,
    # and a run's id and start order are the same.
    runs = query.order_by(ProcessingRun.id.desc()).limit(limit).all()

    leagues_of = _run_leagues(db, [r.id for r in runs], scope)
    run_list = []
    for run in runs:
        duration = None
        if run.completed_at:
            delta = run.completed_at - run.started_at
            duration = f"{max(0, int(delta.total_seconds()))}s"
        leagues = leagues_of.get(run.id, [])

        run_list.append(
            ProcessingRunResponse(
                id=run.id,
                started_at=run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                completed_at=run.completed_at.strftime("%Y-%m-%d %H:%M:%S") if run.completed_at else None,
                duration=duration,
                packets_processed=run.packets_processed,
                packets_unconsumed=run.packets_unconsumed or 0,
                packets_quarantined=run.packets_quarantined or 0,
                status=run.status,
                error_message=run.error_message,
                leagues=[name for name, _ in leagues],
                league_ids=[lid for _, lid in leagues],
                league_name=", ".join(name for name, _ in leagues) or None,
            )
        )

    return run_list


@router.get("/runs/summary", response_model=ProcessingRunSummary,
            summary="Summarise Processing Runs")
async def summarise_runs(
    league_id: Optional[int] = Query(None, description="Only runs in this league"),
    days: Optional[int] = Query(None, ge=1, le=3650, description="Only runs started in "
                                "the last this many days; all when omitted"),
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """
    Count the runs a Processing Runs page would list, by outcome

    Takes the list's league and window filters; the counts are what its status
    and problems filters would then show.

    **Example:**
    ```bash
    curl -X GET "https://hub.example.com/management/api/v1/processing/runs/summary?days=7" \\
      -b cookies.txt
    ```
    """
    query = _runs_query(db, scope, league_id, days)
    by_status = dict(
        query.with_entities(ProcessingRun.status, func.count(ProcessingRun.id))
        .group_by(ProcessingRun.status).all()
    )
    return ProcessingRunSummary(
        total=sum(by_status.values()),
        completed=by_status.get("completed", 0),
        failed=by_status.get("failed", 0),
        running=by_status.get("running", 0),
        problems=query.filter(_problem_clause()).count(),
    )


@router.get("/runs/{run_id}", response_model=ProcessingRunDetail, summary="Get Processing Run Details")
async def get_run(
    run_id: int,
    scope: Scope = Depends(get_scope),
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
    query = db.query(ProcessingRun).filter(ProcessingRun.id == run_id)
    if not scope.is_admin:
        query = query.filter(scope.run_clause())
    run = query.first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Calculate duration
    duration = None
    if run.completed_at:
        delta = run.completed_at - run.started_at
        duration = f"{max(0, int(delta.total_seconds()))}s"

    leagues = [name for name, _ in _run_leagues(db, [run.id], scope)[run.id]]

    # Get packets processed in this run
    packets = db.query(Packet).filter(Packet.processing_run_id == run_id).all()
    # A sysop sees the run's scores, but only their own BBS's packets in it.
    packets = [p for p in packets if scope.sees_packet(p)]

    packet_list = []
    for packet in packets:
        p_league = db.query(League).filter(League.id == packet.league_id).first()
        packet_list.append(
            ProcessingRunPacket(
                filename=packet.filename,
                league_name=p_league.full_id if p_league else "Unknown",
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
        if file.league_id and not scope.sees_league(file.league_id):
            continue
        file_data = None
        file_data_html = None

        if file.file_data:
            file_data = file.file_data
            if file.file_type == "score":
                try:
                    file_data_html = await ansi_to_html(file.file_data)
                except Exception:
                    # The page renders this as HTML; the raw text is game output
                    # carrying names players chose.
                    file_data_html = html.escape(file.file_data)

        file_league_name = None
        if file.league_id:
            file_league = db.query(League).filter(League.id == file.league_id).first()
            if file_league:
                file_league_name = file_league.full_id

        file_schema = ProcessingRunFileSchema(
            id=file.id,
            filename=file.filename,
            file_type=file.file_type,
            league_name=file_league_name,
            file_data=file_data,
            file_data_html=file_data_html,
        )

        if file.file_type == "score":
            score_files.append(file_schema)
        elif file.file_type == "routes":
            routes_files.append(file_schema)
        elif file.file_type == "bbsinfo":
            bbsinfo_files.append(file_schema)

    # Convert ANSI to HTML for dosemu log. The transcript is the hub's
    # troubleshooting record of the whole league's run: admins only.
    dosemu_log_html = None
    if run.dosemu_log and scope.is_admin:
        try:
            dosemu_log_html = await ansi_to_html(run.dosemu_log)
            dosemu_log_html = await strip_multiple_blank_lines(dosemu_log_html)
        except Exception:
            dosemu_log_html = html.escape(run.dosemu_log)

    return ProcessingRunDetail(
        id=run.id,
        started_at=run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
        completed_at=run.completed_at.strftime("%Y-%m-%d %H:%M:%S") if run.completed_at else None,
        duration=duration,
        packets_processed=run.packets_processed,
        packets_unconsumed=run.packets_unconsumed or 0,
        status=run.status,
        leagues=leagues,
        league_name=", ".join(leagues) or None,
        # error_message is where a failed run records why; stderr_log is only ever
        # populated for subprocess plumbing errors and was almost always empty, so
        # the detail view showed nothing for a failure that had a perfectly good
        # explanation recorded next to it.
        error_message=run.error_message or run.stderr_log,
        dosemu_output=run.dosemu_log if scope.is_admin else None,
        dosemu_output_html=dosemu_log_html,
        packets=packet_list,
        score_files=score_files,
        routes_files=routes_files,
        bbsinfo_files=bbsinfo_files,
    )


@router.post("/trigger", response_model=ProcessingTriggerResponse, summary="Trigger Processing")
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


