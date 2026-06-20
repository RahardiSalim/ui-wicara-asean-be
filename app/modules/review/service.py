"""Teacher review workflow: queue, detail, approve / reject / correct, learner flag.

All mutations are NON-BLOCKING relative to AI generation — they act on already
persisted artifacts after the fact and never gate the learner experience.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.modules.accounts.models import UserAccount
from app.modules.review.correctors import CORRECTORS
from app.modules.review.flagger import flag_artifact
from app.modules.review.models import AiReviewAction, AiReviewItem


def _build_conditions(
    *,
    status: str | None,
    artifact_type: str | None,
    trigger: str | None,
    subject: str | None,
) -> list[Any]:
    conditions: list[Any] = []
    if status is None:
        conditions.append(AiReviewItem.status == "open")
    elif status != "all":
        conditions.append(AiReviewItem.status == status)
    if artifact_type:
        conditions.append(AiReviewItem.artifact_type == artifact_type)
    if subject:
        conditions.append(AiReviewItem.subject == subject)
    if trigger:
        conditions.append(cast(AiReviewItem.trigger_reasons, JSONB).contains([trigger]))
    return conditions


def list_queue(
    session: Session,
    *,
    status: str | None = None,
    artifact_type: str | None = None,
    trigger: str | None = None,
    subject: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AiReviewItem], int]:
    conditions = _build_conditions(
        status=status, artifact_type=artifact_type, trigger=trigger, subject=subject
    )
    total = session.scalar(
        select(func.count()).select_from(AiReviewItem).where(*conditions)
    ) or 0
    items = list(
        session.scalars(
            select(AiReviewItem)
            .where(*conditions)
            .order_by(AiReviewItem.priority.desc(), AiReviewItem.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total)


def get_item(session: Session, item_id: uuid.UUID) -> AiReviewItem | None:
    return session.get(AiReviewItem, item_id)


def _stamp_resolution(item: AiReviewItem, reviewer: UserAccount) -> None:
    now = datetime.now(UTC)
    if item.first_reviewed_at is None:
        item.first_reviewed_at = now
    item.resolved_at = now
    item.reviewer_id = reviewer.id


def approve(
    session: Session, item: AiReviewItem, reviewer: UserAccount, notes: str = ""
) -> AiReviewItem:
    item.status = "approved"
    _stamp_resolution(item, reviewer)
    session.add(
        AiReviewAction(
            review_item_id=item.id, reviewer_id=reviewer.id, action="approve", notes=notes
        )
    )
    session.commit()
    session.refresh(item)
    return item


def reject(
    session: Session, item: AiReviewItem, reviewer: UserAccount, reason: str
) -> AiReviewItem:
    item.status = "rejected"
    _stamp_resolution(item, reviewer)
    session.add(
        AiReviewAction(
            review_item_id=item.id, reviewer_id=reviewer.id, action="reject", notes=reason
        )
    )
    session.commit()
    session.refresh(item)
    return item


def correct(
    session: Session,
    item: AiReviewItem,
    reviewer: UserAccount,
    fields: dict[str, Any],
    notes: str = "",
) -> AiReviewItem:
    corrector = CORRECTORS.get(item.artifact_type)
    if corrector is None:
        raise ValueError(f"No corrector for artifact type '{item.artifact_type}'.")
    before, after = corrector(session, item.artifact_id, fields)
    item.status = "corrected"
    _stamp_resolution(item, reviewer)
    session.add(
        AiReviewAction(
            review_item_id=item.id,
            reviewer_id=reviewer.id,
            action="correct",
            notes=notes,
            before_json=before,
            after_json=after,
        )
    )
    session.commit()
    session.refresh(item)
    return item


def create_learner_flag(
    session: Session,
    *,
    artifact_type: str,
    artifact_id: uuid.UUID,
    learner: UserAccount,
    reason: str,
) -> AiReviewItem | None:
    item = flag_artifact(
        session,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        extra_reason="learner_flag",
        learner_id=learner.id,
        summary=reason[:160],
    )
    if item is None:
        return None
    session.add(
        AiReviewAction(
            review_item_id=item.id, reviewer_id=None, action="flag", notes=reason
        )
    )
    session.commit()
    session.refresh(item)
    return item
