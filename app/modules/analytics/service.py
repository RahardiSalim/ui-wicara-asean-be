"""Long-term learning analytics, computed on read from existing tables.

Sources:
- learner_concept_states (mastery/confidence/review timestamps, per user)
- weekly_report_snapshots (weekly grain, per user) -> rolled up to month/all-time
- assessment_attempts joined to assessment_sessions (attempt timeline, per user)

All functions are pure given the session + user and tolerate empty data.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import UserAccount
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentSession,
    LearnerConceptState,
    WeeklyReportSnapshot,
)

MASTERY_THRESHOLD = 0.7
LOW_CONFIDENCE = 0.5


def _to_utc_date(value: datetime) -> date:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).date()


def _attempt_dates(session: Session, user_id: uuid.UUID) -> list[date]:
    rows = session.execute(
        select(AssessmentAttempt.submitted_at)
        .join(AssessmentSession, AssessmentAttempt.session_id == AssessmentSession.id)
        .where(AssessmentSession.user_id == user_id)
    ).all()
    dates = {_to_utc_date(ts) for (ts,) in rows if ts is not None}
    return sorted(dates)


def _total_attempts(session: Session, user_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count(AssessmentAttempt.id)).join(
                AssessmentSession, AssessmentAttempt.session_id == AssessmentSession.id
            ).where(AssessmentSession.user_id == user_id)
        )
        or 0
    )


def compute_overview(session: Session, user: UserAccount) -> dict[str, Any]:
    rows = session.execute(
        select(
            Subject.code,
            Subject.name,
            Subject.display_order,
            LearnerConceptState.mastery_score,
        )
        .join(KnowledgeConcept, LearnerConceptState.concept_id == KnowledgeConcept.id)
        .join(Subject, KnowledgeConcept.subject_id == Subject.id)
        .where(LearnerConceptState.user_id == user.id)
    ).all()

    by_subject: dict[str, dict[str, Any]] = {}
    for code, name, order, mastery in rows:
        bucket = by_subject.setdefault(
            code,
            {"code": code, "name": name, "order": order or 999,
             "concepts": 0, "mastered": 0, "gaps": 0, "mastery_sum": 0.0},
        )
        mastery_value = float(mastery or 0.0)
        bucket["concepts"] += 1
        bucket["mastery_sum"] += mastery_value
        if mastery_value >= MASTERY_THRESHOLD:
            bucket["mastered"] += 1
        else:
            bucket["gaps"] += 1

    subjects = []
    for bucket in sorted(by_subject.values(), key=lambda b: (b["order"], b["code"])):
        concepts = bucket["concepts"]
        subjects.append(
            {
                "subject_code": bucket["code"],
                "subject_name": bucket["name"],
                "concepts_tracked": concepts,
                "mastered": bucket["mastered"],
                "gaps": bucket["gaps"],
                "avg_mastery": round(bucket["mastery_sum"] / concepts, 3) if concepts else 0.0,
            }
        )

    total_concepts = sum(s["concepts_tracked"] for s in subjects)
    overall = (
        round(sum(s["avg_mastery"] * s["concepts_tracked"] for s in subjects) / total_concepts, 3)
        if total_concepts
        else 0.0
    )
    dates = _attempt_dates(session, user.id)
    return {
        "subjects": subjects,
        "subjects_studied": len(subjects),
        "concepts_tracked": total_concepts,
        "overall_avg_mastery": overall,
        "total_attempts": _total_attempts(session, user.id),
        "active_days": len(dates),
    }


def compute_trends(session: Session, user: UserAccount, period: str = "month") -> dict[str, Any]:
    snaps = session.execute(
        select(
            WeeklyReportSnapshot.range_start,
            WeeklyReportSnapshot.score,
            WeeklyReportSnapshot.attempt_count,
            WeeklyReportSnapshot.fixed_gaps,
            WeeklyReportSnapshot.remaining_gaps,
        )
        .where(WeeklyReportSnapshot.user_id == user.id)
        .order_by(WeeklyReportSnapshot.range_start)
    ).all()

    if period == "all":
        points = [
            {
                "period": str(rs),
                "score": int(sc or 0),
                "attempts": int(ac or 0),
                "fixed_gaps": int(fg or 0),
                "remaining_gaps": int(rg or 0),
            }
            for rs, sc, ac, fg, rg in snaps
        ]
        return {"period": "all", "points": points}

    agg: dict[str, dict[str, int]] = {}
    for rs, sc, ac, fg, rg in snaps:
        key = f"{rs.year:04d}-{rs.month:02d}"
        bucket = agg.setdefault(key, {"score_sum": 0, "score_n": 0, "attempts": 0, "fixed_gaps": 0, "remaining_gaps": 0})
        bucket["score_sum"] += int(sc or 0)
        bucket["score_n"] += 1
        bucket["attempts"] += int(ac or 0)
        bucket["fixed_gaps"] += int(fg or 0)
        bucket["remaining_gaps"] = int(rg or 0)  # last week of the month
    points = [
        {
            "period": key,
            "score": round(bucket["score_sum"] / bucket["score_n"]) if bucket["score_n"] else 0,
            "attempts": bucket["attempts"],
            "fixed_gaps": bucket["fixed_gaps"],
            "remaining_gaps": bucket["remaining_gaps"],
        }
        for key, bucket in sorted(agg.items())
    ]
    return {"period": "month", "points": points}


def _streaks(dates: list[date]) -> tuple[int, int]:
    if not dates:
        return 0, 0
    longest = current_run = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            current_run += 1
            longest = max(longest, current_run)
        else:
            current_run = 1

    today = datetime.now(UTC).date()
    if (today - dates[-1]).days > 1:
        current = 0
    else:
        current = 1
        for i in range(len(dates) - 1, 0, -1):
            if (dates[i] - dates[i - 1]).days == 1:
                current += 1
            else:
                break
    return current, longest


def compute_velocity(session: Session, user: UserAccount) -> dict[str, Any]:
    dates = _attempt_dates(session, user.id)
    total_attempts = _total_attempts(session, user.id)
    current_streak, longest_streak = _streaks(dates)
    mastered = int(
        session.scalar(
            select(func.count(LearnerConceptState.id)).where(
                LearnerConceptState.user_id == user.id,
                LearnerConceptState.mastery_score >= MASTERY_THRESHOLD,
            )
        )
        or 0
    )
    tracked = int(
        session.scalar(
            select(func.count(LearnerConceptState.id)).where(
                LearnerConceptState.user_id == user.id
            )
        )
        or 0
    )
    return {
        "total_attempts": total_attempts,
        "active_days": len(dates),
        "current_streak_days": current_streak,
        "longest_streak_days": longest_streak,
        "concepts_mastered": mastered,
        "concepts_tracked": tracked,
        "avg_attempts_per_active_day": round(total_attempts / len(dates), 2) if dates else 0.0,
        "first_active": str(dates[0]) if dates else None,
        "last_active": str(dates[-1]) if dates else None,
    }


def compute_at_risk(session: Session, user: UserAccount, limit: int = 20) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows = session.execute(
        select(
            LearnerConceptState.concept_id,
            LearnerConceptState.mastery_score,
            LearnerConceptState.confidence_score,
            LearnerConceptState.last_evaluated_at,
            LearnerConceptState.next_review_at,
            KnowledgeConcept.title,
            Subject.code,
            Subject.name,
        )
        .join(KnowledgeConcept, LearnerConceptState.concept_id == KnowledgeConcept.id)
        .join(Subject, KnowledgeConcept.subject_id == Subject.id)
        .where(LearnerConceptState.user_id == user.id)
    ).all()

    items = []
    for concept_id, mastery, confidence, last_eval, next_review, title, scode, sname in rows:
        overdue_days = None
        if next_review is not None:
            nr = next_review if next_review.tzinfo else next_review.replace(tzinfo=UTC)
            overdue_days = (now - nr).total_seconds() / 86400
        is_overdue = overdue_days is not None and overdue_days > 0
        low_confidence = float(confidence or 0.0) < LOW_CONFIDENCE
        if not (is_overdue or low_confidence):
            continue

        retention = None
        if last_eval is not None:
            le = last_eval if last_eval.tzinfo else last_eval.replace(tzinfo=UTC)
            days_since = max(0.0, (now - le).total_seconds() / 86400)
            stability = max(1.0, float(mastery or 0.0) * 14.0)
            retention = round(math.exp(-days_since / stability), 3)

        risk_score = (overdue_days or 0.0) + (1.0 - float(confidence or 0.0)) * 5.0
        items.append(
            {
                "concept_id": str(concept_id),
                "title": title,
                "subject_code": scode,
                "subject_name": sname,
                "mastery": round(float(mastery or 0.0), 3),
                "confidence": round(float(confidence or 0.0), 3),
                "overdue_days": round(overdue_days, 1) if overdue_days is not None else None,
                "retention_estimate": retention,
                "risk_score": round(risk_score, 2),
            }
        )

    items.sort(key=lambda item: item["risk_score"], reverse=True)
    return {"items": items[:limit], "total_at_risk": len(items)}
