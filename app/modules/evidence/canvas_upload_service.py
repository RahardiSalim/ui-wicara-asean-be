from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings, resolve_project_path
from app.modules.accounts.models import UserAccount
from app.modules.evidence.models import ImageAsset
from app.modules.evidence.schemas import ImageAssetCreateRequest, ImageAssetRead

SUPPORTED_EVIDENCE_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_EVIDENCE_IMAGE_BYTES = 10 * 1024 * 1024


class CanvasUploadService:
    def create_image_asset(
        self,
        session: Session,
        *,
        user: UserAccount,
        payload: ImageAssetCreateRequest,
    ) -> ImageAssetRead:
        asset = ImageAsset(
            user_id=user.id,
            storage_path=payload.storage_path,
            mime_type=payload.mime_type,
            width=payload.width,
            height=payload.height,
            checksum=payload.checksum,
        )
        session.add(asset)
        session.commit()
        return ImageAssetRead(
            id=asset.id,
            storage_path=asset.storage_path,
            mime_type=asset.mime_type,
            width=asset.width,
            height=asset.height,
            checksum=asset.checksum,
        )

    def create_uploaded_image_asset(
        self,
        session: Session,
        *,
        user: UserAccount,
        content: bytes,
        mime_type: str,
        settings: Settings | None = None,
    ) -> ImageAssetRead:
        normalized_mime = mime_type.strip().lower()
        extension = SUPPORTED_EVIDENCE_IMAGE_TYPES.get(normalized_mime)
        if extension is None:
            raise ValueError("Evidence image must be JPEG, PNG, or WebP.")
        if not content:
            raise ValueError("Evidence image is empty.")
        if len(content) > MAX_EVIDENCE_IMAGE_BYTES:
            raise ValueError("Evidence image exceeds the 10 MB limit.")
        if not _matches_image_signature(content, normalized_mime):
            raise ValueError("Uploaded evidence does not match its image type.")

        resolved_settings = settings or get_settings()
        asset_id = uuid4()
        relative_path = Path("evidence") / str(user.id) / f"{asset_id}{extension}"
        storage_root = resolve_project_path(resolved_settings.media_storage_local_dir)
        target_path = (storage_root / relative_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)

        checksum = hashlib.sha256(content).hexdigest()
        asset = ImageAsset(
            id=asset_id,
            user_id=user.id,
            storage_path=relative_path.as_posix(),
            mime_type=normalized_mime,
            checksum=checksum,
        )
        session.add(asset)
        session.commit()
        return ImageAssetRead(
            id=asset.id,
            storage_path=asset.storage_path,
            mime_type=asset.mime_type,
            width=asset.width,
            height=asset.height,
            checksum=asset.checksum,
        )


def load_owned_image_asset(
    session: Session,
    *,
    user: UserAccount,
    asset_id: UUID,
) -> ImageAsset | None:
    return session.scalar(
        select(ImageAsset).where(ImageAsset.id == asset_id, ImageAsset.user_id == user.id)
    )


def image_asset_file_path(
    asset: ImageAsset,
    *,
    settings: Settings | None = None,
) -> Path | None:
    raw_path = asset.storage_path.strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        resolved_settings = settings or get_settings()
        path = resolve_project_path(resolved_settings.media_storage_local_dir) / path
    resolved = path.resolve()
    return resolved if resolved.is_file() else None


def _matches_image_signature(content: bytes, mime_type: str) -> bool:
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    return False
