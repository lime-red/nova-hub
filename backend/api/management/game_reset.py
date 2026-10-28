"""A league's game reset: the form, with every setting's help; the league's one
saved draft; performing it; and the league's reset history. See
backend/services/game_reset/."""
import os
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import require_admin
from backend.logging_config import get_logger
from backend.core.config import get_config
from backend.models.database import (
    GameReset, League, LeagueGameSettings, LeagueMembership, Packet, SysopUser,
)
from backend.schemas.game_reset import (
    GameResetForm, HubTimezone, ObservedSettings, ResetApplied, ResetChoice, ResetDetail,
    ResetDraft, ResetDraftSave, ResetField, ResetMember, ResetPerform, ResetStep,
    ResetSummary, ResetWarning,
)
from backend.services import audit
from backend.services.game_reset import drafts, history, perform
from backend.services.game_reset.fields import defaults, fields_for

router = APIRouter()
logger = get_logger(context="management_game_reset")


def _iso(t: Optional[datetime]) -> Optional[str]:
    return t.isoformat() if t else None


def _league(db: Session, league_id: int) -> League:
    league = db.query(League).filter(League.id == league_id).first()
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")
    try:
        fields_for(league.game_type)
    except ValueError:
        # Immortal Barons resets through its own Coordinator orders, not BRE's
        # and FE's RESET editor.
        raise HTTPException(status_code=404,
                            detail=f"League {league.full_id} has no reset editor")
    return league


def _draft(d, game_type: str) -> Optional[ResetDraft]:
    if d is None:
        return None
    return ResetDraft(id=d.id, version=d.version, settings=d.settings,
                      warnings=[ResetWarning(field=f, message=m)
                                for f, m in drafts.warnings(game_type, d.settings)],
                      notes=d.notes,
                      created_by=d.created_by, created_at=_iso(d.created_at),
                      updated_by=d.updated_by, updated_at=_iso(d.updated_at))


def _summary(r: GameReset, ended: Optional[datetime] = None) -> dict:
    sent = r.sent_config or {}
    wrong = sent.get("mismatches", [])
    return dict(
        id=r.id, status=r.status, source=r.source, settings=r.settings, notes=r.notes,
        applied_by=r.applied_by, applied_at=_iso(r.applied_at),
        game_start=_iso(r.game_start), ended=_iso(ended), error=r.error,
        confirmed=None if r.source != "console" or not sent else not wrong,
        mismatches=wrong)


def _members(db: Session, league: League, run_id: Optional[int]) -> List[ResetMember]:
    if run_id is None:
        return []
    names = {f"{m.bbs_index:02X}": m.client.bbs_name for m in
             db.query(LeagueMembership).filter(LeagueMembership.league_id == league.id)
             if m.bbs_index is not None and m.client is not None}
    packets = (db.query(Packet)
               .filter(Packet.league_id == league.id, Packet.processing_run_id == run_id)
               .order_by(Packet.dest_bbs_index).all())
    return [ResetMember(bbs_index=p.dest_bbs_index, bbs_name=names.get(p.dest_bbs_index.upper()),
                        filename=p.filename, downloaded_at=_iso(p.downloaded_at))
            for p in packets]


def _detail(db: Session, league: League, r: GameReset,
            ended: Optional[datetime] = None) -> ResetDetail:
    return ResetDetail(
        **_summary(r, ended),
        progress=[ResetStep(**p) for p in r.progress or []],
        warnings=[ResetWarning(field=f, message=m)
                  for f, m in drafts.warnings(league.game_type, r.settings)]
        if r.source == "console" else [],
        transcript=r.transcript, backup_path=r.backup_path,
        processing_run_id=r.processing_run_id, sent_config=r.sent_config,
        members=_members(db, league, r.processing_run_id))


def _hub_timezone() -> HubTimezone:
    now = datetime.now().astimezone()
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        try:
            target = str(Path("/etc/localtime").resolve())
            name = target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""
        except OSError:
            name = ""
    offset = now.strftime("%z")
    return HubTimezone(name=name or now.tzname() or "UTC",
                       utc_offset=f"{offset[:3]}:{offset[3:]}",
                       today=date.today().isoformat())


def _form(db: Session, league: League) -> GameResetForm:
    applied = drafts.last_applied(db, league)
    under_way = (db.query(GameReset)
                 .filter(GameReset.league_id == league.id, GameReset.status == "applying")
                 .first())
    seen = db.query(LeagueGameSettings).filter(
        LeagueGameSettings.league_id == league.id).first()
    return GameResetForm(
        league_id=league.id,
        full_id=league.full_id,
        game_type=league.game_type,
        fields=[ResetField(id=f.id, label=f.label, page=f.page, group=f.group,
                           kind=f.kind, default=f.default, min=f.min, max=f.max,
                           choices=[ResetChoice(letter=l, name=n) for l, n in f.choices],
                           interbbs=f.interbbs, advised_min=f.advised_min,
                           advised_reason=f.advised_reason, note=f.note,
                           admin_note=f.admin_note,
                           depends_on=list(f.depends_on) if f.depends_on else None,
                           help_title=f.help_title, help=f.help)
                for f in fields_for(league.game_type)],
        defaults=defaults(league.game_type),
        draft=_draft(drafts.current_draft(db, league), league.game_type),
        last_applied=None if applied is None else ResetApplied(
            id=applied.id, settings=applied.settings, applied_by=applied.applied_by,
            applied_at=_iso(applied.applied_at), game_start=_iso(applied.game_start)),
        observed=None if seen is None else ObservedSettings(
            game_started_at=_iso(seen.game_started_at),
            protection_turns=seen.protection_turns,
            indiv_attacks=seen.indiv_attacks_per_day,
            lost_attack_days=seen.lost_attack_days,
            settings=seen.settings,
            seen_at=_iso(seen.seen_at)),
        under_way=None if under_way is None else _detail(db, league, under_way),
        hub_timezone=_hub_timezone(),
    )


@router.get("/{league_id}/reset", response_model=GameResetForm,
            summary="Get the Game Reset Form")
async def get_reset_form(
    league_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Every setting the game's reset editor offers, in the editor's order, with
    the game's own help, its default and our limits; the league's draft, if any;
    the settings of the last reset performed from the console; and what the hub
    last decoded from the league's Configupdate."""
    return _form(db, _league(db, league_id))


@router.put("/{league_id}/reset/draft", response_model=GameResetForm,
            summary="Save the Game Reset Draft")
async def save_reset_draft(
    league_id: int,
    body: ResetDraftSave,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Check every setting against the game's limits and save the league's draft.

    A value below our advised minimum is saved, and listed in the draft's
    `warnings`.

    Answers 422 listing each field that fails, and 409 when someone else saved the
    draft since `version` was loaded. Fields left out take the game's defaults.
    """
    league = _league(db, league_id)
    settings, errors = drafts.validate(league.game_type, body.settings, date.today())
    if errors:
        raise HTTPException(status_code=422, detail=[
            {"loc": ["body", "settings", fid], "msg": msg, "type": "value_error"}
            for fid, msg in errors])
    try:
        draft = drafts.save_draft(db, league, settings, body.notes, body.version,
                                  current_user.username)
    except drafts.DraftConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    db.flush()
    audit.record(db, "game_reset.draft_saved", actor=current_user, target=league,
                 detail=f"version {draft.version}", request=request)
    db.commit()
    logger.info(f"Reset draft for {league.full_id} saved by {current_user.username}"
                f" (version {draft.version})")
    return _form(db, league)


@router.delete("/{league_id}/reset/draft", response_model=GameResetForm,
               summary="Discard the Game Reset Draft")
async def discard_reset_draft(
    league_id: int,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    league = _league(db, league_id)
    draft = drafts.current_draft(db, league)
    if draft is None:
        raise HTTPException(status_code=404, detail="No draft to discard")
    db.delete(draft)
    audit.record(db, "game_reset.draft_discarded", actor=current_user, target=league,
                 detail=f"version {draft.version}", request=request)
    db.commit()
    logger.info(f"Reset draft for {league.full_id} discarded by {current_user.username}")
    return _form(db, league)


@router.post("/{league_id}/reset/perform", response_model=ResetDetail, status_code=202,
             summary="Perform the Game Reset")
async def perform_reset(
    league_id: int,
    body: ResetPerform,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reset the league's game with its draft. **Every player on every board in
    the league is wiped** once members run the Game Start this sends.

    The draft must be at `version`, and `confirm` must be the league's code. The
    hub then backs up the game, types the draft into the game's own reset
    editor, checks every setting on both pages, and saves it as a league-wide
    reset; a PLANETARY run then sends the Game Start. Anything unexpected before
    the save puts the game back as it was and returns the settings to the draft.

    Answers 202 at once with the reset under way; follow it with
    `GET /leagues/{id}/resets/{reset_id}`. 409 when another reset is running or
    the draft changed; 422 when the code is wrong, the draft no longer passes,
    or the game isn't where config.toml says.
    """
    league = _league(db, league_id)
    config = get_config()._raw
    try:
        reset = perform.start(db, league, body.version, body.confirm,
                              current_user.username, config, date.today())
    except perform.ResetRefused as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    audit.record(db, "game_reset.performed", actor=current_user, target=league,
                 detail=f"reset {reset.id} from draft version {body.version}",
                 request=request)
    db.commit()
    perform.schedule(reset.id, config)
    return _detail(db, league, reset)


@router.get("/{league_id}/resets", response_model=List[ResetSummary],
            summary="List the League's Resets")
async def list_resets(
    league_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Every reset of the league, newest first: those performed from the console,
    including failed ones, and (BRE) those only seen in a Game Start packet."""
    league = _league(db, league_id)
    return [ResetSummary(**_summary(r, ended)) for r, ended in history.history(db, league)]


@router.get("/{league_id}/resets/{reset_id}", response_model=ResetDetail,
            summary="Get One Reset")
async def get_reset(
    league_id: int,
    reset_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One reset with its progress, the driver's log, and which members have
    downloaded the packets that carry it."""
    league = _league(db, league_id)
    for r, ended in history.history(db, league):
        if r.id == reset_id:
            return _detail(db, league, r, ended)
    raise HTTPException(status_code=404, detail="No such reset in this league")
