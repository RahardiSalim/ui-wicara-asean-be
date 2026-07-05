from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import UserAccount
from app.modules.eduillustrate.service import EduIllustrateGenerationError
from app.modules.workspaces import schemas, service

router = APIRouter(prefix="/workspaces")


@router.get("", response_model=schemas.WorkspaceSessionHistoryRead)
def list_workspaces(
    track_id: UUID,
    module_id: UUID,
    account: UserAccount = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> schemas.WorkspaceSessionHistoryRead:
    try:
        return service.list_workspace_sessions(
            session,
            user=account,
            track_id=track_id,
            module_id=module_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("", response_model=schemas.WorkspaceRead)
def create_workspace(
    payload: schemas.WorkspaceCreateRequest,
    account: UserAccount = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> schemas.WorkspaceRead:
    try:
        return service.create_or_resume_workspace(
            session,
            user=account,
            track_id=payload.track_id,
            module_id=payload.module_id,
            content_mode=payload.content_mode,
            workspace_session_id=payload.workspace_session_id,
            start_new_session=payload.start_new_session,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{workspace_id}", response_model=schemas.WorkspaceRead)
def read_workspace(
    workspace_id: UUID,
    account: UserAccount = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> schemas.WorkspaceRead:
    workspace = service.read_workspace(session, user=account, workspace_id=workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace was not found.")
    return workspace


@router.post("/{workspace_id}/events", response_model=schemas.WorkspaceEventCreateResponse)
async def append_workspace_event(
    workspace_id: UUID,
    payload: schemas.WorkspaceEventCreateRequest,
    account: UserAccount = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> schemas.WorkspaceEventCreateResponse:
    try:
        response = await service.append_workspace_event(
            session,
            user=account,
            workspace_id=workspace_id,
            event_type=payload.event_type,
            actor_type=payload.actor_type,
            text_payload=payload.text_payload,
            image_asset_id=payload.image_asset_id,
            media_artifact_id=payload.media_artifact_id,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace was not found.")
    return response


@router.post("/{workspace_id}/advance-phase", response_model=schemas.WorkspaceRead)
def advance_workspace_phase(
    workspace_id: UUID,
    force: bool = False,
    account: UserAccount = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> schemas.WorkspaceRead:
    try:
        response = service.advance_workspace_phase(
            session,
            user=account,
            workspace_id=workspace_id,
            force=force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace was not found.")
    return response


@router.post("/{workspace_id}/start-posttest", response_model=schemas.WorkspaceRead)
def start_workspace_posttest(
    workspace_id: UUID,
    account: UserAccount = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> schemas.WorkspaceRead:
    try:
        response = service.start_posttest(
            session,
            user=account,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace was not found.")
    return response


@router.post(
    "/{workspace_id}/generate-video",
    response_model=schemas.WorkspaceGenerateVideoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_workspace_video(
    workspace_id: UUID,
    payload: schemas.WorkspaceGenerateVideoRequest,
    account: UserAccount = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> schemas.WorkspaceGenerateVideoResponse:
    try:
        response = service.queue_workspace_video_generation(
            session,
            user=account,
            workspace_id=workspace_id,
            generation_mode=payload.generation_mode,
            template_id=payload.template_id,
            spec_json=payload.spec_json,
            language=payload.language,
            quality_profile=payload.quality_profile,
            concept_id=payload.concept_id,
            metadata=payload.metadata,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace was not found.")
    return response


@router.post(
    "/{workspace_id}/generate-explanation",
    response_model=schemas.WorkspaceGenerateExplanationResponse,
)
async def generate_workspace_explanation(
    workspace_id: UUID,
    payload: schemas.WorkspaceGenerateExplanationRequest,
    account: UserAccount = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> schemas.WorkspaceGenerateExplanationResponse:
    try:
        response = await service.generate_workspace_explanation(
            session,
            user=account,
            workspace_id=workspace_id,
            problem=payload.problem,
            model=payload.model,
            max_retries=payload.max_retries,
            max_scene_concurrency=payload.max_scene_concurrency,
            translate_to_chinese=payload.translate_to_chinese,
        )
    except EduIllustrateGenerationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace was not found.")
    return response
