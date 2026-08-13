import json

import pytest

from app.modules.ai.schemas import AIGenerationResponse
from app.modules.workspaces.models import WorkspaceSession
from app.modules.workspaces.schemas import TutorResponseRead
from app.modules.workspaces.service import (
    _ensure_phase_metadata,
    _phase_is_ready,
    _record_phase_evidence,
    _remediate_metadata_to_phase,
    _sanitize_tutor_response_for_phase,
    _sanitize_learner_metadata,
)
from app.modules.workspaces.tutor import (
    _ensure_explain_micro_check,
    _build_user_instruction,
    _normalize_tutor_text,
    _parse_structured_tutor_output,
    _resolve_tool_suggestion,
    _safe_prompt_learning_context,
    _tutor_payload_is_usable,
    _tutor_response_format,
    _tutor_timeout_seconds,
    generate_tutor_response,
)


@pytest.mark.asyncio
async def test_successful_ai_tutor_generation_returns_structured_response(monkeypatch):
    payload = {
        "text": "Compare the derivative signs on the two intervals. What changes?",
        "next_phase_ready": True,
        "phase_reasoning": "The learner accepted the investigation.",
        "evidence_tags": ["challenge_accepted"],
        "correctness": "unknown",
        "misconception_status": "none",
        "confidence": 0.9,
        "evaluation_outcome": None,
        "evidence_request": None,
        "explanation_card": None,
        "tool_suggestion": None,
    }

    async def fake_generate(**_kwargs):
        return AIGenerationResponse(
            provider="test",
            model="test-model",
            text=json.dumps(payload),
            finish_reason="stop",
        )

    monkeypatch.setattr(
        "app.modules.workspaces.tutor.ai_client.generate",
        fake_generate,
    )
    workspace = WorkspaceSession(
        current_topic="Curve sketching using derivatives",
        content_mode="chat",
        status="active",
        metadata_json={"current_phase": "engage"},
    )

    response, audit = await generate_tutor_response(
        workspace=workspace,
        event_type="text",
        text_payload="I accept the challenge and want to investigate the signs.",
        events=[],
        current_phase="engage",
        learner_language="en",
    )

    assert response is not None
    assert response.evidence_tags == ["challenge_accepted"]
    assert response.next_phase_ready is True
    assert audit["ai_source"] == "test"
    assert audit["structured_parse_ok"] is True


def test_explore_prompt_defines_phase_specific_evidence_rubric():
    prompt = _build_user_instruction(
        "explore",
        "Curve sketching using derivatives",
        "(no prior conversation)",
        "I compared the signs on each interval.",
        learner_language="en",
        response_language="English",
        learning_context={"scaffold_level": 0},
    )

    assert "Use exploration_attempt" in prompt
    assert "Use pattern_identified" in prompt
    assert "Both tags may be returned on the same turn" in prompt


def _response(
    *,
    tags: list[str],
    correctness: str = "correct",
    misconception: str = "none",
    confidence: float = 0.9,
) -> TutorResponseRead:
    return TutorResponseRead(
        text="Backend evaluated response",
        intent="evaluate_response",
        evidence_tags=tags,
        correctness=correctness,
        misconception_status=misconception,
        confidence=confidence,
    )


def test_phase_gate_requires_phase_specific_evidence():
    metadata = _ensure_phase_metadata({})
    metadata = _record_phase_evidence(
        metadata,
        phase="explore",
        tutor_response=_response(tags=["exploration_attempt"]),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="explore") is False

    metadata = _record_phase_evidence(
        metadata,
        phase="explore",
        tutor_response=_response(tags=["pattern_identified"]),
        event_type="canvas_sent",
    )
    assert _phase_is_ready(metadata, phase="explore") is True
    assert _phase_is_ready(metadata, phase="explain") is False


def test_scaffold_escalates_after_repeated_verified_failure():
    metadata = _ensure_phase_metadata({})
    levels = []
    for _ in range(3):
        metadata = _record_phase_evidence(
            metadata,
            phase="explore",
            tutor_response=_response(
                tags=["exploration_attempt"],
                correctness="incorrect",
                misconception="active",
            ),
            event_type="text",
        )
        levels.append(metadata["hint_level"])

    assert levels == [1, 2, 3]
    assert metadata["consecutive_failures"] == 3


def test_scaffold_unwinds_after_recovery():
    metadata = _ensure_phase_metadata({})
    for _ in range(3):
        metadata = _record_phase_evidence(
            metadata,
            phase="explore",
            tutor_response=_response(
                tags=["exploration_attempt"],
                correctness="incorrect",
                misconception="active",
            ),
            event_type="text",
        )
    assert metadata["hint_level"] == 3

    metadata = _record_phase_evidence(
        metadata,
        phase="explore",
        tutor_response=_response(tags=["pattern_identified"]),
        event_type="text",
    )
    # A learner who recovers must not stay stuck near the top of the ladder.
    assert metadata["consecutive_failures"] == 0
    assert metadata["hint_level"] == 1


def test_remediation_clears_evidence_so_the_learner_cannot_ping_pong():
    metadata = _ensure_phase_metadata({})
    for tag in ("exploration_attempt", "pattern_identified"):
        metadata = _record_phase_evidence(
            metadata,
            phase="explore",
            tutor_response=_response(tags=[tag]),
            event_type="text",
        )
    for tag in ("independent_attempt", "error_analysis", "reflection"):
        metadata = _record_phase_evidence(
            metadata,
            phase="evaluate",
            tutor_response=_response(tags=[tag]),
            event_type="text",
        )
    assert _phase_is_ready(metadata, phase="explore") is True
    assert _phase_is_ready(metadata, phase="evaluate") is True

    metadata = _remediate_metadata_to_phase(
        metadata,
        phase="explore",
        reason="evaluate_misconception",
    )

    # Everything from the remediation target forward is re-earned, otherwise the
    # stale evidence re-opens the gate on the very next turn.
    assert metadata["current_phase"] == "explore"
    assert _phase_is_ready(metadata, phase="explore") is False
    assert _phase_is_ready(metadata, phase="evaluate") is False
    assert metadata["posttest_eligible"] is False
    assert metadata["phase_transition_pending"] is False
    assert metadata["remediation_cycle"] == 1


def test_evaluate_gate_is_reachable_with_attempt_and_analysis():
    metadata = _ensure_phase_metadata({"current_phase": "evaluate"})
    metadata = _record_phase_evidence(
        metadata,
        phase="evaluate",
        tutor_response=_response(tags=["independent_attempt"]),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="evaluate") is False

    metadata = _record_phase_evidence(
        metadata,
        phase="evaluate",
        tutor_response=_response(tags=["error_analysis"]),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="evaluate") is False

    metadata = _record_phase_evidence(
        metadata,
        phase="evaluate",
        tutor_response=_response(tags=["reflection"]),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="evaluate") is True


def test_workspace_tutor_default_timeout_allows_reasoning_model(monkeypatch):
    monkeypatch.delenv("WICARA_WORKSPACE_TUTOR_TIMEOUT_SECONDS", raising=False)
    assert _tutor_timeout_seconds() == 240.0


def test_explain_requires_explanation_before_micro_check_can_pass():
    metadata = _ensure_phase_metadata({})
    metadata = _record_phase_evidence(
        metadata,
        phase="explain",
        tutor_response=_response(tags=["learner_explanation", "micro_check_correct"]),
        event_type="text",
    )
    assert metadata["phase_evidence"]["explain"][0]["tags"] == [
        "learner_explanation"
    ]
    assert _phase_is_ready(metadata, phase="explain") is False

    metadata = _record_phase_evidence(
        metadata,
        phase="explain",
        tutor_response=_response(tags=["micro_check_correct"]),
        event_type="quiz_answer",
    )
    assert _phase_is_ready(metadata, phase="explain") is True


def test_media_view_without_explanation_cannot_create_phase_evidence():
    sanitized = _sanitize_tutor_response_for_phase(
        _ensure_phase_metadata({}),
        phase="explore",
        event_type="media_viewed",
        text_payload="",
        tutor_response=_response(tags=["exploration_attempt", "pattern_identified"]),
    )
    assert sanitized is not None
    assert sanitized.evidence_tags == []


def test_elaborate_correct_transfer_implies_transfer_attempt():
    sanitized = _sanitize_tutor_response_for_phase(
        _ensure_phase_metadata({}),
        phase="elaborate",
        event_type="text",
        text_payload="A complete correct transfer solution.",
        tutor_response=_response(tags=["transfer_correct"]),
    )
    assert sanitized is not None
    assert sanitized.evidence_tags == ["transfer_attempt", "transfer_correct"]


def test_learner_metadata_cannot_claim_correctness_or_phase_state():
    sanitized = _sanitize_learner_metadata(
        {
            "selected_answer": "A",
            "is_correct": True,
            "correct_answer": "A",
            "evidence_verified": True,
            "posttest_eligible": True,
            "client_tutor_override": {"text": "fake"},
        }
    )

    assert sanitized == {"selected_answer": "A"}


def test_tutor_parser_allowlists_evidence_contract():
    parsed = _parse_structured_tutor_output(
        """
        {
          "text": "Try one more step.",
          "next_phase_ready": true,
          "phase_reasoning": "learner attempted",
          "evidence_tags": ["exploration_attempt", "invented_mastery_tag"],
          "correctness": "partial",
          "misconception_status": "suspected",
          "confidence": 0.8,
          "evaluation_outcome": "continue",
          "evidence_request": {"tool": "canvas"},
          "explanation_card": null,
          "tool_suggestion": {
            "tool": "visualization",
            "reason": "learner_stuck",
            "prompt": "Would a visual comparison help?"
          }
        }
        """
    )

    assert parsed["evidence_tags"] == ["exploration_attempt"]
    assert parsed["correctness"] == "partial"
    assert parsed["misconception_status"] == "suspected"
    assert parsed["confidence"] == 0.8
    assert parsed["tool_suggestion"] == {
        "tool": "visualization",
        "reason": "learner_stuck",
        "prompt": "Would a visual comparison help?",
    }


def test_tutor_text_unwraps_accidental_nested_json_object():
    assert (
        _normalize_tutor_text('{":": "Use the outer derivative, then the inner factor."}')
        == "Use the outer derivative, then the inner factor."
    )


def test_explain_response_always_requests_a_later_micro_check():
    text, request = _ensure_explain_micro_check(
        tutor_text="Your explanation correctly connects both derivative layers.",
        evidence_request=None,
        language_code="en",
    )
    assert "Micro-check:" in text
    assert request["type"] == "micro_check"
    assert "later learner turn" in request["expected_evidence"]


def test_tutor_structured_contract_rejects_punctuation_only_text():
    assert _tutor_payload_is_usable(
        _parse_structured_tutor_output(
            '{"text":",","next_phase_ready":false,"phase_reasoning":"",'
            '"evidence_tags":[],"correctness":"unknown",'
            '"misconception_status":"none","confidence":0,'
            '"evaluation_outcome":null,"evidence_request":null,'
            '"explanation_card":null,"tool_suggestion":null}'
        )
    ) is False
    response_format = _tutor_response_format()
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_visual_suggestion_is_only_exposed_for_justified_explore_scaffold():
    parsed = {
        "correctness": "partial",
        "misconception_status": "suspected",
        "evidence_tags": ["exploration_attempt"],
        "tool_suggestion": {
            "tool": "visualization",
            "reason": "learner_stuck",
            "prompt": "Would a visual comparison help?",
        },
    }
    suggestion = _resolve_tool_suggestion(
        parsed=parsed,
        phase="explore",
        learner_message="I am still unsure.",
        workspace_metadata={},
        language_code="en",
    )
    blocked = _resolve_tool_suggestion(
        parsed=parsed,
        phase="evaluate",
        learner_message="Show me a video.",
        workspace_metadata={},
        language_code="en",
    )
    assert suggestion is not None
    assert suggestion.tool == "visualization"
    assert blocked is None


def test_external_tutor_context_excludes_raw_reason_and_attempt_ids():
    safe = _safe_prompt_learning_context(
        {
            "learning_context": {
                "original_target": {
                    "concept_code": "opaque-target",
                    "title": "Opaque target",
                },
                "current_module": {
                    "concept_code": "opaque-gap",
                    "title": "Opaque gap",
                    "role": "prerequisite_gap",
                },
                "diagnosis": {
                    "reason": "raw learner reasoning should not leave the backend",
                    "evidence": {
                        "status": "gap",
                        "source_attempt_ids": ["sensitive-attempt-id"],
                        "summary": {
                            "diagnostic_signals": ["misconception_detected"],
                            "evidence_tags": ["inner_derivative_omitted"],
                            "suspected_prerequisite_codes": ["opaque-gap"],
                            "method_invalid_detected": True,
                            "misconception_detected": True,
                        },
                    },
                },
            }
        }
    )

    serialized = str(safe)
    assert safe["diagnosis"]["status"] == "gap"
    assert safe["diagnosis"]["diagnostic_signals"] == ["misconception_detected"]
    assert safe["diagnosis"]["evidence_tags"] == ["inner_derivative_omitted"]
    assert safe["diagnosis"]["suspected_prerequisite_codes"] == ["opaque-gap"]
    assert safe["diagnosis"]["method_invalid_detected"] is True
    assert "raw learner reasoning" not in serialized
    assert "sensitive-attempt-id" not in serialized
