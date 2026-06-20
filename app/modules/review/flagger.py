"""Best-effort, NON-BLOCKING flagging of AI outputs for human review.

Producing services call :func:`flag_artifact` *after* they persist an AI
artifact (and after it has already been handed to the learner). Flagging never
gates generation: every code path is wrapped so an exception here is logged and
swallowed rather than propagated to the learner flow.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.review.models import AiReviewItem

logger = logging.getLogger(__name__)


def _deterministic_sample(artifact_id: uuid.UUID, pct: int) -> bool:
    """Reproducible sampling: same artifact always lands the same side of `pct`."""
    if pct <= 0:
        return False
    return (hash(str(artifact_id)) % 100) < pct


def evaluate_triggers(
    *,
    artifact_id: uuid.UUID,
    confidence: float | None,
    signals: dict[str, Any],
    settings: Any,
) -> list[str]:
    """Return the (deduped, ordered) list of triggers that fire for an artifact."""
    reasons: list[str] = []

    threshold = settings.review_confidence_threshold
    if (confidence is not None and confidence < threshold) or signals.get("force_low_confidence"):
        reasons.append("low_confidence")

    risky = (
        bool(signals.get("validator_failed"))
        or signals.get("generation_source") in {"fallback_generated", "deterministic_fallback"}
        or signals.get("diagnostic_signal") == "misconception_detected"
        or signals.get("structured_parse_ok") is False
        or signals.get("status") == "needs_clarification"
    )
    if risky:
        reasons.append("risk_signal")

    if _deterministic_sample(artifact_id, settings.review_sample_pct):
        reasons.append("sampled")

    return list(dict.fromkeys(reasons))


def _priority(reasons: list[str], confidence: float | None) -> int:
    score = 0
    if "learner_flag" in reasons:
        score += 40
    if "risk_signal" in reasons:
        score += 30
    if "low_confidence" in reasons:
        score += 20
    if "sampled" in reasons:
        score += 5
    if confidence is not None:
        score += int((1.0 - confidence) * 10)
    return score


def flag_artifact(
    session: Session,
    *,
    artifact_type: str,
    artifact_id: uuid.UUID,
    confidence: float | None = None,
    signals: dict[str, Any] | None = None,
    subject: str | None = None,
    concept_id: uuid.UUID | None = None,
    learner_id: uuid.UUID | None = None,
    summary: str = "",
    extra_reason: str | None = None,
) -> AiReviewItem | None:
    """Create or update the single OPEN review item for an artifact.

    Returns the affected item (useful for the learner-flag flow), or ``None`` if
    nothing fired or review is disabled. Never raises.
    """
    settings = get_settings()
    if not settings.review_enabled:
        return None
    try:
        reasons = evaluate_triggers(
            artifact_id=artifact_id,
            confidence=confidence,
            signals=signals or {},
            settings=settings,
        )
        if extra_reason:
            reasons.append(extra_reason)
        reasons = list(dict.fromkeys(reasons))
        if not reasons:
            return None

        item = session.scalar(
            select(AiReviewItem).where(
                AiReviewItem.artifact_type == artifact_type,
                AiReviewItem.artifact_id == artifact_id,
                AiReviewItem.status == "open",
            )
        )
        if item is None:
            item = AiReviewItem(
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                confidence=confidence,
                subject=subject,
                concept_id=concept_id,
                learner_id=learner_id,
                summary=summary,
                trigger_reasons=[],
            )
            session.add(item)

        # Reassign (don't mutate in place) so SQLAlchemy tracks the JSON change.
        item.trigger_reasons = list(dict.fromkeys([*item.trigger_reasons, *reasons]))
        item.priority = _priority(item.trigger_reasons, confidence)
        if confidence is not None:
            item.confidence = confidence
        if learner_id is not None and item.learner_id is None:
            item.learner_id = learner_id
        if summary and not item.summary:
            item.summary = summary

        session.commit()
        session.refresh(item)
        return item
    except Exception:  # noqa: BLE001 - review must never break the learner flow
        session.rollback()
        logger.exception("flag_artifact failed for %s %s", artifact_type, artifact_id)
        return None


def enqueue_flag(
    *,
    artifact_type: str,
    artifact_id: uuid.UUID,
    confidence: float | None = None,
    signals: dict[str, Any] | None = None,
    subject: str | None = None,
    concept_id: uuid.UUID | None = None,
    learner_id: uuid.UUID | None = None,
    summary: str = "",
) -> None:
    """Producer-facing hook.

    Flags on an INDEPENDENT database session so it can never interfere with the
    caller's transaction (no shared commit/rollback). Use this from generation
    code paths that are still mid-transaction. Best-effort; never raises.
    """
    settings = get_settings()
    if not settings.review_enabled:
        return
    try:
        from app.db.session import SessionLocal

        with SessionLocal() as own_session:
            flag_artifact(
                own_session,
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                confidence=confidence,
                signals=signals,
                subject=subject,
                concept_id=concept_id,
                learner_id=learner_id,
                summary=summary,
            )
    except Exception:  # noqa: BLE001 - review must never break the learner flow
        logger.exception("enqueue_flag failed for %s %s", artifact_type, artifact_id)
