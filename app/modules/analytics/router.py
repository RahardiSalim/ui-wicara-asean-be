from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import UserAccount
from app.modules.analytics import service
from app.modules.analytics.schemas import (
    AtRiskResponse,
    OverviewResponse,
    TrendsResponse,
    VelocityResponse,
)

router = APIRouter(prefix="/analytics")


@router.get("/overview", response_model=OverviewResponse)
def get_overview(
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
) -> OverviewResponse:
    return OverviewResponse(**service.compute_overview(session, user))


@router.get("/trends", response_model=TrendsResponse)
def get_trends(
    period: str = Query(default="month", pattern="^(month|all)$"),
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
) -> TrendsResponse:
    return TrendsResponse(**service.compute_trends(session, user, period))


@router.get("/velocity", response_model=VelocityResponse)
def get_velocity(
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
) -> VelocityResponse:
    return VelocityResponse(**service.compute_velocity(session, user))


@router.get("/at-risk", response_model=AtRiskResponse)
def get_at_risk(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
) -> AtRiskResponse:
    return AtRiskResponse(**service.compute_at_risk(session, user, limit))
