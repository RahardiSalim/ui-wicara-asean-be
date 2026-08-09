from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import UserAccount
from app.modules.evidence.canvas_upload_service import CanvasUploadService
from app.modules.evidence.canvas_upload_service import (
    MAX_EVIDENCE_IMAGE_BYTES,
    image_asset_file_path,
    load_owned_image_asset,
)
from app.modules.evidence.schemas import ImageAssetCreateRequest

router = APIRouter()
service = CanvasUploadService()


@router.post("/evidence/image-assets")
def create_image_asset(
    payload: ImageAssetCreateRequest,
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
):
    return service.create_image_asset(session, user=user, payload=payload)


@router.post("/evidence/image-assets/upload")
async def upload_image_asset(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
):
    content = await file.read(MAX_EVIDENCE_IMAGE_BYTES + 1)
    try:
        return service.create_uploaded_image_asset(
            session,
            user=user,
            content=content,
            mime_type=file.content_type or "application/octet-stream",
        )
    except ValueError as exc:
        code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if len(content) > MAX_EVIDENCE_IMAGE_BYTES
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/evidence/image-assets/{asset_id}/file")
def read_image_asset_file(
    asset_id: UUID,
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
) -> FileResponse:
    asset = load_owned_image_asset(session, user=user, asset_id=asset_id)
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image asset was not found."
        )
    path = image_asset_file_path(asset)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image asset file is missing."
        )
    return FileResponse(path, media_type=asset.mime_type)
