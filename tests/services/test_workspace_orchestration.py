import json

import pytest

from app.modules.ai.schemas import AIGenerationResponse
from app.modules.workspaces.models import WorkspaceSession
from app.modules.workspaces.schemas import TutorResponseRead
from app.modules.workspaces.service import (
    _ensure_phase_metadata,
    _phase_is_ready,
    _record_phase_evidence,
    _response_reestablishes_phase_readiness,
    _remediate_metadata_to_phase,
    _sanitize_tutor_response_for_phase,
    _sanitize_learner_metadata,
)
from app.modules.workspaces.tutor import (
    _SYSTEM_INSTRUCTION,
    _evaluate_turn_completes_evidence,
    _fallback_response,
    _ensure_explain_micro_check,
    _ground_checkpoint_question,
    _ensure_initial_target_bridge,
    _build_user_instruction,
    _checkpoint_stay_layer_scaffold,
    _limit_phase_evidence_request,
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
        "text": "Your comparison correctly connects the derivative signs to the curve's direction.",
        "next_phase_ready": True,
        "phase_reasoning": "The learner accepted the investigation.",
        "phase_checkpoint_question": (
            "After comparing those intervals, are you confident why the derivative "
            "sign determines whether the curve rises or falls?"
        ),
        "next_phase_opening_prompt": (
            "Compare the derivative signs on two intervals and describe what changes."
        ),
        "evidence_tags": ["challenge_accepted", "prior_knowledge_shared"],
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
    assert response.evidence_tags == ["challenge_accepted", "prior_knowledge_shared"]
    assert response.next_phase_ready is True
    assert response.phase_checkpoint_question == payload["phase_checkpoint_question"]
    assert response.next_phase_opening_prompt == payload["next_phase_opening_prompt"]
    assert audit["ai_source"] == "test"
    assert audit["structured_parse_ok"] is True


@pytest.mark.asyncio
async def test_completed_explain_micro_check_does_not_assign_another_one(monkeypatch):
    payload = {
        "text": "Your derivative and explanation are both correct.",
        "next_phase_ready": True,
        "phase_reasoning": "Explanation and later micro-check are complete.",
        "phase_checkpoint_question": "Does the inner factor now make sense?",
        "next_phase_opening_prompt": "Application: differentiate (x²-1)³.",
        "evidence_tags": ["learner_explanation", "micro_check_correct"],
        "correctness": "correct",
        "misconception_status": "resolved",
        "confidence": 0.95,
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

    monkeypatch.setattr("app.modules.workspaces.tutor.ai_client.generate", fake_generate)
    response, _audit = await generate_tutor_response(
        workspace=WorkspaceSession(
            current_topic="Chain rule",
            content_mode="chat",
            status="active",
            metadata_json={
                "current_phase": "explain",
                "phase_evidence": {
                    "explain": [
                        {
                            "tags": ["learner_explanation"],
                            "confidence": 0.9,
                            "misconception_status": "none",
                        }
                    ]
                },
            },
        ),
        event_type="text",
        text_payload="The derivative is 1/sqrt(2x+1).",
        events=[],
        current_phase="explain",
        learner_language="en",
    )

    assert response is not None
    assert "Micro-check:" not in response.text
    assert response.evidence_request is None


@pytest.mark.asyncio
async def test_same_turn_explanation_cannot_consume_later_micro_check(monkeypatch):
    payload = {
        "text": "Your explanation connects both rates.",
        "next_phase_ready": True,
        "phase_reasoning": "The learner explained and applied the rule.",
        "phase_checkpoint_question": "Are you confident that both rates multiply?",
        "next_phase_opening_prompt": "Apply it to another problem.",
        "evidence_tags": ["learner_explanation", "micro_check_correct"],
        "correctness": "correct",
        "misconception_status": "resolved",
        "confidence": 0.95,
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

    monkeypatch.setattr("app.modules.workspaces.tutor.ai_client.generate", fake_generate)
    response, _audit = await generate_tutor_response(
        workspace=WorkspaceSession(
            current_topic="Chain rule",
            content_mode="chat",
            status="active",
            metadata_json={"current_phase": "explain"},
        ),
        event_type="text",
        text_payload="Sequential scale factors multiply, so the derivative is 6x.",
        events=[],
        current_phase="explain",
        learner_language="en",
    )

    assert response is not None
    assert response.evidence_tags == ["learner_explanation"]
    assert response.next_phase_ready is False
    assert "Micro-check:" in response.text
    assert response.evidence_request is not None
    assert response.evidence_request["type"] == "micro_check"


@pytest.mark.asyncio
async def test_ready_phase_gets_contextual_checkpoint_when_model_omits_it(monkeypatch):
    payload = {
        "text": "You chose a simpler nested function and named what you already know.",
        "next_phase_ready": True,
        "phase_reasoning": "The learner accepted and shared prior knowledge.",
        "phase_checkpoint_question": None,
        "next_phase_opening_prompt": "Try one Chain rule example.",
        "evidence_tags": ["challenge_accepted", "prior_knowledge_shared"],
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

    monkeypatch.setattr("app.modules.workspaces.tutor.ai_client.generate", fake_generate)
    response, _audit = await generate_tutor_response(
        workspace=WorkspaceSession(
            current_topic="Chain rule",
            content_mode="chat",
            status="active",
            metadata_json={"current_phase": "engage"},
        ),
        event_type="text",
        text_payload="I want to investigate a simpler example.",
        events=[],
        current_phase="engage",
        learner_language="en",
    )

    assert response is not None
    assert response.next_phase_ready is True
    assert response.phase_checkpoint_question == (
        "Does this starting point for Chain rule match what you want to investigate?"
    )


@pytest.mark.asyncio
async def test_completed_evaluate_error_analysis_does_not_append_another_request(monkeypatch):
    payload = {
        "text": "That correction identifies the missing inner derivative.",
        "next_phase_ready": False,
        "phase_reasoning": "All staged Evaluate evidence is complete.",
        "phase_checkpoint_question": None,
        "next_phase_opening_prompt": None,
        "evidence_tags": ["error_analysis"],
        "correctness": "correct",
        "misconception_status": "none",
        "confidence": 0.95,
        "evaluation_outcome": "passed",
        "evidence_request": {
            "type": "open_response",
            "prompt": "Which step still needs checking?",
        },
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

    monkeypatch.setattr("app.modules.workspaces.tutor.ai_client.generate", fake_generate)
    monkeypatch.setattr(
        "app.modules.workspaces.tutor._is_repetitive_response",
        lambda *_args: True,
    )

    def unexpected_anti_repeat(**_kwargs):
        raise AssertionError("Completed Evaluate feedback must not become remediation.")

    monkeypatch.setattr(
        "app.modules.workspaces.tutor._anti_repeat_response",
        unexpected_anti_repeat,
    )
    response, _audit = await generate_tutor_response(
        workspace=WorkspaceSession(
            current_topic="Chain rule",
            content_mode="chat",
            status="active",
            metadata_json={
                "current_phase": "evaluate",
                "phase_evidence": {
                    "evaluate": [
                        {"tags": ["independent_attempt"]},
                        {"tags": ["error_analysis"]},
                    ]
                },
            },
        ),
        event_type="text",
        text_payload="The missing factor is the derivative of the inner function.",
        events=[],
        current_phase="evaluate",
        learner_language="en",
    )

    assert response is not None
    assert response.text == payload["text"]
    assert response.evidence_request is None


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
    assert "Ground it in the learner's actual evidence" in prompt
    assert "Bad: 'Have you understood the learning goal" in prompt
    assert "Do not call an Explore activity transfer" in prompt
    assert "put the Explain opening in next_phase_opening_prompt" in prompt
    assert "choose a small input change" in prompt
    assert "Do not label a pattern as identified" in prompt


def test_explain_prompt_teaches_after_repeated_conceptual_confusion():
    prompt = _build_user_instruction(
        "explain",
        "Chain rule",
        "Student: I know both derivatives, but multiplication feels arbitrary.",
        "I still cannot explain why the rates multiply instead of add.",
        learner_language="en",
        response_language="English",
        learning_context={"scaffold_level": 2},
    )

    assert "stop eliciting and teach the missing conceptual model" in prompt
    assert "rather than asking for the same explanation again" in prompt
    assert "Treat a learner hypothesis" in _SYSTEM_INSTRUCTION


def test_elaborate_prompt_preserves_demonstrated_method_and_isolates_new_error():
    prompt = _build_user_instruction(
        "elaborate",
        "Chain rule",
        "Student: I multiplied the outer and inner derivatives correctly before.",
        "The inner derivative of 2x^3 is 2x^2.",
        learner_language="en",
        response_language="English",
        learning_context={"scaffold_level": 1},
    )

    assert "Preserve any method the learner already demonstrated" in prompt
    assert "recompute only that step" in prompt
    assert "do not claim the earlier concept was forgotten" in prompt
    assert "do not add analysis or skills from the original target" in prompt
    assert "Never mention or test the original target in a phase opening" in prompt


def test_feedback_policy_forbids_generic_or_ungrounded_mastery_claims():
    prompt = _build_user_instruction(
        "explore",
        "Chain rule",
        "(no prior conversation)",
        "Maybe the inner derivative should multiply, but I need to test it.",
        learner_language="en",
        response_language="English",
        learning_context={"scaffold_level": 0},
    )

    assert "Ground feedback in the latest learner action" in _SYSTEM_INSTRUCTION
    assert 'Do not open with generic praise such as "Excellent!"' in _SYSTEM_INSTRUCTION
    assert "tentative" in _SYSTEM_INSTRUCTION


def test_engage_prompt_uses_diagnosis_and_keeps_explore_task_hidden():
    prompt = _build_user_instruction(
        "engage",
        "Chain rule",
        "(no prior conversation)",
        "I am ready to learn.",
        learner_language="en",
        response_language="English",
        learning_context={
            "scaffold_level": 0,
            "original_target": {"title": "Curve sketching using derivatives"},
            "current_module": {"title": "Chain rule", "role": "prerequisite_gap"},
            "diagnosis": {"diagnostic_signals": ["concept_gap_likely"]},
        },
    )

    assert "current prerequisite will later support the original target" in prompt
    assert "Mention the original target only on that first turn" in prompt
    assert "bridges the hook directly to a concrete example" in prompt
    assert "text must contain feedback" in prompt
    assert "must not ask another learning question" in prompt


def test_engage_readiness_requires_acceptance_and_prior_knowledge():
    metadata = _ensure_phase_metadata({})
    metadata = _record_phase_evidence(
        metadata,
        phase="engage",
        tutor_response=_response(tags=["challenge_accepted"]),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="engage") is False

    metadata["phase_history"][-1]["turn_count"] = 1
    metadata = _record_phase_evidence(
        metadata,
        phase="engage",
        tutor_response=_response(tags=["prior_knowledge_shared"]),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="engage") is True


def test_first_engage_turn_cannot_claim_prior_knowledge():
    metadata = _ensure_phase_metadata({})
    sanitized = _sanitize_tutor_response_for_phase(
        metadata,
        phase="engage",
        event_type="text",
        text_payload="I am ready to revisit this topic.",
        tutor_response=_response(
            tags=["challenge_accepted", "prior_knowledge_shared"]
        ),
    )

    assert sanitized is not None
    assert sanitized.evidence_tags == ["challenge_accepted"]


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


def test_explore_recovery_keeps_the_initial_attempt_after_misconception_resolves():
    metadata = _ensure_phase_metadata({})
    metadata = _record_phase_evidence(
        metadata,
        phase="explore",
        tutor_response=_response(
            tags=["exploration_attempt"],
            correctness="partial",
            misconception="active",
        ),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="explore") is False

    metadata = _record_phase_evidence(
        metadata,
        phase="explore",
        tutor_response=_response(
            tags=["pattern_identified"],
            correctness="correct",
            misconception="resolved",
        ),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="explore") is True


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


def test_new_failure_does_not_lower_existing_scaffold_level():
    metadata = _ensure_phase_metadata(
        {"hint_level": 3, "consecutive_failures": 0}
    )

    metadata = _record_phase_evidence(
        metadata,
        phase="explain",
        tutor_response=_response(
            tags=[],
            correctness="unknown",
            misconception="suspected",
        ),
        event_type="text",
    )

    assert metadata["consecutive_failures"] == 1
    assert metadata["hint_level"] == 3


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
    assert _phase_is_ready(metadata, phase="evaluate") is True


def test_evaluate_completion_suppresses_new_request_after_attempt_and_analysis():
    incomplete_context = {
        "phase_evidence": {
            "evaluate": [
                {"tags": ["independent_attempt"]},
            ]
        }
    }
    complete_context = {
        "phase_evidence": {
            "evaluate": [
                {"tags": ["independent_attempt"]},
                {"tags": ["error_analysis"]},
            ]
        }
    }

    assert not _evaluate_turn_completes_evidence(
        learning_context=incomplete_context,
        evidence_tags=["reflection"],
    )
    assert _evaluate_turn_completes_evidence(
        learning_context=incomplete_context,
        evidence_tags=["error_analysis"],
    )
    assert _evaluate_turn_completes_evidence(
        learning_context={
            "phase_evidence": complete_context["phase_evidence"]["evaluate"]
        },
        evidence_tags=["reflection"],
    )


def test_evaluate_collects_attempt_and_analysis_while_reflection_is_optional():
    metadata = _ensure_phase_metadata({"current_phase": "evaluate"})
    first = _sanitize_tutor_response_for_phase(
        metadata,
        phase="evaluate",
        event_type="text",
        text_payload="A complete answer with analysis and reflection.",
        tutor_response=_response(
            tags=["independent_attempt", "error_analysis", "reflection"]
        ),
    )
    assert first is not None
    assert first.evidence_tags == ["independent_attempt", "reflection"]
    metadata = _record_phase_evidence(
        metadata,
        phase="evaluate",
        tutor_response=first,
        event_type="text",
    )

    second = _sanitize_tutor_response_for_phase(
        metadata,
        phase="evaluate",
        event_type="text",
        text_payload="I found and corrected a plausible error, then reflected.",
        tutor_response=_response(tags=["error_analysis", "reflection"]),
    )
    assert second is not None
    assert second.evidence_tags == ["error_analysis", "reflection"]
    metadata = _record_phase_evidence(
        metadata,
        phase="evaluate",
        tutor_response=second,
        event_type="text",
    )

    third = _sanitize_tutor_response_for_phase(
        metadata,
        phase="evaluate",
        event_type="text",
        text_payload="Next time I will name both layers before differentiating.",
        tutor_response=_response(tags=["reflection"]),
    )
    assert third is not None
    assert third.evidence_tags == []


def test_checkpoint_decline_suppresses_evidence_and_requires_fresh_readiness():
    metadata = _ensure_phase_metadata(
        {
            "current_phase": "explore",
            "phase_transition_pending": True,
            "phase_readiness_recheck_required": "explore",
            "phase_evidence": {
                "explore": [
                    {
                        "tags": ["exploration_attempt", "pattern_identified"],
                        "correctness": "correct",
                        "misconception_status": "none",
                        "confidence": 0.9,
                    }
                ]
            },
        }
    )
    response = _sanitize_tutor_response_for_phase(
        metadata,
        phase="explore",
        event_type="text",
        text_payload="Not yet.",
        tutor_response=_response(
            tags=["exploration_attempt", "pattern_identified"],
            correctness="correct",
        ),
        checkpoint_declined=True,
    )

    assert response is not None
    assert response.evidence_tags == []
    assert response.correctness == "unknown"
    assert response.next_phase_ready is False
    assert _phase_is_ready(metadata, phase="explore") is False


def test_only_fresh_correct_evidence_clears_checkpoint_recheck():
    assert not _response_reestablishes_phase_readiness(
        phase="explore",
        tutor_response=_response(
            tags=["exploration_attempt"],
            correctness="partial",
            misconception="suspected",
        ),
    )
    assert _response_reestablishes_phase_readiness(
        phase="explore",
        tutor_response=_response(
            tags=["pattern_identified"],
            correctness="correct",
            misconception="resolved",
        ),
    )


def test_checkpoint_decline_prompt_requests_a_different_scaffold():
    prompt = _build_user_instruction(
        "explain",
        "Chain rule",
        "Tutor: Do the two scale factors now make sense?",
        "Not yet.",
        learner_language="en",
        response_language="English",
        learning_context={
            "scaffold_level": 2,
            "checkpoint_decision": "stay",
        },
    )

    assert "explicitly chose to stay in the current phase" in prompt
    assert "return no evidence_tags" in prompt
    assert "Do not repeat the checkpoint" in prompt


@pytest.mark.asyncio
async def test_checkpoint_decline_metadata_reaches_ai_prompt(monkeypatch):
    payload = {
        "text": "Let's switch to a small-change diagram.",
        "next_phase_ready": False,
        "phase_reasoning": "The learner chose to stay.",
        "phase_checkpoint_question": None,
        "next_phase_opening_prompt": None,
        "evidence_tags": [],
        "correctness": "unknown",
        "misconception_status": "suspected",
        "confidence": 0.8,
        "evaluation_outcome": None,
        "evidence_request": None,
        "explanation_card": None,
        "tool_suggestion": None,
    }

    async def fake_generate(**kwargs):
        assert "Checkpoint response:" in kwargs["user_instruction"]
        assert "return no evidence_tags" in kwargs["user_instruction"]
        return AIGenerationResponse(
            provider="test",
            model="test-model",
            text=json.dumps(payload),
            finish_reason="stop",
        )

    monkeypatch.setattr("app.modules.workspaces.tutor.ai_client.generate", fake_generate)
    response, _audit = await generate_tutor_response(
        workspace=WorkspaceSession(
            current_topic="Chain rule",
            content_mode="chat",
            status="active",
            metadata_json={"current_phase": "explain"},
        ),
        event_type="text",
        text_payload="Not yet, I need another way to see it.",
        events=[],
        current_phase="explain",
        learner_language="en",
        learner_event_metadata={
            "interaction_type": "phase_checkpoint",
            "checkpoint_decision": "stay",
        },
    )

    assert response is not None
    assert response.next_phase_ready is False


def test_workspace_tutor_default_timeout_keeps_retries_below_frontend_cap(monkeypatch):
    monkeypatch.delenv("WICARA_WORKSPACE_TUTOR_TIMEOUT_SECONDS", raising=False)
    assert _tutor_timeout_seconds() == 140.0


@pytest.mark.parametrize(
    ("phase", "expected_fragment"),
    [
        ("engage", "focus on your answer about Chain rule"),
        ("explore", "small input change"),
        ("explain", "two consecutive stages"),
        ("elaborate", "recompute only the uncertain inner step"),
        ("evaluate", "inspect only the step you trust least"),
    ],
)
def test_generation_exhaustion_fallback_is_phase_aware(phase, expected_fragment):
    response = _fallback_response(
        "text",
        language_code="en",
        current_phase=phase,
        student_message="I am stuck on this step.",
        topic="Chain rule",
    )

    assert expected_fragment in response.text
    assert "canvas" not in response.text.lower()
    assert response.evidence_tags == []


def test_checkpoint_stay_switches_to_a_layer_flow_representation():
    text = _checkpoint_stay_layer_scaffold(language_code="en")

    assert "input → inner → outer" in text
    assert "change factor" in text
    assert "x=1" not in text


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


def test_elaborate_requires_three_correct_guided_applications():
    metadata = _ensure_phase_metadata({"current_phase": "elaborate"})
    response = _response(tags=["transfer_attempt", "transfer_correct"])

    for _ in range(2):
        metadata = _record_phase_evidence(
            metadata,
            phase="elaborate",
            tutor_response=response,
            event_type="text",
        )
    assert _phase_is_ready(metadata, phase="elaborate") is False

    metadata = _record_phase_evidence(
        metadata,
        phase="elaborate",
        tutor_response=response,
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="elaborate") is True


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


def test_first_engage_response_gets_data_driven_target_bridge_when_missing():
    text = _ensure_initial_target_bridge(
        "Let's inspect what changes inside the composition.",
        phase="engage",
        events=[],
        topic="Chain rule",
        language_code="en",
        learning_context={
            "original_target": {"title": "Curve sketching using derivatives"}
        },
    )

    assert text.startswith(
        "This work on Chain rule will support Curve sketching using derivatives later."
    )
    assert text.count("Curve sketching using derivatives") == 1

    paraphrased = _ensure_initial_target_bridge(
        "This will help us sketch curves using derivatives later.",
        phase="engage",
        events=[],
        topic="Chain rule",
        language_code="en",
        learning_context={
            "original_target": {"title": "Curve sketching using derivatives"}
        },
    )
    assert paraphrased == "This will help us sketch curves using derivatives later."


def test_confidence_checkpoint_is_rewritten_as_evidence_check():
    assert _ground_checkpoint_question(
        "Are you confident that the derivative is 2x cos(x²) and you can explain why?",
        language_code="en",
    ) == (
        "Does your latest work support this conclusion: the derivative is "
        "2x cos(x²)?"
    )
    assert _ground_checkpoint_question(
        "After correcting 2x, are you confident in using the rule independently?",
        language_code="en",
    ) == (
        "After correcting 2x, does that evidence support using the rule independently?"
    )


def test_explain_micro_check_does_not_repeat_the_same_task_with_extra_detail():
    tutor_text = (
        "Now try this micro-check: differentiate cos(2x^3) using the chain rule."
    )

    text, request = _ensure_explain_micro_check(
        tutor_text=tutor_text,
        evidence_request={
            "type": "micro_check",
            "prompt": (
                "Differentiate cos(2x^3) using the chain rule. "
                "Write your answer step by step."
            ),
        },
        language_code="en",
    )

    assert text == tutor_text
    assert request["type"] == "micro_check"


def test_explain_micro_check_preserves_contextual_task_without_chain_rule_bias():
    text, request = _ensure_explain_micro_check(
        tutor_text="Your explanation is correct.",
        evidence_request={
            "type": "micro_check",
            "prompt": "Classify x^4 at x=0 using derivative signs.",
            "expected_evidence": "A negative-to-positive sign change.",
        },
        language_code="en",
    )

    assert "Classify x^4 at x=0 using derivative signs." in text
    assert "inner function" not in text
    assert request["prompt"] == "Classify x^4 at x=0 using derivative signs."


def test_explore_splits_an_overloaded_evidence_request_into_one_action():
    request = _limit_phase_evidence_request(
        {
            "type": "open_response",
            "prompt": (
                "Compute the inner change. What is its ratio? How does that relate to "
                "the derivative? Then apply it to the original function and explain why "
                "the factors multiply in your own words with another comparison."
            ),
            "expected_evidence": "all requested steps",
        },
        phase="explore",
        topic="Chain rule",
        language_code="en",
        learning_context={},
    )

    assert request is not None
    assert request["prompt"].count("?") == 1
    assert "x=1 and x=1.1" in request["prompt"]
    assert "original function" not in request["prompt"]


def test_phase_opening_fallback_does_not_repeat_original_target_mid_lesson():
    from app.modules.workspaces.tutor import fallback_phase_opening_prompt

    text = fallback_phase_opening_prompt(
        phase="explore",
        topic="Chain rule",
        learner_language="en",
        learning_context={
            "original_target": {"title": "Curve sketching using derivatives"}
        },
    )

    assert "Curve sketching" not in text
    assert text.startswith("Try one Chain rule example")


def test_engage_opening_fallback_uses_pretest_diagnosis_instead_of_generic_hook():
    from app.modules.workspaces.tutor import fallback_phase_opening_prompt

    text = fallback_phase_opening_prompt(
        phase="engage",
        topic="Chain rule",
        learner_language="en",
        learning_context={
            "original_target": {"title": "Curve sketching using derivatives"},
            "diagnosis": {
                "reason": "Your pretest work omitted the inner derivative."
            },
        },
    )

    assert "Your pretest work omitted the inner derivative." in text
    assert "Curve sketching using derivatives" in text
    assert "Let's start with" not in text
    assert "\\sin(x^2)" in text


def test_engage_opening_hides_internal_pretest_telemetry_summary():
    from app.modules.workspaces.tutor import fallback_phase_opening_prompt

    text = fallback_phase_opening_prompt(
        phase="engage",
        topic="Chain rule",
        learner_language="en",
        learning_context={
            "original_target": {"title": "Curve sketching using derivatives"},
            "diagnosis": {
                "reason": "1 written explanations were analyzed as diagnostic insight."
            },
        },
    )

    assert "written explanations were analyzed" not in text
    assert "Your pretest points to one important step" in text


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
    assert (
        "next_phase_opening_prompt"
        in response_format["json_schema"]["schema"]["required"]
    )


def test_system_instruction_requires_context_clear_tutor_actions():
    from app.modules.workspaces.tutor import _SYSTEM_INSTRUCTION

    assert "Context-clarity rule" in _SYSTEM_INSTRUCTION
    assert "referent, action, and purpose" in _SYSTEM_INSTRUCTION
    assert "Do not introduce a symbol such as u" in _SYSTEM_INSTRUCTION


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
