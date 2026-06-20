from __future__ import annotations

from pydantic import BaseModel


class SubjectMastery(BaseModel):
    subject_code: str
    subject_name: str
    concepts_tracked: int
    mastered: int
    gaps: int
    avg_mastery: float


class OverviewResponse(BaseModel):
    subjects: list[SubjectMastery]
    subjects_studied: int
    concepts_tracked: int
    overall_avg_mastery: float
    total_attempts: int
    active_days: int


class TrendPoint(BaseModel):
    period: str
    score: int
    attempts: int
    fixed_gaps: int
    remaining_gaps: int


class TrendsResponse(BaseModel):
    period: str
    points: list[TrendPoint]


class VelocityResponse(BaseModel):
    total_attempts: int
    active_days: int
    current_streak_days: int
    longest_streak_days: int
    concepts_mastered: int
    concepts_tracked: int
    avg_attempts_per_active_day: float
    first_active: str | None
    last_active: str | None


class AtRiskItem(BaseModel):
    concept_id: str
    title: str
    subject_code: str
    subject_name: str
    mastery: float
    confidence: float
    overdue_days: float | None
    retention_estimate: float | None
    risk_score: float


class AtRiskResponse(BaseModel):
    items: list[AtRiskItem]
    total_at_risk: int
