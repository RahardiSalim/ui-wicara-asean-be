import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings, resolve_project_path
from app.modules.speech.router import router as speech_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.project_name)
    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    local_media_storage_dir = resolve_project_path(settings.media_storage_local_dir)
    local_media_storage_dir.mkdir(parents=True, exist_ok=True)
    media_mount_path = (settings.media_storage_public_base_url or "/media-storage").strip()
    if media_mount_path.startswith("/"):
        application.mount(
            media_mount_path,
            StaticFiles(directory=str(local_media_storage_dir)),
            name="media-storage",
        )
    else:
        logger.warning(
            "Skipping local media static mount because MEDIA_STORAGE_PUBLIC_BASE_URL "
            "is not a local path: %s",
            media_mount_path,
        )

    application.include_router(api_router, prefix=settings.api_v1_prefix)
    application.include_router(speech_router, prefix="/api/speech")
    return application


app = create_app()
