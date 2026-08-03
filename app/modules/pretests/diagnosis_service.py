from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.language import normalize_language_code
from app.modules.ai.client import ai_client
from app.modules.ai.config import get_ai_settings
from app.modules.accounts.models import UserAccount
from app.modules.assessments.metrics import (
    PASS_PERCENT,
    calculate_evidence_score,
    confidence_for_attempt,
    diagnostic_signal_for_attempt,
)
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentOption,
    AssessmentQuestion,
    AssessmentSession,
    LearningGoal,
)

PATH_OPTIONS = [
    "review_only",
    "target_reinforcement",
    "target_from_basics",
    "target_intro",
    "repair_prerequisites",
    "full_foundation_path",
]


class PretestDiagnosisService:
    def finalize(
        self,
        session: Session,
        *,
        user: UserAccount,
        assessment: AssessmentSession,
        stop_reason: str,
    ) -> dict[str, Any]:
        graph_scope = assessment.graph_scope_json or {}
        state = assessment.decision_state_json or {}
        language = _assessment_language(assessment)
        state, evidence_report = _apply_deferred_evidence_analysis(
            session,
            assessment=assessment,
            state=state,
            language=language,
        )
        nodes = _diagnosis_nodes(graph_scope=graph_scope, state=state)
        target = next((node for node in nodes if node["role"] == "target"), None)
        recommended_path = _recommended_path(nodes=nodes, stop_reason=stop_reason)
        official = _official_summary(nodes)
        analysis = _analysis_report(
            nodes=nodes,
            target=target,
            recommended_path=recommended_path,
            stop_reason=stop_reason,
            language=language,
            official_percent=official["pure_answer_percent"],
        )
        target_mastery_estimate_percent = round(float((target or {}).get("mastery_score") or 0.0) * 100, 2)
        diagnosis = {
            "language": language,
            "summary": _summary(target=target, recommended_path=recommended_path, language=language),
            "target": target,
            "nodes": nodes,
            "analysis": analysis,
            "stop_reason": stop_reason,
            "score_percent": official["pure_answer_percent"],
            "pure_answer_score": official["correct_count"],
            "pure_answer_total": official["answered_count"],
            "pure_answer_percent": official["pure_answer_percent"],
            "official_scaled_score": official["official_scaled_score"],
            "official_pass": official["official_pass"],
            "official_metric_source": "official_mcq",
            "target_mastery_estimate_percent": target_mastery_estimate_percent,
            "adaptive_mastery_estimate_percent": analysis["adaptive_mastery_estimate_percent"],
            "confidence_percent": 0,
            "overall_mastery_percent": round(official["pure_answer_percent"]),
            "recommended_path": recommended_path,
            "path_options": PATH_OPTIONS,
            "evidence_available": evidence_report["evidence_available"],
            "diagnostic_summary": evidence_report["diagnostic_summary"],
            "evidence_analysis_mode": evidence_report["analysis_mode"],
        }

        assessment.status = "completed"
        assessment.completed_at = datetime.now(UTC)
        assessment.decision_state_json = {**state, "stop_reason": stop_reason}
        goal = session.get(LearningGoal, assessment.learning_goal_id) if assessment.learning_goal_id else None
        if goal is not None:
            goal_metadata = {**(goal.metadata_json or {}), "diagnosis": diagnosis}
            goal.metadata_json = goal_metadata
        assessment.metadata_json = {**(assessment.metadata_json or {}), "diagnosis": diagnosis}
        session.commit()
        return diagnosis


def _apply_deferred_evidence_analysis(
    session: Session,
    *,
    assessment: AssessmentSession,
    state: dict[str, Any],
    language: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = _deferred_evidence_rows(session, assessment=assessment)
    if not rows:
        return state, {
            "evidence_available": False,
            "diagnostic_summary": "",
            "analysis_mode": "none",
        }

    ai_results = _batch_evaluate_evidence_with_ai(rows=rows, language=language)
    analysis_mode = "batched_ai" if ai_results else "batched_heuristic"
    updated_state = {**state}
    for row in rows:
        result = (ai_results or {}).get(row["attempt_id"])
        if result is None:
            result = _heuristic_evidence_result(row=row, language=language)
        result["analysis_mode"] = analysis_mode
        _persist_evidence_result(row=row, result=result)
        _merge_state_attempt(updated_state, attempt_id=row["attempt_id"], result=result)

    return updated_state, {
        "evidence_available": True,
        "diagnostic_summary": _evidence_diagnostic_summary(rows=rows, language=language),
        "analysis_mode": analysis_mode,
    }


def _deferred_evidence_rows(
    session: Session,
    *,
    assessment: AssessmentSession,
) -> list[dict[str, Any]]:
    attempts = list(
        session.scalars(
            select(AssessmentAttempt)
            .where(AssessmentAttempt.session_id == assessment.id)
            .order_by(AssessmentAttempt.submitted_at.asc())
        )
    )
    rows: list[dict[str, Any]] = []
    for attempt in attempts:
        metadata = attempt.evaluation_metadata_json or {}
        has_reasoning = bool((attempt.typed_reasoning or "").strip())
        has_canvas = bool(attempt.used_canvas or attempt.canvas_asset_id is not None)
        if not has_reasoning and not has_canvas:
            continue
        if metadata.get("evidence_deferred") is False and metadata.get("evidence_analysis_mode") not in {
            "deferred_pretest_finalize",
            "batched_ai",
            "batched_heuristic",
        }:
            continue
        if metadata.get("evidence_deferred") is False and metadata.get("evidence_analysis_mode") in {
            "batched_ai",
            "batched_heuristic",
        }:
            continue
        question = session.get(AssessmentQuestion, attempt.question_id)
        selected_option = (
            session.get(AssessmentOption, attempt.selected_option_id)
            if attempt.selected_option_id is not None
            else None
        )
        if question is None or selected_option is None:
            continue
        correct_option = next((option for option in question.options if option.is_correct), None)
        rows.append(
            {
                "attempt": attempt,
                "attempt_id": str(attempt.id),
                "question": question,
                "selected_option": selected_option,
                "correct_option": correct_option,
                "typed_reasoning": (attempt.typed_reasoning or "").strip(),
                "is_correct": bool(attempt.is_correct),
                "answer_score": 1.0 if attempt.is_correct else 0.0,
                "canvas_status": _canvas_status(attempt, metadata),
            }
        )
    return rows


def _batch_evaluate_evidence_with_ai(
    *,
    rows: list[dict[str, Any]],
    language: str,
) -> dict[str, dict[str, Any]] | None:
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
    try:
        response = asyncio.run(
            ai_client.generate(
                system_instruction="Return valid JSON only.",
                user_instruction=_batch_evidence_prompt(rows=rows, language=language),
                params={"temperature": 0.0, "response_format": {"type": "json_object"}},
            )
        )
        payload = json.loads(response.text)
    except Exception:
        return None
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None
    by_id: dict[str, dict[str, Any]] = {}
    source = f"{getattr(response, 'provider', 'ai')}:{getattr(response, 'model', 'model')}"
    valid_ids = {row["attempt_id"] for row in rows}
    row_by_id = {row["attempt_id"]: row for row in rows}
    for item in items:
        if not isinstance(item, dict):
            continue
        attempt_id = str(item.get("attempt_id") or "")
        if attempt_id not in valid_ids:
            continue
        row = row_by_id[attempt_id]
        by_id[attempt_id] = _normalized_evidence_result(
            row=row,
            reasoning_score=_bounded_score(item.get("reasoning_score")),
            reasoning_signal=str(item.get("reasoning_signal") or "ai_evaluated").strip()[:64],
            reasoning_feedback=str(item.get("feedback") or item.get("reasoning_feedback") or "").strip()[:500],
            source=source,
        )
    return by_id or None


def _batch_evidence_prompt(*, rows: list[dict[str, Any]], language: str) -> str:
    compact_rows = []
    for row in rows:
        question: AssessmentQuestion = row["question"]
        selected_option: AssessmentOption = row["selected_option"]
        correct_option: AssessmentOption | None = row["correct_option"]
        compact_rows.append(
            {
                "attempt_id": row["attempt_id"],
                "mcq_is_correct": row["is_correct"],
                "question": question.prompt,
                "selected_option": f"{selected_option.label}. {selected_option.text}",
                "correct_option": (
                    f"{correct_option.label}. {correct_option.text}"
                    if correct_option is not None
                    else ""
                ),
                "expected_reasoning": question.expected_reasoning,
                "question_explanation": (question.metadata_json or {}).get("explanation", ""),
                "learner_reasoning": row["typed_reasoning"],
                "canvas_status": row["canvas_status"],
            }
        )
    language_rule = (
        "Write feedback in natural Bahasa Indonesia."
        if normalize_language_code(language) == "id"
        else "Write feedback in natural English."
    )
    return f"""
Evaluate optional learner reasoning evidence for an adaptive pretest report.

Important rules:
- MCQ correctness has already been decided by the backend and must not change.
- Do not turn a wrong MCQ into correct because the reasoning looks good.
- Do not turn a correct MCQ into wrong because the reasoning is weak.
- Score only diagnostic reasoning quality from 0.0 to 1.0.
- Canvas evidence is currently stored but not vision-evaluated. Do not invent canvas analysis.
- {language_rule}

Return JSON only:
{{
  "items": [
    {{
      "attempt_id": "string",
      "reasoning_score": 0.0,
      "reasoning_signal": "valid_reasoning|partial_reasoning|thin_reasoning|possible_careless_mistake|misconception|unrelated|not_provided",
      "feedback": "short diagnostic feedback"
    }}
  ]
}}

Attempts:
{json.dumps(compact_rows, ensure_ascii=False)}
""".strip()


def _heuristic_evidence_result(*, row: dict[str, Any], language: str) -> dict[str, Any]:
    text = str(row.get("typed_reasoning") or "").strip()
    if not text:
        return _normalized_evidence_result(
            row=row,
            reasoning_score=None,
            reasoning_signal="not_provided",
            reasoning_feedback="",
            source="heuristic",
        )
    question: AssessmentQuestion = row["question"]
    is_correct = bool(row["is_correct"])
    if is_correct:
        score = 0.85 if len(text.split()) >= 3 else 0.65
        signal = "valid_reasoning" if score >= 0.75 else "thin_reasoning"
    else:
        overlap = len(_terms(text) & _terms(question.expected_reasoning))
        if overlap >= 2 or len(text.split()) >= 8:
            score = 0.78
            signal = "possible_careless_mistake"
        elif overlap == 1:
            score = 0.45
            signal = "partial_reasoning"
        else:
            score = 0.2
            signal = "unrelated"
    return _normalized_evidence_result(
        row=row,
        reasoning_score=score,
        reasoning_signal=signal,
        reasoning_feedback=_heuristic_feedback(signal=signal, language=language),
        source="heuristic",
    )


def _normalized_evidence_result(
    *,
    row: dict[str, Any],
    reasoning_score: float | None,
    reasoning_signal: str,
    reasoning_feedback: str,
    source: str,
) -> dict[str, Any]:
    score = _bounded_score(reasoning_score)
    answer_score = float(row["answer_score"])
    evidence_score = calculate_evidence_score(
        answer_score=answer_score,
        reasoning_score=score,
        canvas_score=None,
    )
    diagnostic_signal = diagnostic_signal_for_attempt(
        is_correct=bool(row["is_correct"]),
        reasoning_score=score,
        canvas_score=None,
        reasoning_signal=reasoning_signal,
    )
    confidence = confidence_for_attempt(
        is_correct=bool(row["is_correct"]),
        evidence_score=evidence_score,
        reasoning_score=score,
        canvas_score=None,
    )
    return {
        "answer_score": answer_score,
        "reasoning_score": score,
        "canvas_score": None,
        "canvas_status": row.get("canvas_status"),
        "evidence_score": evidence_score,
        "diagnostic_signal": diagnostic_signal,
        "reasoning_signal": reasoning_signal or "not_provided",
        "reasoning_feedback": reasoning_feedback,
        "reasoning_evaluation_source": source,
        "confidence": confidence,
    }


def _persist_evidence_result(*, row: dict[str, Any], result: dict[str, Any]) -> None:
    attempt: AssessmentAttempt = row["attempt"]
    attempt.reasoning_score = result["reasoning_score"]
    attempt.canvas_score = result["canvas_score"]
    attempt.evidence_score = float(result["evidence_score"])
    attempt.diagnostic_signal = str(result["diagnostic_signal"])
    attempt.confidence = int(round(float(result["confidence"]) * 10))
    attempt.evaluated_result = {
        "verdict": "CORRECT" if attempt.is_correct else "INCORRECT",
        "diagnostic_signal": result["diagnostic_signal"],
        "reasoning_signal": result["reasoning_signal"],
        "reasoning_feedback": result["reasoning_feedback"],
    }
    attempt.evaluation_metadata_json = {
        **(attempt.evaluation_metadata_json or {}),
        "canvas_status": result["canvas_status"],
        "confidence": result["confidence"],
        "reasoning_signal": result["reasoning_signal"],
        "reasoning_feedback": result["reasoning_feedback"],
        "reasoning_evaluation_source": result["reasoning_evaluation_source"],
        "evidence_deferred": False,
        "evidence_analysis_mode": result["analysis_mode"],
    }


def _merge_state_attempt(
    state: dict[str, Any],
    *,
    attempt_id: str,
    result: dict[str, Any],
) -> None:
    node_results = state.get("node_results")
    if not isinstance(node_results, dict):
        return
    for node_state in node_results.values():
        if not isinstance(node_state, dict):
            continue
        attempts = node_state.get("attempts")
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            if not isinstance(attempt, dict) or str(attempt.get("attempt_id") or "") != attempt_id:
                continue
            attempt.update(
                {
                    "reasoning_score": (
                        round(float(result["reasoning_score"]), 4)
                        if result["reasoning_score"] is not None
                        else None
                    ),
                    "canvas_score": result["canvas_score"],
                    "canvas_status": result["canvas_status"],
                    "evidence_score": round(float(result["evidence_score"]), 4),
                    "confidence": round(float(result["confidence"]), 4),
                    "diagnostic_signal": result["diagnostic_signal"],
                    "reasoning_signal": result["reasoning_signal"],
                    "reasoning_feedback": result["reasoning_feedback"],
                    "reasoning_evaluation_source": result["reasoning_evaluation_source"],
                    "evidence_deferred": False,
                }
            )
            return


def _evidence_diagnostic_summary(*, rows: list[dict[str, Any]], language: str) -> str:
    reasoning_count = sum(1 for row in rows if str(row.get("typed_reasoning") or "").strip())
    canvas_count = sum(1 for row in rows if row.get("canvas_status"))
    if normalize_language_code(language) == "id":
        if reasoning_count and canvas_count:
            return f"{reasoning_count} penjelasan langkah dianalisis; {canvas_count} canvas disimpan tetapi tidak memengaruhi skor resmi."
        if reasoning_count:
            return f"{reasoning_count} penjelasan langkah dianalisis sebagai insight diagnostik."
        return f"{canvas_count} canvas disimpan tetapi belum dianalisis visual dan tidak memengaruhi skor resmi."
    if reasoning_count and canvas_count:
        return f"{reasoning_count} written explanations were analyzed; {canvas_count} canvas submissions were stored but did not affect the official score."
    if reasoning_count:
        return f"{reasoning_count} written explanations were analyzed as diagnostic insight."
    return f"{canvas_count} canvas submissions were stored but not vision-evaluated and did not affect the official score."


def _canvas_status(attempt: AssessmentAttempt, metadata: dict[str, Any]) -> str | None:
    status = metadata.get("canvas_status")
    if status:
        return str(status)
    if attempt.canvas_asset_id is not None:
        return "stored_not_evaluated"
    if attempt.used_canvas:
        return "client_canvas_not_uploaded"
    return None


def _heuristic_feedback(*, signal: str, language: str) -> str:
    if normalize_language_code(language) == "id":
        return {
            "valid_reasoning": "Langkah penjelasan terlihat sesuai dengan jawaban.",
            "thin_reasoning": "Penjelasan masih terlalu singkat untuk memastikan proses berpikir.",
            "possible_careless_mistake": "Penjelasan cukup kuat, tetapi pilihan MCQ salah; mungkin ada salah pilih atau kurang teliti.",
            "partial_reasoning": "Sebagian langkah cocok, tetapi belum lengkap.",
            "unrelated": "Penjelasan belum menunjukkan langkah yang sesuai.",
        }.get(signal, "Insight reasoning dibuat dengan heuristic lokal.")
    return {
        "valid_reasoning": "The explanation appears consistent with the answer.",
        "thin_reasoning": "The explanation is still too short to confirm the reasoning process.",
        "possible_careless_mistake": "The explanation is fairly strong, but the MCQ option was wrong; this may be a careless selection.",
        "partial_reasoning": "Some reasoning steps match, but the explanation is incomplete.",
        "unrelated": "The explanation does not show a matching solution path.",
    }.get(signal, "Reasoning insight was generated with a local heuristic.")


def _assessment_language(assessment: AssessmentSession) -> str:
    metadata = assessment.metadata_json or {}
    state = assessment.decision_state_json or {}
    return normalize_language_code(metadata.get("learner_language") or state.get("learner_language") or "id")


def _bounded_score(value: object) -> float | None:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return None


def _terms(value: str) -> set[str]:
    return {
        term
        for term in re.split(r"[^a-z0-9]+", value.lower())
        if len(term) >= 2
    }


def _diagnosis_nodes(
    *,
    graph_scope: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    node_results = state.get("node_results", {})
    rows: list[dict[str, Any]] = []
    for node in graph_scope.get("nodes", []):
        if not isinstance(node, dict):
            continue
        concept_code = str(node.get("concept_code"))
        result = node_results.get(concept_code, {}) if isinstance(node_results, dict) else {}
        status = str(result.get("status", "not_tested"))
        mastery = _mastery(status)
        attempts = result.get("attempts", []) if isinstance(result, dict) else []
        evidence_summary = _evidence_summary(attempts)
        metric_summary = _metric_summary(
            attempts=attempts,
            mastery_score=mastery,
            confidence=_node_confidence(attempts),
        )
        rows.append(
            {
                "concept_id": node.get("concept_id"),
                "concept_code": concept_code,
                "title": node.get("title"),
                "role": node.get("role"),
                "depth": node.get("depth"),
                "parent": node.get("parent"),
                "status": status,
                "mastery_score": mastery,
                "confidence": _node_confidence(attempts),
                "difficulty_reached": _difficulty_reached(result),
                "evidence": attempts,
                "evidence_summary": evidence_summary,
                "answered_count": metric_summary["answered_count"],
                "correct_count": metric_summary["correct_count"],
                "pure_answer_score": metric_summary["correct_count"],
                "pure_answer_total": metric_summary["answered_count"],
                "pure_answer_percent": metric_summary["pure_answer_percent"],
                "official_scaled_score": metric_summary["official_scaled_score"],
                "official_pass": metric_summary["official_pass"],
                "answer_percent": metric_summary["answer_percent"],
                "evidence_percent": metric_summary["evidence_percent"],
                "score_percent": metric_summary["score_percent"],
                "mastery_estimate_percent": metric_summary["mastery_estimate_percent"],
                "confidence_percent": metric_summary["confidence_percent"],
                "metric_source": "official_mcq",
                "diagnostic_metric_source": "adaptive_pretest_diagnosis",
            }
        )
    return rows


def _recommended_path(*, nodes: list[dict[str, Any]], stop_reason: str) -> str:
    if stop_reason == "target_ready":
        return "review_only"
    if stop_reason == "target_reinforcement":
        return "target_reinforcement"
    target = next((node for node in nodes if node["role"] == "target"), {})
    target_status = target.get("status")
    prerequisite_statuses = [
        node.get("status")
        for node in nodes
        if node.get("role") == "prerequisite" and node.get("status") != "not_tested"
    ]
    if any(status == "gap" for status in prerequisite_statuses):
        deepest_gap = any(
            node.get("status") == "gap" and int(node.get("depth") or 0) >= 2
            for node in nodes
        )
        return "full_foundation_path" if deepest_gap else "repair_prerequisites"
    if any(status in {"fragile", "partial"} for status in prerequisite_statuses):
        return "repair_prerequisites"
    if target_status == "fragile":
        return "target_from_basics"
    if target_status == "gap":
        return "target_intro"
    return "target_reinforcement"


def _mastery(status: str) -> float:
    return {
        "ready": 0.9,
        "partial": 0.62,
        "fragile": 0.45,
        "gap": 0.18,
        "probably_ready": 0.72,
        "probably_gap": 0.28,
        "not_tested": 0.0,
    }.get(status, 0.0)


def _node_confidence(attempts: object) -> float:
    if not isinstance(attempts, list) or not attempts:
        return 0.0
    values = [float(item.get("confidence", 0.0)) for item in attempts if isinstance(item, dict)]
    return round(sum(values) / max(1, len(values)), 4)


def _evidence_summary(attempts: object) -> dict[str, Any]:
    if not isinstance(attempts, list) or not attempts:
        return {
            "attempt_count": 0,
            "correct_count": 0,
            "has_evidence": False,
            "reasoning_count": 0,
            "canvas_count": 0,
            "avg_evidence_score": 0.0,
            "avg_reasoning_score": None,
            "reasoning_quality": "not_provided",
            "diagnostic_signals": [],
            "answered_difficulties": [],
            "careless_mistake_possible": False,
            "misconception_detected": False,
        }
    rows = [item for item in attempts if isinstance(item, dict)]
    reasoning_rows = [
        item
        for item in rows
        if _attempt_has_reasoning_evidence(item)
    ]
    canvas_rows = [
        item
        for item in rows
        if str(item.get("canvas_status") or "").strip()
    ]
    evidence_values = [float(item.get("evidence_score", 0.0)) for item in rows]
    reasoning_values = [
        float(item["reasoning_score"])
        for item in rows
        if item.get("reasoning_score") is not None
    ]
    signals = [
        str(item.get("diagnostic_signal") or "")
        for item in rows
        if str(item.get("diagnostic_signal") or "").strip()
    ]
    reasoning_avg = (
        round(sum(reasoning_values) / len(reasoning_values), 4)
        if reasoning_values
        else None
    )
    return {
        "attempt_count": len(rows),
        "correct_count": sum(1 for item in rows if item.get("is_correct") is True),
        "has_evidence": bool(reasoning_rows or canvas_rows),
        "reasoning_count": len(reasoning_rows),
        "canvas_count": len(canvas_rows),
        "avg_evidence_score": round(sum(evidence_values) / max(1, len(evidence_values)), 4),
        "avg_reasoning_score": reasoning_avg,
        "reasoning_quality": _reasoning_quality(reasoning_avg),
        "diagnostic_signals": list(dict.fromkeys(signals)),
        "answered_difficulties": list(dict.fromkeys(str(item.get("difficulty")) for item in rows)),
        "careless_mistake_possible": "possible_careless_mistake" in signals,
        "misconception_detected": bool({"misconception", "misconception_detected"} & set(signals)),
    }


def _attempt_has_reasoning_evidence(item: dict[str, Any]) -> bool:
    signal = str(item.get("reasoning_signal") or "").strip()
    return (
        item.get("reasoning_score") is not None
        or bool(str(item.get("reasoning_feedback") or "").strip())
        or signal not in {"", "not_provided", "deferred"}
    )


def _metric_summary(
    *,
    attempts: object,
    mastery_score: float,
    confidence: float,
) -> dict[str, Any]:
    rows = [item for item in attempts if isinstance(item, dict)] if isinstance(attempts, list) else []
    answered_count = len(rows)
    correct_count = len([item for item in rows if item.get("is_correct") is True])
    answer_values = [
        _attempt_score_value(item, key="answer_score", fallback=1.0 if item.get("is_correct") is True else 0.0)
        for item in rows
    ]
    evidence_values = [
        _attempt_score_value(item, key="evidence_score", fallback=answer_values[index] if index < len(answer_values) else 0.0)
        for index, item in enumerate(rows)
    ]
    answer_avg = correct_count / answered_count if answered_count else 0.0
    evidence_avg = sum(evidence_values) / len(evidence_values) if evidence_values else 0.0
    answer_percent = round(answer_avg * 100, 2)
    return {
        "answered_count": answered_count,
        "correct_count": correct_count,
        "pure_answer_percent": answer_percent,
        "official_scaled_score": round(answer_percent / 10, 2),
        "official_pass": answer_percent >= PASS_PERCENT if answered_count else False,
        "answer_percent": answer_percent,
        "evidence_percent": round(evidence_avg * 100, 2),
        "score_percent": answer_percent,
        "mastery_estimate_percent": round(float(mastery_score or 0.0) * 100, 2),
        "confidence_percent": round(float(confidence or 0.0) * 100, 2),
    }


def _official_summary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [
        attempt
        for node in nodes
        for attempt in (node.get("evidence") or [])
        if isinstance(attempt, dict)
    ]
    answered_count = len(attempts)
    correct_count = len([attempt for attempt in attempts if attempt.get("is_correct") is True])
    pure_answer_percent = round((correct_count / answered_count) * 100, 2) if answered_count else 0.0
    return {
        "answered_count": answered_count,
        "correct_count": correct_count,
        "pure_answer_percent": pure_answer_percent,
        "official_scaled_score": round(pure_answer_percent / 10, 2),
        "official_pass": pure_answer_percent >= PASS_PERCENT if answered_count else False,
    }


def _attempt_score_value(item: dict[str, Any], *, key: str, fallback: float) -> float:
    try:
        return max(0.0, min(1.0, float(item.get(key, fallback))))
    except (TypeError, ValueError):
        return max(0.0, min(1.0, fallback))


def _difficulty_reached(result: dict[str, Any]) -> str | None:
    for difficulty in ("hard", "medium", "easy"):
        if result.get(difficulty) in {"correct", "wrong"}:
            return difficulty
    return None


def _summary(*, target: dict[str, Any] | None, recommended_path: str, language: str) -> str:
    is_id = normalize_language_code(language) == "id"
    title = str(target.get("title")) if target else ("Konsep target" if is_id else "Target concept")
    if not is_id:
        return {
            "review_only": f"You are ready for {title}; a short review is enough.",
            "target_reinforcement": f"You understand the basics of {title}, but need harder practice.",
            "target_from_basics": f"{title} is starting to form, but is not stable at medium level yet.",
            "target_intro": f"{title} is still the main gap; start with an introduction to the concept.",
            "repair_prerequisites": f"Some prerequisites for {title} need strengthening first.",
            "full_foundation_path": f"The foundation before {title} needs to be rebuilt from deeper prerequisites.",
        }.get(recommended_path, f"{title} diagnosis is complete.")
    return {
        "review_only": f"Kamu sudah siap di {title}; cukup review singkat.",
        "target_reinforcement": f"Kamu paham dasar {title}, tapi perlu latihan versi lebih sulit.",
        "target_from_basics": f"{title} mulai terbentuk, tapi belum stabil di level sedang.",
        "target_intro": f"{title} masih menjadi gap utama; mulai dari pengantar konsep.",
        "repair_prerequisites": f"Beberapa prasyarat {title} perlu diperkuat dulu.",
        "full_foundation_path": f"Fondasi sebelum {title} perlu dibangun ulang dari prasyarat terdalam.",
    }.get(recommended_path, f"Diagnosis {title} selesai.")


def _analysis_report(
    *,
    nodes: list[dict[str, Any]],
    target: dict[str, Any] | None,
    recommended_path: str,
    stop_reason: str,
    language: str,
    official_percent: float,
) -> dict[str, Any]:
    is_id = normalize_language_code(language) == "id"
    tested_nodes = [node for node in nodes if node.get("status") != "not_tested"]
    if is_id:
        strengths = [
            f"{node.get('title')} terlihat siap."
            for node in tested_nodes
            if node.get("status") in {"ready", "probably_ready"}
        ]
        gaps = [
            f"{node.get('title')} masih {node.get('status')}."
            for node in tested_nodes
            if node.get("status") in {"gap", "fragile", "partial", "probably_gap"}
        ]
    else:
        strengths = [
            f"{node.get('title')} looks ready."
            for node in tested_nodes
            if node.get("status") in {"ready", "probably_ready"}
        ]
        gaps = [
            f"{node.get('title')} is still {node.get('status')}."
            for node in tested_nodes
            if node.get("status") in {"gap", "fragile", "partial", "probably_gap"}
        ]
    evidence_notes: list[str] = []
    for node in tested_nodes:
        summary = node.get("evidence_summary") or {}
        if not summary.get("has_evidence"):
            continue
        title = node.get("title")
        if summary.get("misconception_detected"):
            evidence_notes.append(
                f"{title}: reasoning menunjukkan miskonsepsi, bukan sekadar salah pilih."
                if is_id
                else f"{title}: the explanation suggests a misconception, not just a wrong option."
            )
        elif summary.get("careless_mistake_possible"):
            evidence_notes.append(
                f"{title}: jawaban MCQ salah, tapi reasoning cukup kuat; mungkin careless."
                if is_id
                else f"{title}: the MCQ answer was wrong, but the reasoning was fairly strong; this may be a careless choice."
            )
        elif summary.get("reasoning_quality") == "not_provided":
            evidence_notes.append(
                f"{title}: evidence tersimpan, tetapi tidak ada penjelasan langkah untuk dianalisis."
                if is_id
                else f"{title}: evidence was stored, but no written steps were available to analyze."
            )
        elif summary.get("reasoning_quality") == "weak":
            evidence_notes.append(
                f"{title}: langkah pengerjaan masih lemah atau belum nyambung."
                if is_id
                else f"{title}: the solution steps are still weak or not connected to the question."
            )
        elif summary.get("canvas_count"):
            evidence_notes.append(
                f"{title}: canvas tersimpan, tapi belum dianalisis visual dan tidak mengubah skor."
                if is_id
                else f"{title}: canvas work was stored, but not vision-evaluated and did not change the score."
            )

    mastery_values = [
        float(node.get("mastery_score") or 0.0)
        for node in tested_nodes
    ]
    return {
        "target_status": target.get("status") if target else "unknown",
        "stop_reason": stop_reason,
        "overall_mastery_percent": round(float(official_percent or 0.0)),
        "adaptive_mastery_estimate_percent": round(
            (sum(mastery_values) / max(1, len(mastery_values))) * 100
        ),
        "strengths": strengths,
        "gaps": gaps,
        "evidence_notes": evidence_notes,
        "recommended_focus": _recommended_focus(recommended_path, language=language),
    }


def _reasoning_quality(score: float | None) -> str:
    if score is None:
        return "not_provided"
    if score >= 0.75:
        return "strong"
    if score >= 0.45:
        return "partial"
    return "weak"


def _recommended_focus(recommended_path: str, *, language: str) -> list[str]:
    if normalize_language_code(language) != "id":
        return {
            "review_only": ["Short target review", "Quick hard-level practice"],
            "target_reinforcement": ["Practice medium-hard target tasks", "Review error patterns"],
            "target_from_basics": ["Start the target from easy tasks", "Build up gradually to medium"],
            "target_intro": ["Introduce the target concept", "Use concrete examples before practice"],
            "repair_prerequisites": ["Review failed prerequisites", "Return to the target after prerequisites stabilize"],
            "full_foundation_path": ["Rebuild the deepest prerequisites", "Reorder the path from foundation"],
        }.get(recommended_path, ["Continue adaptive practice"])
    return {
        "review_only": ["Review singkat target", "Latihan cepat level hard"],
        "target_reinforcement": ["Latihan target level medium-hard", "Bahas pola kesalahan"],
        "target_from_basics": ["Mulai target dari easy", "Naik bertahap ke medium"],
        "target_intro": ["Pengantar konsep target", "Contoh konkret sebelum latihan"],
        "repair_prerequisites": ["Perbaiki prerequisite yang gagal", "Lanjutkan target setelah prerequisite stabil"],
        "full_foundation_path": ["Bangun ulang prerequisite terdalam", "Susun ulang jalur dari fondasi"],
    }.get(recommended_path, ["Lanjutkan latihan adaptif"])
