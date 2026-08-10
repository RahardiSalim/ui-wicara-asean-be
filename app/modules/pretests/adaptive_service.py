from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.core.language import normalize_language_code, preferred_language_code
from app.modules.accounts.models import UserAccount
from app.modules.curriculum.models import KnowledgeConcept
from app.modules.evidence.models import ImageAsset
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentOption,
    AssessmentQuestion,
    AssessmentQuestionPack,
    AssessmentSession,
    LearningGoal,
)
from app.modules.pretests.decision_engine import PretestDecisionEngine
from app.modules.pretests.diagnosis_service import PATH_OPTIONS, PretestDiagnosisService
from app.modules.pretests.evidence_evaluator import PretestEvidenceEvaluator
from app.modules.pretests.generation_service import (
    AdaptivePretestGenerationService,
    _concept_prompt_description,
    _concept_prompt_title,
)
from app.modules.pretests.graph_scope_builder import GraphScopeBuilder
from app.modules.review.flagger import enqueue_flag
from app.modules.pretests.schemas import (
    PretestAnswerResponse,
    PretestEvaluationRead,
    PretestFinalizeResponse,
    PretestQuestionRead,
    PretestSessionRead,
)


class DuplicateQuestionAttempt(Exception):
    pass


PRETEST_NODE_DIFFICULTIES = ["easy", "medium", "hard"]


class AdaptivePretestService:
    def __init__(
        self,
        *,
        graph_builder: GraphScopeBuilder | None = None,
        generation_service: AdaptivePretestGenerationService | None = None,
        decision_engine: PretestDecisionEngine | None = None,
        evidence_evaluator: PretestEvidenceEvaluator | None = None,
        diagnosis_service: PretestDiagnosisService | None = None,
    ) -> None:
        self.graph_builder = graph_builder or GraphScopeBuilder()
        self.generation_service = generation_service or AdaptivePretestGenerationService()
        self.decision_engine = decision_engine or PretestDecisionEngine()
        self.evidence_evaluator = evidence_evaluator or PretestEvidenceEvaluator()
        self.diagnosis_service = diagnosis_service or PretestDiagnosisService()

    def start(
        self,
        session: Session,
        *,
        user: UserAccount,
        learning_goal_id: UUID,
        depth: int = 2,
        max_questions: int = 10,
        max_nodes_visited: int = 5,
    ) -> PretestSessionRead | None:
        existing = _active_pretest_for_goal(session, user=user, learning_goal_id=learning_goal_id)
        if existing is not None:
            return self.read(session, user=user, session_id=existing.id)

        goal = session.scalar(
            select(LearningGoal).where(LearningGoal.id == learning_goal_id, LearningGoal.user_id == user.id)
        )
        if goal is None:
            return None
        if goal.status in {"archived", "cancelled"}:
            raise ValueError("Learning goal is not active.")
        if goal.target_concept_id is None:
            raise ValueError("Learning goal has no target concept.")

        target = session.get(KnowledgeConcept, goal.target_concept_id)
        if target is None:
            raise ValueError("Target concept was not found.")
        effective_depth = min(int(depth), 2)
        effective_max_questions = max(1, min(int(max_questions), 10))
        effective_max_nodes_visited = max(1, min(int(max_nodes_visited), max(1, effective_max_questions // 2)))
        language = _goal_language(goal, user=user)
        graph_scope = self.graph_builder.build(
            session,
            target_concept_id=target.id,
            max_depth=effective_depth,
        )
        graph_scope = _localized_graph_scope(session, graph_scope, language=language)
        target_title = _localized_concept_title(target, language=language)
        assessment = AssessmentSession(
            user_id=user.id,
            learning_goal_id=goal.id,
            track_id=goal.track.id if goal.track else None,
            target_concept_id=target.id,
            session_type="pretest",
            title=f"Adaptive pretest: {target_title}",
            status="active",
            source="adaptive_generated",
            graph_scope_json=graph_scope,
            decision_state_json={},
            max_depth=effective_depth,
            max_questions=effective_max_questions,
            max_nodes_visited=effective_max_nodes_visited,
            metadata_json={
                "source": "adaptive_generated",
                "generation": "fresh_ai_questions",
                "question_reuse": "disabled",
                "learner_language": language,
            },
        )
        session.add(assessment)
        session.flush()

        target_questions = self._generate_pretest_node_questions(
            session,
            user=user,
            assessment=assessment,
            concept=target,
            node_role="goal",
        )
        question = target_questions["medium"]
        decision_state = {
            "target_concept_code": target.code,
            "learner_language": language,
            "current_concept_code": target.code,
            "current_difficulty": "medium",
            "current_pack_id": None,
            "current_question_id": str(question.id),
            "question_count": 1,
            "max_questions": effective_max_questions,
            "max_depth": effective_depth,
            "max_nodes_visited": effective_max_nodes_visited,
            "max_questions_per_node": 2,
            "confidence_threshold": 0.95,
            "probe_queue": self.graph_builder.build_probe_queue(graph_scope),
            "generated_packs": {},
            "generated_questions": {
                target.code: {
                    difficulty: str(node_question.id)
                    for difficulty, node_question in target_questions.items()
                }
            },
            "node_results": {},
            "confidence": 0.0,
            "stop_reason": None,
        }
        assessment.decision_state_json = decision_state
        session.commit()
        return self.read(session, user=user, session_id=assessment.id)

    def read(
        self,
        session: Session,
        *,
        user: UserAccount,
        session_id: UUID,
    ) -> PretestSessionRead | None:
        assessment = _load_assessment(session, user=user, session_id=session_id)
        if assessment is None:
            return None
        state = _read_state_with_effective_limits(assessment)
        current_question = None
        question_id = state.get("current_question_id")
        if question_id and assessment.status in {"active", "awaiting_answer"}:
            question = _question_by_id(assessment, str(question_id))
            if question is not None:
                current_question = _question_to_read(
                    session,
                    question,
                    state=state,
                    language=_assessment_language(assessment, user=user),
                )
        target = session.get(KnowledgeConcept, assessment.target_concept_id) if assessment.target_concept_id else None
        language = _assessment_language(assessment, user=user)
        return PretestSessionRead(
            session_id=assessment.id,
            learning_goal_id=assessment.learning_goal_id,
            status=assessment.status,
            target_concept={
                "concept_id": str(target.id) if target else None,
                "concept_code": target.code if target else "",
                "title": _localized_concept_title(target, language=language) if target else "",
            },
            graph_scope=assessment.graph_scope_json or {},
            decision_state=state,
            current_question=current_question,
            question_count=int(state.get("question_count", 0)),
            max_questions=int(state.get("max_questions", assessment.max_questions)),
        )

    def submit_answer(
        self,
        session: Session,
        *,
        user: UserAccount,
        session_id: UUID,
        question_id: UUID,
        selected_option_id: UUID,
        typed_reasoning: str,
        canvas_asset_id: UUID | None,
        used_canvas: bool,
    ) -> PretestAnswerResponse | None:
        assessment = _load_assessment(session, user=user, session_id=session_id)
        if assessment is None:
            return None
        if assessment.status not in {"active", "awaiting_answer"}:
            raise ValueError("Pretest is not active.")
        assessment_state = _normalize_assessment_limits(assessment)
        question = _question_by_id(assessment, str(question_id))
        if question is None:
            raise LookupError("Question was not found in this pretest session.")
        if session.scalar(
            select(AssessmentAttempt.id).where(
                AssessmentAttempt.session_id == assessment.id,
                AssessmentAttempt.question_id == question.id,
            )
        ):
            raise DuplicateQuestionAttempt()
        option = next((item for item in question.options if item.id == selected_option_id), None)
        if option is None:
            raise LookupError("Selected option was not found for this question.")
        if canvas_asset_id is not None:
            asset = session.get(ImageAsset, canvas_asset_id)
            if asset is None or asset.user_id != user.id:
                raise LookupError("Canvas asset was not found.")

        evaluation = self.evidence_evaluator.evaluate(
            session,
            question=question,
            selected_option=option,
            typed_reasoning=typed_reasoning,
            canvas_asset_id=canvas_asset_id,
            used_canvas=used_canvas,
            graph_scope=assessment.graph_scope_json or {},
        )
        evaluation = _validated_method_evaluation(
            evaluation,
            question=question,
            graph_scope=assessment.graph_scope_json or {},
        )
        evaluation["is_correct"] = bool(option.is_correct)
        evaluation["answer_score"] = 1.0 if option.is_correct else 0.0
        attempt = AssessmentAttempt(
            session_id=assessment.id,
            question_id=question.id,
            selected_option_id=option.id,
            canvas_asset_id=canvas_asset_id,
            confidence=int(round(float(evaluation["confidence"]) * 10)),
            explanation_text=typed_reasoning.strip(),
            typed_reasoning=typed_reasoning.strip(),
            used_canvas=used_canvas or canvas_asset_id is not None,
            score=float(evaluation["answer_score"]),
            is_correct=bool(evaluation["is_correct"]),
            answer_score=float(evaluation["answer_score"]),
            reasoning_score=evaluation["reasoning_score"],
            canvas_score=evaluation["canvas_score"],
            evidence_score=float(evaluation["evidence_score"]),
            diagnostic_signal=str(evaluation["diagnostic_signal"]),
            evaluated_result={
                "verdict": "CORRECT" if evaluation["is_correct"] else "INCORRECT",
                "diagnostic_signal": evaluation["diagnostic_signal"],
                "reasoning_signal": evaluation["reasoning_signal"],
                "reasoning_feedback": evaluation["reasoning_feedback"],
                "method_valid": evaluation["method_valid"],
                "evidence_tags": evaluation["evidence_tags"],
                "suspected_prerequisite_code": evaluation["suspected_prerequisite_code"],
                "method_reason": evaluation["method_reason"],
                "method_evaluation_source": evaluation["method_evaluation_source"],
                "step_results": evaluation.get("step_results", []),
                "gap_confidence": evaluation.get("gap_confidence"),
            },
            evaluation_metadata_json={
                "canvas_status": evaluation["canvas_status"],
                "confidence": evaluation["confidence"],
                "reasoning_signal": evaluation["reasoning_signal"],
                "reasoning_feedback": evaluation["reasoning_feedback"],
                "reasoning_evaluation_source": evaluation["reasoning_evaluation_source"],
                "method_valid": evaluation["method_valid"],
                "evidence_tags": evaluation["evidence_tags"],
                "suspected_prerequisite_code": evaluation["suspected_prerequisite_code"],
                "rejected_suspected_prerequisite_code": evaluation.get(
                    "rejected_suspected_prerequisite_code"
                ),
                "method_reason": evaluation["method_reason"],
                "method_evaluation_source": evaluation["method_evaluation_source"],
                "step_results": evaluation.get("step_results", []),
                "gap_confidence": evaluation.get("gap_confidence"),
                "evidence_deferred": False,
                "evidence_analysis_mode": "upfront_adaptive_routing",
            },
        )
        session.add(attempt)
        session.flush()
        attempt.evaluated_result = {
            **(attempt.evaluated_result or {}),
            "source_attempt_id": str(attempt.id),
        }
        attempt.evaluation_metadata_json = {
            **(attempt.evaluation_metadata_json or {}),
            "source_attempt_id": str(attempt.id),
        }

        enqueue_flag(
            artifact_type="evaluation",
            artifact_id=attempt.id,
            confidence=attempt.reasoning_score,
            signals={
                "diagnostic_signal": attempt.diagnostic_signal,
                "method_valid": evaluation["method_valid"],
                "evidence_tags": evaluation["evidence_tags"],
                "suspected_prerequisite_code": evaluation["suspected_prerequisite_code"],
                "structured_parse_ok": evaluation.get("reasoning_evaluation_source")
                != "parse_error",
            },
            learner_id=user.id,
            summary=(typed_reasoning.strip() or "MCQ-only answer")[:160],
        )

        state = self.decision_engine.record_attempt(
            assessment_state,
            concept_code=str(question.metadata_json.get("concept_code") or _concept_code(session, question)),
            difficulty=question.difficulty_label.lower(),
            is_correct=bool(evaluation["is_correct"]),
            evidence_score=float(evaluation["evidence_score"]),
            confidence=float(evaluation["confidence"]),
            answer_score=float(evaluation["answer_score"]),
            reasoning_score=evaluation["reasoning_score"],
            canvas_score=evaluation["canvas_score"],
            diagnostic_signal=str(evaluation["diagnostic_signal"]),
            reasoning_signal=str(evaluation["reasoning_signal"]),
            attempt_id=str(attempt.id),
            evidence_deferred=False,
            method_valid=evaluation["method_valid"],
            evidence_tags=evaluation["evidence_tags"],
            suspected_prerequisite_code=evaluation["suspected_prerequisite_code"],
            method_reason=evaluation["method_reason"],
            method_evaluation_source=evaluation["method_evaluation_source"],
        )
        state, next_action = self.decision_engine.decide(
            state,
            last_concept_code=str(question.metadata_json.get("concept_code") or _concept_code(session, question)),
            last_difficulty=question.difficulty_label.lower(),
            last_is_correct=bool(evaluation["is_correct"]),
            graph_scope=assessment.graph_scope_json or {},
            method_valid=evaluation["method_valid"],
            suspected_prerequisite_code=evaluation["suspected_prerequisite_code"],
            evidence_tags=evaluation["evidence_tags"],
            method_reason=evaluation["method_reason"],
            source_attempt_id=str(attempt.id),
        )
        assessment.decision_state_json = state

        if next_action["type"] == "finalize":
            diagnosis = self.diagnosis_service.finalize(
                session,
                user=user,
                assessment=assessment,
                stop_reason=str(next_action["reason"]),
            )
            return PretestAnswerResponse(
                attempt_id=attempt.id,
                evaluation=_evaluation_to_read(evaluation),
                next_action=next_action,
                diagnosis=diagnosis,
            )

        next_question = self._prepare_next_question(
            session,
            user=user,
            assessment=assessment,
            state=state,
            next_action=next_action,
        )
        session.commit()
        return PretestAnswerResponse(
            attempt_id=attempt.id,
            evaluation=_evaluation_to_read(evaluation),
            next_action=next_action,
            next_question=_question_to_read(
                session,
                next_question,
                state=assessment.decision_state_json or {},
                language=_assessment_language(assessment, user=user),
            ),
        )

    def finalize(
        self,
        session: Session,
        *,
        user: UserAccount,
        session_id: UUID,
    ) -> PretestFinalizeResponse | None:
        assessment = _load_assessment(session, user=user, session_id=session_id)
        if assessment is None:
            return None
        state = _normalize_assessment_limits(assessment)
        stop_reason = str(state.get("stop_reason") or "manual_finalize")
        diagnosis = self.diagnosis_service.finalize(
            session,
            user=user,
            assessment=assessment,
            stop_reason=stop_reason,
        )
        return PretestFinalizeResponse(
            session_id=assessment.id,
            status="completed",
            diagnosis=diagnosis,
            path_options=PATH_OPTIONS,
        )

    def _prepare_next_question(
        self,
        session: Session,
        *,
        user: UserAccount,
        assessment: AssessmentSession,
        state: dict[str, Any],
        next_action: dict[str, Any],
    ) -> AssessmentQuestion:
        concept_code = str(next_action["concept_code"])
        difficulty = str(next_action["difficulty"])
        concept = _concept_by_code(session, concept_code)
        if concept is None:
            state["stop_reason"] = "concept_not_found"
            assessment.decision_state_json = state
            raise LookupError("Next concept was not found.")
        state.setdefault("generated_packs", {})
        generated_questions = state.setdefault("generated_questions", {})
        question = _question_from_generated_node(
            assessment,
            generated_questions.get(concept_code),
            difficulty=difficulty,
        )
        if question is None:
            node_questions = self._generate_pretest_node_questions(
                session,
                user=user,
                assessment=assessment,
                concept=concept,
                node_role="goal" if concept_code == state.get("target_concept_code") else "prerequisite",
            )
            generated_questions.setdefault(concept_code, {}).update(
                {
                    node_difficulty: str(node_question.id)
                    for node_difficulty, node_question in node_questions.items()
                }
            )
            question = node_questions[difficulty]
        state["current_concept_code"] = concept_code
        state["current_difficulty"] = difficulty
        state["current_pack_id"] = None
        state["current_question_id"] = str(question.id)
        state["question_count"] = int(state.get("question_count", 0)) + 1
        assessment.decision_state_json = deepcopy(state)
        flag_modified(assessment, "decision_state_json")
        return question

    def _generate_pretest_node_questions(
        self,
        session: Session,
        *,
        user: UserAccount,
        assessment: AssessmentSession,
        concept: KnowledgeConcept,
        node_role: str,
    ) -> dict[str, AssessmentQuestion]:
        language = _assessment_language(assessment, user=user)
        graph_scope = assessment.graph_scope_json or {}
        questions = self.generation_service.create_fresh_questions(
            session,
            assessment=assessment,
            concept=concept,
            difficulties=PRETEST_NODE_DIFFICULTIES,
            assessment_type="pretest",
            language=language,
            node_role=node_role,
            skill_candidates=_pretest_skill_candidates(
                graph_scope,
                concept_code=concept.code,
            ),
        )
        by_difficulty = {question.difficulty_label.lower(): question for question in questions}
        missing = [difficulty for difficulty in PRETEST_NODE_DIFFICULTIES if difficulty not in by_difficulty]
        if missing:
            raise LookupError(f"Generated pretest node pack is missing: {', '.join(missing)}")
        return by_difficulty


def _active_pretest_for_goal(
    session: Session,
    *,
    user: UserAccount,
    learning_goal_id: UUID,
) -> AssessmentSession | None:
    return session.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.user_id == user.id,
            AssessmentSession.learning_goal_id == learning_goal_id,
            AssessmentSession.session_type == "pretest",
            AssessmentSession.status.in_({"active", "awaiting_answer"}),
        )
        .options(
            selectinload(AssessmentSession.questions).selectinload(AssessmentQuestion.options),
            selectinload(AssessmentSession.question_packs).selectinload(
                AssessmentQuestionPack.questions
            ),
        )
        .order_by(AssessmentSession.created_at.desc())
    )


def _load_assessment(
    session: Session,
    *,
    user: UserAccount,
    session_id: UUID,
) -> AssessmentSession | None:
    return session.scalar(
        select(AssessmentSession)
        .where(AssessmentSession.id == session_id, AssessmentSession.user_id == user.id)
        .options(
            selectinload(AssessmentSession.questions).selectinload(AssessmentQuestion.options),
            selectinload(AssessmentSession.question_packs).selectinload(
                AssessmentQuestionPack.questions
            ),
        )
    )


def _question_by_id(
    assessment: AssessmentSession,
    question_id: str,
) -> AssessmentQuestion | None:
    return next((question for question in assessment.questions if str(question.id) == question_id), None)


def _question_from_generated_node(
    assessment: AssessmentSession,
    node_questions: object,
    *,
    difficulty: str,
) -> AssessmentQuestion | None:
    if not isinstance(node_questions, dict):
        return None
    question_id = node_questions.get(difficulty)
    if not question_id:
        return None
    return _question_by_id(assessment, str(question_id))


def _read_state_with_effective_limits(assessment: AssessmentSession) -> dict[str, Any]:
    state = {**(assessment.decision_state_json or {})}
    max_questions, max_nodes_visited = _effective_limits(assessment, state)
    state["max_questions"] = max_questions
    state["max_nodes_visited"] = max_nodes_visited
    return state


def _normalize_assessment_limits(assessment: AssessmentSession) -> dict[str, Any]:
    state = {**(assessment.decision_state_json or {})}
    max_questions, max_nodes_visited = _effective_limits(assessment, state)
    assessment.max_questions = max_questions
    assessment.max_nodes_visited = max_nodes_visited
    state["max_questions"] = max_questions
    state["max_nodes_visited"] = max_nodes_visited
    assessment.decision_state_json = state
    return state


def _effective_limits(assessment: AssessmentSession, state: dict[str, Any]) -> tuple[int, int]:
    max_questions = min(
        _positive_int(state.get("max_questions"), assessment.max_questions or 10),
        10,
    )
    requested_nodes = _positive_int(state.get("max_nodes_visited"), assessment.max_nodes_visited or 5)
    max_nodes_visited = max(1, min(requested_nodes, max(1, max_questions // 2)))
    return max_questions, max_nodes_visited


def _positive_int(value: object, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(fallback))


def _question_to_read(
    session: Session,
    question: AssessmentQuestion,
    *,
    state: dict[str, Any],
    language: str,
) -> PretestQuestionRead:
    concept = session.get(KnowledgeConcept, question.concept_id) if question.concept_id else None
    localized_topic = question.topic or (
        _localized_concept_title(concept, language=language) if concept else ""
    )
    return PretestQuestionRead(
        id=question.id,
        pack_id=question.pack_id,
        concept_code=concept.code if concept else str(question.metadata_json.get("concept_code", "")),
        concept_title=localized_topic,
        difficulty=question.difficulty_label.lower(),
        prompt=question.prompt,
        helper=question.helper_text,
        options=[
            {"id": option.id, "label": option.label, "text": option.text}
            for option in question.options
        ],
        progress={
            "current": int(state.get("question_count", 0)),
            "max": int(state.get("max_questions", 10)),
        },
    )


def _evaluation_to_read(evaluation: dict[str, Any]) -> PretestEvaluationRead:
    return PretestEvaluationRead(
        is_correct=bool(evaluation["is_correct"]),
        answer_score=float(evaluation["answer_score"]),
        reasoning_score=evaluation["reasoning_score"],
        reasoning_signal=evaluation["reasoning_signal"],
        reasoning_feedback=evaluation["reasoning_feedback"],
        reasoning_evaluation_source=evaluation["reasoning_evaluation_source"],
        canvas_score=evaluation["canvas_score"],
        evidence_score=float(evaluation["evidence_score"]),
        confidence=float(evaluation["confidence"]),
        diagnostic_signal=str(evaluation["diagnostic_signal"]),
        canvas_status=evaluation["canvas_status"],
        method_valid=evaluation.get("method_valid"),
        evidence_tags=list(evaluation.get("evidence_tags") or []),
        suspected_prerequisite_code=evaluation.get("suspected_prerequisite_code"),
        method_reason=str(evaluation.get("method_reason") or ""),
        method_evaluation_source=str(evaluation.get("method_evaluation_source") or ""),
    )


def _validated_method_evaluation(
    evaluation: dict[str, Any],
    *,
    question: AssessmentQuestion,
    graph_scope: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(evaluation)
    method_valid = normalized.get("method_valid")
    if not isinstance(method_valid, bool):
        method_valid = None
    raw_tags = normalized.get("evidence_tags")
    tags = (
        [str(tag).strip()[:64] for tag in raw_tags[:16] if str(tag).strip()]
        if isinstance(raw_tags, list)
        else []
    )
    graph_codes = {
        str(node.get("concept_code"))
        for node in graph_scope.get("nodes", [])
        if isinstance(node, dict)
        and str(node.get("concept_code") or "").strip()
    }
    trace = (question.metadata_json or {}).get("skill_trace")
    allowed_codes = {
        str(step.get("concept_code") or "").strip()
        for step in trace
        if isinstance(step, dict)
        and str(step.get("concept_code") or "").strip() in graph_codes
    } if isinstance(trace, list) else set()
    raw_step_results = normalized.get("step_results")
    step_results = [
        {
            "concept_code": str(step.get("concept_code") or "").strip(),
            "status": str(step.get("status") or "").strip().lower(),
            "evidence": str(step.get("evidence") or "").strip()[:500],
        }
        for step in raw_step_results[:16]
        if isinstance(step, dict)
        and str(step.get("concept_code") or "").strip() in allowed_codes
        and str(step.get("status") or "").strip().lower()
        in {"pass", "fail", "not_observed"}
    ] if isinstance(raw_step_results, list) else []
    failed_codes = {
        step["concept_code"] for step in step_results if step["status"] == "fail"
    }
    raw_gap_confidence = normalized.get("gap_confidence")
    gap_confidence = (
        max(0.0, min(1.0, float(raw_gap_confidence)))
        if isinstance(raw_gap_confidence, (int, float))
        else None
    )
    raw_code = str(normalized.get("suspected_prerequisite_code") or "").strip()
    suspected_code = (
        raw_code
        if method_valid is False
        and raw_code in allowed_codes
        and raw_code in failed_codes
        and gap_confidence is not None
        and gap_confidence >= 0.7
        else None
    )
    if raw_code and raw_code not in allowed_codes:
        tags.append("suspected_prerequisite_rejected_out_of_scope")
        normalized["rejected_suspected_prerequisite_code"] = raw_code
    elif raw_code and method_valid is not False:
        tags.append("suspected_prerequisite_ignored_without_invalid_method")
        normalized["rejected_suspected_prerequisite_code"] = raw_code
    normalized.update(
        {
            "method_valid": method_valid,
            "evidence_tags": list(dict.fromkeys(tags)),
            "suspected_prerequisite_code": suspected_code,
            "method_reason": str(normalized.get("method_reason") or "").strip()[:500],
            "method_evaluation_source": str(
                normalized.get("method_evaluation_source")
                or normalized.get("reasoning_evaluation_source")
                or "none"
            )[:160],
            "step_results": step_results,
            "gap_confidence": gap_confidence,
        }
    )
    return normalized


def _concept_by_code(session: Session, concept_code: str) -> KnowledgeConcept | None:
    return session.scalar(select(KnowledgeConcept).where(KnowledgeConcept.code == concept_code))


def _concept_code(session: Session, question: AssessmentQuestion) -> str:
    concept = session.get(KnowledgeConcept, question.concept_id) if question.concept_id else None
    return concept.code if concept else ""


def _preferred_language(user: UserAccount) -> str:
    return preferred_language_code(user)


def _goal_language(goal: LearningGoal, *, user: UserAccount) -> str:
    metadata = goal.metadata_json or {}
    return normalize_language_code(metadata.get("language") or _preferred_language(user))


def _assessment_language(assessment: AssessmentSession, *, user: UserAccount) -> str:
    metadata = assessment.metadata_json or {}
    state = assessment.decision_state_json or {}
    return normalize_language_code(
        metadata.get("learner_language")
        or state.get("learner_language")
        or _preferred_language(user)
    )


def _localized_graph_scope(
    session: Session,
    graph_scope: dict[str, Any],
    *,
    language: str,
) -> dict[str, Any]:
    localized = {**graph_scope}
    nodes: list[dict[str, Any]] = []
    for raw_node in graph_scope.get("nodes", []):
        if not isinstance(raw_node, dict):
            continue
        node = {**raw_node}
        concept = None
        concept_id = node.get("concept_id")
        if concept_id:
            try:
                concept = session.get(KnowledgeConcept, UUID(str(concept_id)))
            except Exception:
                concept = None
        if concept is not None:
            title = _localized_concept_title(concept, language=language)
            node["title"] = title
            node["description"] = _concept_prompt_description(
                concept,
                language=language,
                title=title,
            )
            metadata = concept.metadata_json or {}
            suffix = "id" if normalize_language_code(language) == "id" else "en"
            node["assessment_evidence"] = metadata.get(
                f"assessment_evidence_{suffix}", []
            )
            node["common_misconceptions"] = metadata.get(
                f"common_misconceptions_{suffix}", []
            )
        nodes.append(node)
    localized["nodes"] = nodes
    edges: list[dict[str, Any]] = []
    suffix = "id" if normalize_language_code(language) == "id" else "en"
    for raw_edge in graph_scope.get("edges", []):
        if not isinstance(raw_edge, dict):
            continue
        edge = {**raw_edge}
        edge["reason"] = str(
            edge.get(f"reason_{suffix}")
            or edge.get("reason_id")
            or edge.get("reason_en")
            or ""
        )
        edges.append(edge)
    localized["edges"] = edges
    return localized


def _pretest_skill_candidates(
    graph_scope: dict[str, Any],
    *,
    concept_code: str,
) -> list[dict[str, Any]]:
    nodes = {
        str(node.get("concept_code")): node
        for node in graph_scope.get("nodes", [])
        if isinstance(node, dict)
    }
    edges = [
        edge
        for edge in graph_scope.get("edges", [])
        if isinstance(edge, dict)
    ]
    reachable = {concept_code}
    queue = [concept_code]
    ordered_codes: list[str] = []
    while queue:
        parent = queue.pop(0)
        for edge in edges:
            if str(edge.get("from")) != parent:
                continue
            code = str(edge.get("to") or "").strip()
            if not code or code in reachable:
                continue
            reachable.add(code)
            ordered_codes.append(code)
            queue.append(code)
    return [
        {
            "concept_code": code,
            "title": str(nodes[code].get("title") or code).strip(),
            "description": str(nodes[code].get("description") or "").strip(),
            "assessment_evidence": nodes[code].get("assessment_evidence") or [],
            "common_misconceptions": nodes[code].get("common_misconceptions") or [],
        }
        for code in ordered_codes
        if code in nodes
    ]


def _localized_concept_title(concept: KnowledgeConcept, *, language: str) -> str:
    return _concept_prompt_title(concept, language=language)
