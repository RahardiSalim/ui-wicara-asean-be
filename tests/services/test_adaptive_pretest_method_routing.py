from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.modules.accounts.models import UserAccount
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentOption,
    AssessmentQuestion,
    AssessmentSession,
)
from app.modules.pretests import adaptive_service as adaptive_service_module
from app.modules.pretests.adaptive_service import AdaptivePretestService
from app.modules.pretests.evidence_evaluator import PretestEvidenceEvaluator


@pytest.fixture(autouse=True)
def _disable_review_queue(monkeypatch):
    monkeypatch.setattr(adaptive_service_module, "enqueue_flag", lambda **_: None)


class _MethodEvaluator:
    def __init__(
        self,
        *,
        method_valid: bool,
        suspected_prerequisite_code: str | None,
        evidence_tags: list[str],
    ) -> None:
        self.method_valid = method_valid
        self.suspected_prerequisite_code = suspected_prerequisite_code
        self.evidence_tags = evidence_tags

    def evaluate(
        self,
        session,
        *,
        question,
        selected_option,
        typed_reasoning,
        canvas_asset_id,
        used_canvas=False,
        graph_scope=None,
    ) -> dict[str, Any]:
        del session, question, typed_reasoning, canvas_asset_id, used_canvas, graph_scope
        is_correct = bool(selected_option.is_correct)
        return {
            "is_correct": is_correct,
            "answer_score": 1.0 if is_correct else 0.0,
            "reasoning_score": 0.25 if self.method_valid is False else 0.9,
            "canvas_score": None,
            "canvas_status": None,
            "evidence_score": 0.775 if self.method_valid is False else 0.97,
            "diagnostic_signal": (
                "method_invalid_despite_correct_answer"
                if is_correct and self.method_valid is False
                else "correct_with_evidence"
            ),
            "reasoning_signal": (
                "misconception" if self.method_valid is False else "valid_reasoning"
            ),
            "reasoning_feedback": "Observed method evidence.",
            "reasoning_evaluation_source": "test:structured",
            "confidence": 0.82,
            "method_valid": self.method_valid,
            "evidence_tags": self.evidence_tags,
            "suspected_prerequisite_code": self.suspected_prerequisite_code,
            "method_reason": "The written steps rely on an unsupported transformation.",
            "method_evaluation_source": "test:structured",
            "step_results": (
                [
                    {
                        "concept_code": self.suspected_prerequisite_code,
                        "status": "fail",
                        "evidence": "Observed unsupported transformation.",
                    }
                ]
                if self.method_valid is False and self.suspected_prerequisite_code
                else []
            ),
            "gap_confidence": 0.92 if self.method_valid is False else None,
        }


def test_correct_mcq_with_invalid_method_routes_to_synthetic_graph_prerequisite(
    db_session,
):
    scenario = _create_synthetic_scenario(
        db_session,
        evaluator=_MethodEvaluator(
            method_valid=False,
            suspected_prerequisite_code="syn.kappa",
            evidence_tags=["unsupported_transformation"],
        ),
    )

    result = _submit_target_answer(db_session, scenario)

    assert result.evaluation.is_correct is True
    assert result.evaluation.answer_score == 1.0
    assert result.evaluation.method_valid is False
    assert result.next_action == {
        "type": "next_question",
        "concept_code": "syn.kappa",
        "difficulty": "medium",
        "reason": "evidence_directed_gap_probe",
    }
    assert result.next_question is not None
    assert result.next_question.concept_code == "syn.kappa"

    attempt = db_session.scalar(
        select(AssessmentAttempt).where(AssessmentAttempt.id == result.attempt_id)
    )
    assert attempt is not None
    assert attempt.is_correct is True
    assert attempt.answer_score == 1.0
    assert attempt.score == 1.0
    assert attempt.evaluated_result["method_valid"] is False
    assert attempt.evaluated_result["source_attempt_id"] == str(result.attempt_id)
    assert attempt.evaluation_metadata_json["source_attempt_id"] == str(
        result.attempt_id
    )
    assert attempt.evaluation_metadata_json["evidence_analysis_mode"] == (
        "upfront_adaptive_routing"
    )

    db_session.refresh(scenario["assessment"])
    state = scenario["assessment"].decision_state_json
    target_state = state["node_results"]["syn.zeta"]
    assert target_state["status"] == "fragile"
    assert target_state["attempts"][0]["attempt_id"] == str(result.attempt_id)
    assert target_state["attempts"][0]["evidence_tags"] == [
        "unsupported_transformation"
    ]
    assert state["method_evidence_routes"] == [
        {
            "source_attempt_id": str(result.attempt_id),
            "from_concept_code": "syn.zeta",
            "method_valid": False,
            "evidence_tags": ["unsupported_transformation"],
            "reason": "The written steps rely on an unsupported transformation.",
            "suspected_prerequisite_code": "syn.kappa",
            "routed_prerequisite_code": "syn.kappa",
        }
    ]

    finalized = scenario["service"].finalize(
        db_session,
        user=scenario["user"],
        session_id=scenario["assessment"].id,
    )

    assert finalized is not None
    diagnosis = finalized.diagnosis
    assert diagnosis["pure_answer_percent"] == 100.0
    assert diagnosis["answer_only_pass"] is True
    assert diagnosis["diagnostic_pass"] is False
    assert diagnosis["official_pass"] is False
    assert diagnosis["official_metric_source"] == "adaptive_pretest_diagnosis"
    assert diagnosis["overall_mastery_percent"] < 100
    assert diagnosis["recommended_path"] == "target_from_basics"
    assert diagnosis["evidence_available"] is True
    assert diagnosis["evidence_analysis_mode"] == "upfront_adaptive_routing"
    target = next(node for node in diagnosis["nodes"] if node["role"] == "target")
    assert target["status"] == "fragile"
    assert target["evidence_summary"]["source_attempt_ids"] == [
        str(result.attempt_id)
    ]
    assert target["evidence_summary"]["evidence_tags"] == [
        "unsupported_transformation"
    ]
    assert target["evidence_summary"]["method_invalid_detected"] is True
    assert target["evidence_summary"]["misconception_detected"] is True


def test_wrong_mcq_with_step_failure_jumps_directly_to_identified_skill(db_session):
    scenario = _create_synthetic_scenario(
        db_session,
        evaluator=_MethodEvaluator(
            method_valid=False,
            suspected_prerequisite_code="syn.kappa",
            evidence_tags=["inner_derivative_omitted"],
        ),
    )

    result = _submit_target_answer(db_session, scenario, correct=False)

    assert result.evaluation.is_correct is False
    assert result.next_action == {
        "type": "next_question",
        "concept_code": "syn.kappa",
        "difficulty": "medium",
        "reason": "evidence_directed_gap_probe",
    }
    assert result.next_question is not None
    assert result.next_question.concept_code == "syn.kappa"


def test_repeated_invalid_method_on_direct_probe_confirms_gap(db_session):
    scenario = _create_synthetic_scenario(
        db_session,
        evaluator=_MethodEvaluator(
            method_valid=False,
            suspected_prerequisite_code="syn.kappa",
            evidence_tags=["inner_derivative_omitted"],
        ),
    )
    first = _submit_target_answer(db_session, scenario)
    probe = first.next_question
    assert probe is not None
    probe_question = scenario["questions"]["syn.kappa"]["medium"]
    wrong_option = next(
        option for option in probe_question.options if not option.is_correct
    )

    confirmed = scenario["service"].submit_answer(
        db_session,
        user=scenario["user"],
        session_id=scenario["assessment"].id,
        question_id=probe_question.id,
        selected_option_id=wrong_option.id,
        typed_reasoning="I repeat the same unsupported transformation.",
        canvas_asset_id=None,
        used_canvas=False,
    )

    assert confirmed.next_action == {
        "type": "finalize",
        "reason": "evidence_directed_gap_confirmed",
    }
    assert confirmed.diagnosis is not None


def test_out_of_scope_suspect_is_rejected_before_graph_routing(db_session):
    scenario = _create_synthetic_scenario(
        db_session,
        evaluator=_MethodEvaluator(
            method_valid=False,
            suspected_prerequisite_code="outside.hidden",
            evidence_tags=["unsupported_transformation"],
        ),
    )

    result = _submit_target_answer(db_session, scenario)

    assert result.evaluation.suspected_prerequisite_code is None
    assert "suspected_prerequisite_rejected_out_of_scope" in (
        result.evaluation.evidence_tags
    )
    assert result.next_action["concept_code"] == "syn.zeta"
    assert result.next_action["reason"] == "target_medium_correct"
    assert result.next_action["concept_code"] != "outside.hidden"

    attempt = db_session.scalar(
        select(AssessmentAttempt).where(AssessmentAttempt.id == result.attempt_id)
    )
    assert attempt is not None
    assert attempt.evaluation_metadata_json[
        "rejected_suspected_prerequisite_code"
    ] == "outside.hidden"
    db_session.refresh(scenario["assessment"])
    route = scenario["assessment"].decision_state_json["method_evidence_routes"][0]
    assert route["suspected_prerequisite_code"] is None
    assert route["routed_prerequisite_code"] is None


def test_valid_method_keeps_existing_target_difficulty_route(db_session):
    scenario = _create_synthetic_scenario(
        db_session,
        evaluator=_MethodEvaluator(
            method_valid=True,
            suspected_prerequisite_code=None,
            evidence_tags=["coherent_steps"],
        ),
    )

    result = _submit_target_answer(db_session, scenario)

    assert result.evaluation.method_valid is True
    assert result.next_action == {
        "type": "next_question",
        "concept_code": "syn.zeta",
        "difficulty": "hard",
        "reason": "target_medium_correct",
    }
    assert result.next_question is not None
    assert result.next_question.concept_code == "syn.zeta"
    assert result.next_question.difficulty == "hard"
    db_session.refresh(scenario["assessment"])
    state = scenario["assessment"].decision_state_json
    assert state["node_results"]["syn.zeta"]["status"] == "probably_ready"
    assert "method_evidence_routes" not in state


def test_no_ai_key_fallback_does_not_invent_method_verdict(
    db_session,
    monkeypatch,
):
    scenario = _create_synthetic_scenario(
        db_session,
        evaluator=PretestEvidenceEvaluator(),
    )
    monkeypatch.setenv("WICARA_PRETEST_LLM_EVALUATION", "false")
    question = scenario["questions"]["syn.zeta"]["medium"]
    selected_option = next(option for option in question.options if option.is_correct)

    evaluation = scenario["service"].evidence_evaluator.evaluate(
        db_session,
        question=question,
        selected_option=selected_option,
        typed_reasoning="These are several plausible but locally unverified written steps.",
        canvas_asset_id=None,
        used_canvas=False,
        graph_scope=scenario["assessment"].graph_scope_json,
    )

    assert evaluation["method_valid"] is None
    assert evaluation["suspected_prerequisite_code"] is None
    assert evaluation["method_evaluation_source"] == "heuristic"


def _submit_target_answer(db_session, scenario, *, correct: bool = True):
    question = scenario["questions"]["syn.zeta"]["medium"]
    selected_option = next(
        option for option in question.options if bool(option.is_correct) is correct
    )
    return scenario["service"].submit_answer(
        db_session,
        user=scenario["user"],
        session_id=scenario["assessment"].id,
        question_id=question.id,
        selected_option_id=selected_option.id,
        typed_reasoning="I apply an unsupported transformation and then select the result.",
        canvas_asset_id=None,
        used_canvas=False,
    )


def _create_synthetic_scenario(db_session, *, evaluator):
    user = UserAccount(
        supabase_user_id=f"adaptive-method-{id(evaluator)}",
        display_name="Adaptive Method Learner",
        provider_subject=f"adaptive-method-{id(evaluator)}",
    )
    subject = Subject(
        code=f"synthetic-{id(evaluator)}",
        name="Synthetic Subject",
        description="",
        is_active=True,
    )
    db_session.add_all([user, subject])
    db_session.flush()
    target = KnowledgeConcept(
        subject_id=subject.id,
        code="syn.zeta",
        title="Quartz Operation",
        description="Neutral synthetic target.",
        display_order=1,
    )
    prerequisite = KnowledgeConcept(
        subject_id=subject.id,
        code="syn.kappa",
        title="Umber Foundation",
        description="Neutral synthetic prerequisite.",
        display_order=2,
    )
    db_session.add_all([target, prerequisite])
    db_session.flush()

    assessment = AssessmentSession(
        user_id=user.id,
        session_type="pretest",
        title="Synthetic adaptive pretest",
        status="active",
        target_concept_id=target.id,
        source="adaptive_generated",
        max_depth=1,
        max_questions=10,
        max_nodes_visited=5,
        metadata_json={"learner_language": "en"},
    )
    db_session.add(assessment)
    db_session.flush()

    questions = {
        target.code: {
            "medium": _add_question(
                db_session,
                assessment=assessment,
                concept=target,
                difficulty="medium",
                sort_order=1,
            ),
            "hard": _add_question(
                db_session,
                assessment=assessment,
                concept=target,
                difficulty="hard",
                sort_order=2,
            ),
        },
        prerequisite.code: {
            "medium": _add_question(
                db_session,
                assessment=assessment,
                concept=prerequisite,
                difficulty="medium",
                sort_order=3,
            ),
        },
    }
    graph_scope = {
        "target_concept_code": target.code,
        "nodes": [
            {
                "concept_id": str(target.id),
                "concept_code": target.code,
                "title": target.title,
                "description": target.description,
                "role": "target",
                "depth": 0,
                "parent": None,
            },
            {
                "concept_id": str(prerequisite.id),
                "concept_code": prerequisite.code,
                "title": prerequisite.title,
                "description": prerequisite.description,
                "role": "prerequisite",
                "depth": 1,
                "parent": target.code,
            },
        ],
        "edges": [
            {
                "from": target.code,
                "to": prerequisite.code,
                "edge_type": "prerequisite",
                "weight": 0.91,
                "depth": 1,
            }
        ],
    }
    assessment.graph_scope_json = graph_scope
    assessment.decision_state_json = {
        "target_concept_code": target.code,
        "learner_language": "en",
        "current_concept_code": target.code,
        "current_difficulty": "medium",
        "current_pack_id": None,
        "current_question_id": str(questions[target.code]["medium"].id),
        "question_count": 1,
        "max_questions": 10,
        "max_depth": 1,
        "max_nodes_visited": 5,
        "max_questions_per_node": 2,
        "confidence_threshold": 0.95,
        "probe_queue": [
            {
                "concept_code": prerequisite.code,
                "depth": 1,
                "priority": 0.91,
                "parent": target.code,
            }
        ],
        "generated_packs": {},
        "generated_questions": {
            code: {
                difficulty: str(question.id)
                for difficulty, question in by_difficulty.items()
            }
            for code, by_difficulty in questions.items()
        },
        "node_results": {},
        "confidence": 0.0,
        "stop_reason": None,
    }
    db_session.commit()
    return {
        "user": user,
        "assessment": assessment,
        "questions": questions,
        "service": AdaptivePretestService(evidence_evaluator=evaluator),
    }


def _add_question(
    db_session,
    *,
    assessment,
    concept,
    difficulty: str,
    sort_order: int,
):
    question = AssessmentQuestion(
        session_id=assessment.id,
        concept_id=concept.id,
        step_label="Synthetic check",
        topic=concept.title,
        prompt=f"Apply the operation for {concept.title}.",
        helper_text="Show a method.",
        difficulty_label=difficulty.title(),
        sort_order=sort_order,
        metadata_json={
            "concept_code": concept.code,
            "skill_trace": [
                {
                    "concept_code": (
                        "syn.kappa" if concept.code == "syn.zeta" else concept.code
                    ),
                    "criterion": "Use the named operation correctly.",
                }
            ],
        },
        expected_reasoning="Use a valid general method.",
        rubric_json={"criteria": ["valid method"]},
    )
    db_session.add(question)
    db_session.flush()
    db_session.add_all(
        [
            AssessmentOption(
                question_id=question.id,
                option_key="A",
                label="A",
                text="Result A",
                is_correct=True,
                sort_order=1,
            ),
            AssessmentOption(
                question_id=question.id,
                option_key="B",
                label="B",
                text="Result B",
                is_correct=False,
                sort_order=2,
            ),
        ]
    )
    db_session.flush()
    return question
