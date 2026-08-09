from app.modules.workspaces.schemas import TutorResponseRead
from app.modules.workspaces.service import (
    _ensure_phase_metadata,
    _phase_is_ready,
    _record_phase_evidence,
    _remediate_metadata_to_phase,
    _sanitize_learner_metadata,
)
from app.modules.workspaces.tutor import (
    _parse_structured_tutor_output,
    _safe_prompt_learning_context,
)


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
    for tag in ("independent_attempt", "error_analysis"):
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
        tutor_response=_response(tags=["reflection"]),
        event_type="text",
    )
    assert _phase_is_ready(metadata, phase="evaluate") is True


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
          "explanation_card": null
        }
        """
    )

    assert parsed["evidence_tags"] == ["exploration_attempt"]
    assert parsed["correctness"] == "partial"
    assert parsed["misconception_status"] == "suspected"
    assert parsed["confidence"] == 0.8


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
    assert "raw learner reasoning" not in serialized
    assert "sensitive-attempt-id" not in serialized
