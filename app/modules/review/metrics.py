"""Compute "how often is human correction needed" metrics from the review tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.modules.review.models import AiReviewItem

RESOLVED_STATUSES = ("approved", "rejected", "corrected")


def compute_metrics(session: Session) -> dict[str, Any]:
    status_counts = {
        status: count
        for status, count in session.execute(
            select(AiReviewItem.status, func.count()).group_by(AiReviewItem.status)
        ).all()
    }
    reviewed_total = sum(status_counts.get(s, 0) for s in RESOLVED_STATUSES)
    corrected_total = status_counts.get("corrected", 0)
    rejected_total = status_counts.get("rejected", 0)
    approved_total = status_counts.get("approved", 0)
    open_total = status_counts.get("open", 0)

    def rate(value: int) -> float:
        return round(value / reviewed_total, 4) if reviewed_total else 0.0

    # Per-type breakdown.
    by_type_map: dict[str, dict[str, int]] = {}
    for atype, status, count in session.execute(
        select(AiReviewItem.artifact_type, AiReviewItem.status, func.count()).group_by(
            AiReviewItem.artifact_type, AiReviewItem.status
        )
    ).all():
        bucket = by_type_map.setdefault(
            atype, {"reviewed": 0, "corrected": 0, "rejected": 0, "approved": 0}
        )
        if status in RESOLVED_STATUSES:
            bucket["reviewed"] += count
            bucket[status] += count
    by_type = [
        {
            "artifact_type": atype,
            **bucket,
            "correction_rate": round(bucket["corrected"] / bucket["reviewed"], 4)
            if bucket["reviewed"]
            else 0.0,
        }
        for atype, bucket in sorted(by_type_map.items())
    ]

    # Trigger precision: of resolved items carrying a trigger, how many were a real
    # problem (corrected or rejected) rather than approved-as-fine.
    trig_total: dict[str, int] = {}
    trig_caught: dict[str, int] = {}
    for reasons, status in session.execute(
        select(AiReviewItem.trigger_reasons, AiReviewItem.status).where(
            AiReviewItem.status.in_(RESOLVED_STATUSES)
        )
    ).all():
        for reason in reasons or []:
            trig_total[reason] = trig_total.get(reason, 0) + 1
            if status in ("corrected", "rejected"):
                trig_caught[reason] = trig_caught.get(reason, 0) + 1
    trigger_precision = [
        {
            "trigger": reason,
            "total_resolved": trig_total[reason],
            "caught_problem": trig_caught.get(reason, 0),
            "precision": round(trig_caught.get(reason, 0) / trig_total[reason], 4)
            if trig_total[reason]
            else 0.0,
        }
        for reason in sorted(trig_total)
    ]

    # Backlog: oldest open item age in days.
    oldest_open = session.scalar(
        select(func.min(AiReviewItem.created_at)).where(AiReviewItem.status == "open")
    )
    backlog_oldest_age_days = None
    if oldest_open is not None:
        reference = oldest_open
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        backlog_oldest_age_days = round(
            (datetime.now(UTC) - reference).total_seconds() / 86400, 2
        )

    # 14-day daily time series of reviewed vs corrected.
    cutoff = datetime.now(UTC) - timedelta(days=14)
    series = session.execute(
        select(
            func.date(AiReviewItem.resolved_at).label("day"),
            func.count().label("reviewed"),
            func.sum(case((AiReviewItem.status == "corrected", 1), else_=0)).label("corrected"),
        )
        .where(
            AiReviewItem.status.in_(RESOLVED_STATUSES),
            AiReviewItem.resolved_at.is_not(None),
            AiReviewItem.resolved_at >= cutoff,
        )
        .group_by(func.date(AiReviewItem.resolved_at))
        .order_by(func.date(AiReviewItem.resolved_at))
    ).all()
    time_series = [
        {"date": str(day), "reviewed": int(reviewed), "corrected": int(corrected or 0)}
        for day, reviewed, corrected in series
    ]

    return {
        "reviewed_total": reviewed_total,
        "corrected_total": corrected_total,
        "correction_rate": rate(corrected_total),
        "approval_rate": rate(approved_total),
        "rejection_rate": rate(rejected_total),
        "backlog_open": open_total,
        "backlog_oldest_age_days": backlog_oldest_age_days,
        "by_type": by_type,
        "trigger_precision": trigger_precision,
        "time_series": time_series,
    }
