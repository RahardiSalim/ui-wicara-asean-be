from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.language import normalize_language_code, preferred_language_code
from app.modules.accounts.models import UserAccount
from app.modules.assessments.metrics import AssessmentEvidenceEvaluator, PASS_PERCENT
from app.modules.curriculum.kurikulum_merdeka import translate_curriculum_label_to_english
from app.modules.curriculum.models import KnowledgeConcept
from app.modules.evidence.models import ImageAsset
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentQuestion,
    AssessmentQuestionPack,
    AssessmentSession,
    LearnerConceptState,
    LearningGoal,
    LearningTrack,
    TrackModule,
)
from app.modules.posttests.schemas import (
    PosttestAnswerResponse,
    PosttestEvaluationRead,
    PosttestFinalizeResponse,
    PosttestNodeResultRead,
    PosttestProgressionRead,
    PosttestQuestionRead,
    PosttestSessionRead,
)
from app.modules.pretests.generation_service import (
    AdaptivePretestGenerationService,
    AssessmentQuestionGenerationError,
)
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession


class DuplicateQuestionAttempt(Exception):
    pass


POSTTEST_DIFFICULTIES = (
    "medium",
    "medium",
    "medium",
    "hard",
    "hard",
    "hard",
    "hard",
    "hard",
    "hard",
    "hard",
)
POSTTEST_PASS_SCORE = PASS_PERCENT
WORKSPACE_SUMMARY_TEXT_LIMIT = 5000


class AdaptivePosttestService:
    def __init__(
        self,
        *,
        generation_service: AdaptivePretestGenerationService | None = None,
        evidence_evaluator: AssessmentEvidenceEvaluator | None = None,
    ) -> None:
        self.generation_service = generation_service or AdaptivePretestGenerationService()
        self.evidence_evaluator = evidence_evaluator or AssessmentEvidenceEvaluator()

    def start(
        self,
        session: Session,
        *,
        user: UserAccount,
        workspace_session_id: UUID | None = None,
        learning_goal_id: UUID | None = None,
        track_id: UUID | None = None,
        module_id: UUID | None = None,
    ) -> PosttestSessionRead | None:
        context = _resolve_posttest_context(
            session,
            user=user,
            workspace_session_id=workspace_session_id,
            learning_goal_id=learning_goal_id,
            track_id=track_id,
            module_id=module_id,
        )
        if context is None:
            return None
        goal = context["goal"]
        target = context["target_concept"]
        workspace = context.get("workspace")
        posttest_source = str(context["posttest_source"])

        existing = _active_posttest_for_goal(
            session,
            user=user,
            goal_id=goal.id,
            workspace_session_id=workspace.id if workspace is not None else None,
            module_id=context.get("module_id"),
        )
        if existing is not None:
            return self.read(session, user=user, session_id=existing.id)
        if workspace is not None and not _workspace_allows_posttest(workspace):
            raise ValueError(
                "Posttest is not eligible for this workspace. "
                "Complete the Evaluate evidence requirements first."
            )

        language = _preferred_language(user)
        target_title = _localized_concept_title(target, language=language)
        workspace_summary = _workspace_learning_summary(
            session,
            user=user,
            goal=goal,
            target=target,
            workspace=workspace,
            posttest_source=posttest_source,
            language=language,
        )
        posttest_source = str(workspace_summary.get("posttest_source") or posttest_source)
        diagnosis_context = _posttest_generation_context(workspace_summary)

        assessment = AssessmentSession(
            user_id=user.id,
            learning_goal_id=goal.id,
            track_id=context.get("track_id"),
            target_concept_id=target.id,
            session_type="posttest",
            title=f"Posttest: {target_title}",
            status="active",
            source="workspace_history",
            metadata_json={
                "source": "workspace_history",
                "generation": "workspace_context_posttest_v1",
                "posttest_source": posttest_source,
                "workspace_session_id": str(workspace.id) if workspace is not None else None,
                "learning_goal_id": str(goal.id),
                "track_id": str(context["track_id"]) if context.get("track_id") is not None else None,
                "module_id": str(context["module_id"]) if context.get("module_id") is not None else None,
                "target_concept_id": str(target.id),
                "target_concept_code": target.code,
                "target_concept_title": target_title,
                "language": language,
                "learner_language": language,
                "question_count": len(POSTTEST_DIFFICULTIES),
                "difficulty_policy": {"fixed": list(POSTTEST_DIFFICULTIES)},
                "workspace_learning_summary": workspace_summary,
            },
            decision_state_json={},
            graph_scope_json={},
            max_questions=len(POSTTEST_DIFFICULTIES),
            max_depth=0,
            max_nodes_visited=1,
        )
        session.add(assessment)
        session.flush()

        questions = _create_posttest_questions(
            self.generation_service,
            session,
            assessment=assessment,
            concept=target,
            language=language,
            diagnosis_context=diagnosis_context,
        )
        question_ids = [str(question.id) for question in questions]
        assessment.decision_state_json = {
            "question_queue": question_ids,
            "current_index": 0,
            "official_result": _empty_official_result(total_questions=len(question_ids)),
            "node_results": {
                target.code: {
                    "concept_id": str(target.id),
                    "concept_title": target_title,
                    "total_questions": len(question_ids),
                    "answered_count": 0,
                    "correct_count": 0,
                    "answer_score_sum": 0.0,
                    "evidence_score_sum": 0.0,
                    "confidence_sum": 0.0,
                    "answer_percent": 0.0,
                    "evidence_percent": 0.0,
                    "score_percent": 0.0,
                    "confidence_percent": 0.0,
                    "scaled_score": 0.0,
                    "passed": False,
                    "retake_required": True,
                    "metric_source": "official_mcq",
                    "attempts": [],
                }
            },
        }
        session.commit()
        return self.read(session, user=user, session_id=assessment.id)

    def read(
        self,
        session: Session,
        *,
        user: UserAccount,
        session_id: UUID,
    ) -> PosttestSessionRead | None:
        assessment = _load_assessment(session, user=user, session_id=session_id)
        if assessment is None:
            return None
        state = deepcopy(assessment.decision_state_json or {})
        metadata = assessment.metadata_json or {}
        language = str(metadata.get("language") or metadata.get("learner_language") or "en")
        state = _state_with_localized_node_titles(session, state, language=language)
        question_queue = [str(item) for item in state.get("question_queue", [])]
        current_index = int(state.get("current_index", 0))
        current_question = None
        if assessment.status in {"active", "awaiting_answer"} and current_index < len(question_queue):
            current_question = _question_by_id(assessment, question_queue[current_index])
        questions = [
            _question_to_read(
                session,
                _question_by_id(assessment, question_id),
                current=index + 1,
                total=len(question_queue),
            )
            for index, question_id in enumerate(question_queue)
        ]
        workspace_id = metadata.get("workspace_session_id")
        return PosttestSessionRead(
            session_id=assessment.id,
            learning_goal_id=assessment.learning_goal_id,
            track_id=assessment.track_id,
            workspace_session_id=UUID(str(workspace_id)) if workspace_id else None,
            posttest_source=str(metadata.get("posttest_source") or ""),
            language=language,
            status=assessment.status,
            current_question=(
                _question_to_read(session, current_question, current=current_index + 1, total=len(question_queue))
                if current_question
                else None
            ),
            questions=[item for item in questions if item is not None],
            node_results=_node_results_read(state),
            question_count=current_index,
            total_questions=len(question_queue),
        )

    def submit_answer(
        self,
        session: Session,
        *,
        user: UserAccount,
        session_id: UUID,
        question_id: UUID,
        selected_option_id: UUID,
        confidence: int,
        typed_reasoning: str = "",
        canvas_asset_id: UUID | None = None,
        used_canvas: bool = False,
    ) -> PosttestAnswerResponse | None:
        assessment = _load_assessment(session, user=user, session_id=session_id)
        if assessment is None:
            return None
        if assessment.status not in {"active", "awaiting_answer"}:
            raise ValueError("Posttest is not active.")

        question = _question_by_id(assessment, str(question_id))
        if question is None:
            raise LookupError("Question was not found in this posttest session.")
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

        evaluation = _mcq_only_posttest_evaluation(selected_option=option)
        if canvas_asset_id is not None:
            evaluation["canvas_status"] = "stored_not_evaluated"
        elif used_canvas:
            evaluation["canvas_status"] = "client_canvas_not_uploaded"
        is_correct = bool(evaluation["is_correct"])
        attempt = AssessmentAttempt(
            session_id=assessment.id,
            question_id=question.id,
            selected_option_id=option.id,
            canvas_asset_id=canvas_asset_id,
            confidence=confidence,
            explanation_text=typed_reasoning.strip(),
            typed_reasoning=typed_reasoning.strip(),
            used_canvas=used_canvas or canvas_asset_id is not None,
            score=float(evaluation["answer_score"]),
            is_correct=is_correct,
            answer_score=float(evaluation["answer_score"]),
            reasoning_score=evaluation["reasoning_score"],
            canvas_score=evaluation["canvas_score"],
            evidence_score=float(evaluation["evidence_score"]),
            diagnostic_signal=str(evaluation["diagnostic_signal"]),
            evaluated_result={
                "verdict": "CORRECT" if is_correct else "INCORRECT",
                "official_scoring": "mcq_only",
                "diagnostic_signal": evaluation["diagnostic_signal"],
                "reasoning_signal": evaluation["reasoning_signal"],
                "reasoning_feedback": evaluation["reasoning_feedback"],
            },
            evaluation_metadata_json={
                "source": "posttest_mcq_only",
                "official_scoring": "mcq_only",
                "self_reported_confidence": confidence,
                "confidence": evaluation["confidence"],
                "canvas_status": evaluation["canvas_status"],
                "reasoning_signal": evaluation["reasoning_signal"],
                "reasoning_feedback": evaluation["reasoning_feedback"],
                "reasoning_evaluation_source": evaluation["reasoning_evaluation_source"],
                "written_evidence_status": (
                    "stored_not_evaluated" if typed_reasoning.strip() else "not_provided"
                ),
                "diagnostic_evidence_only": bool(
                    typed_reasoning.strip() or used_canvas or canvas_asset_id is not None
                ),
            },
        )
        session.add(attempt)

        state = deepcopy(assessment.decision_state_json or {})
        concept_code = str(question.metadata_json.get("concept_code") or _concept_code(session, question))
        node_results = state.get("node_results", {}) if isinstance(state.get("node_results"), dict) else {}
        node_state = node_results.get(concept_code)
        if not isinstance(node_state, dict):
            raise ValueError("Node state was not found for this posttest question.")
        if question.concept_id is not None:
            concept = session.get(KnowledgeConcept, question.concept_id)
            if concept is not None:
                question_metadata = question.metadata_json or {}
                language = str(
                    question_metadata.get("language")
                    or question_metadata.get("learner_language")
                    or (assessment.metadata_json or {}).get("language")
                    or "en"
                )
                node_state["concept_title"] = _localized_concept_title(concept, language=language)
        _record_node_attempt(node_state, question=question, evaluation=evaluation, is_correct=is_correct)
        _refresh_node_result_score(node_state)

        question_queue = [str(item) for item in state.get("question_queue", [])]
        current_index = int(state.get("current_index", 0)) + 1
        state["current_index"] = current_index
        state["node_results"] = node_results
        state["official_result"] = _official_result_from_node(node_state)
        assessment.decision_state_json = state

        completed = current_index >= len(question_queue)
        next_question = None
        if completed:
            assessment.status = "completed"
            assessment.completed_at = datetime.now(UTC)
        else:
            next_question = _question_by_id(assessment, question_queue[current_index])

        session.commit()
        return PosttestAnswerResponse(
            attempt_id=attempt.id,
            is_correct=is_correct,
            evaluation=_evaluation_to_read(evaluation),
            node_result=_node_result_read(concept_code, node_state),
            next_question=(
                _question_to_read(session, next_question, current=current_index + 1, total=len(question_queue))
                if next_question
                else None
            ),
            completed=completed,
        )

    def finalize(
        self,
        session: Session,
        *,
        user: UserAccount,
        session_id: UUID,
    ) -> PosttestFinalizeResponse | None:
        assessment = _load_assessment(session, user=user, session_id=session_id)
        if assessment is None:
            return None

        state = deepcopy(assessment.decision_state_json or {})
        metadata = assessment.metadata_json or {}
        language = str(metadata.get("language") or metadata.get("learner_language") or "en")
        state = _state_with_localized_node_titles(session, state, language=language)
        node_results = state.get("node_results", {}) if isinstance(state.get("node_results"), dict) else {}
        target_code = str((assessment.metadata_json or {}).get("target_concept_code") or "")
        target_payload = node_results.get(target_code) if target_code else None
        if not isinstance(target_payload, dict) and node_results:
            target_code, target_payload = next(
                ((code, payload) for code, payload in node_results.items() if isinstance(payload, dict)),
                ("", None),
            )
        if not isinstance(target_payload, dict):
            raise ValueError("Posttest has no result payload.")

        _refresh_node_result_score(target_payload)
        official_result = _official_result_from_node(target_payload)
        state["official_result"] = official_result

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        already_finalized = bool((assessment.metadata_json or {}).get("posttest_finalized_at"))
        target_concept = session.get(KnowledgeConcept, assessment.target_concept_id) if assessment.target_concept_id else None
        if target_concept is not None:
            concept_state = session.scalar(
                select(LearnerConceptState).where(
                    LearnerConceptState.user_id == user.id,
                    LearnerConceptState.concept_id == target_concept.id,
                )
            )
            if concept_state is None:
                concept_state = LearnerConceptState(
                    user_id=user.id,
                    concept_id=target_concept.id,
                    status="review_due",
                    mastery_score=0.0,
                    confidence_score=0.0,
                    evidence_count=0,
                )
                session.add(concept_state)
            scaled_mastery = _clamp01(float(official_result["official_scaled_score"]) / 10.0)
            concept_state.mastery_score = scaled_mastery
            concept_state.confidence_score = scaled_mastery
            if not already_finalized:
                concept_state.evidence_count = (concept_state.evidence_count or 0) + int(
                    official_result["answered_count"]
                )
            concept_state.last_evaluated_at = now
            if official_result["official_pass"]:
                concept_state.status = "mastered"
                concept_state.next_review_at = now + timedelta(days=7)
            else:
                concept_state.status = "review_due"
                concept_state.next_review_at = now

        assessment.status = "completed"
        assessment.completed_at = assessment.completed_at or now
        assessment.decision_state_json = {**state, "node_results": node_results}
        progression = _apply_posttest_progression(
            session,
            user=user,
            assessment=assessment,
            passed=bool(official_result["official_pass"]),
            node_result=target_payload,
            now=now,
        )
        assessment.metadata_json = {
            **(assessment.metadata_json or {}),
            "posttest_finalized_at": now_iso,
            "node_results": node_results,
            "official_result": official_result,
            "recommended_next_step": _recommended_next_step(
                official_pass=bool(official_result["official_pass"]),
                metadata=assessment.metadata_json or {},
                node_result=target_payload,
            ),
            "progression": progression.model_dump(mode="json"),
        }

        session.commit()
        node_reads = _node_results_read(state)
        return PosttestFinalizeResponse(
            session_id=assessment.id,
            status=assessment.status,
            node_results=node_reads,
            retake_required_concepts=[item.concept_code for item in node_reads if item.retake_required],
            progression=progression,
        )


def _resolve_posttest_context(
    session: Session,
    *,
    user: UserAccount,
    workspace_session_id: UUID | None,
    learning_goal_id: UUID | None,
    track_id: UUID | None,
    module_id: UUID | None,
) -> dict[str, Any] | None:
    if workspace_session_id is not None:
        workspace = _load_workspace(session, user=user, workspace_id=workspace_session_id)
        if workspace is None:
            return None
        track = session.scalar(
            select(LearningTrack).where(LearningTrack.id == workspace.track_id, LearningTrack.user_id == user.id)
        )
        if track is None:
            return None
        goal = session.get(LearningGoal, track.learning_goal_id)
        if goal is None or goal.user_id != user.id:
            return None
        # The exact workspace session is authoritative; track/module/goal request fields
        # are legacy hints and may be stale in clients that resume from workspace UI.
        target = _target_concept_for_goal_or_workspace(
            session,
            goal=goal,
            workspace=workspace,
            module_id=workspace.module_id,
        )
        if target is None:
            raise ValueError("Posttest target concept was not found.")
        return {
            "goal": goal,
            "target_concept": target,
            "track_id": track.id,
            "module_id": workspace.module_id,
            "workspace": workspace,
            "posttest_source": "workspace_session",
        }

    goal, resolved_track_id, resolved_module_id = _resolve_goal_context(
        session,
        user=user,
        learning_goal_id=learning_goal_id,
        track_id=track_id,
        module_id=module_id,
    )
    if goal is None:
        return None
    workspace = _latest_workspace_for_goal(
        session,
        user=user,
        goal=goal,
        track_id=resolved_track_id,
        module_id=resolved_module_id,
    )
    target = _target_concept_for_goal_or_workspace(
        session,
        goal=goal,
        workspace=workspace,
        module_id=resolved_module_id,
    )
    if target is None:
        raise ValueError("Posttest target concept was not found.")
    return {
        "goal": goal,
        "target_concept": target,
        "track_id": resolved_track_id or (workspace.track_id if workspace else None),
        "module_id": resolved_module_id or (workspace.module_id if workspace else None),
        "workspace": workspace,
        "posttest_source": "latest_workspace_for_goal" if workspace is not None else "learning_goal_fallback",
    }


def _workspace_allows_posttest(workspace: WorkspaceSession) -> bool:
    metadata = workspace.metadata_json or {}
    if bool(metadata.get("posttest_eligible", False)):
        return True
    trigger = metadata.get("posttest_trigger")
    return isinstance(trigger, dict) and str(trigger.get("status") or "") == "ready"


def _resolve_goal_context(
    session: Session,
    *,
    user: UserAccount,
    learning_goal_id: UUID | None,
    track_id: UUID | None,
    module_id: UUID | None,
) -> tuple[LearningGoal | None, UUID | None, UUID | None]:
    resolved_track_id = track_id
    resolved_module_id = module_id
    if module_id is not None:
        module = session.scalar(
            select(TrackModule)
            .join(LearningTrack, TrackModule.track_id == LearningTrack.id)
            .where(TrackModule.id == module_id, LearningTrack.user_id == user.id)
        )
        if module is None:
            return None, resolved_track_id, resolved_module_id
        resolved_track_id = module.track_id

    if learning_goal_id is not None:
        goal = session.scalar(
            select(LearningGoal)
            .where(LearningGoal.id == learning_goal_id, LearningGoal.user_id == user.id)
            .options(selectinload(LearningGoal.track))
        )
        if goal is None:
            return None, resolved_track_id, resolved_module_id
        if resolved_track_id is not None:
            owned_track = session.scalar(
                select(LearningTrack).where(
                    LearningTrack.id == resolved_track_id,
                    LearningTrack.user_id == user.id,
                    LearningTrack.learning_goal_id == goal.id,
                )
            )
            if owned_track is None:
                raise ValueError("track_id/module_id does not belong to learning_goal_id.")
        return goal, resolved_track_id or (goal.track.id if goal.track else None), resolved_module_id

    if resolved_track_id is not None:
        track = session.scalar(
            select(LearningTrack).where(LearningTrack.id == resolved_track_id, LearningTrack.user_id == user.id)
        )
        if track is None:
            return None, resolved_track_id, resolved_module_id
        goal = session.get(LearningGoal, track.learning_goal_id)
        return goal, track.id, resolved_module_id

    return None, resolved_track_id, resolved_module_id


def _load_workspace(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID,
) -> WorkspaceSession | None:
    return session.scalar(
        select(WorkspaceSession)
        .where(WorkspaceSession.id == workspace_id, WorkspaceSession.user_id == user.id)
        .options(selectinload(WorkspaceSession.events))
    )


def _latest_workspace_for_goal(
    session: Session,
    *,
    user: UserAccount,
    goal: LearningGoal,
    track_id: UUID | None,
    module_id: UUID | None,
) -> WorkspaceSession | None:
    statement = (
        select(WorkspaceSession)
        .join(LearningTrack, WorkspaceSession.track_id == LearningTrack.id)
        .where(
            WorkspaceSession.user_id == user.id,
            LearningTrack.learning_goal_id == goal.id,
            WorkspaceSession.status.in_({"active", "completed"}),
        )
        .options(selectinload(WorkspaceSession.events))
        .order_by(WorkspaceSession.updated_at.desc(), WorkspaceSession.created_at.desc())
    )
    if track_id is not None:
        statement = statement.where(WorkspaceSession.track_id == track_id)
    if module_id is not None:
        statement = statement.where(WorkspaceSession.module_id == module_id)
    return session.scalar(statement)


def _target_concept_for_goal_or_workspace(
    session: Session,
    *,
    goal: LearningGoal,
    workspace: WorkspaceSession | None,
    module_id: UUID | None,
) -> KnowledgeConcept | None:
    scoped_module_id = workspace.module_id if workspace is not None else module_id
    if scoped_module_id is not None:
        module = session.get(TrackModule, scoped_module_id)
        if module is not None and module.concept_id is not None:
            concept = session.get(KnowledgeConcept, module.concept_id)
            if concept is not None:
                return concept
    if goal.target_concept_id is not None:
        return session.get(KnowledgeConcept, goal.target_concept_id)
    return None


def _apply_posttest_progression(
    session: Session,
    *,
    user: UserAccount,
    assessment: AssessmentSession,
    passed: bool,
    node_result: dict[str, Any],
    now: datetime,
) -> PosttestProgressionRead:
    metadata = assessment.metadata_json or {}
    workspace_id = _uuid_value(metadata.get("workspace_session_id"))
    workspace = (
        _load_workspace(session, user=user, workspace_id=workspace_id)
        if workspace_id is not None
        else None
    )

    module_id = workspace.module_id if workspace is not None else _uuid_value(metadata.get("module_id"))
    track_id = assessment.track_id or _uuid_value(metadata.get("track_id"))
    if track_id is None and module_id is not None:
        scoped_module = session.scalar(
            select(TrackModule)
            .join(LearningTrack, TrackModule.track_id == LearningTrack.id)
            .where(TrackModule.id == module_id, LearningTrack.user_id == user.id)
        )
        track_id = scoped_module.track_id if scoped_module is not None else None

    track = (
        session.scalar(
            select(LearningTrack)
            .where(LearningTrack.id == track_id, LearningTrack.user_id == user.id)
            .options(selectinload(LearningTrack.modules))
        )
        if track_id is not None
        else None
    )
    modules = sorted(track.modules, key=lambda item: item.sort_order) if track is not None else []
    module = next((item for item in modules if item.id == module_id), None)
    if module is None and module_id is None and assessment.target_concept_id is not None:
        matching_modules = [
            item for item in modules if item.concept_id == assessment.target_concept_id
        ]
        if len(matching_modules) == 1:
            module = matching_modules[0]
            module_id = module.id
    next_module = _next_track_module(modules, module=module)

    if module is not None:
        module.status = "completed" if passed else "active"
        if passed:
            if next_module is not None and next_module.status == "locked":
                next_module.status = "ready"
        elif next_module is not None and next_module.status == "ready":
            next_module.status = "locked"

    goal = (
        session.get(LearningGoal, assessment.learning_goal_id)
        if assessment.learning_goal_id is not None
        else (session.get(LearningGoal, track.learning_goal_id) if track is not None else None)
    )
    all_modules_completed = bool(modules) and all(item.status == "completed" for item in modules)
    target_module_completed = _target_module_completed(goal=goal, modules=modules)
    learning_goal_completed = all_modules_completed and target_module_completed

    if track is not None:
        completed_count = sum(1 for item in modules if item.status == "completed")
        track.progress_percent = int(round((completed_count / max(1, len(modules))) * 100))
        track.status = "completed" if learning_goal_completed else "active"

    if goal is not None and goal.status not in {"archived", "cancelled"}:
        if learning_goal_completed:
            goal.status = "completed"
            goal.completed_at = goal.completed_at or now
        else:
            goal.status = "in_progress"
            goal.completed_at = None

    remediation_phase: str | None = None
    remediation_reason: str | None = None
    if workspace is not None:
        workspace_metadata = dict(workspace.metadata_json or {})
        workspace_metadata["posttest_eligible"] = False
        workspace_metadata["phase_transition_pending"] = False
        workspace_metadata["posttest_outcome"] = {
            "assessment_session_id": str(assessment.id),
            "passed": passed,
            "finalized_at": now.isoformat(),
        }
        if passed:
            workspace.status = "completed"
            workspace_metadata = _update_posttest_trigger(
                workspace_metadata,
                status="completed",
                passed=True,
            )
        else:
            workspace.status = "active"
            remediation_phase, remediation_reason = _remediation_destination(node_result)
            workspace_metadata = _route_workspace_to_remediation(
                workspace_metadata,
                phase=remediation_phase,
                reason=remediation_reason,
                assessment_id=assessment.id,
                now=now,
                node_result=node_result,
            )
            workspace_metadata = _update_posttest_trigger(
                workspace_metadata,
                status="needs_remediation",
                passed=False,
            )
        workspace.metadata_json = workspace_metadata
        workspace.updated_at = now

    progression = PosttestProgressionRead(
        passed=passed,
        track_id=track.id if track is not None else track_id,
        track_status=track.status if track is not None else None,
        track_progress_percent=track.progress_percent if track is not None else None,
        module_id=module.id if module is not None else module_id,
        module_status=module.status if module is not None else None,
        next_module_id=next_module.id if next_module is not None else None,
        next_module_status=next_module.status if next_module is not None else None,
        workspace_session_id=workspace.id if workspace is not None else workspace_id,
        workspace_status=workspace.status if workspace is not None else None,
        goal_status=goal.status if goal is not None else None,
        remediation_phase=remediation_phase,
        remediation_reason=remediation_reason,
    )
    if workspace is not None:
        workspace.metadata_json = {
            **dict(workspace.metadata_json or {}),
            "posttest_progression": progression.model_dump(mode="json"),
        }
    return progression


def _next_track_module(
    modules: list[TrackModule],
    *,
    module: TrackModule | None,
) -> TrackModule | None:
    if module is None:
        return None
    for index, candidate in enumerate(modules):
        if candidate.id == module.id and index + 1 < len(modules):
            return modules[index + 1]
    return None


def _target_module_completed(
    *,
    goal: LearningGoal | None,
    modules: list[TrackModule],
) -> bool:
    if goal is None or goal.target_concept_id is None:
        return False
    return any(
        module.concept_id == goal.target_concept_id and module.status == "completed"
        for module in modules
    )


def _remediation_destination(node_result: dict[str, Any]) -> tuple[str, str]:
    attempts = node_result.get("attempts") if isinstance(node_result.get("attempts"), list) else []
    signals: list[str] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        for key in ("diagnostic_signal", "reasoning_signal", "misconception_status"):
            value = str(attempt.get(key) or "").strip().lower()
            if value:
                signals.append(value)
        evidence_tags = attempt.get("evidence_tags")
        if isinstance(evidence_tags, list):
            signals.extend(str(tag).strip().lower() for tag in evidence_tags if str(tag).strip())

    misconception_tokens = (
        "misconception",
        "wrong_method",
        "invalid_method",
        "method_invalid",
        "omitted_inner",
        "still_active",
    )
    if any(token in signal for signal in signals for token in misconception_tokens):
        return "explore", "posttest_misconception_still_active"
    return "elaborate", "posttest_transfer_needs_practice"


def _route_workspace_to_remediation(
    metadata: dict[str, Any],
    *,
    phase: str,
    reason: str,
    assessment_id: UUID,
    now: datetime,
    node_result: dict[str, Any],
) -> dict[str, Any]:
    routed = dict(metadata)
    now_iso = now.isoformat()
    history = [dict(item) for item in routed.get("phase_history", []) if isinstance(item, dict)]
    if not history or str(history[-1].get("phase") or "") != phase:
        if history:
            history[-1]["exited_at"] = history[-1].get("exited_at") or now_iso
        history.append(
            {
                "phase": phase,
                "entered_at": now_iso,
                "exited_at": None,
                "turn_count": 0,
            }
        )
    else:
        history[-1]["exited_at"] = None
    visited = [str(item) for item in routed.get("visited_5e_phases", []) if str(item)]
    if phase not in visited:
        visited.append(phase)
    routed.update(
        {
            "current_phase": phase,
            "phase_history": history,
            "visited_5e_phases": visited,
            "posttest_remediation": {
                "assessment_session_id": str(assessment_id),
                "phase": phase,
                "reason": reason,
                "weak_question_types": _weak_question_types(node_result),
                "routed_at": now_iso,
            },
        }
    )
    return routed


def _update_posttest_trigger(
    metadata: dict[str, Any],
    *,
    status: str,
    passed: bool,
) -> dict[str, Any]:
    updated = dict(metadata)
    trigger = updated.get("posttest_trigger")
    if isinstance(trigger, dict):
        updated["posttest_trigger"] = {
            **trigger,
            "status": status,
            "passed": passed,
        }
    return updated


def _weak_question_types(node_result: dict[str, Any]) -> list[str]:
    attempts = node_result.get("attempts") if isinstance(node_result.get("attempts"), list) else []
    return list(
        dict.fromkeys(
            str(item.get("question_type") or "").strip()
            for item in attempts
            if isinstance(item, dict)
            and item.get("is_correct") is False
            and str(item.get("question_type") or "").strip()
        )
    )


def _uuid_value(value: object) -> UUID | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _workspace_learning_summary(
    session: Session,
    *,
    user: UserAccount,
    goal: LearningGoal,
    target: KnowledgeConcept,
    workspace: WorkspaceSession | None,
    posttest_source: str,
    language: str,
) -> dict[str, Any]:
    events = sorted(workspace.events, key=lambda event: event.event_index) if workspace is not None else []
    text_events = [
        event
        for event in events
        if event.text_payload.strip() and event.event_type in {"text", "quiz_answer", "note"}
    ]
    quiz_events = [event for event in events if event.event_type == "quiz_answer"]
    media_events = [event for event in events if event.event_type == "media_generated"]
    mistake_events = [
        event
        for event in quiz_events
        if (event.metadata_json or {}).get("is_correct") is False
        or str((event.metadata_json or {}).get("diagnostic_signal") or "").strip()
    ]
    raw_transcript = _compact_event_transcript(text_events)
    metadata = workspace.metadata_json if workspace is not None else {}
    covered_codes = [
        str(item).strip()
        for item in [
            metadata.get("active_node_id"),
            *(
                metadata.get("active_prerequisites")
                if isinstance(metadata.get("active_prerequisites"), list)
                else []
            ),
        ]
        if str(item).strip()
    ]
    target_title = _localized_concept_title(target, language=language)
    target_description = _localized_concept_description(target, language=language, title=target_title)
    summary = {
        "posttest_source": posttest_source,
        "workspace_session_id": str(workspace.id) if workspace is not None else None,
        "learning_goal_id": str(goal.id),
        "selected_learning_goal": _localized_goal_topic(goal, fallback=target_title, language=language),
        "target_concept": {
            "concept_id": str(target.id),
            "concept_code": target.code,
            "title": target_title,
            "description": target_description,
        },
        "concepts_covered": list(dict.fromkeys([target.code, *covered_codes])),
        "explanations_and_materials": _event_snippets(
            [event for event in text_events if event.actor_type in {"tutor", "system"}],
            limit=8,
        ),
        "examples_discussed": _event_snippets(_events_matching(text_events, ("contoh", "example", "misal")), limit=5),
        "practice_questions_attempted": [
            {
                "text": _truncate(event.text_payload, 240),
                "is_correct": (event.metadata_json or {}).get("is_correct"),
                "metadata": _public_event_metadata(event.metadata_json or {}),
            }
            for event in quiz_events[-8:]
        ],
        "mistakes_or_misconceptions": [
            {
                "text": _truncate(event.text_payload, 240),
                "metadata": _public_event_metadata(event.metadata_json or {}),
            }
            for event in mistake_events[-8:]
        ],
        "materials_shown": [
            _public_event_metadata(event.metadata_json or {})
            for event in media_events[-5:]
        ],
        "final_workspace_summary": _final_workspace_state(workspace, text_events=text_events),
        "learner_language": language,
        "learner_level": {
            "education_level": getattr(user.learner_profile, "education_level", None) if user.learner_profile else None,
            "grade_level": getattr(user.learner_profile, "grade_level", None) if user.learner_profile else None,
        },
        "compact_transcript": raw_transcript,
        "summary_quality": "workspace_history" if text_events or quiz_events or media_events else "fallback_target_concept",
    }
    if not text_events and not quiz_events and not media_events:
        summary["posttest_source"] = "learning_goal_fallback"
        summary["compact_transcript"] = ""
    return summary


def _posttest_generation_context(summary: dict[str, Any]) -> str:
    return (
        "Use this compact workspace learning summary as the primary source for a fixed posttest. "
        "Do not use pretest diagnosis as the posttest scope. "
        "The complete posttest is 10 MCQs overall: 3 medium and 7 hard. "
        "This generation call may request either the full set or a subset; follow the requested difficulty sequence exactly. "
        "No easy questions unless explicitly requested. Official scoring is MCQ-only.\n"
        f"{summary}"
    )


def _create_posttest_questions(
    generation_service: AdaptivePretestGenerationService,
    session: Session,
    *,
    assessment: AssessmentSession,
    concept: KnowledgeConcept,
    language: str,
    diagnosis_context: str,
) -> list[AssessmentQuestion]:
    questions: list[AssessmentQuestion] = []
    generation_batches = [
        ["medium", "medium", "medium"],
        ["hard", "hard", "hard"],
        ["hard", "hard"],
        ["hard", "hard"],
    ]
    for batch in generation_batches:
        try:
            questions.extend(
                _generate_posttest_question_chunk(
                    generation_service,
                    session,
                    assessment=assessment,
                    concept=concept,
                    difficulties=batch,
                    language=language,
                    diagnosis_context=diagnosis_context,
                )
            )
        except ValueError as exc:
            if _non_retryable_generation_error(exc):
                raise
            raise AssessmentQuestionGenerationError(
                f"Posttest question generation failed for difficulty batch {batch}: {exc}"
            ) from exc

    full_sequence = list(POSTTEST_DIFFICULTIES)
    if len(questions) != len(full_sequence):
        raise AssessmentQuestionGenerationError(
            f"Posttest question generation produced {len(questions)} questions, expected {len(full_sequence)}."
        )
    return questions


def _generate_posttest_question_chunk(
    generation_service: AdaptivePretestGenerationService,
    session: Session,
    *,
    assessment: AssessmentSession,
    concept: KnowledgeConcept,
    difficulties: list[str],
    language: str,
    diagnosis_context: str,
) -> list[AssessmentQuestion]:
    return generation_service.create_fresh_questions(
        session,
        assessment=assessment,
        concept=concept,
        difficulties=difficulties,
        assessment_type="posttest",
        language=language,
        node_role="goal",
        diagnosis_context=diagnosis_context,
        step_label="Posttest",
        topic=_localized_concept_title(concept, language=language),
    )


def _non_retryable_generation_error(exc: ValueError) -> bool:
    message = str(exc).lower()
    return "api key" in message or "cannot run inside" in message


def _compact_event_transcript(events: list[WorkspaceEvent]) -> str:
    rows: list[str] = []
    for event in events[-40:]:
        text = _truncate(event.text_payload.strip(), 420)
        if text:
            rows.append(f"{event.actor_type}:{event.event_type}: {text}")
    transcript = "\n".join(rows)
    if len(transcript) > WORKSPACE_SUMMARY_TEXT_LIMIT:
        return transcript[-WORKSPACE_SUMMARY_TEXT_LIMIT:]
    return transcript


def _event_snippets(events: list[WorkspaceEvent], *, limit: int) -> list[str]:
    return [_truncate(event.text_payload.strip(), 260) for event in events[-limit:] if event.text_payload.strip()]


def _events_matching(events: list[WorkspaceEvent], terms: tuple[str, ...]) -> list[WorkspaceEvent]:
    return [event for event in events if any(term in event.text_payload.lower() for term in terms)]


def _final_workspace_state(workspace: WorkspaceSession | None, *, text_events: list[WorkspaceEvent]) -> dict[str, Any]:
    if workspace is None:
        return {"status": "no_workspace_history", "last_message": ""}
    last_text = next((event.text_payload.strip() for event in reversed(text_events) if event.text_payload.strip()), "")
    return {
        "status": workspace.status,
        "current_topic": workspace.current_topic,
        "updated_at": workspace.updated_at.isoformat() if workspace.updated_at else "",
        "last_message": _truncate(last_text, 360),
    }


def _public_event_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    excluded = {"ai_audit", "raw_prompt", "prompt", "token_usage"}
    return {
        str(key): value
        for key, value in metadata.items()
        if key not in excluded and isinstance(value, (str, int, float, bool, type(None), list, dict))
    }


def _active_posttest_for_goal(
    session: Session,
    *,
    user: UserAccount,
    goal_id: UUID,
    workspace_session_id: UUID | None,
    module_id: UUID | None,
) -> AssessmentSession | None:
    statement = (
        select(AssessmentSession)
        .where(
            AssessmentSession.user_id == user.id,
            AssessmentSession.learning_goal_id == goal_id,
            AssessmentSession.session_type == "posttest",
            AssessmentSession.status.in_({"active", "awaiting_answer"}),
        )
        .options(selectinload(AssessmentSession.questions).selectinload(AssessmentQuestion.options))
        .order_by(AssessmentSession.created_at.desc())
    )
    for assessment in session.scalars(statement):
        metadata = assessment.metadata_json or {}
        if workspace_session_id is not None:
            if str(metadata.get("workspace_session_id") or "") == str(workspace_session_id):
                return assessment
            continue
        if module_id is not None:
            if str(metadata.get("module_id") or "") == str(module_id):
                return assessment
            continue
        if workspace_session_id is None and module_id is None:
            return assessment
    return None


def _load_assessment(session: Session, *, user: UserAccount, session_id: UUID) -> AssessmentSession | None:
    return session.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.id == session_id,
            AssessmentSession.user_id == user.id,
            AssessmentSession.session_type == "posttest",
        )
        .options(
            selectinload(AssessmentSession.questions).selectinload(AssessmentQuestion.options),
            selectinload(AssessmentSession.question_packs).selectinload(AssessmentQuestionPack.questions),
        )
    )


def _question_by_id(assessment: AssessmentSession, question_id: str) -> AssessmentQuestion | None:
    return next((question for question in assessment.questions if str(question.id) == question_id), None)


def _question_to_read(
    session: Session,
    question: AssessmentQuestion | None,
    *,
    current: int,
    total: int,
) -> PosttestQuestionRead | None:
    if question is None:
        return None
    concept = session.get(KnowledgeConcept, question.concept_id) if question.concept_id else None
    question_metadata = question.metadata_json or {}
    question_language = str(
        question_metadata.get("language")
        or question_metadata.get("learner_language")
        or "en"
    )
    return PosttestQuestionRead(
        id=question.id,
        concept_id=question.concept_id,
        concept_code=concept.code if concept else str(question_metadata.get("concept_code", "")),
        concept_title=(
            _localized_concept_title(concept, language=question_language)
            if concept is not None
            else question.topic
        ),
        difficulty=question.difficulty_label.lower(),
        prompt=question.prompt,
        helper=question.helper_text,
        options=[{"id": option.id, "label": option.label, "text": option.text} for option in question.options],
        progress={"current": current, "max": total, "label": f"Question {current} of {total}"},
    )


def _record_node_attempt(
    node_state: dict[str, Any],
    *,
    question: AssessmentQuestion,
    evaluation: dict[str, Any],
    is_correct: bool,
) -> None:
    node_state["answered_count"] = int(node_state.get("answered_count", 0)) + 1
    node_state["correct_count"] = int(node_state.get("correct_count", 0)) + (1 if is_correct else 0)
    node_state["answer_score_sum"] = round(
        float(node_state.get("answer_score_sum") or 0.0) + float(evaluation["answer_score"]),
        4,
    )
    node_state["evidence_score_sum"] = round(
        float(node_state.get("evidence_score_sum") or 0.0) + float(evaluation["evidence_score"]),
        4,
    )
    node_state["confidence_sum"] = round(
        float(node_state.get("confidence_sum") or 0.0) + float(evaluation["confidence"]),
        4,
    )
    attempts = node_state.setdefault("attempts", [])
    if isinstance(attempts, list):
        attempts.append(
            {
                "question_id": str(question.id),
                "difficulty": question.difficulty_label.lower(),
                "question_type": str((question.metadata_json or {}).get("question_type") or ""),
                "is_correct": is_correct,
                "answer_score": round(float(evaluation["answer_score"]), 4),
                "reasoning_score": (
                    round(float(evaluation["reasoning_score"]), 4)
                    if evaluation["reasoning_score"] is not None
                    else None
                ),
                "canvas_score": (
                    round(float(evaluation["canvas_score"]), 4)
                    if evaluation["canvas_score"] is not None
                    else None
                ),
                "evidence_score": round(float(evaluation["evidence_score"]), 4),
                "confidence": round(float(evaluation["confidence"]), 4),
                "diagnostic_signal": str(evaluation["diagnostic_signal"]),
                "reasoning_signal": str(evaluation["reasoning_signal"]),
                "canvas_status": evaluation["canvas_status"],
            }
        )


def _refresh_node_result_score(payload: dict[str, Any]) -> None:
    total_questions = max(1, int(payload.get("total_questions") or len(POSTTEST_DIFFICULTIES)))
    answered_count = max(0, int(payload.get("answered_count") or 0))
    correct_count = max(0, int(payload.get("correct_count") or 0))
    answer_score_sum = _float_value(payload.get("answer_score_sum"), fallback=float(correct_count))
    evidence_score_sum = _float_value(payload.get("evidence_score_sum"), fallback=answer_score_sum)
    confidence_sum = _float_value(payload.get("confidence_sum"), fallback=0.0)
    answered_for_average = max(1, answered_count)
    answer_percent = round((answer_score_sum / total_questions) * 100, 2)
    evidence_percent = round((evidence_score_sum / answered_for_average) * 100, 2) if answered_count else 0.0
    confidence_percent = round((confidence_sum / answered_for_average) * 100, 2) if answered_count else 0.0
    scaled_score = _posttest_scaled_score(score_percent=answer_percent)
    passed = answered_count >= total_questions and answer_percent >= POSTTEST_PASS_SCORE
    payload.update(
        {
            "total_questions": total_questions,
            "answered_count": answered_count,
            "correct_count": correct_count,
            "answer_score_sum": round(answer_score_sum, 4),
            "evidence_score_sum": round(evidence_score_sum, 4),
            "confidence_sum": round(confidence_sum, 4),
            "answer_percent": answer_percent,
            "evidence_percent": evidence_percent,
            "score_percent": answer_percent,
            "confidence_percent": confidence_percent,
            "scaled_score": scaled_score,
            "passed": passed,
            "retake_required": not passed,
            "metric_source": "official_mcq",
        }
    )


def _official_result_from_node(payload: dict[str, Any]) -> dict[str, Any]:
    _refresh_node_result_score(payload)
    return {
        "answered_count": int(payload.get("answered_count") or 0),
        "total_questions": int(payload.get("total_questions") or len(POSTTEST_DIFFICULTIES)),
        "correct_count": int(payload.get("correct_count") or 0),
        "pure_answer_percent": float(payload.get("answer_percent") or 0.0),
        "official_scaled_score": float(payload.get("scaled_score") or 0.0),
        "official_pass": bool(payload.get("passed")),
        "metric_source": "official_mcq",
    }


def _empty_official_result(*, total_questions: int) -> dict[str, Any]:
    return {
        "answered_count": 0,
        "total_questions": total_questions,
        "correct_count": 0,
        "pure_answer_percent": 0.0,
        "official_scaled_score": 0.0,
        "official_pass": False,
        "metric_source": "official_mcq",
    }


def _node_result_read(concept_code: str, payload: dict[str, Any]) -> PosttestNodeResultRead:
    concept_id = payload.get("concept_id")
    return PosttestNodeResultRead(
        concept_id=UUID(str(concept_id)) if concept_id else None,
        concept_code=concept_code,
        concept_title=str(payload.get("concept_title") or concept_code),
        total_questions=int(payload.get("total_questions", len(POSTTEST_DIFFICULTIES))),
        answered_count=int(payload.get("answered_count", 0)),
        correct_count=int(payload.get("correct_count", 0)),
        answer_percent=float(payload.get("answer_percent", 0.0)),
        evidence_percent=float(payload.get("evidence_percent", 0.0)),
        score_percent=float(payload.get("score_percent", 0.0)),
        confidence_percent=float(payload.get("confidence_percent", 0.0)),
        scaled_score=float(payload.get("scaled_score", 0.0)),
        passed=bool(payload.get("passed", False)),
        retake_required=bool(payload.get("retake_required", True)),
        metric_source=str(payload.get("metric_source") or "official_mcq"),
    )


def _state_with_localized_node_titles(
    session: Session,
    state: dict[str, Any],
    *,
    language: str,
) -> dict[str, Any]:
    node_results = state.get("node_results", {}) if isinstance(state.get("node_results"), dict) else {}
    for payload in node_results.values():
        if not isinstance(payload, dict):
            continue
        concept_id = payload.get("concept_id")
        if not concept_id:
            continue
        try:
            concept = session.get(KnowledgeConcept, UUID(str(concept_id)))
        except (TypeError, ValueError):
            concept = None
        if concept is not None:
            payload["concept_title"] = _localized_concept_title(concept, language=language)
    return state


def _node_results_read(state: dict[str, Any]) -> list[PosttestNodeResultRead]:
    node_results = state.get("node_results", {}) if isinstance(state.get("node_results"), dict) else {}
    return [_node_result_read(code, payload) for code, payload in node_results.items() if isinstance(payload, dict)]


def _evaluation_to_read(evaluation: dict[str, Any]) -> PosttestEvaluationRead:
    return PosttestEvaluationRead(
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
    )


def _mcq_only_posttest_evaluation(*, selected_option: Any) -> dict[str, Any]:
    is_correct = bool(selected_option.is_correct)
    answer_score = 1.0 if is_correct else 0.0
    return {
        "is_correct": is_correct,
        "answer_score": answer_score,
        "reasoning_score": None,
        "reasoning_signal": "not_applicable",
        "reasoning_feedback": "",
        "reasoning_evaluation_source": "posttest_mcq_only",
        "canvas_score": None,
        "canvas_status": None,
        "evidence_score": answer_score,
        "confidence": 0.68 if is_correct else 0.82,
        "diagnostic_signal": "correct_mcq_only" if is_correct else "concept_gap_likely",
    }


def _recommended_next_step(
    *,
    official_pass: bool,
    metadata: dict[str, Any],
    node_result: dict[str, Any],
) -> dict[str, Any]:
    summary = metadata.get("workspace_learning_summary") if isinstance(metadata.get("workspace_learning_summary"), dict) else {}
    attempts = node_result.get("attempts") if isinstance(node_result.get("attempts"), list) else []
    weak_types = list(
        dict.fromkeys(
            str(item.get("question_type"))
            for item in attempts
            if isinstance(item, dict) and item.get("is_correct") is False and str(item.get("question_type") or "").strip()
        )
    )
    return {
        "source": "latest_posttest_and_workspace_history",
        "status": "mastery_unlocked" if official_pass else "review_recommended",
        "workspace_source": summary.get("posttest_source") or metadata.get("posttest_source"),
        "weak_question_types": weak_types,
        "message": (
            "Posttest passed. Continue with review cadence."
            if official_pass
            else "Review the weak posttest areas using the latest workspace history."
        ),
    }


def _posttest_scaled_score(*, score_percent: float) -> float:
    return float(round(max(0.0, min(100.0, score_percent)) / 10, 2))


def _float_value(value: object, *, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _concept_code(session: Session, question: AssessmentQuestion) -> str:
    concept = session.get(KnowledgeConcept, question.concept_id) if question.concept_id else None
    return concept.code if concept else ""


def _preferred_language(user: UserAccount) -> str:
    return preferred_language_code(user)


def _localized_goal_topic(goal: LearningGoal, *, fallback: str, language: str) -> str:
    topic = str(goal.normalized_topic or "").strip()
    if normalize_language_code(language) == "en":
        translated = translate_curriculum_label_to_english(topic)
        return translated or fallback or topic
    return topic or fallback


def _localized_concept_title(concept: KnowledgeConcept, *, language: str) -> str:
    metadata = concept.metadata_json or {}
    if normalize_language_code(language) == "id":
        return _metadata_text(metadata, "label_id") or _localized_metadata(metadata, "label", "id") or concept.title

    explicit = _metadata_text(metadata, "label_en") or _localized_metadata(metadata, "label", "en")
    label_id = _metadata_text(metadata, "label_id") or _localized_metadata(metadata, "label", "id") or concept.title
    if explicit and explicit.casefold() != label_id.casefold():
        return explicit
    translated = translate_curriculum_label_to_english(label_id)
    return translated or explicit or label_id or concept.title


def _localized_concept_description(
    concept: KnowledgeConcept,
    *,
    language: str,
    title: str,
) -> str:
    metadata = concept.metadata_json or {}
    if normalize_language_code(language) == "id":
        return (
            concept.id_desc
            or _metadata_text(metadata, "description_id")
            or _localized_metadata(metadata, "description", "id")
            or concept.description
            or f"Memahami dan menerapkan {title}."
        )
    return (
        concept.en_desc
        or _metadata_text(metadata, "description_en")
        or _metadata_text(metadata, "en_desc")
        or _localized_metadata(metadata, "description", "en")
        or f"Understand and apply {title}."
    )


def _localized_metadata(metadata: dict[str, Any], base_key: str, locale: str) -> str:
    return _metadata_text(metadata, f"{base_key}_{locale}") or _metadata_text(metadata, base_key)


def _metadata_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return str(value).strip() if value is not None else ""


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."
