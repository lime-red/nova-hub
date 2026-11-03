"""Golden images (admin only): list, take in, check, delete.

An image comes in three ways, all ending in the same import, which checks every
file against the image's manifest: an upload, an archive dropped into
`[games] images`, or a fetch from `[games] image_url`. See
backend/services/game_images.py.
"""

import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from backend.core.config import get_config
from backend.core.database import get_db
from backend.core.security import require_admin
from backend.logging_config import get_logger
from backend.models.database import GameImage, SysopUser
from backend.schemas.images import (
    ImageCheck,
    ImageImported,
    ImageInfo,
    ImageName,
    ImagesResponse,
    WaitingArchive,
)
from backend.services import audit
from backend.services.game_images import (
    ARCHIVE,
    INCOMING,
    MAX_UNPACKED,
    ImageError,
    check_image,
    delete_image,
    image_folder,
    import_archive,
    read_manifest,
    waiting_archives,
)
from backend.services.image_fetch import NAME, FetchError, config_problems, fetch
from backend.services.league_games import games_config

logger = get_logger(context="images")
router = APIRouter()


def _info(config: dict, image: GameImage) -> ImageInfo:
    made_from, recipe = None, None
    try:
        manifest, _ = read_manifest(image_folder(config, image))
        made_from = (manifest.get("source") or {}).get("name")
        recipe = manifest.get("recipe")
    except ImageError:
        pass
    return ImageInfo(id=image.id, dirname=image.dirname, game_type=image.game_type,
                     version=image.version, source=image.source, made_from=made_from,
                     recipe=recipe, created_at=image.created_at,
                     leagues=sorted(g.league.full_id for g in image.games))


def _take(db: Session, config: dict, archive: Path, source: str, user: SysopUser,
          request: Request) -> ImageImported:
    try:
        image, created = import_archive(db, config, archive, source)
    except ImageError as e:
        db.rollback()
        logger.warning(f"Image from {source} refused for {user.username}: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    if created:
        audit.record(db, "image.imported", actor=user, target=image, detail=source, request=request)
    db.commit()
    logger.info(f"Image {image.dirname} {'imported' if created else 'already held'} "
                f"from {source} by {user.username}")
    return ImageImported(image=_info(config, image), created=created)


@router.get("", response_model=ImagesResponse, summary="List Images")
async def list_images(
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    The golden images the hub holds, newest first, with the leagues made from
    each; the archives dropped into `[games] images` that are waiting to be
    imported; and where the hub can fetch from, if anywhere.
    """
    config = get_config()._raw
    cfg = games_config(config)
    images = db.query(GameImage).order_by(GameImage.created_at.desc(), GameImage.id.desc()).all()
    return ImagesResponse(
        images=[_info(config, i) for i in images],
        waiting=[WaitingArchive(name=p.name, size=p.stat().st_size) for p in waiting_archives(config)],
        folder=str(cfg.images),
        fetch_from=cfg.image_url,
        fetch_problems=config_problems(cfg),
    )


@router.post("/upload", response_model=ImageImported, summary="Upload Image")
async def upload_image(
    request: Request,
    file: UploadFile = File(..., description=f"an image archive, <dirname>{ARCHIVE}"),
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Take in an image archive made by make_golden.py. Every file is checked
    against the image's manifest before it is kept.

    **Returns:** the image, and whether it was new; 422 with the reason if it
    is refused (not an image archive, a file that does not match, a different
    image under a name already taken)
    """
    config = get_config()._raw
    images = games_config(config).images
    images.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=INCOMING, suffix=".part", dir=images) as tmp:
        size = 0
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UNPACKED:
                raise HTTPException(status_code=413, detail="That is larger than any image")
            tmp.write(chunk)
        tmp.flush()
        return _take(db, config, Path(tmp.name), f"upload {file.filename}"[:255], current_user,
                     request)


def _waiting(config: dict, name: str) -> Path:
    for p in waiting_archives(config):
        if p.name == name:
            return p
    raise HTTPException(status_code=404, detail=f"No archive {name!r} is waiting in "
                                                f"{games_config(config).images}")


@router.post("/import", response_model=ImageImported, summary="Import Waiting Image")
async def import_waiting(
    body: ImageName,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Import an archive dropped into `[games] images`. It is checked as an
    upload is, and removed once the image is in.

    **Returns:** the image, and whether it was new; 404 if no such archive is
    waiting; 422 with the reason if it is refused (the archive is left there)
    """
    config = get_config()._raw
    archive = _waiting(config, body.name)
    result = _take(db, config, archive, f"file {archive.name}", current_user, request)
    archive.unlink(missing_ok=True)
    return result


@router.post("/fetch", response_model=ImageImported, summary="Fetch Image")
async def fetch_image(
    body: ImageName,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Fetch an image archive by name from `[games] image_url`, with the login in
    `[games] image_credentials`, and import it as an upload is.

    **Returns:** the image, and whether it was new; 409 if the hub has no
    image_url or its login file is refused; 502 if the fetch failed; 422 if the
    name is not an image archive's, or the archive is refused
    """
    config = get_config()._raw
    cfg = games_config(config)
    if not cfg.image_url or config_problems(cfg):
        raise HTTPException(status_code=409, detail="; ".join(config_problems(cfg))
                            or "This hub has no [games] image_url to fetch from")
    if not NAME.match(body.name):
        raise HTTPException(status_code=422, detail=f"{body.name!r} is not an image archive "
                                                    f"name, as bre-sj1-r2{ARCHIVE}")
    try:
        archive = await fetch(cfg, body.name)
    except FetchError as e:
        logger.warning(f"Fetch of {body.name} for {current_user.username} failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    try:
        return _take(db, config, archive, f"fetch {body.name}", current_user, request)
    finally:
        archive.unlink(missing_ok=True)


def _image(db: Session, image_id: int) -> GameImage:
    image = db.query(GameImage).filter(GameImage.id == image_id).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return image


@router.post("/{image_id}/check", response_model=ImageCheck, summary="Check Image")
async def check(
    image_id: int,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Check an image's files and folders against its manifest.

    **Returns:** the problems found; none means it is as it was made
    """
    image = _image(db, image_id)
    problems = check_image(image_folder(get_config()._raw, image))
    logger.info(f"Check of image {image.dirname} by {current_user.username}: "
                + ("; ".join(problems) or "ok"))
    return ImageCheck(dirname=image.dirname, problems=problems)


@router.delete("/{image_id}", status_code=204, summary="Delete Image")
async def delete(
    image_id: int,
    request: Request,
    current_user: SysopUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Delete an image no league's game was made from, files and all.

    **Returns:** 204; 409 if a league uses it
    """
    image = _image(db, image_id)
    dirname = image.dirname
    audit.record(db, "image.deleted", actor=current_user, target=image, request=request)
    refused: Optional[str] = delete_image(db, get_config()._raw, image)
    if refused:
        db.rollback()
        raise HTTPException(status_code=409, detail=refused)
    db.commit()
    logger.info(f"Image {dirname} deleted by {current_user.username}")
