from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ai.client import ai_client
from app.modules.ai.config import get_ai_settings
from app.modules.assessments.metrics import AssessmentEvidenceEvaluator
from app.modules.learning.models import AssessmentOption, AssessmentQuestion


class PretestEvidenceEvaluator(AssessmentEvidenceEvaluator):
    def evaluate(
        self,
        session: Session,
        *,
        question: AssessmentQuestion,
        selected_option: AssessmentOption,
        typed_reasoning: str,
        canvas_asset_id: object | None,
        used_canvas: bool = False,
        graph_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidates = _prerequisite_candidates(graph_scope or {})
        structured = _evaluate_written_method_with_ai(
            question=question,
            selected_option=selected_option,
            typed_reasoning=typed_reasoning,
            candidates=candidates,
        )
        evaluation = super().evaluate(
            session,
            question=question,
            selected_option=selected_option,
            typed_reasoning=typed_reasoning,
            canvas_asset_id=canvas_asset_id,
            used_canvas=used_canvas,
            reasoning_result_override=structured,
        )
        method = structured or _safe_method_fallback(
            evaluation=evaluation,
            typed_reasoning=typed_reasoning,
        )
        evaluation.update(
            {
                "method_valid": method["method_valid"],
                "evidence_tags": method["evidence_tags"],
                "suspected_prerequisite_code": method["suspected_prerequisite_code"],
                "method_reason": method["method_reason"],
                "method_evaluation_source": method["method_evaluation_source"],
            }
        )
        if evaluation["is_correct"] and method["method_valid"] is False:
            evaluation["diagnostic_signal"] = "method_invalid_despite_correct_answer"
        return evaluation


def _prerequisite_candidates(graph_scope: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    edges = [
        edge
        for edge in graph_scope.get("edges", [])
        if isinstance(edge, dict)
    ]
    for node in graph_scope.get("nodes", []):
        if not isinstance(node, dict) or node.get("role") != "prerequisite":
            continue
        code = str(node.get("concept_code") or "").strip()
        if not code:
            continue
        candidates.append(
            {
                "concept_code": code,
                "title": str(node.get("title") or "").strip(),
                "description": str(node.get("description") or "").strip(),
                "assessment_evidence": node.get("assessment_evidence") or [],
                "common_misconceptions": node.get("common_misconceptions") or [],
                "depth": int(node.get("depth") or 0),
                "parent": str(node.get("parent") or "").strip() or None,
                "relationships": [
                    {
                        "prerequisite_of": str(edge.get("from") or ""),
                        "applicability": str(
                            edge.get("applicability") or "required"
                        ),
                        "reason": str(edge.get("reason") or ""),
                    }
                    for edge in edges
                    if str(edge.get("to") or "") == code
                ],
            }
        )
    return candidates


def _evaluate_written_method_with_ai(
    *,
    question: AssessmentQuestion,
    selected_option: AssessmentOption,
    typed_reasoning: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    reasoning = typed_reasoning.strip()
    if not reasoning:
        return None
    if os.getenv("WICARA_PRETEST_LLM_EVALUATION", "true").strip().lower() in {
        "0",
        "false",
        "no",
    }:
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
    prompt = _written_method_prompt(
        question=question,
        selected_option=selected_option,
        correct_option=correct_option,
        typed_reasoning=reasoning,
        candidates=candidates,
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
    if not isinstance(payload, dict):
        return None
    return _normalize_structured_method_result(
        payload,
        allowed_codes={candidate["concept_code"] for candidate in candidates},
        source=f"{response.provider}:{response.model}",
    )


def _written_method_prompt(
    *,
    question: AssessmentQuestion,
    selected_option: AssessmentOption,
    correct_option: AssessmentOption | None,
    typed_reasoning: str,
    candidates: list[dict[str, Any]],
) -> str:
    return f"""
Evaluate the learner's written method before the adaptive pretest chooses its next node.

Rules:
- MCQ correctness is fixed by the backend. Never change it.
- Judge whether the written method is valid independently from the selected MCQ option.
- Use evidence_tags to describe observable reasoning evidence, not a topic-specific rule.
- suspected_prerequisite_code must be null or exactly one concept_code from prerequisite_candidates.
- Select a prerequisite only when the written evidence supports it. Do not infer a code from its name alone.
- Select a conditional prerequisite only when both the question and the learner's written method satisfy its relationship condition.
- Compare the written method with candidate misconceptions and assessment evidence when they are provided.
- If evidence is insufficient, use method_valid=null and suspected_prerequisite_code=null.

Return JSON only:
{{
  "reasoning_score": 0.0,
  "reasoning_signal": "valid_reasoning|partial_reasoning|thin_reasoning|misconception|unrelated",
  "feedback": "short learner-facing feedback",
  "method_valid": true,
  "evidence_tags": ["observable_evidence_tag"],
  "suspected_prerequisite_code": null,
  "method_reason": "short evidence-based diagnostic reason"
}}

Question:
{question.prompt}

Selected option:
{selected_option.label}. {selected_option.text}

Correct option:
{f'{correct_option.label}. {correct_option.text}' if correct_option is not None else ''}

Backend MCQ correctness:
{bool(selected_option.is_correct)}

Expected reasoning:
{question.expected_reasoning}

Rubric:
{json.dumps(question.rubric_json or {}, ensure_ascii=False)}

Prerequisite candidates:
{json.dumps(candidates, ensure_ascii=False)}

Learner written method:
{typed_reasoning}
""".strip()


def _normalize_structured_method_result(
    payload: dict[str, Any],
    *,
    allowed_codes: set[str],
    source: str,
) -> dict[str, Any]:
    method_valid = _optional_bool(payload.get("method_valid"))
    reasoning_score = _bounded_score(payload.get("reasoning_score"))
    if reasoning_score is None:
        reasoning_score = 0.85 if method_valid is True else 0.3 if method_valid is False else 0.5
    reasoning_signal = str(payload.get("reasoning_signal") or "ai_evaluated").strip()[:64]
    feedback = str(payload.get("feedback") or "").strip()[:500]
    evidence_tags = _evidence_tags(payload.get("evidence_tags"))
    raw_code = str(payload.get("suspected_prerequisite_code") or "").strip()
    suspected_code = raw_code if raw_code in allowed_codes else None
    if raw_code and suspected_code is None:
        evidence_tags = [*evidence_tags, "suspected_prerequisite_rejected_out_of_scope"]
    if method_valid is not False:
        suspected_code = None
    return {
        "reasoning_score": reasoning_score,
        "reasoning_signal": reasoning_signal,
        "reasoning_feedback": feedback,
        "reasoning_evaluation_source": source,
        "method_valid": method_valid,
        "evidence_tags": list(dict.fromkeys(evidence_tags)),
        "suspected_prerequisite_code": suspected_code,
        "method_reason": str(payload.get("method_reason") or feedback).strip()[:500],
        "method_evaluation_source": source,
    }


def _safe_method_fallback(
    *,
    evaluation: dict[str, Any],
    typed_reasoning: str,
) -> dict[str, Any]:
    if not typed_reasoning.strip():
        return {
            "method_valid": None,
            "evidence_tags": [],
            "suspected_prerequisite_code": None,
            "method_reason": "",
            "method_evaluation_source": "none",
        }
    source = str(evaluation.get("reasoning_evaluation_source") or "none")
    signal = str(evaluation.get("reasoning_signal") or "").strip()
    method_valid: bool | None = None
    if source not in {"none", "heuristic"}:
        if signal in {"valid_reasoning", "likely_valid"}:
            method_valid = True
        elif signal in {"misconception", "wrong_method", "unrelated"}:
            method_valid = False
    return {
        "method_valid": method_valid,
        "evidence_tags": [signal] if signal else [],
        "suspected_prerequisite_code": None,
        "method_reason": str(evaluation.get("reasoning_feedback") or "").strip()[:500],
        "method_evaluation_source": source,
    }


def _evidence_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value[:16]:
        tag = re.sub(r"[^a-z0-9]+", "_", str(item).strip().lower()).strip("_")[:64]
        if tag:
            tags.append(tag)
    return list(dict.fromkeys(tags))


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _bounded_score(value: object) -> float | None:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return None
