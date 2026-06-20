from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReviewActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action: str
    notes: str
    reviewer_id: UUID | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    created_at: datetime


class ReviewItemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    artifact_type: str
    artifact_id: UUID
    status: str
    trigger_reasons: list[str]
    confidence: float | None
    priority: int
    subject: str | None
    concept_id: UUID | None
    learner_id: UUID | None
    summary: str
    reviewer_id: UUID | None
    created_at: datetime
    first_reviewed_at: datetime | None
    resolved_at: datetime | None


class ReviewItemDetail(ReviewItemSummary):
    artifact: dict[str, Any] | None = None
    actions: list[ReviewActionRead] = Field(default_factory=list)


class ReviewQueueResponse(BaseModel):
    items: list[ReviewItemSummary]
    total: int
    limit: int
    offset: int


class ApproveRequest(BaseModel):
    notes: str = ""


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class CorrectRequest(BaseModel):
    fields: dict[str, Any] = Field(..., description="Corrected fields, validated per artifact type")
    notes: str = ""


class FlagRequest(BaseModel):
    artifact_type: str = Field(..., pattern="^(question|diagnosis|evaluation)$")
    artifact_id: UUID
    reason: str = Field(..., min_length=1)


class TimeSeriesPoint(BaseModel):
    date: str
    reviewed: int
    corrected: int


class TypeBreakdown(BaseModel):
    artifact_type: str
    reviewed: int
    corrected: int
    rejected: int
    approved: int
    correction_rate: float


class TriggerPrecision(BaseModel):
    trigger: str
    total_resolved: int
    caught_problem: int
    precision: float


class ReviewMetricsResponse(BaseModel):
    reviewed_total: int
    corrected_total: int
    correction_rate: float
    approval_rate: float
    rejection_rate: float
    backlog_open: int
    backlog_oldest_age_days: float | None
    by_type: list[TypeBreakdown]
    trigger_precision: list[TriggerPrecision]
    time_series: list[TimeSeriesPoint]
