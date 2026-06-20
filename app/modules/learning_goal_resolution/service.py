from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.language import is_indonesian_language, normalize_language_code
from app.modules.accounts.models import UserAccount
from app.modules.ai.client import ai_client
from app.modules.ai.config import get_ai_settings
from app.modules.curriculum.kurikulum_merdeka import (
    canonical_subject_code,
    translate_curriculum_label_to_english,
)
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.curriculum.seed import seed_curriculum
from app.modules.learning.models import AssessmentSession, LearningGoal
from app.modules.review.flagger import enqueue_flag
from app.modules.learning_goal_resolution.candidate_retriever import (
    CandidateConceptRetriever,
    ConceptCandidate,
    score_to_confidence,
)
from app.modules.learning_goal_resolution.description_cleaner import (
    first_course_description,
)
from app.modules.learning_goal_resolution.models import LearningGoalResolution
from app.modules.learning_goal_resolution.level_context import (
    grade_relation_for_concept,
    level_note_for_relation,
)
from app.modules.learning_goal_resolution.prompt_builder import (
    PROMPT_VERSION,
    build_goal_resolution_prompt,
)
from app.modules.learning_goal_resolution.schemas import (
    ActiveGoalRead,
    ActiveLearningGoalResponse,
    ArchiveGoalResponse,
    CancelGoalResponse,
    ConceptCandidateRead,
    ConfirmLearningGoalResponse,
    ResolveLearningGoalResponse,
    SessionGoalHistoryResponse,
    SessionGoalSummaryRead,
    SubjectSessionGoalHistoryRead,
)
from app.modules.tracks.path_builder import TrackBuilderService
from app.modules.workspaces.models import WorkspaceSession


ACTIVE_GOAL_STATUSES = {"confirmed", "pretest_in_progress", "diagnosed", "in_progress"}
INACTIVE_GOAL_STATUSES = {"completed", "cancelled", "archived"}
EXACT_MATCH_CONFIDENCE_THRESHOLD = 0.75
DETERMINISTIC_EXACT_SCORE_THRESHOLD = 16.0
DEFAULT_RESOLVE_SUBJECT_CODE = "math"


class ActiveLearningGoalExists(Exception):
    def __init__(self, active_goal: ActiveGoalRead) -> None:
        self.active_goal = active_goal


@dataclass(frozen=True)
class ScopeAttempt:
    scope: str
    scope_reason: str
    candidates: list[ConceptCandidate]
    llm_result: dict[str, Any]
    selected: ConceptCandidate
    alternatives: list[ConceptCandidate]
    confidence: float
    status: str


class GoalResolverService:
    def __init__(
        self,
        *,
        retriever: CandidateConceptRetriever | None = None,
        track_builder: TrackBuilderService | None = None,
    ) -> None:
        self.retriever = retriever or CandidateConceptRetriever()
        self.track_builder = track_builder or TrackBuilderService()

    async def resolve(
        self,
        session: Session,
        *,
        user: UserAccount,
        raw_query: str,
        subject_code: str | None,
        education_level: str | None,
        grade_level: str | None,
        language: str | None,
    ) -> ResolveLearningGoalResponse:
        _ensure_curriculum(session)
        response_language = _preferred_response_language(
            user=user, requested_language=language
        )
        explicit_subject_code = DEFAULT_RESOLVE_SUBJECT_CODE
        attempt = await self._resolve_progressively(
            session,
            raw_query=raw_query,
            subject_code=explicit_subject_code,
            education_level=education_level,
            grade_level=grade_level,
            language=response_language,
            allow_cross_subject=False,
        )
        if attempt is None:
            clarification = _localized_message(
                response_language,
                id_text="Aku belum menemukan materi yang cocok. Bisa jelaskan lebih spesifik?",
                en_text="I could not find a matching concept yet. Can you make the goal more specific?",
            )
            resolution = LearningGoalResolution(
                user_id=user.id,
                raw_query=raw_query.strip(),
                subject_code=explicit_subject_code,
                education_level=education_level or "",
                grade_level=grade_level or "",
                language=response_language,
                status="needs_clarification",
                llm_provider="none",
                llm_model="none",
                prompt_version=PROMPT_VERSION,
                llm_response_json={"status": "needs_clarification"},
            )
            session.add(resolution)
            session.commit()
            enqueue_flag(
                artifact_type="diagnosis",
                artifact_id=resolution.id,
                confidence=0.0,
                signals={"status": resolution.status},
                subject=resolution.subject_code,
                learner_id=resolution.user_id,
                summary=resolution.raw_query[:160],
            )
            return ResolveLearningGoalResponse(
                resolution_id=resolution.id,
                status=resolution.status,
                confidence=0.0,
                clarification_question=clarification,
                search_scope="no_match",
                search_scope_reason="No scope returned any candidate.",
            )

        selected = attempt.selected
        candidates = attempt.candidates
        alternatives = attempt.alternatives
        confidence = attempt.confidence
        status = attempt.status
        llm_result = attempt.llm_result
        resolution = LearningGoalResolution(
            user_id=user.id,
            raw_query=raw_query.strip(),
            subject_code=_resolution_subject_code(
                explicit_subject_code=explicit_subject_code,
                selected=selected,
                status=status,
            ),
            education_level=education_level or "",
            grade_level=grade_level or "",
            language=response_language,
            suggested_concept_id=(
                selected.concept.id if status == "needs_confirmation" else None
            ),
            confidence=confidence,
            alternatives_json=[candidate.snapshot() for candidate in alternatives],
            candidate_snapshot_json=[candidate.snapshot() for candidate in candidates],
            llm_response_json=llm_result,
            status=status,
            llm_provider=str(llm_result.get("provider", "deterministic_fallback")),
            llm_model=str(llm_result.get("model", "deterministic_fallback")),
            prompt_version=PROMPT_VERSION,
        )
        session.add(resolution)
        session.commit()
        enqueue_flag(
            artifact_type="diagnosis",
            artifact_id=resolution.id,
            confidence=resolution.confidence,
            signals={"status": resolution.status},
            subject=resolution.subject_code,
            concept_id=resolution.suggested_concept_id,
            learner_id=resolution.user_id,
            summary=resolution.raw_query[:160],
        )

        return ResolveLearningGoalResponse(
            resolution_id=resolution.id,
            status=resolution.status,
            suggested_concept=(
                _concept_to_read(
                    selected,
                    confidence=confidence,
                    education_level=education_level,
                    grade_level=grade_level,
                    language=response_language,
                )
                if resolution.suggested_concept_id
                else None
            ),
            confidence=confidence,
            alternatives=[
                _concept_to_read(
                    candidate,
                    confidence=score_to_confidence(candidate.score),
                    education_level=education_level,
                    grade_level=grade_level,
                    language=response_language,
                )
                for candidate in alternatives
            ],
            clarification_question=(
                _localized_message(
                    response_language,
                    id_text=f"Benar kamu mau belajar {_concept_display_title(selected.concept, language=response_language)}?",
                    en_text=f"Do you want to learn {_concept_display_title(selected.concept, language=response_language)}?",
                )
                if status == "needs_confirmation"
                else _clarification_from_candidates(
                    language=response_language, candidates=alternatives
                )
            ),
            search_scope=attempt.scope,
            search_scope_reason=attempt.scope_reason,
            graph_focus=_graph_focus(selected=selected, alternatives=alternatives),
            can_expand_scope=_can_expand_scope(
                attempt.scope,
                allow_cross_subject=False,
            ),
            candidate_debug=_candidate_debug(
                candidates, scope=attempt.scope, language=response_language
            ),
        )

    async def reprompt(
        self,
        session: Session,
        *,
        user: UserAccount,
        resolution_id: UUID,
        raw_query: str,
    ) -> ResolveLearningGoalResponse | None:
        previous = session.scalar(
            select(LearningGoalResolution).where(
                LearningGoalResolution.id == resolution_id,
                LearningGoalResolution.user_id == user.id,
            )
        )
        if previous is None:
            return None
        previous.status = "rejected"
        return await self.resolve(
            session,
            user=user,
            raw_query=raw_query,
            subject_code=previous.subject_code or None,
            education_level=previous.education_level or None,
            grade_level=previous.grade_level or None,
            language=previous.language or None,
        )

    def select_concept(
        self,
        session: Session,
        *,
        user: UserAccount,
        resolution_id: UUID,
        concept_id: UUID | None,
        concept_code: str | None,
    ) -> ResolveLearningGoalResponse | None:
        resolution = session.scalar(
            select(LearningGoalResolution).where(
                LearningGoalResolution.id == resolution_id,
                LearningGoalResolution.user_id == user.id,
            )
        )
        if resolution is None:
            return None
        response_language = _preferred_response_language(
            user=user,
            requested_language=resolution.language or None,
        )
        if concept_id is None and not (concept_code or "").strip():
            raise ValueError("Select either concept_id or concept_code.")

        snapshots = _resolution_candidate_snapshots(resolution)
        selected_snapshot = _find_candidate_snapshot(
            snapshots,
            concept_id=concept_id,
            concept_code=concept_code,
        )
        concept = (
            _concept_from_candidate_snapshot(session, selected_snapshot)
            if selected_snapshot is not None
            else _concept_from_selection(
                session, concept_id=concept_id, concept_code=concept_code
            )
        )
        if concept is None:
            raise ValueError("Selected concept was not found.")
        if not _concept_allowed_for_resolution(concept, resolution=resolution):
            raise ValueError(
                "Selected concept is outside the locked subject for this resolution."
            )
        if selected_snapshot is None:
            selected_snapshot = _snapshot_from_concept(concept, confidence=0.99)

        selected_confidence = max(_snapshot_confidence(selected_snapshot), 0.99)
        selected_candidate = _candidate_from_concept_snapshot(
            concept, selected_snapshot
        )
        alternatives = [
            candidate
            for snapshot in snapshots
            if (candidate := _candidate_from_snapshot(session, snapshot)) is not None
            and candidate.concept.id != concept.id
        ][:4]

        resolution.suggested_concept_id = concept.id
        resolution.confidence = selected_confidence
        resolution.status = "needs_confirmation"
        resolution.language = response_language
        resolution.alternatives_json = [
            candidate.snapshot() for candidate in alternatives
        ]
        resolution.llm_response_json = {
            **(resolution.llm_response_json or {}),
            "manual_candidate_selection": {
                "concept_id": str(concept.id),
                "concept_code": concept.code,
                "selected_at": datetime.now(UTC).isoformat(),
            },
        }
        session.commit()

        return ResolveLearningGoalResponse(
            resolution_id=resolution.id,
            status=resolution.status,
            suggested_concept=_concept_to_read(
                selected_candidate,
                confidence=selected_confidence,
                education_level=resolution.education_level or None,
                grade_level=resolution.grade_level or None,
                language=response_language,
            ),
            confidence=selected_confidence,
            alternatives=[
                _concept_to_read(
                    candidate,
                    confidence=score_to_confidence(candidate.score),
                    education_level=resolution.education_level or None,
                    grade_level=resolution.grade_level or None,
                    language=response_language,
                )
                for candidate in alternatives
            ],
            clarification_question=_localized_message(
                response_language,
                id_text=f"Benar kamu mau belajar {_concept_display_title(concept, language=response_language)}?",
                en_text=f"Do you want to learn {_concept_display_title(concept, language=response_language)}?",
            ),
            search_scope=str(
                (resolution.llm_response_json or {}).get("scope")
                or "manual_candidate_selection"
            ),
            search_scope_reason=_localized_message(
                response_language,
                id_text="Kandidat dipilih langsung dari hasil rekomendasi sebelumnya.",
                en_text="Candidate selected directly from the previous recommendation set.",
            ),
            graph_focus=_graph_focus(
                selected=selected_candidate, alternatives=alternatives
            ),
            can_expand_scope=False,
            candidate_debug=_candidate_debug(
                [selected_candidate, *alternatives],
                scope="manual_candidate_selection",
                language=response_language,
            ),
        )

    def confirm(
        self,
        session: Session,
        *,
        user: UserAccount,
        resolution_id: UUID,
    ) -> ConfirmLearningGoalResponse | None:
        resolution = session.scalar(
            select(LearningGoalResolution).where(
                LearningGoalResolution.id == resolution_id,
                LearningGoalResolution.user_id == user.id,
            )
        )
        if resolution is None:
            return None
        if resolution.suggested_concept_id is None:
            raise ValueError("Resolution has no suggested concept to confirm.")

        concept = session.get(KnowledgeConcept, resolution.suggested_concept_id)
        if concept is None:
            raise ValueError("Suggested concept was not found.")
        response_language = _preferred_response_language(
            user=user,
            requested_language=resolution.language or None,
        )
        resolution.language = response_language
        subject = session.get(Subject, concept.subject_id)
        goal = LearningGoal(
            user_id=user.id,
            subject_id=concept.subject_id,
            target_concept_id=concept.id,
            resolution_id=resolution.id,
            raw_topic=resolution.raw_query,
            normalized_topic=_concept_display_title(
                concept, language=response_language
            ),
            status="confirmed",
            metadata_json={
                "source": "goal_resolution",
                "resolution_id": str(resolution.id),
                "subject_code": subject.code if subject else resolution.subject_code,
                "language": response_language,
            },
        )
        resolution.status = "confirmed"
        resolution.confirmed_at = datetime.now(UTC)
        session.add(goal)
        session.commit()
        return ConfirmLearningGoalResponse(
            learning_goal_id=goal.id,
            status=goal.status,
            target_concept=_concept_to_read(
                ConceptCandidate(concept=concept, score=resolution.confidence),
                confidence=resolution.confidence,
                language=response_language,
            ),
        )

    def create_from_concept(
        self,
        session: Session,
        *,
        user: UserAccount,
        concept_id: UUID | None,
        concept_code: str | None,
        subject_code: str | None,
        language: str | None,
    ) -> ConfirmLearningGoalResponse:
        _ensure_curriculum(session)
        if concept_id is None and not (concept_code or "").strip():
            raise ValueError("Select either concept_id or concept_code.")

        response_language = _preferred_response_language(
            user=user,
            requested_language=language,
        )
        concept = _concept_from_direct_selection(
            session,
            concept_id=concept_id,
            concept_code=concept_code,
            subject_code=subject_code,
        )
        if concept is None:
            raise ValueError("Selected concept was not found.")

        subject = session.get(Subject, concept.subject_id)
        title = _concept_display_title(concept, language=response_language)
        goal = LearningGoal(
            user_id=user.id,
            subject_id=concept.subject_id,
            target_concept_id=concept.id,
            resolution_id=None,
            raw_topic=title,
            normalized_topic=title,
            status="confirmed",
            metadata_json={
                "source": "direct_concept_selection",
                "subject_code": subject.code if subject else (subject_code or ""),
                "language": response_language,
            },
        )
        session.add(goal)
        session.commit()

        return ConfirmLearningGoalResponse(
            learning_goal_id=goal.id,
            status=goal.status,
            target_concept=_concept_to_read(
                ConceptCandidate(concept=concept, score=1.0),
                confidence=1.0,
                language=response_language,
            ),
        )

    def get_active_goal(
        self,
        session: Session,
        *,
        user: UserAccount,
    ) -> ActiveLearningGoalResponse:
        goals = list(
            session.scalars(
                select(LearningGoal)
                .where(
                    LearningGoal.user_id == user.id,
                    LearningGoal.status.in_(ACTIVE_GOAL_STATUSES),
                )
                .options(
                    selectinload(LearningGoal.track),
                    selectinload(LearningGoal.assessment_sessions),
                )
                .order_by(LearningGoal.created_at.desc())
            )
        )
        goal = goals[0] if goals else None
        return ActiveLearningGoalResponse(
            has_active_goal=goal is not None,
            goal=(
                _active_goal_to_read(
                    session,
                    goal,
                    language=_preferred_response_language(user=user),
                )
                if goal
                else None
            ),
            active_goals=[
                _active_goal_to_read(
                    session,
                    item,
                    language=_preferred_response_language(user=user),
                )
                for item in goals
            ],
        )

    def list_session_goal_history(
        self,
        session: Session,
        *,
        user: UserAccount,
    ) -> SessionGoalHistoryResponse:
        response_language = _preferred_response_language(user=user)
        goals = list(
            session.scalars(
                select(LearningGoal)
                .where(
                    LearningGoal.user_id == user.id,
                    LearningGoal.status != "archived",
                )
                .options(
                    selectinload(LearningGoal.track),
                    selectinload(LearningGoal.assessment_sessions),
                )
                .order_by(
                    LearningGoal.updated_at.desc(), LearningGoal.created_at.desc()
                )
            )
        )
        if not goals:
            return SessionGoalHistoryResponse(subjects=[])

        subject_ids = {goal.subject_id for goal in goals}
        subjects_by_id = {
            item.id: item
            for item in session.scalars(
                select(Subject).where(Subject.id.in_(subject_ids))
            )
        }
        concept_ids = {
            goal.target_concept_id
            for goal in goals
            if goal.target_concept_id is not None
        }
        concepts_by_id = {
            item.id: item
            for item in session.scalars(
                select(KnowledgeConcept).where(KnowledgeConcept.id.in_(concept_ids))
            )
        }
        track_ids = {goal.track.id for goal in goals if goal.track is not None}
        workspaces_by_track: dict[UUID, WorkspaceSession] = {}
        if track_ids:
            workspace_rows = list(
                session.scalars(
                    select(WorkspaceSession)
                    .where(
                        WorkspaceSession.user_id == user.id,
                        WorkspaceSession.track_id.in_(track_ids),
                    )
                    .order_by(
                        WorkspaceSession.track_id,
                        WorkspaceSession.updated_at.desc(),
                        WorkspaceSession.created_at.desc(),
                    )
                )
            )
            for item in workspace_rows:
                if item.track_id not in workspaces_by_track:
                    workspaces_by_track[item.track_id] = item

        grouped: dict[str, SubjectSessionGoalHistoryRead] = {}
        for goal in goals:
            subject = subjects_by_id.get(goal.subject_id)
            if subject is None:
                continue
            concept = (
                concepts_by_id.get(goal.target_concept_id)
                if goal.target_concept_id
                else None
            )
            track = goal.track
            latest_workspace = (
                workspaces_by_track.get(track.id) if track is not None else None
            )
            next_action = {
                "confirmed": "start_pretest",
                "pretest_in_progress": "continue_pretest",
                "diagnosed": "enter_workspace",
                "in_progress": "enter_workspace",
                "completed": "review_or_new_goal",
                "cancelled": "resume_or_new_goal",
            }.get(goal.status, "view_progress")
            session_goal = SessionGoalSummaryRead(
                learning_goal_id=goal.id,
                status=goal.status,
                raw_topic=goal.raw_topic,
                normalized_topic=goal.normalized_topic,
                target_concept_id=concept.id if concept else goal.target_concept_id,
                target_concept_code=concept.code if concept else None,
                target_concept_title=(
                    _concept_display_title(concept, language=response_language)
                    if concept
                    else None
                ),
                track_id=track.id if track else None,
                workspace_session_id=latest_workspace.id if latest_workspace else None,
                next_action=next_action,
                created_at=goal.created_at.isoformat() if goal.created_at else "",
                updated_at=goal.updated_at.isoformat() if goal.updated_at else "",
            )
            key = subject.code
            if key not in grouped:
                grouped[key] = SubjectSessionGoalHistoryRead(
                    subject_code=subject.code,
                    subject_name=_subject_display_name(
                        subject, language=response_language
                    ),
                    session_goals=[],
                )
            grouped[key].session_goals.append(session_goal)

        ordered_subjects = sorted(
            grouped.values(),
            key=lambda item: (item.subject_name.lower(), item.subject_code.lower()),
        )
        return SessionGoalHistoryResponse(subjects=ordered_subjects)

    def cancel_goal(
        self,
        session: Session,
        *,
        user: UserAccount,
        learning_goal_id: UUID,
    ) -> CancelGoalResponse | None:
        goal = session.scalar(
            select(LearningGoal).where(
                LearningGoal.id == learning_goal_id, LearningGoal.user_id == user.id
            )
        )
        if goal is None:
            return None
        abandoned: list[UUID] = []
        for assessment in session.scalars(
            select(AssessmentSession).where(
                AssessmentSession.learning_goal_id == goal.id,
                AssessmentSession.session_type == "pretest",
                AssessmentSession.status.in_({"active", "awaiting_answer"}),
            )
        ):
            assessment.status = "cancelled"
            abandoned.append(assessment.id)
        goal.status = "cancelled"
        goal.cancelled_at = datetime.now(UTC)
        session.commit()
        return CancelGoalResponse(
            learning_goal_id=goal.id,
            status=goal.status,
            abandoned_pretest_session_ids=abandoned,
        )

    def archive_goal(
        self,
        session: Session,
        *,
        user: UserAccount,
        learning_goal_id: UUID,
    ) -> ArchiveGoalResponse | None:
        goal = session.scalar(
            select(LearningGoal).where(
                LearningGoal.id == learning_goal_id, LearningGoal.user_id == user.id
            )
        )
        if goal is None:
            return None
        if goal.status not in INACTIVE_GOAL_STATUSES:
            raise ValueError("Only completed or cancelled goals can be archived.")
        goal.status = "archived"
        goal.archived_at = datetime.now(UTC)
        session.commit()
        return ArchiveGoalResponse(learning_goal_id=goal.id, status=goal.status)

    def search_materials(
        self,
        session: Session,
        *,
        query: str,
        subject_code: str | None,
        user: UserAccount,
    ) -> list[ConceptCandidateRead]:
        _ensure_curriculum(session)
        response_language = _preferred_response_language(user=user)
        candidates = self.retriever.search(
            session,
            query=query,
            subject_code=subject_code,
            education_level=(
                user.learner_profile.education_level if user.learner_profile else None
            ),
            grade_level=(
                user.learner_profile.grade_level if user.learner_profile else None
            ),
            limit=20,
        )
        return [
            _concept_to_read(
                candidate,
                confidence=score_to_confidence(candidate.score),
                education_level=(
                    user.learner_profile.education_level
                    if user.learner_profile
                    else None
                ),
                grade_level=(
                    user.learner_profile.grade_level if user.learner_profile else None
                ),
                language=response_language,
            )
            for candidate in candidates
        ]

    async def _resolve_progressively(
        self,
        session: Session,
        *,
        raw_query: str,
        subject_code: str | None,
        education_level: str | None,
        grade_level: str | None,
        language: str,
        allow_cross_subject: bool,
    ) -> ScopeAttempt | None:
        best_attempt: ScopeAttempt | None = None
        for scope in _progressive_scopes(
            subject_code=subject_code,
            language=language,
            allow_cross_subject=allow_cross_subject,
        ):
            candidates = self.retriever.search(
                session,
                query=raw_query,
                subject_code=scope["subject_code"],
                education_level=education_level,
                grade_level=grade_level,
                grade_scope=scope["grade_scope"],
                limit=_candidate_limit_for_scope(str(scope["name"])),
            )
            if not candidates:
                continue
            llm_result = await self._resolve_with_ai(
                raw_query=raw_query,
                candidates=candidates,
                language=language,
                search_scope=str(scope["name"]),
            )
            llm_status = _normalized_llm_status(llm_result)
            selected = _validated_candidate(llm_result, candidates)
            deterministic_selected = _deterministic_exact_candidate(candidates)
            if deterministic_selected is not None and (
                llm_status != "exact_match" or selected is None
            ):
                selected = deterministic_selected
                llm_status = "exact_match"
                llm_result = {
                    **llm_result,
                    "selected_concept_code": selected.concept.code,
                    "confidence": max(
                        score_to_confidence(selected.score),
                        EXACT_MATCH_CONFIDENCE_THRESHOLD,
                    ),
                    "deterministic_override": "strong_query_match",
                }
            fallback_candidate = selected or candidates[0]
            fallback_confidence = score_to_confidence(fallback_candidate.score)
            if llm_status == "exact_match" and selected is not None:
                fallback_confidence = max(
                    fallback_confidence,
                    EXACT_MATCH_CONFIDENCE_THRESHOLD,
                )
            confidence = _coerce_confidence(
                llm_result.get("confidence"),
                fallback=fallback_confidence,
            )
            alternatives = _validated_alternatives(
                llm_result, candidates, selected=selected
            )

            if (
                llm_status == "exact_match"
                and selected is not None
                and confidence >= EXACT_MATCH_CONFIDENCE_THRESHOLD
            ):
                status = "needs_confirmation"
            elif llm_status == "ambiguous":
                status = "needs_clarification"
            elif llm_status == "exact_match":
                status = "no_match"
            else:
                status = "no_match"

            if not alternatives:
                alternatives = [
                    candidate
                    for candidate in candidates
                    if selected is None or candidate.concept.id != selected.concept.id
                ][:4]
            attempt = ScopeAttempt(
                scope=str(scope["name"]),
                scope_reason=str(scope["reason"]),
                candidates=candidates,
                llm_result={
                    **llm_result,
                    "scope": scope["name"],
                    "scope_reason": scope["reason"],
                    "candidate_count": len(candidates),
                    "backend_interpreted_status": status,
                },
                selected=fallback_candidate,
                alternatives=alternatives,
                confidence=confidence,
                status=(
                    "needs_confirmation"
                    if status == "needs_confirmation"
                    else "needs_clarification"
                ),
            )
            if _attempt_rank(attempt) > _attempt_rank(best_attempt):
                best_attempt = attempt
            if status == "needs_confirmation":
                return attempt
        return best_attempt

    async def _resolve_with_ai(
        self,
        *,
        raw_query: str,
        candidates: list[ConceptCandidate],
        language: str,
        search_scope: str,
    ) -> dict[str, Any]:
        settings = get_ai_settings()
        if not settings.openrouter_api_key.strip():
            return {
                "status": "no_match",
                "selected_concept_code": None,
                "confidence": 0.0,
                "alternatives": [
                    candidate.concept.code for candidate in candidates[:4]
                ],
                "reason": "LLM resolution unavailable because OpenRouter is not configured.",
                "should_expand_scope": True,
                "clarification_question": _localized_message(
                    language,
                    id_text="Coba tulis goal belajar yang lebih spesifik.",
                    en_text="Try writing a more specific learning goal.",
                ),
                "provider": "none",
                "model": "none",
            }
        prompt = build_goal_resolution_prompt(
            raw_query=raw_query,
            candidates=candidates,
            language=language,
            search_scope=search_scope,
        )
        try:
            response = await ai_client.generate(
                system_instruction="Return valid JSON only.",
                user_instruction=prompt,
                params={"temperature": 0.0, "response_format": {"type": "json_object"}},
            )
            payload = json.loads(response.text)
            payload["provider"] = response.provider
            payload["model"] = response.model
            return payload
        except Exception as exc:
            return {
                "status": "no_match",
                "selected_concept_code": None,
                "confidence": 0.0,
                "alternatives": [
                    candidate.concept.code for candidate in candidates[:4]
                ],
                "reason": "LLM resolution failed.",
                "should_expand_scope": True,
                "clarification_question": _localized_message(
                    language,
                    id_text="Coba tulis goal belajar yang lebih spesifik.",
                    en_text="Try writing a more specific learning goal.",
                ),
                "provider": "none",
                "model": "none",
                "fallback_reason": str(exc),
            }


def _validated_candidate(
    llm_result: dict[str, Any],
    candidates: list[ConceptCandidate],
) -> ConceptCandidate | None:
    selected_code = str(
        llm_result.get("selected_concept_code") or llm_result.get("concept_code") or ""
    ).strip()
    if not selected_code:
        return None
    return next(
        (
            candidate
            for candidate in candidates
            if candidate.concept.code == selected_code
        ),
        None,
    )


def _deterministic_exact_candidate(
    candidates: list[ConceptCandidate],
) -> ConceptCandidate | None:
    if not candidates:
        return None
    candidate = candidates[0]
    if candidate.score < DETERMINISTIC_EXACT_SCORE_THRESHOLD:
        return None
    return candidate


def _validated_alternatives(
    llm_result: dict[str, Any],
    candidates: list[ConceptCandidate],
    *,
    selected: ConceptCandidate | None,
) -> list[ConceptCandidate]:
    raw_alternatives = llm_result.get("alternatives", [])
    if not isinstance(raw_alternatives, list):
        return []
    by_code = {candidate.concept.code: candidate for candidate in candidates}
    alternatives: list[ConceptCandidate] = []
    for raw_code in raw_alternatives:
        code = str(raw_code or "").strip()
        candidate = by_code.get(code)
        if candidate is None:
            continue
        if selected is not None and candidate.concept.id == selected.concept.id:
            continue
        if any(item.concept.id == candidate.concept.id for item in alternatives):
            continue
        alternatives.append(candidate)
        if len(alternatives) >= 4:
            break
    return alternatives


def _normalized_llm_status(llm_result: dict[str, Any]) -> str:
    status = str(llm_result.get("status") or "").strip().lower()
    if status in {"exact_match", "needs_confirmation", "confirmed", "match"}:
        return "exact_match"
    if status in {"ambiguous", "needs_clarification", "clarification"}:
        return "ambiguous"
    return "no_match"


def _resolution_candidate_snapshots(
    resolution: LearningGoalResolution,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for raw_snapshot in [
        *(resolution.candidate_snapshot_json or []),
        *(resolution.alternatives_json or []),
    ]:
        if not isinstance(raw_snapshot, dict):
            continue
        key = str(
            raw_snapshot.get("concept_id") or raw_snapshot.get("concept_code") or ""
        ).strip()
        if not key:
            continue
        if any(
            str(item.get("concept_id") or item.get("concept_code")) == key
            for item in snapshots
        ):
            continue
        snapshots.append(raw_snapshot)
    return snapshots


def _find_candidate_snapshot(
    snapshots: list[dict[str, Any]],
    *,
    concept_id: UUID | None,
    concept_code: str | None,
) -> dict[str, Any] | None:
    target_id = str(concept_id) if concept_id is not None else ""
    target_code = (concept_code or "").strip()
    for snapshot in snapshots:
        if target_id and str(snapshot.get("concept_id") or "") == target_id:
            return snapshot
        if target_code and str(snapshot.get("concept_code") or "") == target_code:
            return snapshot
    return None


def _concept_from_candidate_snapshot(
    session: Session,
    snapshot: dict[str, Any],
) -> KnowledgeConcept | None:
    raw_id = str(snapshot.get("concept_id") or "").strip()
    if raw_id:
        try:
            concept = session.get(KnowledgeConcept, UUID(raw_id))
            if concept is not None:
                return concept
        except ValueError:
            pass
    code = str(snapshot.get("concept_code") or "").strip()
    if not code:
        return None
    return session.scalar(select(KnowledgeConcept).where(KnowledgeConcept.code == code))


def _concept_from_selection(
    session: Session,
    *,
    concept_id: UUID | None,
    concept_code: str | None,
) -> KnowledgeConcept | None:
    if concept_id is not None:
        concept = session.get(KnowledgeConcept, concept_id)
        if concept is not None:
            return concept
    code = (concept_code or "").strip()
    if not code:
        return None
    return session.scalar(select(KnowledgeConcept).where(KnowledgeConcept.code == code))


def _concept_from_direct_selection(
    session: Session,
    *,
    concept_id: UUID | None,
    concept_code: str | None,
    subject_code: str | None,
) -> KnowledgeConcept | None:
    normalized_subject = (
        canonical_subject_code(subject_code or "") if subject_code else ""
    )
    if concept_id is not None:
        concept = session.get(KnowledgeConcept, concept_id)
        if concept is None or not normalized_subject:
            return concept
        subject = concept.subject
        return (
            concept
            if subject is not None and subject.code == normalized_subject
            else None
        )

    code = (concept_code or "").strip()
    if not code:
        return None
    query = select(KnowledgeConcept).where(KnowledgeConcept.code == code)
    if normalized_subject:
        query = query.join(Subject).where(Subject.code == normalized_subject)
    concepts = list(session.scalars(query.limit(2)))
    if len(concepts) > 1:
        raise ValueError("subject_code is required when concept_code is ambiguous.")
    return concepts[0] if concepts else None


def _concept_allowed_for_resolution(
    concept: KnowledgeConcept,
    *,
    resolution: LearningGoalResolution,
) -> bool:
    locked_subject = (resolution.subject_code or "").strip()
    if not locked_subject:
        return True
    subject = concept.subject
    return subject is not None and subject.code == canonical_subject_code(
        locked_subject
    )


def _snapshot_from_concept(
    concept: KnowledgeConcept, *, confidence: float
) -> dict[str, Any]:
    subject = concept.subject
    return {
        "concept_id": str(concept.id),
        "concept_code": concept.code,
        "title": concept.title,
        "description": concept.id_desc or concept.description,
        "id_desc": concept.id_desc or concept.description,
        "en_desc": concept.en_desc,
        "subject_code": subject.code if subject else "",
        "subject": subject.name if subject else "",
        "grade_band": concept.grade_band,
        "aliases": [],
        "score": 18.0 * confidence,
        "confidence": confidence,
        "matched_signals": ["manual_selection"],
    }


def _candidate_from_snapshot(
    session: Session,
    snapshot: dict[str, Any],
) -> ConceptCandidate | None:
    concept = _concept_from_candidate_snapshot(session, snapshot)
    if concept is None:
        return None
    return _candidate_from_concept_snapshot(concept, snapshot)


def _candidate_from_concept_snapshot(
    concept: KnowledgeConcept,
    snapshot: dict[str, Any],
) -> ConceptCandidate:
    return ConceptCandidate(
        concept=concept,
        score=_snapshot_score(snapshot),
        matched_signals=(
            tuple(
                str(item)
                for item in snapshot.get("matched_signals", [])
                if str(item).strip()
            )
            if isinstance(snapshot.get("matched_signals"), list)
            else ()
        ),
        aliases=(
            tuple(
                str(item) for item in snapshot.get("aliases", []) if str(item).strip()
            )
            if isinstance(snapshot.get("aliases"), list)
            else ()
        ),
    )


def _snapshot_confidence(snapshot: dict[str, Any]) -> float:
    value = snapshot.get("confidence")
    try:
        return max(0.0, min(0.99, float(value)))
    except (TypeError, ValueError):
        return score_to_confidence(_snapshot_score(snapshot))


def _snapshot_score(snapshot: dict[str, Any]) -> float:
    try:
        return float(snapshot.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _coerce_confidence(value: Any, *, fallback: float) -> float:
    fallback_confidence = max(0.0, min(0.99, float(fallback or 0.0)))
    if value is None:
        return fallback_confidence
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return fallback_confidence
    if confidence > 1.0:
        return score_to_confidence(confidence)
    return max(0.0, min(0.99, confidence))


def _localized_message(language: str, *, id_text: str, en_text: str) -> str:
    normalized = (language or "").strip().lower()
    if normalized in {"id", "indonesian", "bahasa indonesia"} or "indo" in normalized:
        return id_text
    return en_text


def _preferred_response_language(
    *,
    user: UserAccount,
    requested_language: str | None = None,
) -> str:
    profile_language = (
        user.learner_profile.preferred_language
        if user.learner_profile and user.learner_profile.preferred_language
        else None
    )
    return _normalize_response_language(requested_language or profile_language)


def _normalize_response_language(language: str | None) -> str:
    return normalize_language_code(language)


def _is_english_language(language: str) -> bool:
    return not _is_indonesian_language(language)


def _metadata_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return str(value).strip() if value is not None else ""


def _concept_display_title(concept: KnowledgeConcept, *, language: str) -> str:
    metadata = concept.metadata_json or {}
    if _is_english_language(language):
        explicit = _metadata_text(metadata, "label_en")
        label_id = _metadata_text(metadata, "label_id") or concept.title
        if explicit and explicit.casefold() != label_id.casefold():
            return explicit
        translated = translate_curriculum_label_to_english(label_id)
        return translated or explicit or label_id or concept.title
    return _metadata_text(metadata, "label_id") or concept.title


def _concept_display_description(
    concept: KnowledgeConcept,
    *,
    language: str,
    title: str,
) -> str | None:
    metadata = concept.metadata_json or {}
    if _is_english_language(language):
        description = first_course_description(
            concept.en_desc,
            _metadata_text(metadata, "description_en"),
            _metadata_text(metadata, "en_desc"),
        )
        if description:
            return description
        return None
    return first_course_description(
        concept.id_desc,
        _metadata_text(metadata, "description_id"),
        concept.description,
    )


def _subject_display_name(subject: Subject, *, language: str) -> str:
    metadata = subject.metadata_json or {}
    if _is_english_language(language):
        return _metadata_text(metadata, "name_en") or subject.name
    return _metadata_text(metadata, "name_id") or subject.name


def _concept_to_read(
    candidate: ConceptCandidate,
    *,
    confidence: float | None = None,
    education_level: str | None = None,
    grade_level: str | None = None,
    language: str = "",
) -> ConceptCandidateRead:
    concept = candidate.concept
    subject = concept.subject
    display_title = _concept_display_title(concept, language=language)
    display_description = _concept_display_description(
        concept,
        language=language,
        title=display_title,
    )
    metadata = concept.metadata_json or {}
    id_description = first_course_description(
        concept.id_desc,
        _metadata_text(metadata, "description_id"),
        concept.description,
    )
    en_description = first_course_description(
        concept.en_desc,
        _metadata_text(metadata, "description_en"),
        _metadata_text(metadata, "en_desc"),
    )
    relation = grade_relation_for_concept(
        concept,
        education_level=education_level,
        grade_level=grade_level,
    )
    return ConceptCandidateRead(
        concept_id=concept.id,
        concept_code=concept.code,
        title=display_title,
        description=display_description,
        id_desc=id_description,
        en_desc=(
            display_description if _is_english_language(language) else en_description
        ),
        subject_code=subject.code if subject else "",
        subject=_subject_display_name(subject, language=language) if subject else "",
        grade_band=concept.grade_band,
        grade_relation=relation,
        level_note=level_note_for_relation(relation, language=language),
        confidence=round(
            float(
                confidence
                if confidence is not None
                else score_to_confidence(candidate.score)
            ),
            3,
        ),
        aliases=list(candidate.aliases),
        matched_signals=list(candidate.matched_signals),
    )


def _is_ambiguous(candidates: list[ConceptCandidate]) -> bool:
    if len(candidates) < 2:
        return False
    top, runner_up = candidates[0], candidates[1]
    if top.score <= 0:
        return False
    margin_ratio = (top.score - runner_up.score) / max(top.score, 1.0)
    return margin_ratio < 0.12


def _clarification_from_candidates(
    *, language: str, candidates: list[ConceptCandidate]
) -> str:
    titles = [
        _concept_display_title(candidate.concept, language=language)
        for candidate in candidates[:3]
    ]
    if not titles:
        return _localized_message(
            language,
            id_text="Maksud kamu materi yang mana? Coba tulis lebih spesifik.",
            en_text="Which material did you mean? Try a more specific query.",
        )
    options = ", ".join(titles)
    return _localized_message(
        language,
        id_text=f"Maksud kamu yang mana: {options}?",
        en_text=f"Which one did you mean: {options}?",
    )


def _candidate_debug(
    candidates: list[ConceptCandidate],
    *,
    scope: str,
    language: str,
) -> list[dict[str, Any]]:
    return [
        {
            "scope": scope,
            "concept_code": candidate.concept.code,
            "title": _concept_display_title(candidate.concept, language=language),
            "candidate_source": "scope_nodes",
            "matched_signals": list(candidate.matched_signals),
            "aliases": list(candidate.aliases[:8]),
        }
        for candidate in candidates[:12]
    ]


def _progressive_scopes(
    *,
    subject_code: str | None,
    language: str,
    allow_cross_subject: bool,
) -> list[dict[str, str | None]]:
    is_id = _is_indonesian_language(language)
    scopes: list[dict[str, str | None]] = [
        {
            "name": "current_grade",
            "subject_code": subject_code,
            "grade_scope": "current_grade",
            "reason": (
                "Mencari node di level kelas saat ini."
                if is_id
                else "Searching nodes at the learner's current grade level."
            ),
        },
        {
            "name": "nearby_grade",
            "subject_code": subject_code,
            "grade_scope": "nearby_grade",
            "reason": (
                "Memperluas ke level sekitar karena kandidat awal belum cukup yakin."
                if is_id
                else "Expanding to nearby grade levels because the first scope was not confident enough."
            ),
        },
    ]
    if subject_code:
        scopes.insert(
            2,
            {
                "name": "same_subject_all_grades",
                "subject_code": subject_code,
                "grade_scope": "all",
                "reason": (
                    "Mencari semua grade di subject yang sama."
                    if is_id
                    else "Searching all grades in the same subject."
                ),
            },
        )
    if allow_cross_subject:
        scopes.append(
            {
                "name": "all_subjects_all_grades",
                "subject_code": None,
                "grade_scope": "all",
                "reason": (
                    "Mencari semua subject dan semua grade karena belum ada subject yang dikunci."
                    if is_id
                    else "Searching all subjects and all grades because no subject was locked."
                ),
            }
        )
    seen: set[tuple[str, str | None, str | None]] = set()
    unique_scopes: list[dict[str, str | None]] = []
    for scope in scopes:
        key = (str(scope["name"]), scope["subject_code"], scope["grade_scope"])
        if key in seen:
            continue
        seen.add(key)
        unique_scopes.append(scope)
    return unique_scopes


def _attempt_rank(attempt: ScopeAttempt | None) -> float:
    if attempt is None:
        return -1.0
    status_bonus = 2.0 if attempt.status == "needs_confirmation" else 0.0
    return status_bonus + attempt.confidence


def _resolution_subject_code(
    *,
    explicit_subject_code: str | None,
    selected: ConceptCandidate,
    status: str,
) -> str:
    if explicit_subject_code:
        return explicit_subject_code
    if status != "needs_confirmation":
        return ""
    subject = selected.concept.subject
    return subject.code if subject else ""


def _candidate_limit_for_scope(scope_name: str) -> int:
    if scope_name == "all_subjects_all_grades":
        return 80
    if scope_name == "same_subject_all_grades":
        return 60
    return 40


def _can_expand_scope(scope_name: str, *, allow_cross_subject: bool) -> bool:
    if scope_name in {"current_grade", "nearby_grade"}:
        return True
    if scope_name == "same_subject_all_grades":
        return allow_cross_subject
    return False


def _graph_focus(
    *, selected: ConceptCandidate, alternatives: list[ConceptCandidate]
) -> dict[str, Any]:
    subject = selected.concept.subject
    codes = [selected.concept.code]
    codes.extend(candidate.concept.code for candidate in alternatives)
    unique_codes = list(dict.fromkeys(code for code in codes if code))
    return {
        "subject_code": subject.code if subject else "",
        "highlight_concept_codes": unique_codes[:8],
        "selected_concept_code": selected.concept.code,
    }


def _is_indonesian_language(language: str) -> bool:
    return is_indonesian_language(language)


def _active_goal_to_read(
    session: Session,
    goal: LearningGoal,
    *,
    language: str,
) -> ActiveGoalRead:
    concept = (
        session.get(KnowledgeConcept, goal.target_concept_id)
        if goal.target_concept_id
        else None
    )
    pretest = next(
        (
            assessment
            for assessment in goal.assessment_sessions
            if assessment.session_type == "pretest"
            and assessment.status in {"active", "awaiting_answer"}
        ),
        None,
    )
    next_action = {
        "confirmed": "start_pretest",
        "pretest_in_progress": "continue_pretest",
        "diagnosed": "enter_workspace",
        "in_progress": "continue_learning",
    }.get(goal.status, "view_progress")
    target_concept = (
        _concept_to_read(
            ConceptCandidate(concept=concept, score=0.0),
            confidence=None,
            language=language,
        )
        if concept
        else None
    )
    return ActiveGoalRead(
        id=goal.id,
        status=goal.status,
        raw_topic=goal.raw_topic,
        target_concept=target_concept,
        pretest_session_id=pretest.id if pretest else None,
        track_id=goal.track.id if goal.track else None,
        next_action=next_action,
    )


def _ensure_curriculum(session: Session) -> None:
    if session.scalar(select(Subject.id).limit(1)) is None:
        seed_curriculum(session)
