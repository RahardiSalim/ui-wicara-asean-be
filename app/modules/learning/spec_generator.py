from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.language import normalize_language_code
from app.modules.ai import ai_client
from app.modules.ai.config import DEFAULT_AI_MODEL
from app.modules.ai.errors import AIConfigurationError, AIError
from app.modules.ai.schemas import AIGenerationResponse
from app.modules.learning.concept_template_router import (
    resolve_primary_template_id,
    resolve_template_candidates,
)
from app.modules.learning.template_registry import (
    TemplateRegistryError,
    registered_template_ids,
    resolve_template_entry,
)
from app.modules.learning.template_quality import evaluate_template_quality
from app.modules.learning.template_validation import (
    TemplateValidationError,
    validate_template_spec,
)
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession

_PROMPT_VERSION = "workspace_context_spec_openrouter_v1"
_ROUTER_PROMPT_VERSION = "workspace_context_template_router_openrouter_v1"
_DEFAULT_MODEL = DEFAULT_AI_MODEL
_MAX_ATTEMPTS = 3
_SPEC_MAX_OUTPUT_TOKENS = 8192
_PREVIOUS_RESPONSE_FEEDBACK_LIMIT = 1800
_VALIDATION_FEEDBACK_LIMIT = 24
_VALIDATION_FEEDBACK_ITEM_LIMIT = 280
_ROOT_DIR = Path(__file__).resolve().parents[3]
_SAMPLE_SPECS_DIR = _ROOT_DIR / "wicara_mvp_10_manim_templates" / "specs" / "samples"
_EQUATION_SCHEMA_ID = "manim.equation_balance.v1"
_SCHEMA_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    _EQUATION_SCHEMA_ID: (
        "equation",
        "left_expression",
        "right_expression",
        "solution_steps",
        "final_solution",
    ),
}

_SYSTEM_INSTRUCTION = """
You are a backend spec generator for educational video templates (Manim or Remotion).
Task:
- Produce exactly one JSON object that follows the requested template schema.
- Adapt content to the latest workspace conversation context.
- Keep the tone instructional and concise for students.

Hard requirements:
- Return JSON only, no markdown, no explanation.
- Keep `template_id` exactly as requested.
- Use `language` exactly as requested in `requested_language`.
- Keep all textual fields (title, subtitle, steps, narration) in that same language.
- Include narration fields so voiceover can be generated cleanly.
- Keep values realistic and classroom-safe.
- Keep narration pacing balanced: avoid long intro and provide clear narration per step.
""".strip()

_ROUTER_SYSTEM_INSTRUCTION = """
You are a backend template router for educational video templates (Manim or Remotion).
Task:
- Choose exactly one template_id from allowed_template_ids.
- Use the workspace context and concept_type signal.

Hard requirements:
- Return JSON only, no markdown.
- Use exact format: {"template_id":"...","reason":"..."}.
- template_id must be one value from allowed_template_ids.
- Prefer templates whose semantic domain matches learner context.
""".strip()


class WorkspaceContextSpecGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class WorkspaceGeneratedSpec:
    template_id: str
    spec_json: dict[str, Any]
    debug_meta: dict[str, Any]


def generate_spec_from_workspace_context(
    *,
    workspace: WorkspaceSession,
    language: str,
) -> WorkspaceGeneratedSpec:
    metadata = dict(workspace.metadata_json or {})
    active_concept_type = str(metadata.get("active_concept_type") or "").strip().lower()
    raw_template_id = str(metadata.get("active_template_id") or "").strip().lower()
    template_resolution_source = "active_template_id"
    router_candidates = resolve_template_candidates(active_concept_type)
    mapped_template_id = resolve_primary_template_id(active_concept_type)
    if not router_candidates and mapped_template_id:
        router_candidates = [mapped_template_id]
    router_used = False
    requested_language = _normalize_language(language)
    context_snapshot = _build_context_snapshot(
        workspace=workspace,
        metadata=metadata,
        requested_language=requested_language,
    )

    if raw_template_id and router_candidates:
        normalized_candidates = {
            str(candidate).strip().lower()
            for candidate in router_candidates
            if str(candidate).strip()
        }
        if raw_template_id not in normalized_candidates:
            raw_template_id = mapped_template_id or router_candidates[0]
            template_resolution_source = "concept_type_route_overrode_active_template_id"

    if not raw_template_id:
        if router_candidates:
            planned = _select_template_id_with_ai(
                concept_type=active_concept_type,
                requested_language=requested_language,
                context_snapshot=context_snapshot,
                allowed_template_ids=router_candidates,
            )
            if planned:
                raw_template_id = planned
                template_resolution_source = "openrouter_router_candidates"
                router_used = True
            else:
                raw_template_id = router_candidates[0]
                template_resolution_source = "concept_type_candidates_fallback"
        elif mapped_template_id:
            raw_template_id = mapped_template_id
            template_resolution_source = "concept_type_route_primary"
        else:
            global_candidates = _global_router_candidates()
            router_candidates = global_candidates
            planned = _select_template_id_with_ai(
                concept_type=active_concept_type,
                requested_language=requested_language,
                context_snapshot=context_snapshot,
                allowed_template_ids=global_candidates,
            )
            if planned:
                raw_template_id = planned
                template_resolution_source = "openrouter_router_global"
                router_used = True

    if not raw_template_id:
        raise WorkspaceContextSpecGenerationError(
            "Workspace context is missing active_template_id and no template route could be resolved."
        )

    try:
        resolved = resolve_template_entry(raw_template_id)
    except TemplateRegistryError as exc:
        raise WorkspaceContextSpecGenerationError(str(exc)) from exc

    template_id = resolved.entry.template_id
    node_id = str(metadata.get("active_node_id") or "").strip()
    schema_id = resolved.entry.schema_id
    sample_spec = _normalize_legacy_schema_fields(
        template_id=template_id,
        schema_id=schema_id,
        payload=_load_sample_spec(template_id),
    )
    required_fields = _required_fields_for_schema(schema_id)

    last_error: str | None = None
    last_response: str | None = None
    validation_details: list[dict[str, Any]] = []
    final_ai_response: AIGenerationResponse | None = None
    retry_history: list[dict[str, Any]] = []

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        user_instruction = _build_user_instruction(
            template_id=template_id,
            requested_language=requested_language,
            workspace_id=str(workspace.id),
            context_snapshot=context_snapshot,
            sample_spec=sample_spec,
            required_fields=required_fields,
            attempt_number=attempt,
            max_attempts=_MAX_ATTEMPTS,
            previous_error=last_error,
            validation_details=validation_details,
            previous_response=last_response,
            retry_history=retry_history,
        )
        ai_response = _generate_with_ai(user_instruction=user_instruction)
        final_ai_response = ai_response
        try:
            candidate_payload = _parse_candidate_spec(ai_response.text)
        except WorkspaceContextSpecGenerationError as exc:
            last_error = str(exc)
            validation_details = [
                {
                    "path": "response",
                    "message": str(exc),
                    "type": "json_parse_error",
                    "finish_reason": ai_response.finish_reason,
                }
            ]
            retry_history.append(
                {
                    "attempt": attempt,
                    "error": str(exc),
                    "validation_details": _compact_validation_details(validation_details),
                    "response_excerpt": _feedback_response_excerpt(ai_response.text),
                }
            )
            last_response = _feedback_response_excerpt(ai_response.text)
            if attempt >= _MAX_ATTEMPTS:
                raise
            continue
        candidate_payload = _normalize_legacy_schema_fields(
            template_id=template_id,
            schema_id=schema_id,
            payload=candidate_payload,
        )
        candidate_payload["template_id"] = template_id
        candidate_payload["language"] = requested_language
        candidate_payload.setdefault("id", f"context_auto_{workspace.id}")
        if node_id:
            candidate_payload.setdefault("node_id", node_id)

        try:
            validation_result = validate_template_spec(
                template_id=template_id,
                spec_json=candidate_payload,
            )
        except TemplateValidationError as exc:
            last_error = exc.message
            validation_details = exc.details
            compact_details = _compact_validation_details(exc.details)
            retry_history.append(
                {
                    "attempt": attempt,
                    "error": exc.message,
                    "validation_details": compact_details,
                    "response_excerpt": _feedback_response_excerpt(ai_response.text),
                }
            )
            last_response = _feedback_response_excerpt(ai_response.text)
            if attempt >= _MAX_ATTEMPTS:
                detail_summary = _validation_details_summary(compact_details)
                raise WorkspaceContextSpecGenerationError(
                    (
                        f"AI generated an invalid spec for {template_id} after "
                        f"{_MAX_ATTEMPTS} attempts: {exc.message}. "
                        f"Failed fields: {detail_summary}"
                    )
                ) from exc
            continue

        quality_result = evaluate_template_quality(
            template_id=template_id,
            spec_json=validation_result.normalized_spec,
        )
        if not quality_result.passed:
            quality_errors = [issue.message for issue in quality_result.errors]
            last_error = "Template quality lint failed."
            validation_details = quality_result.to_feedback_details()
            compact_details = _compact_validation_details(validation_details)
            retry_history.append(
                {
                    "attempt": attempt,
                    "error": last_error,
                    "validation_details": compact_details,
                    "response_excerpt": _feedback_response_excerpt(ai_response.text),
                }
            )
            last_response = _feedback_response_excerpt(ai_response.text)
            if attempt >= _MAX_ATTEMPTS:
                error_text = "; ".join(quality_errors) if quality_errors else "Unknown quality issue."
                raise WorkspaceContextSpecGenerationError(
                    f"AI generated low-quality pacing for {template_id}: {error_text}"
                )
            continue

        debug_meta: dict[str, Any] = {
            "spec_source": "context_auto_backend_openrouter",
            "prompt_version": _PROMPT_VERSION,
            "resolved_template_id": template_id,
            "template_resolution_source": template_resolution_source,
            "resolved_node_id": node_id or None,
            "resolved_concept_type": active_concept_type or None,
            "resolved_prerequisites": metadata.get("active_prerequisites"),
            "context_source": metadata.get("context_source"),
            "language": requested_language,
            "requested_language": requested_language,
            "router_used": router_used,
            "router_prompt_version": _ROUTER_PROMPT_VERSION if router_used else None,
            "router_candidate_count": len(router_candidates),
            "router_candidates": router_candidates[:20],
            "attempt": attempt,
            "ai_source": ai_response.provider,
            "ai_model": ai_response.model,
            "ai_finish_reason": ai_response.finish_reason,
            "input_tokens": ai_response.usage.input_tokens if ai_response.usage else None,
            "output_tokens": ai_response.usage.output_tokens if ai_response.usage else None,
            "conversation_turns_used": len(context_snapshot["recent_turns"]),
            "quality_lint": {
                "passed": quality_result.passed,
                "details": quality_result.to_feedback_details()[:8],
                "error_count": len(quality_result.errors),
                "warning_count": len(quality_result.warnings),
                "metrics": quality_result.metrics,
            },
            "retry_history": retry_history[-3:],
        }
        return WorkspaceGeneratedSpec(
            template_id=template_id,
            spec_json=validation_result.normalized_spec,
            debug_meta=debug_meta,
        )

    raise WorkspaceContextSpecGenerationError(
        "AI spec generation failed unexpectedly."
    )


def _normalize_language(language: str) -> str:
    return normalize_language_code(language)[:16]


def _load_sample_spec(template_id: str) -> dict[str, Any]:
    sample_path = _SAMPLE_SPECS_DIR / template_id / "sample_01.json"
    if not sample_path.exists():
        raise WorkspaceContextSpecGenerationError(
            f"Sample spec not found for template '{template_id}' at {sample_path}."
        )
    try:
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceContextSpecGenerationError(
            f"Failed to load sample spec for '{template_id}': {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise WorkspaceContextSpecGenerationError(
            f"Sample spec for '{template_id}' must be a JSON object."
        )
    return payload


def _build_context_snapshot(
    *,
    workspace: WorkspaceSession,
    metadata: dict[str, Any],
    requested_language: str,
) -> dict[str, Any]:
    recent_turns = _recent_turns(workspace.events or [], max_turns=8)
    latest_learner_text = ""
    for turn in reversed(recent_turns):
        if turn["role"] == "learner":
            latest_learner_text = turn["text"]
            break

    learning_context = metadata.get("learning_context")
    learning_context = learning_context if isinstance(learning_context, dict) else {}
    current_module = learning_context.get("current_module")
    current_module = current_module if isinstance(current_module, dict) else {}
    original_target = learning_context.get("original_target")
    original_target = original_target if isinstance(original_target, dict) else {}
    diagnosis = learning_context.get("diagnosis")
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    diagnosis_evidence = diagnosis.get("evidence")
    diagnosis_evidence = (
        diagnosis_evidence if isinstance(diagnosis_evidence, dict) else {}
    )
    evidence_summary = diagnosis_evidence.get("summary")
    evidence_summary = evidence_summary if isinstance(evidence_summary, dict) else {}

    return {
        "workspace_id": str(workspace.id),
        "current_topic": (workspace.current_topic or "").strip(),
        "requested_language": requested_language,
        "active_node_id": _jsonable(metadata.get("active_node_id")),
        "active_concept_type": _jsonable(metadata.get("active_concept_type")),
        "active_template_id": _jsonable(metadata.get("active_template_id")),
        "active_prerequisites": _jsonable(metadata.get("active_prerequisites")),
        "context_source": _jsonable(metadata.get("context_source")),
        "current_phase": _jsonable(metadata.get("current_phase")),
        "hint_level": _jsonable(metadata.get("hint_level")),
        "module_role": _jsonable(current_module.get("role")),
        "current_module": {
            "concept_code": _jsonable(current_module.get("concept_code")),
            "title": _jsonable(current_module.get("title")),
        },
        "original_target": {
            "concept_code": _jsonable(original_target.get("concept_code")),
            "title": _jsonable(original_target.get("title")),
        },
        "learning_objective": _jsonable(
            metadata.get("session_goal_concept_description")
        ),
        "diagnosis_evidence": {
            "status": _jsonable(diagnosis_evidence.get("status")),
            "diagnostic_signals": _jsonable(
                evidence_summary.get("diagnostic_signals", [])
            ),
            "misconception_detected": bool(
                evidence_summary.get("misconception_detected", False)
            ),
        },
        "latest_learner_text": latest_learner_text,
        "recent_turns": recent_turns,
    }


def _recent_turns(events: list[WorkspaceEvent], *, max_turns: int) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for event in events[-(max_turns * 2) :]:
        text = str(event.text_payload or "").strip()
        if not text:
            continue
        actor = str(event.actor_type or "").strip().lower()
        role = "learner" if actor == "learner" else "assistant"
        lines.append({"role": role, "text": text})
    return lines[-max_turns:]


def _global_router_candidates() -> list[str]:
    try:
        rows = registered_template_ids()
    except TemplateRegistryError:
        return []
    return sorted(set(rows))


def _select_template_id_with_ai(
    *,
    concept_type: str,
    requested_language: str,
    context_snapshot: dict[str, Any],
    allowed_template_ids: list[str],
) -> str | None:
    normalized_candidates = [str(item).strip().lower() for item in allowed_template_ids if str(item).strip()]
    normalized_candidates = sorted(set(normalized_candidates))
    if not normalized_candidates:
        return None

    instruction_payload = {
        "task": "choose_template_id",
        "requested_language": requested_language or "en",
        "active_concept_type": concept_type or "",
        "allowed_template_ids": normalized_candidates,
        "workspace_context": {
            "current_topic": context_snapshot.get("current_topic"),
            "latest_learner_text": context_snapshot.get("latest_learner_text"),
            "recent_turns": context_snapshot.get("recent_turns", []),
            "active_prerequisites": context_snapshot.get("active_prerequisites"),
        },
        "output_contract": {
            "json_only": True,
            "must_use_allowed_template_id": True,
            "format": {"template_id": "string", "reason": "string"},
        },
    }
    user_instruction = json.dumps(instruction_payload, ensure_ascii=True, indent=2)
    params = {
        "temperature": 0.1,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
    }

    try:
        response = _run_async_generate(
            system_instruction=_ROUTER_SYSTEM_INSTRUCTION,
            user_instruction=user_instruction,
            params=params,
        )
    except (AIConfigurationError, AIError):
        return None

    payload = _try_parse_json_object_response(response.text)
    if payload is None:
        return None

    selected = str(payload.get("template_id") or "").strip().lower()
    if not selected:
        return None
    if selected not in normalized_candidates:
        return None
    return selected


def _build_user_instruction(
    *,
    template_id: str,
    requested_language: str,
    workspace_id: str,
    context_snapshot: dict[str, Any],
    sample_spec: dict[str, Any],
    required_fields: list[str],
    attempt_number: int,
    max_attempts: int,
    previous_error: str | None,
    validation_details: list[dict[str, Any]],
    previous_response: str | None,
    retry_history: list[dict[str, Any]],
) -> str:
    base_payload: dict[str, Any] = {
        "task": "generate_template_spec_json",
        "template_id": template_id,
        "requested_language": requested_language or "en",
        "attempt_context": {
            "attempt_number": attempt_number,
            "max_attempts": max_attempts,
            "is_retry": attempt_number > 1,
        },
        "output_contract": {
            "must_return_json_object": True,
            "must_include_language_field": True,
            "must_match_text_language_with_language_field": True,
            "must_use_requested_language_exactly": True,
            "required_fields": required_fields,
        },
        "workspace_id": workspace_id,
        "instructions": [
            "Use the sample spec structure as reference.",
            "Adapt the content to the context conversation.",
            "Use requested_language exactly for all user-facing text.",
            "Do not switch language, mix languages, or auto-detect another language.",
            "Keep required fields complete.",
            "Keep narration fields coherent with steps.",
            "Provide at least 2 instructional steps with narration on each step.",
            "Distribute explanation to step narration, not only intro.",
            "Do not return markdown.",
            "If this is a retry, you MUST fix every validator issue listed in retry_feedback.",
        ],
        "context_snapshot": context_snapshot,
        "sample_spec_reference": sample_spec,
    }
    if previous_error:
        compact_details = _compact_validation_details(validation_details)
        base_payload["retry_feedback"] = {
            "previous_error": previous_error,
            "validation_details": compact_details,
            "failed_fields_summary": _validation_details_summary(compact_details),
            "previous_response": previous_response,
            "recent_retry_history": retry_history[-2:],
        }
    return json.dumps(base_payload, ensure_ascii=True, indent=2)


def _required_fields_for_schema(schema_id: str) -> list[str]:
    normalized = str(schema_id or "").strip().lower()
    fields = _SCHEMA_REQUIRED_FIELDS.get(normalized)
    if not fields:
        return []
    return list(fields)


def _normalize_legacy_schema_fields(
    *,
    template_id: str,
    schema_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(payload or {})
    normalized_schema_id = str(schema_id or "").strip().lower()
    if normalized_schema_id == _EQUATION_SCHEMA_ID:
        return _normalize_equation_balance_payload(normalized)
    return normalized


def _normalize_equation_balance_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)

    equation = _first_nonempty_text(
        normalized.get("equation"),
        normalized.get("equation_latex"),
        normalized.get("unbalanced_equation"),
        normalized.get("balanced_equation"),
    )
    if equation and not _first_nonempty_text(normalized.get("equation")):
        normalized["equation"] = equation

    left_expression = _first_nonempty_text(normalized.get("left_expression"))
    right_expression = _first_nonempty_text(normalized.get("right_expression"))
    if not left_expression:
        left_expression = _coerce_expression_terms(normalized.get("left_terms"))
    if not right_expression:
        right_expression = _coerce_expression_terms(normalized.get("right_terms"))
    if (not left_expression or not right_expression) and equation:
        split_left, split_right = _split_equation_sides(equation)
        left_expression = left_expression or split_left
        right_expression = right_expression or split_right
    if left_expression and not _first_nonempty_text(normalized.get("left_expression")):
        normalized["left_expression"] = left_expression
    if right_expression and not _first_nonempty_text(normalized.get("right_expression")):
        normalized["right_expression"] = right_expression

    solution_steps = normalized.get("solution_steps")
    if not isinstance(solution_steps, list) or not solution_steps:
        solution_steps = _coerce_legacy_solution_steps(
            payload=normalized,
            fallback_left=left_expression or "",
            fallback_right=right_expression or "",
        )
    else:
        solution_steps = _coerce_solution_steps(
            source_steps=solution_steps,
            fallback_left=left_expression or "",
            fallback_right=right_expression or "",
        )
    if solution_steps:
        normalized["solution_steps"] = solution_steps

    if not _first_nonempty_text(normalized.get("final_solution")):
        unknown_value = normalized.get("unknown_value")
        if isinstance(unknown_value, (int, float)):
            variable = _infer_variable_symbol(left_expression or equation or "") or "x"
            normalized["final_solution"] = f"{variable} = {unknown_value}"
        else:
            fallback_final = _first_nonempty_text(
                normalized.get("balanced_equation"),
                normalized.get("right_expression"),
            )
            if fallback_final:
                normalized["final_solution"] = fallback_final

    return normalized


def _coerce_expression_terms(raw_terms: Any) -> str:
    if not isinstance(raw_terms, list):
        return ""
    parts: list[str] = []
    for item in raw_terms:
        text = _first_nonempty_text(item)
        if text:
            parts.append(text)
    return " + ".join(parts)


def _coerce_legacy_solution_steps(
    *,
    payload: dict[str, Any],
    fallback_left: str,
    fallback_right: str,
) -> list[dict[str, Any]]:
    candidate_lists: list[list[Any]] = []
    for key in ("balancing_steps", "steps"):
        raw = payload.get(key)
        if isinstance(raw, list) and raw:
            candidate_lists.append(raw)
    for source_steps in candidate_lists:
        coerced = _coerce_solution_steps(
            source_steps=source_steps,
            fallback_left=fallback_left,
            fallback_right=fallback_right,
        )
        if coerced:
            return coerced
    return []


def _coerce_solution_steps(
    *,
    source_steps: list[Any],
    fallback_left: str,
    fallback_right: str,
) -> list[dict[str, Any]]:
    normalized_steps: list[dict[str, Any]] = []
    for idx, item in enumerate(source_steps[:12]):
        if not isinstance(item, dict):
            continue
        operation = _first_nonempty_text(
            item.get("operation"),
            item.get("title"),
            item.get("label"),
            f"Langkah {idx + 1}",
        )
        left_result = _first_nonempty_text(
            item.get("left_result"),
            item.get("equation"),
            fallback_left,
        )
        right_result = _first_nonempty_text(
            item.get("right_result"),
            fallback_right,
        )
        explanation = _first_nonempty_text(
            item.get("explanation"),
            item.get("body"),
            item.get("label"),
            "",
        )
        narration = _first_nonempty_text(
            item.get("narration"),
            item.get("voiceover"),
            explanation,
        )
        value = _coerce_float(item.get("value"), default=0.0)
        normalized_steps.append(
            {
                "operation": operation,
                "value": value,
                "left_result": left_result,
                "right_result": right_result,
                "explanation": explanation,
                "narration": narration,
            }
        )
    return normalized_steps


def _coerce_float(value: Any, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = _first_nonempty_text(value)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _split_equation_sides(equation: str) -> tuple[str, str]:
    text = _first_nonempty_text(equation)
    if not text:
        return "", ""
    for separator in ("=", "->", "→", "=>"):
        if separator not in text:
            continue
        left, right = text.split(separator, 1)
        return left.strip(), right.strip()
    return "", ""


def _infer_variable_symbol(text: str) -> str:
    value = _first_nonempty_text(text)
    if not value:
        return ""
    match = re.search(r"[a-zA-Z]", value)
    if not match:
        return ""
    return match.group(0)


def _first_nonempty_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _generate_with_ai(*, user_instruction: str) -> AIGenerationResponse:
    params = {
        "temperature": 0.3,
        "max_tokens": _SPEC_MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
    }
    try:
        return _run_async_generate(
            system_instruction=_SYSTEM_INSTRUCTION,
            user_instruction=user_instruction,
            params=params,
        )
    except AIConfigurationError as exc:
        raise WorkspaceContextSpecGenerationError(str(exc)) from exc
    except AIError as exc:
        raise WorkspaceContextSpecGenerationError(
            f"AI spec generation failed: {exc}"
        ) from exc


def _run_async_generate(
    *,
    system_instruction: str,
    user_instruction: str,
    params: dict[str, Any],
) -> AIGenerationResponse:
    async def _call() -> AIGenerationResponse:
        return await ai_client.generate(
            provider="openrouter",
            model=_DEFAULT_MODEL,
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            params=params,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call())

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(_call())).result()


def _parse_candidate_spec(raw_text: str) -> dict[str, Any]:
    payload = _try_parse_json_object_response(raw_text)
    if payload is None:
        raise WorkspaceContextSpecGenerationError(
            "AI response is not a valid JSON object for spec generation."
        )
    return payload


def _try_parse_json_object_response(raw_text: str) -> dict[str, Any] | None:
    text = (raw_text or "").strip()
    if not text:
        return None

    candidates = [text]
    fenced = _extract_fenced_json(text)
    if fenced:
        candidates.append(fenced)
    sliced = _slice_outer_object(text)
    if sliced and sliced not in candidates:
        candidates.append(sliced)

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _feedback_response_excerpt(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if len(text) <= _PREVIOUS_RESPONSE_FEEDBACK_LIMIT:
        return text
    return f"{text[:_PREVIOUS_RESPONSE_FEEDBACK_LIMIT]}...[truncated]"


def _compact_validation_details(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in details[:_VALIDATION_FEEDBACK_LIMIT]:
        path = str(item.get("path") or "spec_json").strip() or "spec_json"
        message = str(item.get("message") or "Invalid value.").strip()
        if len(message) > _VALIDATION_FEEDBACK_ITEM_LIMIT:
            message = f"{message[:_VALIDATION_FEEDBACK_ITEM_LIMIT]}...[truncated]"
        issue_type = str(item.get("type") or "validation_error").strip() or "validation_error"
        compact.append(
            {
                "path": path,
                "message": message,
                "type": issue_type,
            }
        )
    return compact


def _validation_details_summary(details: list[dict[str, Any]]) -> str:
    if not details:
        return "n/a"
    parts: list[str] = []
    for item in details[:6]:
        path = str(item.get("path") or "spec_json").strip() or "spec_json"
        message = str(item.get("message") or "Invalid value.").strip()
        parts.append(f"{path}: {message}")
    return "; ".join(parts)


def _extract_fenced_json(text: str) -> str:
    marker = "```"
    start = text.find(marker)
    if start < 0:
        return ""
    end = text.find(marker, start + len(marker))
    if end < 0:
        return ""
    chunk = text[start + len(marker) : end].strip()
    if chunk.lower().startswith("json"):
        chunk = chunk[4:].strip()
    return chunk


def _slice_outer_object(text: str) -> str:
    left = text.find("{")
    right = text.rfind("}")
    if left < 0 or right < 0 or right <= left:
        return ""
    return text[left : right + 1]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return str(value)
