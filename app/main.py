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
        allow_origins=settings.cors_allow_origins,
        allow_origin_regex=settings.cors_allow_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    _mount_local_media_storage(application, settings)

    demo_video_path = resolve_project_path(settings.workspace_demo_chain_rule_video_path)
    if settings.workspace_demo_script_mode and demo_video_path.is_file():
        application.mount(
            "/demo-media",
            StaticFiles(directory=str(demo_video_path.parent)),
            name="demo-media",
        )
    elif settings.workspace_demo_script_mode:
        logger.warning("Demo Chain Rule video is unavailable: %s", demo_video_path)

    application.include_router(api_router, prefix=settings.api_v1_prefix)
    application.include_router(speech_router, prefix="/api/speech")
    return application



def _mount_local_media_storage(application: FastAPI, settings) -> None:
    """Serve rendered media off disk, when this process is the one holding it.

    Only the `local` storage backend keeps files here. On a read-only or
    ephemeral filesystem -- a serverless host, for instance -- creating the
    directory raises, and that used to happen at import time and take the whole
    app down. Storage there is Supabase, so there is nothing to mount.
    """

    if settings.media_storage_backend != "local":
        logger.info(
            "Skipping local media mount; storage backend is %s.",
            settings.media_storage_backend,
        )
        return

    media_mount_path = (settings.media_storage_public_base_url or "/media-storage").strip()
    if not media_mount_path.startswith("/"):
        logger.warning(
            "Skipping local media static mount because MEDIA_STORAGE_PUBLIC_BASE_URL "
            "is not a local path: %s",
            media_mount_path,
        )
        return

    local_media_storage_dir = resolve_project_path(settings.media_storage_local_dir)
    try:
        local_media_storage_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning(
            "Cannot create local media directory %s; serving it is disabled.",
            local_media_storage_dir,
            exc_info=True,
        )
        return

    application.mount(
        media_mount_path,
        StaticFiles(directory=str(local_media_storage_dir)),
        name="media-storage",
    )


app = create_app()
