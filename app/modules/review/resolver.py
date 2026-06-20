"""Render a flagged artifact into a display dict for the teacher detail view."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.learning.models import AssessmentAttempt, AssessmentQuestion
from app.modules.learning_goal_resolution.models import LearningGoalResolution


def _resolve_question(session: Session, artifact_id: uuid.UUID) -> dict[str, Any] | None:
    question = session.get(AssessmentQuestion, artifact_id)
    if question is None:
        return None
    return {
        "type": "question",
        "id": str(question.id),
        "prompt": question.prompt,
        "topic": question.topic,
        "helper_text": question.helper_text,
        "difficulty_label": question.difficulty_label,
        "expected_reasoning": question.expected_reasoning,
        "rubric_json": question.rubric_json,
        "generation_source": question.generation_source,
        "llm_metadata": question.llm_metadata_json,
        "options": [
            {
                "id": str(o.id),
                "option_key": o.option_key,
                "label": o.label,
                "text": o.text,
                "is_correct": o.is_correct,
            }
            for o in sorted(question.options, key=lambda o: o.sort_order)
        ],
    }


def _resolve_diagnosis(session: Session, artifact_id: uuid.UUID) -> dict[str, Any] | None:
    resolution = session.get(LearningGoalResolution, artifact_id)
    if resolution is None:
        return None
    return {
        "type": "diagnosis",
        "id": str(resolution.id),
        "raw_query": resolution.raw_query,
        "subject_code": resolution.subject_code,
        "grade_level": resolution.grade_level,
        "language": resolution.language,
        "suggested_concept_id": str(resolution.suggested_concept_id)
        if resolution.suggested_concept_id
        else None,
        "confidence": resolution.confidence,
        "status": resolution.status,
        "alternatives": resolution.alternatives_json,
        "llm_provider": resolution.llm_provider,
        "llm_model": resolution.llm_model,
    }


def _resolve_evaluation(session: Session, artifact_id: uuid.UUID) -> dict[str, Any] | None:
    attempt = session.get(AssessmentAttempt, artifact_id)
    if attempt is None:
        return None
    return {
        "type": "evaluation",
        "id": str(attempt.id),
        "question_id": str(attempt.question_id),
        "typed_reasoning": attempt.typed_reasoning,
        "explanation_text": attempt.explanation_text,
        "is_correct": attempt.is_correct,
        "score": attempt.score,
        "answer_score": attempt.answer_score,
        "reasoning_score": attempt.reasoning_score,
        "evidence_score": attempt.evidence_score,
        "diagnostic_signal": attempt.diagnostic_signal,
        "evaluated_result": attempt.evaluated_result,
        "evaluation_metadata": attempt.evaluation_metadata_json,
    }


_RESOLVERS = {
    "question": _resolve_question,
    "diagnosis": _resolve_diagnosis,
    "evaluation": _resolve_evaluation,
}


def resolve_artifact(
    session: Session, artifact_type: str, artifact_id: uuid.UUID
) -> dict[str, Any] | None:
    resolver = _RESOLVERS.get(artifact_type)
    if resolver is None:
        return None
    return resolver(session, artifact_id)
