from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from app.db.base import Base

json_type = JSON().with_variant(JSONB, "postgresql")

ARTIFACT_TYPES = ("question", "diagnosis", "evaluation")
REVIEW_STATUSES = ("open", "approved", "rejected", "corrected")
TRIGGER_REASONS = ("low_confidence", "risk_signal", "sampled", "learner_flag")


class AiReviewItem(Base):
    """One queued AI output awaiting (or having received) human review.

    Polymorphic by (``artifact_type``, ``artifact_id``) so the same queue spans
    questions, diagnoses and evaluations without touching their tables. At most
    one OPEN item exists per artifact (enforced by the partial unique index).
    """

    __tablename__ = "ai_review_items"
    __table_args__ = (
        Index(
            "uq_ai_review_items_open_artifact",
            "artifact_type",
            "artifact_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_ai_review_items_status_priority", "status", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    trigger_reasons: Mapped[list[str]] = mapped_column(
        json_type, nullable=False, default=list
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subject: Mapped[str | None] = mapped_column(String(64))
    concept_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    learner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    first_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    actions: Mapped[list[AiReviewAction]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="AiReviewAction.created_at",
    )


class AiReviewAction(Base):
    """Immutable audit record of a single human action on a review item.

    Powers the "how often is correction needed" metrics: each correct/reject/
    approve/flag is one row, with before/after snapshots for corrections.
    """

    __tablename__ = "ai_review_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_review_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_json: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    item: Mapped[AiReviewItem] = relationship(back_populates="actions")
