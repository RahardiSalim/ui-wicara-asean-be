from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.modules.ai.client import ai_client
from app.modules.ai.config import get_ai_settings
from app.modules.evidence.models import ImageAsset
from app.modules.learning.models import AssessmentAttempt, AssessmentOption, AssessmentQuestion

PASS_PERCENT = 70.0


class AssessmentEvidenceEvaluator:
    def evaluate(
        self,
        session: Session,
        *,
        question: AssessmentQuestion,
        selected_option: AssessmentOption,
        typed_reasoning: str,
        canvas_asset_id: object | None,
        used_canvas: bool = False,
        reasoning_result_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        is_correct = bool(selected_option.is_correct)
        answer_score = 1.0 if is_correct else 0.0
        reasoning_result = reasoning_result_override
        if reasoning_result is None:
            reasoning_result = _evaluate_reasoning(
                question=question,
                selected_option=selected_option,
                typed_reasoning=typed_reasoning,
                is_correct=is_correct,
            )
        reasoning_score = reasoning_result["reasoning_score"]
        canvas_status = None
        canvas_score = None
        if canvas_asset_id is not None:
            asset = session.get(ImageAsset, canvas_asset_id)
            canvas_status = "stored_not_evaluated" if asset is not None else "not_found"
        elif used_canvas:
            canvas_status = "client_canvas_not_uploaded"

        evidence_score = calculate_evidence_score(
            answer_score=answer_score,
            reasoning_score=reasoning_score,
            canvas_score=canvas_score,
        )
        diagnostic_signal = diagnostic_signal_for_attempt(
            is_correct=is_correct,
            reasoning_score=reasoning_score,
            canvas_score=canvas_score,
            reasoning_signal=reasoning_result["reasoning_signal"],
        )
        confidence = confidence_for_attempt(
            is_correct=is_correct,
            evidence_score=evidence_score,
            reasoning_score=reasoning_score,
            canvas_score=canvas_score,
        )
        return {
            "is_correct": is_correct,
            "answer_score": answer_score,
            "reasoning_score": reasoning_score,
            "canvas_score": canvas_score,
            "canvas_status": canvas_status,
            "evidence_score": evidence_score,
            "diagnostic_signal": diagnostic_signal,
            "reasoning_signal": reasoning_result["reasoning_signal"],
            "reasoning_feedback": reasoning_result["reasoning_feedback"],
            "reasoning_evaluation_source": reasoning_result["reasoning_evaluation_source"],
            "confidence": confidence,
        }


def calculate_evidence_score(
    *,
    answer_score: float,
    reasoning_score: float | None,
    canvas_score: float | None,
) -> float:
    answer = _clamp01(answer_score)
    reasoning = _clamp01(reasoning_score) if reasoning_score is not None else None
    canvas = _clamp01(canvas_score) if canvas_score is not None else None
    if reasoning is not None and canvas is not None:
        return round((0.60 * answer) + (0.25 * reasoning) + (0.15 * canvas), 4)
    if reasoning is not None:
        return round((0.70 * answer) + (0.30 * reasoning), 4)
    if canvas is not None:
        return round((0.70 * answer) + (0.30 * canvas), 4)
    return round(answer, 4)


def diagnostic_signal_for_attempt(
    *,
    is_correct: bool,
    reasoning_score: float | None,
    canvas_score: float | None,
    reasoning_signal: str,
) -> str:
    strong_reasoning = reasoning_score is not None and reasoning_score >= 0.75
    strong_canvas = canvas_score is not None and canvas_score >= 0.75
    if not is_correct and reasoning_signal in {"misconception", "wrong_method", "unrelated"}:
        return "misconception_detected"
    if not is_correct and reasoning_signal == "possible_careless_mistake":
        return "possible_careless_mistake"
    if is_correct and (strong_reasoning or strong_canvas):
        return "correct_with_evidence"
    if is_correct:
        return "correct_mcq_only" if reasoning_score is None and canvas_score is None else "correct_low_evidence"
    if strong_reasoning or strong_canvas:
        return "possible_careless_mistake"
    return "concept_gap_likely"


def confidence_for_attempt(
    *,
    is_correct: bool,
    evidence_score: float,
    reasoning_score: float | None,
    canvas_score: float | None,
) -> float:
    if is_correct and (reasoning_score is not None or canvas_score is not None):
        return round(max(0.75, min(0.92, evidence_score + 0.08)), 4)
    if is_correct:
        return 0.68
    if reasoning_score is not None and reasoning_score >= 0.75:
        return 0.56
    return 0.82


def attempt_answer_score(attempt: AssessmentAttempt) -> float:
    return _clamp01(max(float(attempt.answer_score or 0.0), float(attempt.score or 0.0)))


def attempt_evidence_score(attempt: AssessmentAttempt) -> float:
    return _clamp01(max(float(attempt.evidence_score or 0.0), float(attempt.score or 0.0)))


def percent_from_unit_score(value: float | None) -> float:
    return round(_clamp01(value or 0.0) * 100, 2)


def average_unit_score(values: Iterable[float]) -> float:
    rows = [_clamp01(value) for value in values]
    if not rows:
        return 0.0
    return round(sum(rows) / len(rows), 4)


def average_percent(values: Iterable[float]) -> float:
    return percent_from_unit_score(average_unit_score(values))


def _evaluate_reasoning(
    *,
    question: AssessmentQuestion,
    selected_option: AssessmentOption,
    typed_reasoning: str,
    is_correct: bool,
) -> dict[str, Any]:
    text = typed_reasoning.strip()
    if not text:
        return {
            "reasoning_score": None,
            "reasoning_signal": "not_provided",
            "reasoning_feedback": "",
            "reasoning_evaluation_source": "none",
        }
    ai_result = _evaluate_reasoning_with_ai(
        question=question,
        selected_option=selected_option,
        typed_reasoning=text,
        is_correct=is_correct,
    )
    if ai_result is not None:
        return ai_result
    return _heuristic_reasoning_result(
        typed_reasoning=text,
        expected_reasoning=question.expected_reasoning,
        is_correct=is_correct,
    )


def _heuristic_reasoning_result(
    *,
    typed_reasoning: str,
    expected_reasoning: str,
    is_correct: bool,
) -> dict[str, Any]:
    text = typed_reasoning.strip()
    if is_correct:
        score = 0.85 if len(text.split()) >= 3 else 0.65
        return {
            "reasoning_score": score,
            "reasoning_signal": "likely_valid" if score >= 0.75 else "thin_reasoning",
            "reasoning_feedback": "Reasoning was scored by local heuristic because AI evaluation was unavailable.",
            "reasoning_evaluation_source": "heuristic",
        }

    user_terms = _terms(text)
    expected_terms = _terms(expected_reasoning)
    overlap = len(user_terms & expected_terms)
    if overlap >= 2 or len(text.split()) >= 8:
        score = 0.78
        signal = "possible_careless_mistake"
    elif overlap == 1:
        score = 0.45
        signal = "partial_reasoning"
    else:
        score = 0.2
        signal = "weak_or_unrelated"
    return {
        "reasoning_score": score,
        "reasoning_signal": signal,
        "reasoning_feedback": "Reasoning was scored by local heuristic because AI evaluation was unavailable.",
        "reasoning_evaluation_source": "heuristic",
    }


def _evaluate_reasoning_with_ai(
    *,
    question: AssessmentQuestion,
    selected_option: AssessmentOption,
    typed_reasoning: str,
    is_correct: bool,
) -> dict[str, Any] | None:
    if os.getenv("WICARA_PRETEST_LLM_EVALUATION", "true").strip().lower() in {"0", "false", "no"}:
        return None
    settings = get_ai_settings()
    if not settings.openrouter_api_key.strip():
        return None
    try:
        asyncio.get_running_loop()
        return None
    except RuntimeError:
        pass

    correct_option = next((option for option in question.options if option.is_correct), None)
    prompt = _reasoning_prompt(
        question=question,
        selected_option=selected_option,
        correct_option=correct_option,
        typed_reasoning=typed_reasoning,
        is_correct=is_correct,
    )
    try:
        response = asyncio.run(
            ai_client.generate(
                system_instruction="Return valid JSON only.",
                user_instruction=prompt,
                params={"temperature": 0.0, "response_format": {"type": "json_object"}},
            )
        )
        payload = json.loads(response.text)
    except Exception:
        return None

    score = _bounded_score(payload.get("reasoning_score"))
    if score is None:
        return None
    return {
        "reasoning_score": score,
        "reasoning_signal": str(payload.get("reasoning_signal") or "ai_evaluated").strip()[:64],
        "reasoning_feedback": str(payload.get("feedback") or "").strip()[:500],
        "reasoning_evaluation_source": f"{response.provider}:{response.model}",
    }


def _terms(value: str) -> set[str]:
    return {
        term
        for term in re.split(r"[^a-z0-9]+", value.lower())
        if len(term) >= 2
    }


def _reasoning_prompt(
    *,
    question: AssessmentQuestion,
    selected_option: AssessmentOption,
    correct_option: AssessmentOption | None,
    typed_reasoning: str,
    is_correct: bool,
) -> str:
    correct_text = correct_option.text if correct_option is not None else ""
    return f"""
Evaluate the learner's written reasoning for an assessment answer.

Important rules:
- MCQ correctness is already determined by the backend and must remain the anchor.
- Do not mark a wrong MCQ as correct.
- If MCQ is wrong but reasoning is mostly valid, use reasoning_signal="possible_careless_mistake".
- If the method contains a concept error, use reasoning_signal="misconception".
- Score only the reasoning quality from 0.0 to 1.0.

Return JSON only:
{{
  "reasoning_score": 0.0,
  "reasoning_signal": "valid_reasoning|partial_reasoning|thin_reasoning|possible_careless_mistake|misconception|unrelated",
  "feedback": "short diagnostic feedback"
}}

Question prompt:
{question.prompt}

Selected option:
{selected_option.label}. {selected_option.text}

Correct option:
{correct_text}

Backend MCQ correctness:
{is_correct}

Expected reasoning:
{question.expected_reasoning}

Question explanation:
{(question.metadata_json or {}).get("explanation", "")}

Rubric:
{json.dumps(question.rubric_json or {}, ensure_ascii=False)}

Learner reasoning:
{typed_reasoning}
""".strip()


def _bounded_score(value: object) -> float | None:
    try:
        return round(_clamp01(float(value)), 4)
    except (TypeError, ValueError):
        return None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
