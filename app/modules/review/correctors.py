"""Per-artifact-type write-back of teacher corrections to the source tables.

Each corrector loads the real domain row, snapshots a ``before`` view, applies
the teacher's ``fields``, and returns ``(before_json, after_json)`` for the audit
log. They raise :class:`ValueError` on a missing artifact or invalid payload;
the service layer turns that into a 4xx and rolls back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentOption,
    AssessmentQuestion,
)
from app.modules.learning_goal_resolution.models import LearningGoalResolution


def _as_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _question_snapshot(question: AssessmentQuestion) -> dict[str, Any]:
    return {
        "prompt": question.prompt,
        "expected_reasoning": question.expected_reasoning,
        "rubric_json": dict(question.rubric_json or {}),
        "options": [
            {"id": str(o.id), "option_key": o.option_key, "text": o.text, "is_correct": o.is_correct}
            for o in sorted(question.options, key=lambda o: o.sort_order)
        ],
    }


def correct_question(
    session: Session, artifact_id: uuid.UUID, fields: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    question = session.get(AssessmentQuestion, artifact_id)
    if question is None:
        raise ValueError("Question not found.")

    before = _question_snapshot(question)

    if "prompt" in fields:
        question.prompt = str(fields["prompt"]).strip()
    if "expected_reasoning" in fields:
        question.expected_reasoning = str(fields["expected_reasoning"]).strip()
    if "rubric_json" in fields:
        rubric = fields["rubric_json"]
        if not isinstance(rubric, dict):
            raise ValueError("rubric_json must be an object.")
        question.rubric_json = rubric

    option_edits = fields.get("options")
    if option_edits is not None:
        if not isinstance(option_edits, list):
            raise ValueError("options must be a list.")
        by_id = {str(o.id): o for o in question.options}
        by_key = {o.option_key: o for o in question.options}
        for edit in option_edits:
            target: AssessmentOption | None = None
            if edit.get("id") is not None:
                target = by_id.get(str(edit["id"]))
            elif edit.get("option_key") is not None:
                target = by_key.get(str(edit["option_key"]))
            if target is None:
                raise ValueError(f"Option not found for edit: {edit}")
            if "text" in edit:
                target.text = str(edit["text"]).strip()
            if "is_correct" in edit:
                target.is_correct = bool(edit["is_correct"])

    session.flush()
    return before, _question_snapshot(question)


def correct_diagnosis(
    session: Session, artifact_id: uuid.UUID, fields: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolution = session.get(LearningGoalResolution, artifact_id)
    if resolution is None:
        raise ValueError("Diagnosis not found.")

    before = {
        "suggested_concept_id": str(resolution.suggested_concept_id)
        if resolution.suggested_concept_id
        else None,
        "status": resolution.status,
    }

    if "suggested_concept_id" in fields and fields["suggested_concept_id"] is not None:
        resolution.suggested_concept_id = _as_uuid(fields["suggested_concept_id"])
    resolution.status = "confirmed"
    resolution.confirmed_at = datetime.now(UTC)

    session.flush()
    after = {
        "suggested_concept_id": str(resolution.suggested_concept_id)
        if resolution.suggested_concept_id
        else None,
        "status": resolution.status,
    }
    return before, after


def correct_evaluation(
    session: Session, artifact_id: uuid.UUID, fields: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempt = session.get(AssessmentAttempt, artifact_id)
    if attempt is None:
        raise ValueError("Evaluation (attempt) not found.")

    before = {
        "reasoning_score": attempt.reasoning_score,
        "diagnostic_signal": attempt.diagnostic_signal,
        "evaluated_result": dict(attempt.evaluated_result or {}),
    }

    if "reasoning_score" in fields and fields["reasoning_score"] is not None:
        score = float(fields["reasoning_score"])
        attempt.reasoning_score = max(0.0, min(1.0, score))
    if "diagnostic_signal" in fields:
        attempt.diagnostic_signal = str(fields["diagnostic_signal"]).strip()
    if "teacher_feedback" in fields:
        merged = dict(attempt.evaluated_result or {})
        merged["teacher_feedback"] = str(fields["teacher_feedback"]).strip()
        attempt.evaluated_result = merged

    session.flush()
    after = {
        "reasoning_score": attempt.reasoning_score,
        "diagnostic_signal": attempt.diagnostic_signal,
        "evaluated_result": dict(attempt.evaluated_result or {}),
    }
    return before, after


CORRECTORS: dict[str, Callable[[Session, uuid.UUID, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]] = {
    "question": correct_question,
    "diagnosis": correct_diagnosis,
    "evaluation": correct_evaluation,
}
