from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.analytics.schemas import (
    AtRiskResponse,
    OverviewResponse,
    TrendsResponse,
    VelocityResponse,
)


class InviteStudentRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class ConnectionRead(BaseModel):
    id: UUID
    status: str
    teacher_id: UUID
    teacher_name: str
    teacher_email: str | None
    student_id: UUID
    student_name: str
    student_email: str | None
    requested_at: datetime
    responded_at: datetime | None


class ConnectionListResponse(BaseModel):
    items: list[ConnectionRead]


class StudentProgressResponse(BaseModel):
    student_id: UUID
    student_name: str
    student_email: str | None
    overview: OverviewResponse
    trends: TrendsResponse
    velocity: VelocityResponse
    at_risk: AtRiskResponse
