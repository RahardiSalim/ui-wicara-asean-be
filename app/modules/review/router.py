from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account, get_current_teacher
from app.modules.accounts.models import UserAccount
from app.modules.review import service
from app.modules.review.metrics import compute_metrics
from app.modules.review.resolver import resolve_artifact
from app.modules.review.schemas import (
    ApproveRequest,
    CorrectRequest,
    FlagRequest,
    RejectRequest,
    ReviewItemDetail,
    ReviewItemSummary,
    ReviewMetricsResponse,
    ReviewQueueResponse,
)

router = APIRouter(prefix="/review")


def _detail(session: Session, item) -> ReviewItemDetail:
    detail = ReviewItemDetail.model_validate(item)
    detail.artifact = resolve_artifact(session, item.artifact_type, item.artifact_id)
    return detail


@router.get("/queue", response_model=ReviewQueueResponse)
def get_queue(
    status_filter: str | None = Query(default=None, alias="status"),
    artifact_type: str | None = Query(default=None),
    trigger: str | None = Query(default=None),
    subject: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> ReviewQueueResponse:
    items, total = service.list_queue(
        session,
        status=status_filter,
        artifact_type=artifact_type,
        trigger=trigger,
        subject=subject,
        limit=limit,
        offset=offset,
    )
    return ReviewQueueResponse(
        items=[ReviewItemSummary.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/metrics", response_model=ReviewMetricsResponse)
def get_metrics(
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> ReviewMetricsResponse:
    return ReviewMetricsResponse(**compute_metrics(session))


@router.get("/items/{item_id}", response_model=ReviewItemDetail)
def get_item(
    item_id: uuid.UUID,
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> ReviewItemDetail:
    item = service.get_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found.")
    return _detail(session, item)


@router.post("/items/{item_id}/approve", response_model=ReviewItemSummary)
def approve_item(
    item_id: uuid.UUID,
    payload: ApproveRequest,
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> ReviewItemSummary:
    item = service.get_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found.")
    return ReviewItemSummary.model_validate(service.approve(session, item, teacher, payload.notes))


@router.post("/items/{item_id}/reject", response_model=ReviewItemSummary)
def reject_item(
    item_id: uuid.UUID,
    payload: RejectRequest,
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> ReviewItemSummary:
    item = service.get_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found.")
    return ReviewItemSummary.model_validate(service.reject(session, item, teacher, payload.reason))


@router.post("/items/{item_id}/correct", response_model=ReviewItemDetail)
def correct_item(
    item_id: uuid.UUID,
    payload: CorrectRequest,
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> ReviewItemDetail:
    item = service.get_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found.")
    try:
        updated = service.correct(session, item, teacher, payload.fields, payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _detail(session, updated)


@router.post("/flag", response_model=ReviewItemSummary)
def flag_artifact_endpoint(
    payload: FlagRequest,
    session: Session = Depends(get_session),
    learner: UserAccount = Depends(get_current_account),
) -> ReviewItemSummary:
    item = service.create_learner_flag(
        session,
        artifact_type=payload.artifact_type,
        artifact_id=payload.artifact_id,
        learner=learner,
        reason=payload.reason,
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Review is currently disabled."
        )
    return ReviewItemSummary.model_validate(item)
