from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.language import normalize_language_code, preferred_language_code
from app.modules.accounts.models import UserAccount
from app.modules.assessments.metrics import attempt_answer_score, attempt_evidence_score
from app.modules.curriculum.kurikulum_merdeka import (
    canonical_subject_code,
    translate_curriculum_label_to_english,
)
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.curriculum.seed import seed_curriculum
from app.modules.learning.job_queue import build_media_job_queue_adapter
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentOption,
    AssessmentQuestion,
    AssessmentSession,
    LearnerConceptState,
    LearningGoal,
    LearningTrack,
    MediaArtifact,
    MediaJob,
    TrackModule,
    WeeklyReportSnapshot,
)
from app.modules.learning.schemas import (
    AssessmentDashboardActionRead,
    AssessmentDashboardComparisonRead,
    AssessmentDashboardPosttestRead,
    AssessmentDashboardPretestRead,
    AssessmentDashboardResponse,
    AnimationJobStatusResponse,
    AnimationQueueResponse,
    ActionRead,
    AssessmentOptionRead,
    AssessmentQuestionRead,
    DailyEvaluationAnswerResponse,
    DailyEvaluationNextReviewRead,
    DailyEvaluationResponse,
    DailyEvaluationResultResponse,
    DailySummaryRead,
    ReportDataQualityRead,
    ReportEffortImpactRead,
    ReportConceptMoverRead,
    GapMetricRead,
    HomeSummaryResponse,
    KnowledgeStateResponse,
    LearningQueueResponse,
    LearningGoalCreateResponse,
    LearningGoalRead,
    MediaArtifactListResponse,
    MediaArtifactRead,
    MediaArtifactStatusResponse,
    PretestReadResponse,
    QueueItemRead,
    RecommendationCalloutRead,
    RecommendedNextActionRead,
    ReportTrendRead,
    ReportPerformanceGroupRead,
    RetentionForecastPointRead,
    RetentionForecastRead,
    ReviewDueRead,
    ReviewedConceptRead,
    SpacedRepetitionImpactRead,
    SubmitAnswerResponse,
    TrackListResponse,
    TrackModuleRead,
    TrackModuleStateUpdateResponse,
    TrackRead,
    UnlockedConceptSummaryRead,
    UpcomingRecommendationRead,
    ConsistencySummaryRead,
    WeeklyTimelinePointRead,
    WeeklyNarrativeRead,
    WeeklyReportResponse,
    ProgressRead,
)
from app.modules.posttests.schemas import PosttestNodeResultRead
from app.modules.learning.render_engine import (
    RenderEngineError,
    RenderOutput,
    render_template_scene,
)
from app.modules.learning.media_postprocess import (
    MediaPostprocessError,
    MediaPostprocessOutput,
    postprocess_render_output,
)
from app.modules.learning.storage import (
    MediaStorageError,
    MediaStorageUploadOutput,
    upload_media_artifact_files,
)
from app.modules.learning.template_validation import (
    TemplateValidationError,
    validate_template_spec,
)
from app.modules.workspaces.models import WorkspaceSession

logger = logging.getLogger(__name__)
from app.modules.question_bank.service import (
    DAILY_SELECTOR_VERSION,
    LearnerStep,
    SelectedQuestion,
    ensure_question_bank_seeded,
    import_seed_directory,
    resolve_learner_step,
    select_daily_questions,
)


@dataclass(frozen=True)
class _DailyQuestionResult:
    question: AssessmentQuestion
    attempt: AssessmentAttempt
    is_correct: bool


@dataclass(frozen=True)
class _DailyConceptSummary:
    concept_id: UUID | None
    title: str
    status_key: str
    status_label: str
    mastery_score: float
    attempted_count: int
    correct_count: int
    score_percent: int


@dataclass(frozen=True)
class _DailySessionResultSummary:
    question_results: list[_DailyQuestionResult]
    reviewed_count: int
    correct_count: int
    review_again_count: int
    score_percent: int
    reviewed_concepts: list[_DailyConceptSummary]


PRETEST_TEMPLATES: list[dict[str, Any]] = [
    {
        "topic": "Prerequisite probe",
        "prompt": (
            "A student wants to learn derivatives, but their graph approaches y = 3 "
            "as x gets closer to 2 from both sides. What should WICARA check first?"
        ),
        "helper": "Choose the prerequisite signal that best guides the first learning path.",
        "concept_hints": ["intuitive_limits", "limit", "bilangan_rasional"],
        "correct": "B",
        "options": [
            ("A", "Start derivative rules immediately"),
            ("B", "Check whether the learner understands limits from graphs"),
            ("C", "Skip prerequisite diagnosis and generate a video"),
            ("D", "Mark calculus as mastered from one topic request"),
        ],
    },
    {
        "topic": "Learning path diagnosis",
        "prompt": (
            "A learner can apply a formula after seeing it, but cannot explain why "
            "the formula works. What should the adaptive track do next?"
        ),
        "helper": "Pick the action that keeps the path prerequisite-first.",
        "concept_hints": ["functions", "bilangan_bulat"],
        "correct": "C",
        "options": [
            ("A", "Increase difficulty because the formula was copied correctly"),
            ("B", "Skip explanation and only give more multiple-choice questions"),
            ("C", "Add a short concept-building module before harder practice"),
            ("D", "Remove the concept from the learning map"),
        ],
    },
]


DAILY_REVIEW_TEMPLATES: list[dict[str, Any]] = [
    {
        "topic": "Spaced review",
        "prompt": (
            "You answered a concept correctly today after struggling yesterday. "
            "What is the best next step?"
        ),
        "helper": "Use memory strength to decide.",
        "concept_hints": ["bilangan_rasional", "intuitive_limits"],
        "correct": "A",
        "options": [
            ("A", "Review it again after a short delay"),
            ("B", "Mark it mastered forever immediately"),
            ("C", "Remove it from all future practice"),
            ("D", "Only study brand new concepts now"),
        ],
    },
    {
        "topic": "Application",
        "prompt": (
            "A student can solve derivative rules but misses word problems. "
            "What should they review next?"
        ),
        "helper": "Pick the next learning action.",
        "concept_hints": ["applications_derivatives", "functions", "bilangan_bulat"],
        "correct": "B",
        "options": [
            ("A", "Repeat only memorized derivative formulas"),
            ("B", "Practice translating situations into equations"),
            ("C", "Skip application questions until later"),
            ("D", "Review unrelated facts without checking the gap"),
        ],
    },
    {
        "topic": "Concept decay",
        "prompt": (
            "A learner mastered a prerequisite last week, but confidence is dropping. "
            "How should WICARA schedule it?"
        ),
        "helper": "Choose the retention-oriented action.",
        "concept_hints": ["functions", "bilangan_rasional"],
        "correct": "D",
        "options": [
            ("A", "Ignore it because it was once mastered"),
            ("B", "Reset the full track to the beginning"),
            ("C", "Only show new concepts today"),
            ("D", "Add a short review question before the next module"),
        ],
    },
]

MEDIA_JOB_PROGRESS_STAGES: list[tuple[int, str]] = [
    (20, "Validating render payload."),
    (45, "Preparing template rendering inputs."),
]


def create_learning_goal(
    session: Session,
    *,
    user: UserAccount,
    raw_topic: str,
    subject_code: str | None,
) -> LearningGoalCreateResponse:
    ensure_curriculum_seeded(session)
    subject = _resolve_subject(session, subject_code=subject_code, user=user)
    concept = _pick_concept(session, subject=subject, raw_topic=raw_topic)

    normalized_topic = _normalize_topic(raw_topic)
    goal = LearningGoal(
        user_id=user.id,
        subject_id=subject.id,
        target_concept_id=concept.id if concept else None,
        raw_topic=raw_topic.strip(),
        normalized_topic=normalized_topic,
        status="pretest_ready",
        metadata_json={"source": "learning_goal_api", "generation": "deterministic_seed"},
    )
    session.add(goal)
    session.flush()

    track = _create_track(session, user=user, goal=goal, subject=subject, concept=concept)
    pretest = _create_assessment_session(
        session,
        user=user,
        learning_goal=goal,
        track=track,
        session_type="pretest",
        title=f"Pretest for {normalized_topic}",
        templates=PRETEST_TEMPLATES,
    )
    session.commit()

    return LearningGoalCreateResponse(
        learning_goal_id=goal.id,
        status=goal.status,
        subject=subject.name,
        subject_code=subject.code,
        pretest_session_id=pretest.id,
        track_id=track.id,
    )


def read_learning_goal(
    session: Session,
    *,
    user: UserAccount,
    learning_goal_id: UUID,
) -> LearningGoalRead | None:
    goal = session.scalar(
        select(LearningGoal)
        .where(LearningGoal.id == learning_goal_id, LearningGoal.user_id == user.id)
        .options(selectinload(LearningGoal.track), selectinload(LearningGoal.assessment_sessions))
    )
    if goal is None:
        return None
    subject = session.get(Subject, goal.subject_id)
    pretest = next(
        (item for item in goal.assessment_sessions if item.session_type == "pretest"),
        None,
    )
    return LearningGoalRead(
        id=goal.id,
        raw_topic=goal.raw_topic,
        normalized_topic=goal.normalized_topic,
        status=goal.status,
        subject_code=subject.code if subject else "",
        pretest_session_id=pretest.id if pretest else None,
        track_id=goal.track.id if goal.track else None,
    )


def get_assessment_dashboard(
    session: Session,
    *,
    user: UserAccount,
    learning_goal_id: UUID,
) -> AssessmentDashboardResponse | None:
    goal = session.scalar(
        select(LearningGoal)
        .where(LearningGoal.id == learning_goal_id, LearningGoal.user_id == user.id)
        .options(selectinload(LearningGoal.track), selectinload(LearningGoal.assessment_sessions))
    )
    if goal is None:
        return None

    target = session.get(KnowledgeConcept, goal.target_concept_id) if goal.target_concept_id else None
    pretest_session = _latest_assessment_session(goal.assessment_sessions, session_type="pretest")
    posttest_session = _latest_assessment_session(goal.assessment_sessions, session_type="posttest")
    pretest = _assessment_dashboard_pretest(goal=goal, assessment=pretest_session)
    posttest = _assessment_dashboard_posttest(posttest_session)
    paired_scores = _paired_pre_post_scores(session, user=user, learning_goal_id=goal.id)
    comparison = AssessmentDashboardComparisonRead(
        available=bool(paired_scores["paired_concept_count"]),
        pretest_score_percent=paired_scores["pretest_score_percent"],
        posttest_score_percent=paired_scores["posttest_score_percent"],
        learning_gain_percent=paired_scores["learning_gain_percent"],
        paired_concept_count=int(paired_scores["paired_concept_count"] or 0),
    )
    state = _assessment_dashboard_state(
        goal=goal,
        pretest=pretest,
        posttest=posttest,
        posttest_session=posttest_session,
    )
    recommendations = _assessment_dashboard_recommendations(
        state=state,
        pretest=pretest,
        posttest=posttest,
    )
    return AssessmentDashboardResponse(
        learning_goal_id=goal.id,
        target_title=target.title if target else goal.normalized_topic,
        state=state,
        pretest=pretest,
        posttest=posttest,
        comparison=comparison,
        primary_action=_assessment_dashboard_action(
            state=state,
            goal=goal,
            pretest_session=pretest_session,
            posttest_session=posttest_session,
        ),
        recommendations=recommendations,
    )


def get_pretest_for_goal(
    session: Session,
    *,
    user: UserAccount,
    learning_goal_id: UUID,
) -> PretestReadResponse | None:
    assessment = session.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.learning_goal_id == learning_goal_id,
            AssessmentSession.user_id == user.id,
            AssessmentSession.session_type == "pretest",
        )
        .options(
            selectinload(AssessmentSession.questions).selectinload(AssessmentQuestion.options)
        )
    )
    if assessment is None:
        return None
    return PretestReadResponse(
        session_id=assessment.id,
        learning_goal_id=learning_goal_id,
        title=assessment.title,
        status=assessment.status,
        questions=[question_to_schema(question) for question in assessment.questions],
    )


def _latest_assessment_session(
    assessments: list[AssessmentSession],
    *,
    session_type: str,
) -> AssessmentSession | None:
    candidates = [item for item in assessments if item.session_type == session_type]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: _as_utc(item.completed_at or item.created_at)
        if (item.completed_at or item.created_at)
        else datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )[0]


def _assessment_dashboard_pretest(
    *,
    goal: LearningGoal,
    assessment: AssessmentSession | None,
) -> AssessmentDashboardPretestRead | None:
    diagnosis = None
    if assessment is not None:
        metadata_diagnosis = (assessment.metadata_json or {}).get("diagnosis")
        if isinstance(metadata_diagnosis, dict):
            diagnosis = metadata_diagnosis
    if diagnosis is None:
        goal_diagnosis = (goal.metadata_json or {}).get("diagnosis")
        if isinstance(goal_diagnosis, dict):
            diagnosis = goal_diagnosis
    if diagnosis is None:
        return None

    analysis = diagnosis.get("analysis") if isinstance(diagnosis.get("analysis"), dict) else {}
    nodes = diagnosis.get("nodes") if isinstance(diagnosis.get("nodes"), list) else []
    return AssessmentDashboardPretestRead(
        session_id=assessment.id if assessment is not None else None,
        status=assessment.status if assessment is not None else "completed",
        score_percent=_float_value(diagnosis.get("score_percent"), fallback=0.0),
        overall_mastery_percent=_float_value(
            diagnosis.get("overall_mastery_percent"),
            fallback=_float_value(analysis.get("overall_mastery_percent"), fallback=0.0),
        ),
        confidence_percent=_float_value(diagnosis.get("confidence_percent"), fallback=0.0),
        recommended_path=str(diagnosis.get("recommended_path") or ""),
        summary=str(diagnosis.get("summary") or ""),
        strengths=_string_list(analysis.get("strengths")),
        gaps=_string_list(analysis.get("gaps")),
        evidence_notes=_string_list(analysis.get("evidence_notes")),
        nodes=[dict(node) for node in nodes if isinstance(node, dict)],
    )


def _assessment_dashboard_posttest(
    assessment: AssessmentSession | None,
) -> AssessmentDashboardPosttestRead | None:
    if assessment is None:
        return None
    node_results = (assessment.metadata_json or {}).get("node_results")
    if not isinstance(node_results, dict):
        state = assessment.decision_state_json or {}
        node_results = state.get("node_results") if isinstance(state.get("node_results"), dict) else {}
    nodes = [
        _assessment_dashboard_posttest_node(concept_code, payload)
        for concept_code, payload in node_results.items()
        if isinstance(payload, dict)
    ]
    passed_count = len([node for node in nodes if node.passed])
    total_count = len(nodes)
    passed = total_count > 0 and passed_count == total_count
    return AssessmentDashboardPosttestRead(
        session_id=assessment.id,
        status=assessment.status,
        answer_percent=_average([node.answer_percent for node in nodes]),
        evidence_percent=_average([node.evidence_percent for node in nodes]),
        score_percent=_average([node.score_percent for node in nodes]),
        confidence_percent=_average([node.confidence_percent for node in nodes]),
        passed_node_count=passed_count,
        total_node_count=total_count,
        retake_required_concepts=[node.concept_code for node in nodes if node.retake_required],
        passed=passed,
        nodes=nodes,
    )


def _assessment_dashboard_posttest_node(
    concept_code: str,
    payload: dict[str, Any],
) -> PosttestNodeResultRead:
    concept_id = payload.get("concept_id")
    return PosttestNodeResultRead(
        concept_id=UUID(str(concept_id)) if concept_id else None,
        concept_code=concept_code,
        concept_title=str(payload.get("concept_title") or concept_code),
        total_questions=int(payload.get("total_questions") or 3),
        answered_count=int(payload.get("answered_count") or 0),
        correct_count=int(payload.get("correct_count") or 0),
        answer_percent=_float_value(payload.get("answer_percent"), fallback=0.0),
        evidence_percent=_float_value(payload.get("evidence_percent"), fallback=0.0),
        score_percent=_float_value(payload.get("score_percent"), fallback=0.0),
        confidence_percent=_float_value(payload.get("confidence_percent"), fallback=0.0),
        scaled_score=_float_value(payload.get("scaled_score"), fallback=0.0),
        passed=bool(payload.get("passed")),
        retake_required=bool(payload.get("retake_required", not bool(payload.get("passed")))),
        metric_source=str(payload.get("metric_source") or "adaptive_posttest_evidence"),
    )


def _assessment_dashboard_state(
    *,
    goal: LearningGoal,
    pretest: AssessmentDashboardPretestRead | None,
    posttest: AssessmentDashboardPosttestRead | None,
    posttest_session: AssessmentSession | None,
) -> str:
    if pretest is None:
        return "needs_pretest"
    if posttest_session is not None and posttest_session.status in {"active", "awaiting_answer"}:
        return "posttest_in_progress"
    if posttest is not None and posttest.total_node_count > 0:
        return "mastered" if posttest.passed else "needs_retake"
    if goal.status == "in_progress" and goal.track is not None and goal.track.progress_percent > 0:
        return "learning_in_progress"
    return "diagnosed"


def _assessment_dashboard_action(
    *,
    state: str,
    goal: LearningGoal,
    pretest_session: AssessmentSession | None,
    posttest_session: AssessmentSession | None,
) -> AssessmentDashboardActionRead:
    if state == "needs_pretest":
        return AssessmentDashboardActionRead(
            label="Start pretest",
            action_type="start_pretest",
            target_id=str(pretest_session.id if pretest_session else goal.id),
        )
    if state == "posttest_in_progress":
        return AssessmentDashboardActionRead(
            label="Continue posttest",
            action_type="continue_posttest",
            target_id=str(posttest_session.id) if posttest_session else None,
        )
    if state == "needs_retake":
        return AssessmentDashboardActionRead(
            label="Review weak nodes",
            action_type="review",
            target_id=str(goal.track.id if goal.track else goal.id),
        )
    return AssessmentDashboardActionRead(
        label="Continue learning",
        action_type="continue_learning",
        target_id=str(goal.track.id if goal.track else goal.id),
    )


def _assessment_dashboard_recommendations(
    *,
    state: str,
    pretest: AssessmentDashboardPretestRead | None,
    posttest: AssessmentDashboardPosttestRead | None,
) -> list[str]:
    if state == "needs_retake" and posttest is not None:
        return [f"Review: {concept_code}" for concept_code in posttest.retake_required_concepts]
    if pretest is not None:
        return pretest.gaps[:3] or pretest.strengths[:3]
    return []


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _float_value(value: object, *, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def submit_answer(
    session: Session,
    *,
    user: UserAccount,
    assessment_session_id: UUID,
    question_id: str,
    option_id: str,
    confidence: int,
    explanation: str = "",
    used_canvas: bool = False,
) -> tuple[AssessmentAttempt, bool]:
    assessment, question, option = _resolve_submission_targets(
        session,
        user=user,
        assessment_session_id=assessment_session_id,
        question_id=question_id,
        option_id=option_id,
    )
    answer_score = 1.0 if option.is_correct else 0.0
    attempt = AssessmentAttempt(
        session_id=assessment.id,
        question_id=question.id,
        selected_option_id=option.id,
        confidence=confidence,
        explanation_text=explanation.strip(),
        used_canvas=used_canvas,
        score=answer_score,
        is_correct=bool(option.is_correct),
        answer_score=answer_score,
        evidence_score=answer_score,
        diagnostic_signal="correct_mcq_only" if option.is_correct else "concept_gap_likely",
        evaluated_result={
            "verdict": "CORRECT" if option.is_correct else "INCORRECT",
            "grading": "deterministic_seed",
        },
        evaluation_metadata_json={"source": "legacy_objective", "confidence": confidence},
    )
    session.add(attempt)
    _update_mastery(
        session,
        user=user,
        question=question,
        is_correct=option.is_correct,
        session_type=assessment.session_type,
    )
    session.commit()
    session.refresh(attempt)
    return attempt, option.is_correct


def submit_answer_response(
    session: Session,
    *,
    user: UserAccount,
    assessment_session_id: UUID,
    question_id: str,
    option_id: str,
    confidence: int,
) -> SubmitAnswerResponse:
    attempt, is_correct = submit_answer(
        session,
        user=user,
        assessment_session_id=assessment_session_id,
        question_id=question_id,
        option_id=option_id,
        confidence=confidence,
    )
    return SubmitAnswerResponse(attempt_id=attempt.id, is_correct=is_correct)


def submit_reasoning_response(
    session: Session,
    *,
    user: UserAccount,
    assessment_session_id: UUID,
    question_id: str,
    option_id: str,
    confidence: int,
    explanation: str,
    used_canvas: bool,
) -> KnowledgeStateResponse:
    _attempt, is_correct = submit_answer(
        session,
        user=user,
        assessment_session_id=assessment_session_id,
        question_id=question_id,
        option_id=option_id,
        confidence=confidence,
        explanation=explanation,
        used_canvas=used_canvas,
    )
    assessment = session.get(AssessmentSession, assessment_session_id)
    if assessment:
        assessment.status = "completed"
        assessment.completed_at = datetime.now(UTC)
        if assessment.learning_goal_id:
            goal = session.get(LearningGoal, assessment.learning_goal_id)
            if goal:
                goal.status = "track_ready"
        if assessment.track_id:
            track = session.get(LearningTrack, assessment.track_id)
            if track:
                track.status = "active"
                track.progress_percent = 8
        session.commit()

    if is_correct:
        return KnowledgeStateResponse(
            skill="Ready concept: prerequisite reading",
            gap_label="READY",
            message=(
                "Your pretest evidence is enough to start the generated path. "
                "WICARA will still keep early prerequisites in review."
            ),
            path_title="Personalized path generated",
            path_meta="20-28 min | 3 modules",
            path_description="Start with the detected prerequisite, then move into the requested topic.",
        )
    return KnowledgeStateResponse(
        skill="Missing prerequisite: concept diagnosis",
        gap_label="GAP",
        message=(
            "The gap looks like jumping to the target topic before confirming the "
            "prerequisite signal. The path will repair that first."
        ),
        path_title="Prerequisite-first path generated",
        path_meta="18-24 min | 3 modules",
        path_description="Review the missing foundation, then return to your original learning goal.",
    )


def list_tracks(session: Session, *, user: UserAccount) -> TrackListResponse:
    tracks = list(
        session.scalars(
            select(LearningTrack)
            .where(LearningTrack.user_id == user.id)
            .options(*_track_schema_load_options())
            .order_by(LearningTrack.created_at.desc())
        )
    )
    return TrackListResponse(items=[track_to_schema(track) for track in tracks])


def get_home_summary(session: Session, *, user: UserAccount) -> HomeSummaryResponse:
    tracks = _user_tracks(session, user=user)
    queue = _build_queue_items(tracks)
    daily_session = _today_daily_session(session, user=user)
    completed_today = 0
    if daily_session is not None:
        completed_today = session.scalar(
            select(func.count(AssessmentAttempt.id)).where(
                AssessmentAttempt.session_id == daily_session.id
            )
        ) or 0
    display_name = _display_name_for_user(user)
    return HomeSummaryResponse(
        display_name=display_name,
        first_name=_first_name(display_name),
        onboarding_completed=bool(user.learner_profile and user.learner_profile.onboarding_completed),
        streak_days=_streak_days(session, user=user),
        active_tracks_count=len([track for track in tracks if track.status != "completed"]),
        next_queue_item=queue[0] if queue else None,
        daily_evaluation=DailySummaryRead(
            status="completed" if completed_today else "ready",
            title="Daily Evaluation",
            due_count=max(0, 3 - completed_today),
            completed_count=completed_today,
        ),
        active_tracks=[track_to_schema(track) for track in tracks[:3]],
    )


def get_learning_queue(session: Session, *, user: UserAccount) -> LearningQueueResponse:
    tracks = _user_tracks(session, user=user)
    return LearningQueueResponse(
        recommended=_build_queue_items(tracks),
        tracks=[track_to_schema(track) for track in tracks],
    )


def get_track_modules(
    session: Session,
    *,
    user: UserAccount,
    track_id: UUID,
) -> TrackRead | None:
    track = session.scalar(
        select(LearningTrack)
        .where(LearningTrack.id == track_id, LearningTrack.user_id == user.id)
        .options(*_track_schema_load_options())
    )
    return track_to_schema(track) if track is not None else None


def update_track_module_state(
    session: Session,
    *,
    user: UserAccount,
    track_id: UUID,
    module_id: UUID,
    status: str,
) -> TrackModuleStateUpdateResponse | None:
    normalized_status = status.strip().lower()
    if normalized_status != "active":
        raise ValueError(
            "Learners may only activate an eligible module; completion is assessment-owned."
        )
    track = session.scalar(
        select(LearningTrack)
        .where(LearningTrack.id == track_id, LearningTrack.user_id == user.id)
        .options(*_track_schema_load_options())
    )
    if track is None:
        return None
    module = next((item for item in track.modules if item.id == module_id), None)
    if module is None:
        return None
    if module.status == "locked":
        raise ValueError("Locked modules cannot be activated before prerequisites pass.")
    if module.status == "completed":
        raise ValueError("Completed modules cannot be mutated through learner state API.")
    module.status = "active"
    track.status = "active"
    session.commit()
    session.refresh(track)
    return TrackModuleStateUpdateResponse(track=track_to_schema(track))


def queue_animation_job(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID | None,
    concept_id: UUID | None,
    template_id: str,
    spec_json: dict[str, Any],
    language: str,
    quality_profile: str,
) -> AnimationQueueResponse:
    normalized_template_id = template_id.strip().lower()
    if not normalized_template_id:
        raise ValueError("template_id must not be empty.")

    workspace = _resolve_owned_workspace(session, user=user, workspace_id=workspace_id)
    resolved_concept_id = _resolve_concept_id(
        session,
        concept_id=concept_id,
        workspace=workspace,
    )
    normalized_language = normalize_language_code(language)[:16]
    normalized_quality = _normalize_short_label(
        quality_profile, fallback="standard", max_length=32
    )
    validation_result = None
    validation_error: TemplateValidationError | None = None
    try:
        validation_result = validate_template_spec(
            template_id=normalized_template_id,
            spec_json=spec_json,
        )
    except TemplateValidationError as exc:
        validation_error = exc

    canonical_template_id = (
        validation_result.template_id
        if validation_result is not None
        else (validation_error.template_id or normalized_template_id)
    )
    normalized_spec = (
        validation_result.normalized_spec
        if validation_result is not None
        else dict(spec_json)
    )
    job_status = "queued" if validation_error is None else "failed"
    job_message = (
        "Job is queued for rendering."
        if validation_error is None
        else "Template validation failed."
    )
    error_details = validation_error.to_dict() if validation_error is not None else None
    artifact = MediaArtifact(
        user_id=user.id,
        track_id=workspace.track_id if workspace else None,
        module_id=workspace.module_id if workspace else None,
        workspace_id=workspace.id if workspace else None,
        concept_id=resolved_concept_id,
        template_id=canonical_template_id,
        spec_json=normalized_spec,
        language=normalized_language,
        quality_profile=normalized_quality,
        artifact_type="video",
        title=_queued_artifact_title(normalized_spec, canonical_template_id),
        subtitle=_queued_artifact_subtitle(normalized_spec),
        status=job_status,
        duration_seconds=0,
        thumbnail_url="",
        playback_url="",
        video_url="",
        transcript="",
        notes_json=[],
        metadata_json={
            "source": "animation_queue_api",
            "progress": 0,
            "job_state": job_status,
        },
        render_meta_json=_initial_render_meta(
            validation_result=validation_result,
            validation_error=error_details,
        ),
    )
    session.add(artifact)
    session.flush()

    job = MediaJob(
        artifact_id=artifact.id,
        status=job_status,
        progress=0,
        message=job_message,
        attempt=0,
        error=validation_error.message if validation_error is not None else None,
    )
    session.add(job)
    _sync_artifact_job_state(
        artifact=artifact,
        job=job,
        error_message=validation_error.message if validation_error is not None else None,
        error_details=error_details,
        error_code=validation_error.code if validation_error is not None else None,
    )
    session.commit()
    session.refresh(job)
    if validation_error is None:
        published = _publish_media_job_to_queue(job_id=job.id)
        if not published:
            job.message = "Job queued in database. Waiting for worker polling fallback."
            session.commit()
    return AnimationQueueResponse(
        job_id=job.id,
        artifact_id=artifact.id,
        status=job.status,
        error_details=error_details,
    )


def get_animation_job_status(
    session: Session,
    *,
    user: UserAccount,
    job_id: UUID,
) -> AnimationJobStatusResponse | None:
    row = session.execute(
        select(MediaJob, MediaArtifact)
        .join(MediaArtifact, MediaArtifact.id == MediaJob.artifact_id)
        .where(
            MediaJob.id == job_id,
            MediaArtifact.user_id == user.id,
        )
    ).first()
    if row is None:
        return None

    job, artifact = row
    video_url = _coalesce_video_url(artifact)
    thumbnail_url = _optional_text(artifact.thumbnail_url)
    render_meta = artifact.render_meta_json if isinstance(artifact.render_meta_json, dict) else {}
    error_details = render_meta.get("error_details")
    return AnimationJobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress=max(0, min(100, int(job.progress or 0))),
        message=job.message or "",
        artifact_id=artifact.id,
        video_url=video_url,
        thumbnail_url=thumbnail_url,
        error=job.error,
        error_details=error_details if isinstance(error_details, dict) else None,
    )


def pick_next_queued_animation_job_id(session: Session) -> UUID | None:
    return session.scalar(
        select(MediaJob.id).where(MediaJob.status == "queued").order_by(MediaJob.created_at).limit(1)
    )


def process_animation_job_for_worker(
    session: Session,
    *,
    job_id: UUID,
) -> bool:
    row = _load_job_with_artifact(session, job_id=job_id)
    if row is None:
        logger.warning("Media worker received unknown job id %s.", job_id)
        return False
    job, artifact = row
    if job.status in {"ready", "failed"}:
        return True

    worker_started = time.perf_counter()
    timing_meta: dict[str, float] = {}
    context = {
        "job_id": str(job.id),
        "artifact_id": str(artifact.id),
        "template_id": artifact.template_id,
    }

    try:
        settings = get_settings()
        _log_job_event(
            level="info",
            stage="worker_start",
            message="Worker started processing media job.",
            context=context,
        )
        _mark_job_processing(session, job=job, artifact=artifact)
        _validate_job_payload(session=session, job=job, artifact=artifact)
        _log_job_event(
            level="info",
            stage="validation_ok",
            message="Template payload validation succeeded.",
            context=context,
        )
        for progress, message in MEDIA_JOB_PROGRESS_STAGES:
            _update_job_progress(
                session,
                job=job,
                artifact=artifact,
                progress=progress,
                message=message,
            )

        render_started = time.perf_counter()
        render_output, attempts_used = _render_artifact_with_retry(
            session,
            job=job,
            artifact=artifact,
            max_attempts=settings.media_render_max_attempts,
            timeout_seconds=settings.media_render_timeout_seconds,
            settings=settings,
        )
        timing_meta["render_seconds"] = round(time.perf_counter() - render_started, 3)
        _attach_render_output_to_artifact(
            session,
            artifact=artifact,
            render_output=render_output,
            attempts_used=attempts_used,
        )
        _update_job_progress(
            session,
            job=job,
            artifact=artifact,
            progress=88,
            message="Render output stored in local artifact path.",
        )
        _log_job_event(
            level="info",
            stage="render_ok",
            message="Template rendering completed.",
            context={
                **context,
                "attempts_used": attempts_used,
                "render_engine": (
                    str((artifact.render_meta_json or {}).get("render_engine", "")).strip().lower()
                    or ("remotion" if artifact.template_id.startswith("remotion.") else "manim")
                ),
                **timing_meta,
            },
        )
        _update_job_progress(
            session,
            job=job,
            artifact=artifact,
            progress=92,
            message=(
                "Finalizing rendered video, probing audio stream, extracting thumbnail, "
                "and evaluating duration gate."
            ),
        )
        postprocess_started = time.perf_counter()
        postprocess_output, postprocess_attempts = _postprocess_output_with_retry(
            session=session,
            job=job,
            artifact=artifact,
            render_output=render_output,
            settings=settings,
        )
        timing_meta["postprocess_seconds"] = round(time.perf_counter() - postprocess_started, 3)
        _attach_postprocess_output_to_artifact(
            session,
            artifact=artifact,
            output=postprocess_output,
        )
        _update_job_progress(
            session,
            job=job,
            artifact=artifact,
            progress=97,
            message="Media post-process finished.",
        )
        _log_job_event(
            level="info",
            stage="postprocess_ok",
            message="Media post-process completed.",
            context={**context, "attempts_used": postprocess_attempts, **timing_meta},
        )
        _update_job_progress(
            session,
            job=job,
            artifact=artifact,
            progress=99,
            message="Uploading final media artifact to storage.",
        )
        upload_started = time.perf_counter()
        storage_output, upload_attempts = _upload_media_files_with_retry(
            session=session,
            job=job,
            artifact=artifact,
            postprocess_output=postprocess_output,
            settings=settings,
        )
        timing_meta["upload_seconds"] = round(time.perf_counter() - upload_started, 3)
        _attach_storage_output_to_artifact(
            session=session,
            artifact=artifact,
            storage_output=storage_output,
        )
        _log_job_event(
            level="info",
            stage="upload_ok",
            message="Media artifact uploaded to storage backend.",
            context={
                **context,
                "attempts_used": upload_attempts,
                "storage_backend": storage_output.storage_backend,
                **timing_meta,
            },
        )
        timing_meta["total_seconds"] = round(time.perf_counter() - worker_started, 3)
        _attach_worker_metrics_to_artifact(
            session=session,
            artifact=artifact,
            metrics=timing_meta,
        )
        _mark_job_ready(
            session,
            job=job,
            artifact=artifact,
            final_message="Render lifecycle finished. Artifact is ready for playback.",
        )
        _log_job_event(
            level="info",
            stage="worker_done",
            message="Media job completed successfully.",
            context={**context, **timing_meta},
        )
        return True
    except TemplateValidationError as exc:
        _rollback_session_quietly(session)
        _log_job_event(
            level="error",
            stage="validation_failed",
            message=exc.message,
            context={**context, "error_code": exc.code},
        )
        _mark_job_failed(
            session,
            job=job,
            artifact=artifact,
            error_message=exc.message,
            error_code=exc.code,
            error_details=exc.to_dict(),
        )
        return False
    except ValueError as exc:
        _rollback_session_quietly(session)
        _log_job_event(
            level="error",
            stage="validation_failed",
            message=str(exc),
            context={**context, "error_code": "validation_error"},
        )
        _mark_job_failed(
            session,
            job=job,
            artifact=artifact,
            error_message=str(exc),
            error_code="validation_error",
            error_details={"code": "validation_error", "message": str(exc)},
        )
        return False
    except RenderEngineError as exc:
        _rollback_session_quietly(session)
        _log_job_event(
            level="error",
            stage="render_failed",
            message=exc.message,
            context={**context, "error_code": exc.code},
        )
        _mark_job_failed(
            session,
            job=job,
            artifact=artifact,
            error_message=exc.message,
            error_code=exc.code,
            error_details=exc.to_dict(),
        )
        return False
    except MediaPostprocessError as exc:
        _rollback_session_quietly(session)
        _log_job_event(
            level="error",
            stage="postprocess_failed",
            message=exc.message,
            context={**context, "error_code": exc.code},
        )
        _mark_job_failed(
            session,
            job=job,
            artifact=artifact,
            error_message=exc.message,
            error_code=exc.code,
            error_details=exc.to_dict(),
        )
        return False
    except MediaStorageError as exc:
        _rollback_session_quietly(session)
        _log_job_event(
            level="error",
            stage="upload_failed",
            message=exc.message,
            context={**context, "error_code": exc.code},
        )
        _mark_job_failed(
            session,
            job=job,
            artifact=artifact,
            error_message=exc.message,
            error_code=exc.code,
            error_details=exc.to_dict(),
        )
        return False
    except Exception as exc:
        _rollback_session_quietly(session)
        _log_job_event(
            level="exception",
            stage="worker_failed",
            message=str(exc),
            context={**context, "error_code": "unknown_error"},
        )
        _mark_job_failed(
            session,
            job=job,
            artifact=artifact,
            error_message=str(exc),
            error_code="unknown_error",
            error_details={"code": "unknown_error", "message": str(exc)},
        )
        return False


def list_media_artifacts(session: Session, *, user: UserAccount) -> MediaArtifactListResponse:
    _ensure_sample_media_artifacts(session, user=user)
    artifacts = list(
        session.scalars(
            select(MediaArtifact)
            .where(MediaArtifact.user_id == user.id)
            .order_by(MediaArtifact.created_at.desc(), MediaArtifact.title)
        )
    )
    return MediaArtifactListResponse(items=[media_artifact_to_schema(item) for item in artifacts])


def get_media_artifact(
    session: Session,
    *,
    user: UserAccount,
    artifact_id: UUID,
) -> MediaArtifactRead | None:
    _ensure_sample_media_artifacts(session, user=user)
    artifact = session.scalar(
        select(MediaArtifact).where(
            MediaArtifact.id == artifact_id,
            MediaArtifact.user_id == user.id,
        )
    )
    return media_artifact_to_schema(artifact) if artifact else None


def get_media_artifact_status(
    session: Session,
    *,
    user: UserAccount,
    artifact_id: UUID,
) -> MediaArtifactStatusResponse | None:
    _ensure_sample_media_artifacts(session, user=user)
    artifact = session.scalar(
        select(MediaArtifact).where(
            MediaArtifact.id == artifact_id,
            MediaArtifact.user_id == user.id,
        )
    )
    if artifact is None:
        return None
    progress = artifact.metadata_json.get("progress")
    render_meta = artifact.render_meta_json if isinstance(artifact.render_meta_json, dict) else {}
    error_details = render_meta.get("error_details")
    return MediaArtifactStatusResponse(
        artifact_id=artifact.id,
        status=artifact.status,
        progress=int(progress) if isinstance(progress, int) else (100 if artifact.status == "ready" else 0),
        error=str(artifact.metadata_json.get("error")) if artifact.metadata_json.get("error") else None,
        error_code=(
            str(artifact.metadata_json.get("error_code"))
            if artifact.metadata_json.get("error_code")
            else None
        ),
        error_details=error_details if isinstance(error_details, dict) else None,
    )


def get_latest_weekly_report(session: Session, *, user: UserAccount) -> WeeklyReportResponse:
    week_start, week_end = _current_week_range()
    return get_weekly_report(session, user=user, start=week_start, end=week_end)


def get_weekly_report(
    session: Session,
    *,
    user: UserAccount,
    start: date,
    end: date,
) -> WeeklyReportResponse:
    snapshot_table_available = _weekly_snapshot_table_exists(session)
    start_at, end_at = _date_range_bounds(start, end)
    range_attempts = _assessment_attempt_rows(
        session,
        user=user,
        submitted_from=start_at,
        submitted_before=end_at,
    )
    baseline_attempts = _assessment_attempt_rows(
        session,
        user=user,
        submitted_before=start_at,
    )
    attempts_count = len(range_attempts)
    states = list(
        session.scalars(select(LearnerConceptState).where(LearnerConceptState.user_id == user.id))
    )
    paired_scores = _paired_pre_post_scores(
        session,
        user=user,
        submitted_from=start_at,
        submitted_before=end_at,
    )
    mastered_or_ready = len([state for state in states if state.status in {"mastered", "ready"}])
    review_due = len([state for state in states if state.status in {"review_due", "gap"}])
    fixed_in_range = len(
        [
            state
            for state in states
            if state.status in {"mastered", "ready"}
            and state.last_evaluated_at is not None
            and start_at <= _as_utc(state.last_evaluated_at) < end_at
        ]
    )
    score = _attempt_correct_percent(range_attempts)
    fixed_gaps = fixed_in_range
    remaining_gaps = review_due
    retention_minutes = int(sum(state.evidence_count for state in states) * 6) if states else 0
    concept_names = _recent_concept_names(session, user=user)
    trends = _report_trends(range_attempts=range_attempts, baseline_attempts=baseline_attempts)
    previous_snapshot = (
        _latest_snapshot_before_range(session, user=user, start=start)
        if snapshot_table_available
        else None
    )
    fixed_delta = _fixed_gap_delta(
        fixed_gaps=fixed_gaps,
        fixed_in_range=fixed_in_range,
        attempts_count=attempts_count,
        has_state=bool(states),
        previous_snapshot=previous_snapshot,
    )
    remaining_delta = _remaining_gap_delta(
        remaining_gaps=remaining_gaps,
        attempts_count=attempts_count,
        fixed_in_range=fixed_in_range,
        previous_snapshot=previous_snapshot,
    )
    new_gaps = _new_gaps_count(
        remaining_gaps=remaining_gaps,
        previous_snapshot=previous_snapshot,
    )
    unlocked_count = max(0, fixed_in_range)
    unlocked_concepts = concept_names[: min(len(concept_names), unlocked_count)]
    recommendations = _weekly_recommendations(
        session=session,
        user=user,
        states=states,
    )
    has_baseline = bool(baseline_attempts)
    status = "complete" if attempts_count else ("state_only" if states else "no_data")
    if attempts_count and has_baseline:
        source = "derived_from_range_assessments_and_mastery"
    elif attempts_count:
        source = "derived_from_range_assessments_no_baseline"
    elif states:
        source = "derived_from_mastery_state"
    else:
        source = "no_assessment_or_mastery_data"
    data_quality = _report_data_quality(
        attempts_count=attempts_count,
        paired_concepts=int(paired_scores["paired_concept_count"] or 0),
        has_baseline=has_baseline,
        has_state=bool(states),
        status=status,
        source=source,
    )
    effort_impact = _report_effort_impact(
        attempts_count=attempts_count,
        retention_minutes=retention_minutes,
        review_due_count=review_due,
        new_gaps_count=new_gaps,
        trends=trends,
        learning_gain_percent=paired_scores["learning_gain_percent"],
        range_attempts=range_attempts,
    )
    concept_movers = _report_concept_movers(
        session=session,
        range_attempts=range_attempts,
        baseline_attempts=baseline_attempts,
        states=states,
    )
    weekly_timeline = _weekly_timeline(
        session=session,
        user=user,
        selected_start=start,
        selected_end=end,
        current_score=score,
        current_fixed_gaps=fixed_gaps,
        current_remaining_gaps=remaining_gaps,
        current_attempt_count=attempts_count,
        snapshot_table_available=snapshot_table_available,
    )
    weekly_narrative = _weekly_narrative(
        concept_movers=concept_movers,
        remaining_gaps=remaining_gaps,
        recommendations=recommendations,
    )
    report = WeeklyReportResponse(
        range_label=_format_week_label(start, end),
        range_start=start.isoformat(),
        range_end=end.isoformat(),
        status=status,
        source=source,
        score=score,
        pretest_score_percent=paired_scores["pretest_score_percent"],
        posttest_score_percent=paired_scores["posttest_score_percent"],
        learning_gain_percent=paired_scores["learning_gain_percent"],
        paired_concept_count=paired_scores["paired_concept_count"],
        fixed_gaps=fixed_gaps,
        fixed_gaps_delta=fixed_delta,
        remaining_gaps=remaining_gaps,
        remaining_gaps_delta=remaining_delta,
        retention_minutes=retention_minutes,
        concepts=", ".join(concept_names),
        summary_notes=[
            "Report is aggregated from persisted attempts submitted inside the selected date range.",
            (
                "Gap deltas and movers are stabilized with weekly snapshots when history exists."
                if snapshot_table_available
                else "Weekly snapshot table is not available yet; report uses runtime inference."
            ),
        ],
        trends=trends,
        performance_groups=[
            ReportPerformanceGroupRead(
                label=trend.label,
                pre_test_percent=round(trend.before * 100),
                post_test_percent=round(trend.after * 100),
            )
            for trend in trends
        ],
        gap_metrics={
            "fixed": GapMetricRead(
                count=fixed_gaps,
                weekly_delta=fixed_delta,
                delta_label=f"+{fixed_delta} this week",
            ),
            "remaining": GapMetricRead(
                count=remaining_gaps,
                weekly_delta=remaining_delta,
                delta_label=f"{remaining_delta} this week",
            ),
        },
        unlocked_this_week=UnlockedConceptSummaryRead(
            count=unlocked_count,
            concepts=unlocked_concepts,
        ),
        upcoming_recommendations=recommendations,
        consistency_summary=_consistency_summary(
            attempts_count=attempts_count,
            active_days=effort_impact.active_days,
            remaining_gaps=remaining_gaps,
        ),
        data_quality=data_quality,
        effort_impact=effort_impact,
        concept_movers=concept_movers,
        weekly_timeline=weekly_timeline,
        weekly_narrative=weekly_narrative,
    )
    if snapshot_table_available:
        _upsert_weekly_report_snapshot(
            session=session,
            user=user,
            start=start,
            end=end,
            report=report,
            attempt_count=attempts_count,
            active_days=effort_impact.active_days,
            overdue_reviews=review_due,
            new_gaps=new_gaps,
        )
        session.commit()
    return report


def get_or_create_daily_evaluation(
    session: Session,
    *,
    user: UserAccount,
) -> DailyEvaluationResponse:
    ensure_curriculum_seeded(session)
    today = datetime.now(UTC).date().isoformat()
    refresh_reason: str | None = None
    assessment = session.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.user_id == user.id,
            AssessmentSession.session_type == "daily_evaluation",
            AssessmentSession.metadata_json["review_date"].as_string() == today,
        )
        .options(
            selectinload(AssessmentSession.questions).selectinload(AssessmentQuestion.options)
        )
    )
    if assessment is not None and _should_refresh_daily_assessment_from_bank(
        session,
        assessment=assessment,
        user=user,
    ):
        refresh_reason = assessment.metadata_json.get("refresh_reason")
        session.delete(assessment)
        session.flush()
        assessment = None
    if assessment is None:
        language = _language_for_user(user)
        ensure_question_bank_seeded(
            session,
            commit=False,
            preferred_language=language,
        )
        learner_step, selected_questions = select_daily_questions(session, user=user)
        if not selected_questions:
            import_seed_directory(session, strict=False, commit=False)
            learner_step, selected_questions = select_daily_questions(session, user=user)
        if not selected_questions:
            raise LookupError(
                "Daily Evaluation requires active question-bank items with "
                "assessment_types including daily_quiz."
            )
        assessment = _create_daily_assessment_from_bank(
            session,
            user=user,
            review_date=today,
            learner_step=learner_step,
            selected_questions=selected_questions,
            refresh_reason=refresh_reason,
        )
        session.commit()
        assessment = session.scalar(
            select(AssessmentSession)
            .where(AssessmentSession.id == assessment.id)
            .options(
                selectinload(AssessmentSession.questions).selectinload(
                    AssessmentQuestion.options
                )
            )
        )
    assert assessment is not None
    language = _language_for_user(user)
    completed_question_ids = _answered_question_ids(session, assessment_id=assessment.id)
    concept_titles_by_id = _daily_concept_titles_by_id(
        session,
        questions=assessment.questions,
        language=language,
    )
    option_titles_by_text = _daily_option_titles_by_text(
        session,
        assessment=assessment,
        language=language,
    )
    questions = [
        _daily_question_to_schema(
            question,
            language=language,
            concept_titles_by_id=concept_titles_by_id,
            option_titles_by_text=option_titles_by_text,
        )
        for question in assessment.questions
    ]
    total_questions = len(questions)
    completed_count = len(completed_question_ids)
    current_question = next(
        (question for question in assessment.questions if question.id not in completed_question_ids),
        assessment.questions[0] if assessment.questions else None,
    )
    due_count = max(0, total_questions - completed_count)
    return DailyEvaluationResponse(
        session_id=assessment.id,
        title=_daily_session_title(language),
        status=assessment.status,
        language=language,
        source=_daily_source(assessment),
        review_policy={
            "strategy": str(assessment.metadata_json.get("policy") or "spaced_repetition_mvp"),
            "basis": _daily_review_policy_basis(assessment, language=language),
        },
        review_due=ReviewDueRead(
            title=_daily_copy(language, id_text="Review yang jatuh tempo", en_text="Review due"),
            due_count=due_count,
            summary=_daily_due_summary(due_count, language=language),
            action_label=_daily_copy(
                language,
                id_text="Mulai" if completed_count == 0 else "Lanjutkan",
                en_text="Start" if completed_count == 0 else "Continue",
            ),
        ),
        progress=ProgressRead(
            current=min(total_questions, completed_count + 1) if total_questions else 0,
            total=total_questions,
            completed=completed_count,
            label=_daily_progress_label(
                min(total_questions, completed_count + 1) if total_questions else 0,
                total_questions,
                language=language,
            ),
        ),
        question=(
            _daily_question_to_schema(
                current_question,
                language=language,
                concept_titles_by_id=concept_titles_by_id,
                option_titles_by_text=option_titles_by_text,
            )
            if current_question
            else None
        ),
        questions=questions,
        retention_forecast=_retention_forecast(
            completed_count=completed_count,
            language=language,
        ),
        recommendation_callout=_daily_recommendation_callout(
            due_count=due_count,
            language=language,
        ),
    )


def submit_daily_answer_response(
    session: Session,
    *,
    user: UserAccount,
    assessment_session_id: UUID,
    question_id: str,
    option_id: str,
    confidence: int,
) -> DailyEvaluationAnswerResponse:
    language = _language_for_user(user)
    attempt, is_correct = submit_answer(
        session,
        user=user,
        assessment_session_id=assessment_session_id,
        question_id=question_id,
        option_id=option_id,
        confidence=confidence,
    )
    assessment = session.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.id == assessment_session_id,
            AssessmentSession.user_id == user.id,
            AssessmentSession.session_type == "daily_evaluation",
        )
        .options(selectinload(AssessmentSession.questions))
    )
    completed = False
    session_status = "active"
    if assessment is not None:
        completed_question_ids = _answered_question_ids(session, assessment_id=assessment.id)
        completed = bool(assessment.questions) and len(completed_question_ids) >= len(assessment.questions)
        if completed:
            assessment.status = "completed"
            assessment.completed_at = datetime.now(UTC)
            session.commit()
        session_status = assessment.status
    return DailyEvaluationAnswerResponse(
        attempt_id=attempt.id,
        is_correct=is_correct,
        next_review_label=_daily_next_review_label(
            _daily_next_review_interval_for_attempt(
                session,
                user=user,
                attempt=attempt,
                fallback_days=3 if is_correct else 1,
            ),
            language=language,
        ),
        mastery_delta=0.08 if is_correct else -0.04,
        session_status=session_status,
        completed=completed,
    )


def get_daily_evaluation_result(
    session: Session,
    *,
    user: UserAccount,
    assessment_session_id: UUID,
) -> DailyEvaluationResultResponse | None:
    assessment = session.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.id == assessment_session_id,
            AssessmentSession.user_id == user.id,
            AssessmentSession.session_type == "daily_evaluation",
        )
        .options(selectinload(AssessmentSession.questions))
    )
    if assessment is None:
        return None

    language = _language_for_user(user)
    result_summary = _daily_session_result_summary(
        session,
        user=user,
        assessment=assessment,
        language=language,
    )
    interval_days = 3 if result_summary.review_again_count else 7
    due_date = datetime.now(UTC).date() + timedelta(days=interval_days)

    if assessment.questions and result_summary.reviewed_count >= len(assessment.questions):
        assessment.status = "completed"
        assessment.completed_at = assessment.completed_at or datetime.now(UTC)
        session.commit()

    reviewed_concepts = [
        ReviewedConceptRead(
            concept_id=str(concept.concept_id) if concept.concept_id else None,
            title=concept.title,
            status_label=concept.status_label,
            mastery_score=concept.mastery_score,
        )
        for concept in result_summary.reviewed_concepts
    ]
    return DailyEvaluationResultResponse(
        session_id=assessment.id,
        title=_daily_session_title(language),
        status=assessment.status,
        source=_daily_source(assessment),
        score_percent=result_summary.score_percent,
        reviewed_count=result_summary.reviewed_count,
        correct_count=result_summary.correct_count,
        review_again_count=result_summary.review_again_count,
        reviewed_concepts=reviewed_concepts,
        spaced_repetition_impact=SpacedRepetitionImpactRead(
            retention_lift_percent=_retention_lift_percent(
                result_summary.correct_count,
                result_summary.review_again_count,
            ),
            days_until_next_review=interval_days,
            summary=_daily_result_summary_label(
                reviewed_count=result_summary.reviewed_count,
                language=language,
            ),
        ),
        next_review=DailyEvaluationNextReviewRead(
            label=_daily_next_review_label(interval_days, language=language),
            due_date=due_date.isoformat(),
            interval_days=interval_days,
        ),
        recommended_next_actions=_daily_next_actions(
            reviewed_concepts=result_summary.reviewed_concepts,
            review_again_count=result_summary.review_again_count,
            due_date=due_date,
            language=language,
        ),
        back_to_home=ActionRead(
            label=_daily_copy(language, id_text="Kembali ke Beranda", en_text="Back to Home"),
            action_type="navigate",
            target="/home",
        ),
    )


def ensure_curriculum_seeded(session: Session) -> None:
    if session.scalar(select(Subject.id).limit(1)) is None:
        seed_curriculum(session, commit=False)
        session.flush()


def question_to_schema(question: AssessmentQuestion) -> AssessmentQuestionRead:
    return AssessmentQuestionRead(
        id=str(question.id),
        step_label=question.step_label,
        topic=question.topic,
        prompt=question.prompt,
        helper=question.helper_text,
        options=[
            AssessmentOptionRead(id=str(option.id), label=option.label, text=option.text)
            for option in question.options
        ],
    )


def _daily_question_to_schema(
    question: AssessmentQuestion,
    *,
    language: str,
    concept_titles_by_id: dict[UUID, str],
    option_titles_by_text: dict[str, str],
) -> AssessmentQuestionRead:
    topic = _daily_question_topic(
        question,
        language=language,
        concept_titles_by_id=concept_titles_by_id,
    )
    return AssessmentQuestionRead(
        id=str(question.id),
        step_label=_daily_question_step_label(question, language=language),
        topic=topic,
        prompt=_daily_question_prompt(question.prompt, language=language),
        helper=_daily_question_helper(question.helper_text, language=language),
        options=[
            AssessmentOptionRead(
                id=str(option.id),
                label=option.label,
                text=_daily_option_text(
                    option.text,
                    language=language,
                    option_titles_by_text=option_titles_by_text,
                ),
            )
            for option in question.options
        ],
    )


def _should_refresh_daily_assessment_from_bank(
    session: Session,
    *,
    assessment: AssessmentSession,
    user: UserAccount,
) -> bool:
    attempt_count = session.scalar(
        select(func.count())
        .select_from(AssessmentAttempt)
        .where(AssessmentAttempt.session_id == assessment.id)
    )
    if int(attempt_count or 0) > 0:
        return False

    if assessment.metadata_json.get("policy") != "personalized_daily_ebbinghaus_v1":
        return True

    resolved_step = resolve_learner_step(session, user=user)
    if _daily_assessment_track_changed(assessment, learner_step=resolved_step):
        metadata = dict(assessment.metadata_json or {})
        metadata["refresh_reason"] = "active_track_changed"
        assessment.metadata_json = metadata
        return True

    preferred_language = _language_for_user(user)
    stored_language = normalize_language_code(
        str(assessment.metadata_json.get("preferred_language") or "")
    )
    if stored_language and stored_language != preferred_language:
        return True

    question_languages = {
        normalize_language_code(str(question.metadata_json.get("question_bank_language") or ""))
        for question in assessment.questions
        if question.metadata_json.get("question_bank_language")
    }
    return bool(question_languages and preferred_language not in question_languages)


def _daily_assessment_track_changed(
    assessment: AssessmentSession,
    *,
    learner_step: LearnerStep,
) -> bool:
    stored_track_id = assessment.track_id or _uuid_from_metadata(
        assessment.metadata_json.get("active_track_id")
    )
    return stored_track_id != learner_step.active_track_id


def _uuid_from_metadata(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _daily_concept_titles_by_id(
    session: Session,
    *,
    questions: list[AssessmentQuestion],
    language: str,
) -> dict[UUID, str]:
    concept_ids = {
        question.concept_id for question in questions if question.concept_id is not None
    }
    if not concept_ids:
        return {}
    return {
        concept.id: _localized_topic(concept.title, concept, language=language)
        for concept in session.scalars(
            select(KnowledgeConcept).where(KnowledgeConcept.id.in_(concept_ids))
        )
    }


def _daily_option_titles_by_text(
    session: Session,
    *,
    assessment: AssessmentSession,
    language: str,
) -> dict[str, str]:
    option_texts = {
        _normalize_daily_text(option.text)
        for question in assessment.questions
        for option in question.options
        if option.text.strip()
    }
    if not option_texts:
        return {}

    query = select(KnowledgeConcept)
    subject_code = str(
        assessment.metadata_json.get("selected_subject_code") or ""
    ).strip()
    if subject_code:
        subject_id = session.scalar(select(Subject.id).where(Subject.code == subject_code))
        if subject_id is not None:
            query = query.where(KnowledgeConcept.subject_id == subject_id)

    title_by_text: dict[str, str] = {}
    for concept in session.scalars(query):
        localized = _localized_topic(concept.title, concept, language=language)
        for candidate in _daily_concept_title_candidates(concept):
            key = _normalize_daily_text(candidate)
            if key in option_texts:
                title_by_text[key] = localized
    return title_by_text


def _daily_concept_title_candidates(concept: KnowledgeConcept) -> set[str]:
    metadata = dict(concept.metadata_json or {})
    candidates = {
        concept.title,
        str(metadata.get("label_id") or ""),
        str(metadata.get("label_en") or ""),
        str(metadata.get("en_title") or ""),
    }
    english_title = translate_curriculum_label_to_english(concept.title)
    if english_title:
        candidates.add(english_title)
    return {candidate.strip() for candidate in candidates if candidate.strip()}


def _daily_question_topic(
    question: AssessmentQuestion,
    *,
    language: str,
    concept_titles_by_id: dict[UUID, str],
) -> str:
    if question.concept_id is not None:
        localized = concept_titles_by_id.get(question.concept_id)
        if localized:
            return localized
    return _daily_option_text(
        question.topic,
        language=language,
        option_titles_by_text={},
    )


def _daily_question_step_label(question: AssessmentQuestion, *, language: str) -> str:
    step_label = question.step_label.strip()
    if not _is_indonesian(language):
        return step_label
    if "/" in step_label:
        return f"Soal {step_label}"
    if step_label.lower() in {"daily evals", "daily evaluation"}:
        return "Evaluasi Harian"
    return step_label


def _daily_question_prompt(prompt: str, *, language: str) -> str:
    text = prompt.strip()
    if not _is_indonesian(language) or not text:
        return text

    normalized = _normalize_daily_text(text)
    if normalized.startswith("a quick review of ") and normalized.endswith(
        " belongs to which topic?"
    ):
        idea = text[len("A quick review of ") : -len(" belongs to which topic?")]
        return f"Review cepat tentang {idea} termasuk topik apa?"
    if normalized.startswith("which topic is about ") and normalized.endswith("?"):
        idea = text[len("Which topic is about ") : -1]
        return f"Topik mana yang membahas {idea}?"
    if normalized.startswith("topic for ") and normalized.endswith("?"):
        idea = text[len("Topic for ") : -1]
        return f"Topik untuk {idea}?"
    if normalized.startswith(
        "before starting this strand, which topic would assess "
    ) and normalized.endswith("?"):
        idea = text[
            len("Before starting this strand, which topic would assess ") : -1
        ]
        return f"Sebelum memulai rangkaian ini, topik mana yang mengecek {idea}?"
    if normalized.startswith(
        "after learning this strand, which topic best fits "
    ) and normalized.endswith("?"):
        idea = text[len("After learning this strand, which topic best fits ") : -1]
        return f"Setelah belajar rangkaian ini, topik mana yang paling cocok dengan {idea}?"
    return text


def _daily_question_helper(helper: str, *, language: str) -> str:
    text = helper.strip()
    if not _is_indonesian(language) or not text:
        return text
    helper_map = {
        "choose the topic that best matches the key skill or idea.": (
            "Pilih topik yang paling cocok dengan skill atau ide kunci."
        ),
        "pick the topic that best matches the idea.": (
            "Pilih topik yang paling cocok dengan ide tersebut."
        ),
        "choose the topic that best fits the full mathematical idea.": (
            "Pilih topik yang paling sesuai dengan ide matematika lengkapnya."
        ),
        "pick the best topic match.": "Pilih topik yang paling cocok.",
    }
    return helper_map.get(_normalize_daily_text(text), text)


def _daily_option_text(
    text: str,
    *,
    language: str,
    option_titles_by_text: dict[str, str],
) -> str:
    if not text.strip():
        return ""
    localized = option_titles_by_text.get(_normalize_daily_text(text))
    if localized:
        return localized
    return text


def _normalize_daily_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def track_to_schema(track: LearningTrack) -> TrackRead:
    subject = track.learning_goal.subject if track.learning_goal else None
    return TrackRead(
        id=track.id,
        learning_goal_id=track.learning_goal_id,
        subject_code=subject.code if subject else "",
        subject_name=subject.name if subject else "",
        title=track.title,
        subtitle=track.subtitle,
        status=track.status,
        progress_percent=track.progress_percent,
        modules=[
            TrackModuleRead(
                id=module.id,
                track_id=module.track_id,
                title=module.title,
                description=module.description,
                estimated_minutes=module.estimated_minutes,
                difficulty_label=module.difficulty_label,
                sort_order=module.sort_order,
                status=module.status,
            )
            for module in track.modules
        ],
    )


def _track_schema_load_options():
    return (
        selectinload(LearningTrack.modules),
        selectinload(LearningTrack.learning_goal).selectinload(LearningGoal.subject),
    )


def media_artifact_to_schema(artifact: MediaArtifact) -> MediaArtifactRead:
    video_url = _coalesce_video_url(artifact)
    return MediaArtifactRead(
        id=artifact.id,
        title=artifact.title,
        subtitle=artifact.subtitle,
        artifact_type=artifact.artifact_type,
        status=artifact.status,
        duration_seconds=artifact.duration_seconds,
        duration_label=_duration_label(artifact.duration_seconds),
        thumbnail_url=_optional_text(artifact.thumbnail_url),
        video_url=video_url,
        playback_url=video_url,
        transcript=artifact.transcript,
        notes=artifact.notes_json,
        track_id=artifact.track_id,
        module_id=artifact.module_id,
        created_at=artifact.created_at.isoformat() if artifact.created_at else "",
    )


def _user_tracks(session: Session, *, user: UserAccount) -> list[LearningTrack]:
    return list(
        session.scalars(
            select(LearningTrack)
            .where(LearningTrack.user_id == user.id)
            .options(*_track_schema_load_options())
            .order_by(LearningTrack.created_at.desc())
        )
    )


def _build_queue_items(tracks: list[LearningTrack]) -> list[QueueItemRead]:
    items: list[QueueItemRead] = []
    for track in tracks:
        modules = sorted(track.modules, key=lambda item: item.sort_order)
        ready_module = next(
            (module for module in modules if module.status in {"ready", "active"}),
            modules[0] if modules else None,
        )
        if ready_module is None:
            continue
        items.append(
            QueueItemRead(
                id=f"module:{ready_module.id}",
                track_id=track.id,
                module_id=ready_module.id,
                title=ready_module.title,
                subtitle=track.title,
                meta=f"{ready_module.estimated_minutes} min | {ready_module.difficulty_label}",
                status=ready_module.status,
                estimated_minutes=ready_module.estimated_minutes,
                action_label="Continue",
            )
        )
    if items:
        return items
    return [
        QueueItemRead(
            id="seed:create-goal",
            title="Create your first learning goal",
            subtitle="WICARA will generate a pretest, track, and modules from backend data.",
            meta="2 min setup",
            status="ready",
            estimated_minutes=2,
            action_label="Create goal",
        )
    ]


def _today_daily_session(session: Session, *, user: UserAccount) -> AssessmentSession | None:
    today = datetime.now(UTC).date().isoformat()
    return session.scalar(
        select(AssessmentSession).where(
            AssessmentSession.user_id == user.id,
            AssessmentSession.session_type == "daily_evaluation",
            AssessmentSession.metadata_json["review_date"].as_string() == today,
        )
    )


def _ensure_sample_media_artifacts(session: Session, *, user: UserAccount) -> None:
    tracks = _user_tracks(session, user=user)
    track = tracks[0] if tracks else None
    first_module = track.modules[0] if track and track.modules else None
    second_module = track.modules[1] if track and len(track.modules) > 1 else None
    seed_source = "media_gallery_demo_videos_20260515"
    samples = [
        {
            "module": first_module,
            "title": "Perkalian",
            "subtitle": "Video konsep perkalian",
            "duration_seconds": 300,
            "playback_url": (
                "https://gwbqhirtkgkghnpahtgt.supabase.co/storage/v1/object/public/"
                "video/perkalian.mp4"
            ),
            "transcript": "Video pembelajaran perkalian untuk demo galeri.",
            "notes": [
                "Demo gallery seed.",
                "Source: Supabase public object storage.",
            ],
        },
        {
            "module": second_module,
            "title": "Aljabar",
            "subtitle": "Video konsep aljabar",
            "duration_seconds": 300,
            "playback_url": (
                "https://gwbqhirtkgkghnpahtgt.supabase.co/storage/v1/object/public/"
                "video/aljabar.mp4"
            ),
            "transcript": "Video pembelajaran aljabar untuk demo galeri.",
            "notes": [
                "Demo gallery seed.",
                "Source: Supabase public object storage.",
            ],
        },
    ]
    existing_artifacts = list(
        session.scalars(select(MediaArtifact).where(MediaArtifact.user_id == user.id))
    )
    existing_by_url = {
        item.playback_url.strip(): item
        for item in existing_artifacts
        if item.playback_url.strip()
    }
    existing_by_title = {
        item.title.strip().lower(): item
        for item in existing_artifacts
        if item.title.strip()
    }
    has_changes = False
    for sample in samples:
        module = sample["module"]
        playback_url = str(sample["playback_url"]).strip()
        existing = existing_by_url.get(playback_url)
        if existing is None:
            existing = existing_by_title.get(str(sample["title"]).strip().lower())

        if existing is None:
            session.add(
                MediaArtifact(
                    user_id=user.id,
                    track_id=track.id if track else None,
                    module_id=module.id if module else None,
                    concept_id=module.concept_id if module else None,
                    artifact_type="video",
                    title=str(sample["title"]),
                    subtitle=str(sample["subtitle"]),
                    status="ready",
                    duration_seconds=int(sample["duration_seconds"]),
                    thumbnail_url="",
                    playback_url=playback_url,
                    transcript=str(sample["transcript"]),
                    notes_json=list(sample["notes"]),
                    metadata_json={"seed_source": seed_source},
                )
            )
            has_changes = True
            continue

        existing.track_id = track.id if track else existing.track_id
        existing.module_id = module.id if module else existing.module_id
        existing.concept_id = module.concept_id if module else existing.concept_id
        existing.artifact_type = "video"
        existing.title = str(sample["title"])
        existing.subtitle = str(sample["subtitle"])
        existing.status = "ready"
        existing.duration_seconds = int(sample["duration_seconds"])
        existing.playback_url = playback_url
        existing.transcript = str(sample["transcript"])
        existing.notes_json = list(sample["notes"])
        metadata = dict(existing.metadata_json or {})
        metadata["seed_source"] = seed_source
        existing.metadata_json = metadata
        has_changes = True

    if has_changes:
        session.commit()


def _date_range_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    start_at = datetime(start.year, start.month, start.day, tzinfo=UTC)
    exclusive_end = end + timedelta(days=1)
    end_at = datetime(exclusive_end.year, exclusive_end.month, exclusive_end.day, tzinfo=UTC)
    return start_at, end_at


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _assessment_attempt_rows(
    session: Session,
    *,
    user: UserAccount,
    submitted_from: datetime | None = None,
    submitted_before: datetime | None = None,
) -> list[tuple[AssessmentAttempt, AssessmentQuestion]]:
    query = (
        select(AssessmentAttempt, AssessmentQuestion)
        .join(AssessmentSession, AssessmentAttempt.session_id == AssessmentSession.id)
        .join(AssessmentQuestion, AssessmentAttempt.question_id == AssessmentQuestion.id)
        .where(AssessmentSession.user_id == user.id)
        .order_by(AssessmentAttempt.submitted_at)
    )
    if submitted_from is not None:
        query = query.where(AssessmentAttempt.submitted_at >= submitted_from)
    if submitted_before is not None:
        query = query.where(AssessmentAttempt.submitted_at < submitted_before)
    return [(attempt, question) for attempt, question in session.execute(query)]


def _paired_pre_post_scores(
    session: Session,
    *,
    user: UserAccount,
    submitted_from: datetime | None = None,
    submitted_before: datetime | None = None,
    learning_goal_id: UUID | None = None,
) -> dict[str, int | None]:
    query = (
        select(AssessmentAttempt, AssessmentQuestion.concept_id, AssessmentSession.session_type)
        .join(AssessmentSession, AssessmentAttempt.session_id == AssessmentSession.id)
        .join(AssessmentQuestion, AssessmentAttempt.question_id == AssessmentQuestion.id)
        .where(
            AssessmentSession.user_id == user.id,
            AssessmentSession.session_type.in_({"pretest", "posttest"}),
            AssessmentQuestion.concept_id.is_not(None),
        )
        .order_by(AssessmentAttempt.submitted_at)
    )
    if submitted_from is not None:
        query = query.where(AssessmentAttempt.submitted_at >= submitted_from)
    if submitted_before is not None:
        query = query.where(AssessmentAttempt.submitted_at < submitted_before)
    if learning_goal_id is not None:
        query = query.where(AssessmentSession.learning_goal_id == learning_goal_id)

    by_concept: dict[UUID, dict[str, list[float]]] = {}
    for attempt, concept_id, session_type in session.execute(query):
        if concept_id is None:
            continue
        concept_scores = by_concept.setdefault(concept_id, {"pretest": [], "posttest": []})
        if session_type in concept_scores:
            concept_scores[str(session_type)].append(attempt_evidence_score(attempt))

    paired_pre: list[float] = []
    paired_post: list[float] = []
    for scores in by_concept.values():
        pre_scores = scores["pretest"]
        post_scores = scores["posttest"]
        if not pre_scores or not post_scores:
            continue
        paired_pre.append(sum(pre_scores) / len(pre_scores))
        paired_post.append(sum(post_scores) / len(post_scores))

    paired_count = len(paired_pre)
    if paired_count == 0:
        return {
            "pretest_score_percent": None,
            "posttest_score_percent": None,
            "learning_gain_percent": None,
            "paired_concept_count": 0,
        }
    pretest_score = int(round((sum(paired_pre) / paired_count) * 100))
    posttest_score = int(round((sum(paired_post) / paired_count) * 100))
    return {
        "pretest_score_percent": pretest_score,
        "posttest_score_percent": posttest_score,
        "learning_gain_percent": posttest_score - pretest_score,
        "paired_concept_count": paired_count,
    }


def _attempt_correct_percent(rows: list[tuple[AssessmentAttempt, AssessmentQuestion]]) -> int:
    if not rows:
        return 0
    correct = len([attempt for attempt, _question in rows if attempt_answer_score(attempt) >= 1.0])
    return int(round((correct / len(rows)) * 100))


def _weighted_analysis_percent(rows: list[tuple[AssessmentAttempt, AssessmentQuestion]]) -> int:
    if not rows:
        return 0
    weighted_score = 0.0
    total_weight = 0.0
    for attempt, _question in rows:
        confidence_weight = max(1, min(10, attempt.confidence or 1)) / 10
        total_weight += confidence_weight
        weighted_score += attempt_evidence_score(attempt) * confidence_weight
    return int(round((weighted_score / max(total_weight, 0.01)) * 100))


def _application_rows(
    rows: list[tuple[AssessmentAttempt, AssessmentQuestion]],
) -> list[tuple[AssessmentAttempt, AssessmentQuestion]]:
    selected: list[tuple[AssessmentAttempt, AssessmentQuestion]] = []
    for row in rows:
        _attempt, question = row
        marker = f"{question.topic} {question.helper_text} {question.difficulty_label}".lower()
        if any(token in marker for token in ("application", "apply", "problem", "equation")):
            selected.append(row)
    return selected


def _report_period_value(
    rows: list[tuple[AssessmentAttempt, AssessmentQuestion]],
    *,
    analysis: bool = False,
) -> int:
    if not rows:
        return 0
    return _weighted_analysis_percent(rows) if analysis else _attempt_correct_percent(rows)


def _report_trends(
    *,
    range_attempts: list[tuple[AssessmentAttempt, AssessmentQuestion]],
    baseline_attempts: list[tuple[AssessmentAttempt, AssessmentQuestion]],
) -> list[ReportTrendRead]:
    overall_after = _report_period_value(range_attempts)
    if baseline_attempts:
        overall_before = _report_period_value(baseline_attempts)
    elif range_attempts:
        overall_before = overall_after
    else:
        overall_before = 0

    range_application = _application_rows(range_attempts)
    baseline_application = _application_rows(baseline_attempts)
    application_after = _report_period_value(range_application)
    if baseline_application:
        application_before = _report_period_value(baseline_application)
    elif range_application:
        application_before = application_after
    else:
        application_before = 0

    analysis_after = _report_period_value(range_attempts, analysis=True)
    if baseline_attempts:
        analysis_before = _report_period_value(baseline_attempts, analysis=True)
    elif range_attempts:
        analysis_before = analysis_after
    else:
        analysis_before = 0

    return [
        ReportTrendRead(label="Overall", before=overall_before / 100, after=overall_after / 100),
        ReportTrendRead(
            label="Application",
            before=application_before / 100,
            after=application_after / 100,
        ),
        ReportTrendRead(label="Analysis", before=analysis_before / 100, after=analysis_after / 100),
    ]


def _weekly_snapshot_table_exists(session: Session) -> bool:
    try:
        return bool(inspect(session.get_bind()).has_table("weekly_report_snapshots"))
    except Exception:
        logger.warning(
            "Unable to inspect weekly_report_snapshots table; fallback to attempts-only report."
        )
        return False


def _latest_snapshot_before_range(
    session: Session,
    *,
    user: UserAccount,
    start: date,
) -> WeeklyReportSnapshot | None:
    try:
        return session.scalar(
            select(WeeklyReportSnapshot)
            .where(
                WeeklyReportSnapshot.user_id == user.id,
                WeeklyReportSnapshot.range_end < start,
            )
            .order_by(WeeklyReportSnapshot.range_end.desc())
        )
    except ProgrammingError:
        session.rollback()
        logger.warning(
            "weekly_report_snapshots query failed; continuing without snapshot baseline."
        )
        return None


def _fixed_gap_delta(
    *,
    fixed_gaps: int,
    fixed_in_range: int,
    attempts_count: int,
    has_state: bool,
    previous_snapshot: WeeklyReportSnapshot | None,
) -> int:
    if previous_snapshot is not None:
        return fixed_gaps - previous_snapshot.fixed_gaps
    if attempts_count > 0 or has_state:
        return fixed_in_range
    return 0


def _remaining_gap_delta(
    *,
    remaining_gaps: int,
    attempts_count: int,
    fixed_in_range: int,
    previous_snapshot: WeeklyReportSnapshot | None,
) -> int:
    if previous_snapshot is not None:
        return remaining_gaps - previous_snapshot.remaining_gaps
    if attempts_count > 0 and fixed_in_range > 0:
        return -fixed_in_range
    return 0


def _new_gaps_count(
    *,
    remaining_gaps: int,
    previous_snapshot: WeeklyReportSnapshot | None,
) -> int:
    if previous_snapshot is None:
        return 0
    return max(0, remaining_gaps - previous_snapshot.remaining_gaps)


def _active_days_from_rows(rows: list[tuple[AssessmentAttempt, AssessmentQuestion]]) -> int:
    if not rows:
        return 0
    return len(
        {
            _as_utc(attempt.submitted_at).date()
            for attempt, _ in rows
            if attempt.submitted_at is not None
        }
    )


def _report_data_quality(
    *,
    attempts_count: int,
    paired_concepts: int,
    has_baseline: bool,
    has_state: bool,
    status: str,
    source: str,
) -> ReportDataQualityRead:
    confidence_score = 0
    confidence_score += min(35, attempts_count * 2)
    confidence_score += min(35, paired_concepts * 10)
    if has_baseline:
        confidence_score += 10
    if has_state:
        confidence_score += 10
    confidence_score = max(0, min(100, confidence_score))
    if confidence_score >= 80:
        confidence_label = "high"
    elif confidence_score >= 55:
        confidence_label = "medium"
    else:
        confidence_label = "low"

    if status == "no_data":
        coverage_status = "no_data"
    elif source == "derived_from_range_assessments_no_baseline":
        coverage_status = "partial_history"
    else:
        coverage_status = "evidence_backed"

    notes = [
        f"{attempts_count} assessment attempts in selected range.",
        f"{paired_concepts} paired pretest/posttest concepts detected.",
    ]
    if coverage_status == "no_data":
        notes.append("No attempts found in this date range yet.")
    elif coverage_status == "partial_history":
        notes.append("Range attempts exist, but historical baseline is still limited.")
    else:
        notes.append("Report uses persisted attempts and baseline context.")

    return ReportDataQualityRead(
        confidence_label=confidence_label,
        confidence_score=confidence_score,
        coverage_status=coverage_status,
        attempts_covered=attempts_count,
        paired_concepts=paired_concepts,
        notes=notes,
    )


def _report_effort_impact(
    *,
    attempts_count: int,
    retention_minutes: int,
    review_due_count: int,
    new_gaps_count: int,
    trends: list[ReportTrendRead],
    learning_gain_percent: int | None,
    range_attempts: list[tuple[AssessmentAttempt, AssessmentQuestion]],
) -> ReportEffortImpactRead:
    active_days = _active_days_from_rows(range_attempts)
    if learning_gain_percent is not None:
        impact_score_delta = learning_gain_percent
    elif trends:
        impact_score_delta = round((trends[0].after - trends[0].before) * 100)
    else:
        impact_score_delta = 0

    if attempts_count == 0:
        efficiency_label = "no_signal"
    elif impact_score_delta >= 10 and active_days >= 3:
        efficiency_label = "high_leverage"
    elif impact_score_delta >= 0:
        efficiency_label = "steady"
    else:
        efficiency_label = "needs_focus"

    narrative = (
        f"{attempts_count} attempts across {active_days} active days "
        f"produced {impact_score_delta:+d}% impact trend."
    )

    return ReportEffortImpactRead(
        attempt_count=attempts_count,
        active_days=active_days,
        retention_minutes=retention_minutes,
        review_due_count=review_due_count,
        new_gaps_count=new_gaps_count,
        impact_score_delta=impact_score_delta,
        efficiency_label=efficiency_label,
        narrative=narrative,
    )


def _consistency_summary(
    *,
    attempts_count: int,
    active_days: int,
    remaining_gaps: int,
) -> ConsistencySummaryRead:
    if attempts_count <= 0:
        return ConsistencySummaryRead(
            title="No activity in selected range.",
            narrative="Complete assessments in this date range to generate a consistency signal.",
            signal="no_activity",
        )
    if active_days >= 4:
        return ConsistencySummaryRead(
            title="Consistency is strong.",
            narrative=(
                f"Learning activity happened across {active_days} active days with "
                f"{remaining_gaps} remaining gaps."
            ),
            signal="high_consistency",
        )
    return ConsistencySummaryRead(
        title="Consistency is building.",
        narrative=(
            f"Learning activity happened across {active_days} active days with "
            f"{remaining_gaps} remaining gaps."
        ),
        signal="building_consistency",
    )


def _report_concept_movers(
    *,
    session: Session,
    range_attempts: list[tuple[AssessmentAttempt, AssessmentQuestion]],
    baseline_attempts: list[tuple[AssessmentAttempt, AssessmentQuestion]],
    states: list[LearnerConceptState],
) -> list[ReportConceptMoverRead]:
    range_scores: dict[UUID, list[float]] = {}
    baseline_scores: dict[UUID, list[float]] = {}
    for attempt, question in range_attempts:
        if question.concept_id is None:
            continue
        range_scores.setdefault(question.concept_id, []).append(attempt_evidence_score(attempt))
    for attempt, question in baseline_attempts:
        if question.concept_id is None:
            continue
        baseline_scores.setdefault(question.concept_id, []).append(attempt_evidence_score(attempt))

    state_by_concept = {state.concept_id: state for state in states}
    candidate_ids: set[UUID] = set(range_scores)
    candidate_ids.update(
        state.concept_id for state in states if state.status in {"review_due", "gap"}
    )
    if not candidate_ids:
        return []

    concept_rows = session.execute(
        select(KnowledgeConcept.id, KnowledgeConcept.title).where(
            KnowledgeConcept.id.in_(candidate_ids)
        )
    )
    title_by_concept: dict[UUID, str] = {concept_id: title for concept_id, title in concept_rows}

    improved: list[ReportConceptMoverRead] = []
    at_risk: list[ReportConceptMoverRead] = []
    for concept_id in candidate_ids:
        state = state_by_concept.get(concept_id)
        current_scores = range_scores.get(concept_id, [])
        previous_scores = baseline_scores.get(concept_id, [])
        if current_scores:
            mastery_after = sum(current_scores) / len(current_scores)
        elif state is not None:
            mastery_after = state.mastery_score
        else:
            mastery_after = 0.0

        if previous_scores:
            mastery_before = sum(previous_scores) / len(previous_scores)
        elif current_scores:
            mastery_before = mastery_after
        elif state is not None:
            mastery_before = state.mastery_score
        else:
            mastery_before = 0.0

        delta_percent = int(round((mastery_after - mastery_before) * 100))
        evidence_delta = len(current_scores)
        status = state.status if state is not None else ("in_progress" if current_scores else "unknown")
        next_review_date = (
            state.next_review_at.date().isoformat()
            if state is not None and state.next_review_at is not None
            else None
        )
        title = title_by_concept.get(concept_id, "Concept")

        if evidence_delta > 0 and delta_percent >= 5:
            improved.append(
                ReportConceptMoverRead(
                    concept_id=str(concept_id),
                    title=title,
                    movement_type="improved",
                    status=status,
                    mastery_before_percent=int(round(mastery_before * 100)),
                    mastery_after_percent=int(round(mastery_after * 100)),
                    mastery_delta_percent=delta_percent,
                    evidence_delta=evidence_delta,
                    next_review_date=next_review_date,
                    reason="Answer evidence improved in this range.",
                )
            )
            continue

        if status in {"review_due", "gap"} or delta_percent <= -3:
            reason = (
                "Concept is currently marked as gap/review due."
                if status in {"review_due", "gap"}
                else "Mastery trend moved down in this range."
            )
            at_risk.append(
                ReportConceptMoverRead(
                    concept_id=str(concept_id),
                    title=title,
                    movement_type="at_risk",
                    status=status,
                    mastery_before_percent=int(round(mastery_before * 100)),
                    mastery_after_percent=int(round(mastery_after * 100)),
                    mastery_delta_percent=delta_percent,
                    evidence_delta=evidence_delta,
                    next_review_date=next_review_date,
                    reason=reason,
                )
            )

    improved.sort(
        key=lambda item: (item.mastery_delta_percent, item.evidence_delta),
        reverse=True,
    )
    at_risk.sort(
        key=lambda item: (
            0 if item.status == "gap" else 1,
            item.mastery_delta_percent,
            item.next_review_date or "9999-12-31",
        )
    )
    return [*improved[:3], *at_risk[:3]]


def _weekly_timeline(
    *,
    session: Session,
    user: UserAccount,
    selected_start: date,
    selected_end: date,
    current_score: int,
    current_fixed_gaps: int,
    current_remaining_gaps: int,
    current_attempt_count: int,
    snapshot_table_available: bool,
) -> list[WeeklyTimelinePointRead]:
    anchor_week_start = selected_end - timedelta(days=selected_end.weekday())
    rows: list[WeeklyTimelinePointRead] = []
    for offset in range(3, -1, -1):
        week_start = anchor_week_start - timedelta(days=offset * 7)
        week_end = week_start + timedelta(days=6)
        snapshot = None
        if snapshot_table_available:
            try:
                snapshot = session.scalar(
                    select(WeeklyReportSnapshot).where(
                        WeeklyReportSnapshot.user_id == user.id,
                        WeeklyReportSnapshot.range_start == week_start,
                        WeeklyReportSnapshot.range_end == week_end,
                    )
                )
            except ProgrammingError:
                session.rollback()
                logger.warning(
                    "weekly_report_snapshots timeline lookup failed; using attempts-only timeline."
                )
                snapshot_table_available = False
        if snapshot is not None:
            rows.append(
                WeeklyTimelinePointRead(
                    label=f"W{week_start.isocalendar().week}",
                    range_start=week_start.isoformat(),
                    range_end=week_end.isoformat(),
                    score=snapshot.score,
                    fixed_gaps=snapshot.fixed_gaps,
                    remaining_gaps=snapshot.remaining_gaps,
                    attempt_count=snapshot.attempt_count,
                )
            )
            continue

        if week_start == selected_start and week_end == selected_end:
            score = current_score
            attempt_count = current_attempt_count
            fixed_gaps = current_fixed_gaps
            remaining_gaps = current_remaining_gaps
        else:
            range_start_at, range_end_at = _date_range_bounds(week_start, week_end)
            attempts = _assessment_attempt_rows(
                session,
                user=user,
                submitted_from=range_start_at,
                submitted_before=range_end_at,
            )
            attempt_count = len(attempts)
            score = _attempt_correct_percent(attempts) if attempts else 0
            fixed_gaps = 0
            remaining_gaps = 0

        rows.append(
            WeeklyTimelinePointRead(
                label=f"W{week_start.isocalendar().week}",
                range_start=week_start.isoformat(),
                range_end=week_end.isoformat(),
                score=score,
                fixed_gaps=fixed_gaps,
                remaining_gaps=remaining_gaps,
                attempt_count=attempt_count,
            )
        )
    return rows


def _weekly_narrative(
    *,
    concept_movers: list[ReportConceptMoverRead],
    remaining_gaps: int,
    recommendations: list[UpcomingRecommendationRead],
) -> WeeklyNarrativeRead:
    improved = [item for item in concept_movers if item.movement_type == "improved"]
    at_risk = [item for item in concept_movers if item.movement_type == "at_risk"]

    if improved:
        top = improved[0]
        improved_text = (
            f"{top.title} led improvement at {top.mastery_delta_percent:+d}% mastery change."
        )
    else:
        improved_text = "No strong concept jump yet. Keep building attempt evidence this week."

    if at_risk:
        top_risk = at_risk[0]
        stagnant_text = (
            f"{top_risk.title} remains at risk ({top_risk.status}) with "
            f"{top_risk.mastery_delta_percent:+d}% trend."
        )
    else:
        stagnant_text = f"{remaining_gaps} gaps remain and need spaced reinforcement."

    if recommendations:
        focus_text = recommendations[0].title
        if recommendations[0].due_label:
            focus_text = f"{focus_text} ({recommendations[0].due_label})"
    else:
        focus_text = "Start with review due concepts in the next 7 days."

    return WeeklyNarrativeRead(
        improved=improved_text,
        stagnant=stagnant_text,
        focus=focus_text,
    )


def _upsert_weekly_report_snapshot(
    *,
    session: Session,
    user: UserAccount,
    start: date,
    end: date,
    report: WeeklyReportResponse,
    attempt_count: int,
    active_days: int,
    overdue_reviews: int,
    new_gaps: int,
) -> None:
    try:
        snapshot = session.scalar(
            select(WeeklyReportSnapshot).where(
                WeeklyReportSnapshot.user_id == user.id,
                WeeklyReportSnapshot.range_start == start,
                WeeklyReportSnapshot.range_end == end,
            )
        )
        if snapshot is None:
            snapshot = WeeklyReportSnapshot(
                user_id=user.id,
                range_start=start,
                range_end=end,
            )
            session.add(snapshot)

        snapshot.status = report.status
        snapshot.source = report.source
        snapshot.score = report.score
        snapshot.attempt_count = attempt_count
        snapshot.active_days = active_days
        snapshot.fixed_gaps = report.fixed_gaps
        snapshot.remaining_gaps = report.remaining_gaps
        snapshot.overdue_reviews = overdue_reviews
        snapshot.new_gaps = new_gaps
        snapshot.paired_concept_count = report.paired_concept_count
        snapshot.payload_json = report.model_dump(mode="json")
    except ProgrammingError:
        session.rollback()
        logger.warning(
            "weekly_report_snapshots upsert failed; report returned without snapshot persistence."
        )


def _recent_concept_names(session: Session, *, user: UserAccount) -> list[str]:
    rows = session.execute(
        select(KnowledgeConcept.title)
        .join(LearnerConceptState, LearnerConceptState.concept_id == KnowledgeConcept.id)
        .where(LearnerConceptState.user_id == user.id)
        .order_by(LearnerConceptState.last_evaluated_at.desc().nullslast())
        .limit(3)
    )
    return [str(row[0]) for row in rows]


def _weekly_recommendations(
    *,
    session: Session,
    user: UserAccount,
    states: list[LearnerConceptState],
) -> list[UpcomingRecommendationRead]:
    state_rows = session.execute(
        select(LearnerConceptState, KnowledgeConcept)
        .join(KnowledgeConcept, LearnerConceptState.concept_id == KnowledgeConcept.id)
        .where(LearnerConceptState.user_id == user.id)
        .order_by(
            LearnerConceptState.next_review_at.asc().nullslast(),
            LearnerConceptState.mastery_score.asc(),
        )
        .limit(3)
    )
    recommendations: list[UpcomingRecommendationRead] = []
    today = datetime.now(UTC).date()
    for priority, (state, concept) in enumerate(state_rows, start=1):
        due_date = state.next_review_at.date() if state.next_review_at else today + timedelta(days=priority + 1)
        if state.status == "review_due" or due_date <= today + timedelta(days=2):
            action_type = "review"
            title = f"Review: {concept.title}"
            reason = "Next review is due soon based on spaced repetition state."
        elif state.mastery_score < 0.55:
            action_type = "practice"
            title = f"Practice: {concept.title}"
            reason = "Mastery is still low, so retrieval practice is the highest-leverage action."
        elif state.mastery_score < 0.75:
            action_type = "deepen"
            title = f"Deepen: {concept.title}"
            reason = "Strengthen transfer before the next post-test."
        else:
            action_type = "continue_learning"
            title = "Continue: next module"
            reason = f"{concept.title} is stable enough to continue the path."
        recommendations.append(
            UpcomingRecommendationRead(
                title=title,
                action_type=action_type,
                reason=reason,
                due_date=due_date.isoformat(),
                due_label=_due_label(due_date, today=today),
            )
        )
    if recommendations:
        return recommendations

    return []


def _due_label(due_date: date, *, today: date, language: str = "en") -> str:
    day_delta = (due_date - today).days
    if _is_indonesian(language):
        if day_delta < 0:
            return "Terlambat"
        if day_delta == 0:
            return "Jatuh tempo hari ini"
        if day_delta == 1:
            return "Jatuh tempo besok"
        return f"Jatuh tempo dalam {day_delta} hari"
    if day_delta < 0:
        return "Overdue"
    if day_delta == 0:
        return "Due today"
    if day_delta == 1:
        return "Due tomorrow"
    return f"Due in {day_delta} days"


def _answered_question_ids(session: Session, *, assessment_id: UUID) -> set[UUID]:
    return set(
        session.scalars(
            select(AssessmentAttempt.question_id).where(
                AssessmentAttempt.session_id == assessment_id
            )
        )
    )


def _language_for_user(user: UserAccount) -> str:
    return preferred_language_code(user)


def _is_indonesian(language: str) -> bool:
    return normalize_language_code(language) == "id"


def _daily_copy(language: str, *, id_text: str, en_text: str) -> str:
    return id_text if _is_indonesian(language) else en_text


def _daily_session_title(language: str) -> str:
    return _daily_copy(
        language,
        id_text="Evaluasi Harian",
        en_text="Daily Evaluation",
    )


def _daily_due_summary(due_count: int, *, language: str) -> str:
    return _daily_copy(
        language,
        id_text=f"{due_count} item siap direview",
        en_text=f"{due_count} items ready for review",
    )


def _daily_progress_label(current: int, total: int, *, language: str) -> str:
    if total <= 0:
        return _daily_copy(language, id_text="0 dari 0", en_text="0 of 0")
    return _daily_copy(
        language,
        id_text=f"{current} dari {total}",
        en_text=f"{current} of {total}",
    )


def _daily_result_summary_label(*, reviewed_count: int, language: str) -> str:
    if reviewed_count:
        return _daily_copy(
            language,
            id_text="Memorimu makin kuat.",
            en_text="You've strengthened your memory.",
        )
    return _daily_copy(
        language,
        id_text="Belum ada bukti review.",
        en_text="No review evidence yet.",
    )


def _daily_next_review_label(interval_days: int, *, language: str) -> str:
    if _is_indonesian(language):
        if interval_days <= 1:
            return "Tinjau besok"
        return f"Tinjau dalam {interval_days} hari"
    if interval_days <= 1:
        return "Review tomorrow"
    return f"Review in {interval_days} days"


def _daily_action_title(
    action_type: str,
    *,
    concept_title: str | None,
    language: str,
) -> str:
    if action_type == "review":
        if concept_title:
            prefix = "Tinjau" if _is_indonesian(language) else "Review"
            return f"{prefix}: {concept_title}"
        return _daily_copy(
            language,
            id_text="Tinjau konsep 2 minggu",
            en_text="Review 2-week concepts",
        )
    if action_type == "practice":
        if concept_title:
            prefix = "Latihan" if _is_indonesian(language) else "Practice"
            return f"{prefix}: {concept_title}"
        return _daily_copy(
            language,
            id_text="Latihan 5 soal lagi",
            en_text="Practice 5 more questions",
        )
    return _daily_copy(
        language,
        id_text="Lanjutkan belajar",
        en_text="Continue learning",
    )


def _daily_source(assessment: AssessmentSession) -> str:
    policy = assessment.metadata_json.get("policy")
    if policy == "personalized_daily_ebbinghaus_v1":
        return "question_bank_personalized_daily_ebbinghaus_v1"
    if policy == "personalized_daily_v2":
        return "question_bank_personalized_daily_v2"
    if policy == "spaced_repetition_mvp":
        return "seeded_spaced_repetition_mvp"
    return str(assessment.metadata_json.get("generation") or "deterministic_mvp")


def _daily_review_policy_basis(assessment: AssessmentSession, *, language: str = "en") -> str:
    policy = assessment.metadata_json.get("policy")
    if policy == "personalized_daily_ebbinghaus_v1":
        return _daily_copy(
            language,
            id_text="selector Ebbinghaus memakai review lama/jatuh tempo, lalu fallback ke goal jika data timestamp belum cukup",
            en_text="Ebbinghaus selector using old or due review items, then goal fallback when timestamp data is insufficient",
        )
    if policy == "personalized_daily_v2":
        return _daily_copy(
            language,
            id_text="selector bank soal memakai review jatuh tempo, mastery lemah, dan modul aktif",
            en_text="question bank selector using due review, weak mastery, and current module state",
        )
    return _daily_copy(
        language,
        id_text="item question bank dengan assessment_types daily_quiz",
        en_text="question bank items with assessment_types daily_quiz",
    )


def _retention_forecast(*, completed_count: int, language: str = "en") -> RetentionForecastRead:
    lift = min(12, completed_count * 2)
    raw_points = (
        [
            ("Hari ini", 100, False),
            ("Hari 1", 70 + lift, False),
            ("Hari 2", 52 + lift, False),
            ("Hari 7", 38 + lift, False),
            ("Hari 14", 25 + lift, True),
            ("Hari 30", 17 + lift, True),
        ]
        if _is_indonesian(language)
        else [
            ("Today", 100, False),
            ("Day 1", 70 + lift, False),
            ("Day 2", 52 + lift, False),
            ("Day 7", 38 + lift, False),
            ("Day 14", 25 + lift, True),
            ("Day 30", 17 + lift, True),
        ]
    )
    return RetentionForecastRead(
        title=_daily_copy(
            language,
            id_text="Perkiraan retensimu",
            en_text="Your retention forecast",
        ),
        basis=_daily_copy(
            language,
            id_text="Berdasarkan MVP kurva lupa Ebbinghaus.",
            en_text="Based on the Ebbinghaus forgetting curve MVP.",
        ),
        points=[
            RetentionForecastPointRead(
                label=label,
                retention_percent=min(100, percent),
                projected=projected,
            )
            for label, percent, projected in raw_points
        ],
    )


def _daily_recommendation_callout(
    *,
    due_count: int,
    language: str = "en",
) -> RecommendationCalloutRead:
    return RecommendationCalloutRead(
        title=_daily_copy(language, id_text="Review sekarang", en_text="Review now"),
        message=(
            _daily_copy(
                language,
                id_text="Terus review untuk menaikkan kurva dan memperkuat retensi jangka panjang.",
                en_text="Keep reviewing to move the curve up and improve long-term retention.",
            )
            if due_count
            else _daily_copy(
                language,
                id_text="Kamu sudah menyelesaikan antrian review hari ini.",
                en_text="You are caught up for today's review queue.",
            )
        ),
        impact_label=_daily_copy(
            language,
            id_text="Dampak tinggi" if due_count else "Terjaga",
            en_text="High impact" if due_count else "Maintained",
        ),
        action_label=_daily_copy(
            language,
            id_text="Review sekarang" if due_count else "Kembali ke beranda",
            en_text="Review now" if due_count else "Back to home",
        ),
    )


def _daily_session_result_summary(
    session: Session,
    *,
    user: UserAccount,
    assessment: AssessmentSession,
    language: str,
) -> _DailySessionResultSummary:
    latest_attempt_by_question = _latest_attempts_by_assessment_question(
        session,
        assessment=assessment,
    )

    question_results: list[_DailyQuestionResult] = []
    for question in sorted(assessment.questions, key=lambda item: item.sort_order):
        attempt = latest_attempt_by_question.get(question.id)
        if attempt is None:
            continue
        question_results.append(
            _DailyQuestionResult(
                question=question,
                attempt=attempt,
                is_correct=attempt_answer_score(attempt) >= 1.0,
            )
        )

    reviewed_count = len(question_results)
    correct_count = sum(1 for result in question_results if result.is_correct)
    review_again_count = max(0, reviewed_count - correct_count)
    score_percent = int(round((correct_count / reviewed_count) * 100)) if reviewed_count else 0

    return _DailySessionResultSummary(
        question_results=question_results,
        reviewed_count=reviewed_count,
        correct_count=correct_count,
        review_again_count=review_again_count,
        score_percent=score_percent,
        reviewed_concepts=_daily_reviewed_concept_summaries(
            session,
            user=user,
            question_results=question_results,
            language=language,
        ),
    )


def _latest_attempts_by_assessment_question(
    session: Session,
    *,
    assessment: AssessmentSession,
) -> dict[UUID, AssessmentAttempt]:
    question_ids = {question.id for question in assessment.questions}
    if not question_ids:
        return {}

    attempts = list(
        session.scalars(
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.session_id == assessment.id,
                AssessmentAttempt.question_id.in_(question_ids),
            )
            .order_by(AssessmentAttempt.submitted_at, AssessmentAttempt.id)
        )
    )
    latest_attempt_by_question: dict[UUID, AssessmentAttempt] = {}
    for attempt in attempts:
        latest_attempt_by_question[attempt.question_id] = attempt
    return latest_attempt_by_question


def _daily_reviewed_concept_summaries(
    session: Session,
    *,
    user: UserAccount,
    question_results: list[_DailyQuestionResult],
    language: str,
) -> list[_DailyConceptSummary]:
    concept_ids = {
        result.question.concept_id
        for result in question_results
        if result.question.concept_id is not None
    }
    concepts_by_id: dict[UUID, KnowledgeConcept] = {}
    state_by_concept: dict[UUID, LearnerConceptState] = {}
    if concept_ids:
        concepts_by_id = {
            concept.id: concept
            for concept in session.scalars(
                select(KnowledgeConcept).where(KnowledgeConcept.id.in_(concept_ids))
            )
        }
        state_by_concept = {
            state.concept_id: state
            for state in session.scalars(
                select(LearnerConceptState).where(
                    LearnerConceptState.user_id == user.id,
                    LearnerConceptState.concept_id.in_(concept_ids),
                )
            )
        }

    grouped: dict[str, dict[str, Any]] = {}
    for result in question_results:
        question = result.question
        key = (
            f"concept:{question.concept_id}"
            if question.concept_id
            else f"topic:{str(question.topic or question.id).strip().lower()}"
        )
        bucket = grouped.setdefault(
            key,
            {
                "concept_id": question.concept_id,
                "title": _daily_concept_title(
                    question,
                    concepts_by_id.get(question.concept_id) if question.concept_id else None,
                    language=language,
                ),
                "sort_order": question.sort_order,
                "attempted_count": 0,
                "correct_count": 0,
            },
        )
        bucket["sort_order"] = min(int(bucket["sort_order"]), question.sort_order)
        bucket["attempted_count"] = int(bucket["attempted_count"]) + 1
        bucket["correct_count"] = int(bucket["correct_count"]) + (1 if result.is_correct else 0)

    summaries: list[_DailyConceptSummary] = []
    for bucket in sorted(grouped.values(), key=lambda item: int(item["sort_order"])):
        concept_id = bucket["concept_id"]
        attempted_count = max(1, int(bucket["attempted_count"]))
        correct_count = int(bucket["correct_count"])
        score_percent = int(round((correct_count / attempted_count) * 100))
        state = state_by_concept.get(concept_id) if concept_id else None
        mastery_score = (
            float(state.mastery_score)
            if state is not None
            else float(score_percent / 100)
        )
        status_key = _concept_status_key(
            attempted_count=attempted_count,
            correct_count=correct_count,
            mastery_score=mastery_score,
        )
        summaries.append(
            _DailyConceptSummary(
                concept_id=concept_id,
                title=str(bucket["title"]),
                status_key=status_key,
                status_label=_concept_status_label(status_key, language=language),
                mastery_score=round(mastery_score, 2),
                attempted_count=attempted_count,
                correct_count=correct_count,
                score_percent=score_percent,
            )
        )
    return summaries


def _daily_concept_title(
    question: AssessmentQuestion,
    concept: KnowledgeConcept | None,
    *,
    language: str,
) -> str:
    if concept is not None:
        return _localized_topic(concept.title, concept, language=language)
    topic = (question.topic or "").strip()
    return topic or question.prompt


def _concept_status_key(
    *,
    attempted_count: int,
    correct_count: int,
    mastery_score: float,
) -> str:
    if correct_count < attempted_count:
        return "review"
    if mastery_score >= 0.78:
        return "strong"
    return "good"


def _concept_status_label(status_key: str, *, language: str) -> str:
    if _is_indonesian(language):
        return {
            "review": "Tinjau",
            "strong": "Kuat",
            "good": "Bagus",
        }.get(status_key, "Tinjau")
    return {
        "review": "Review",
        "strong": "Strong",
        "good": "Good",
    }.get(status_key, "Review")


def _retention_lift_percent(correct_count: int, review_again_count: int) -> int:
    return max(0, min(40, 12 + (correct_count * 5) - (review_again_count * 2)))


def _daily_next_actions(
    *,
    reviewed_concepts: list[_DailyConceptSummary],
    review_again_count: int,
    due_date: date,
    language: str,
) -> list[RecommendedNextActionRead]:
    review_concept = next(
        (concept for concept in reviewed_concepts if concept.status_key == "review"),
        reviewed_concepts[0] if reviewed_concepts else None,
    )
    practice_concept = next(
        (
            concept
            for concept in reviewed_concepts
            if concept.status_key != "review" and concept.mastery_score < 0.75
        ),
        review_concept,
    )
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    return [
        RecommendedNextActionRead(
            title=_daily_action_title(
                "review",
                concept_title=review_concept.title if review_concept else None,
                language=language,
            ),
            action_type="review",
            reason=_daily_copy(
                language,
                id_text=(
                    "Kamu melewatkan konsep ini di evaluasi hari ini."
                    if review_again_count
                    else "Fokus ke penguatan memori yang paling berdampak."
                ),
                en_text=(
                    "You missed this concept in today's evaluation."
                    if review_again_count
                    else "Focus on high-impact memory reinforcement."
                ),
            ),
            due_date=due_date.isoformat(),
            due_label=_due_label(due_date, today=datetime.now(UTC).date(), language=language),
            priority=1,
        ),
        RecommendedNextActionRead(
            title=_daily_action_title(
                "practice",
                concept_title=(
                    practice_concept.title
                    if practice_concept and practice_concept.mastery_score < 0.75
                    else None
                ),
                language=language,
            ),
            action_type="practice",
            reason=_daily_copy(
                language,
                id_text="Kuatkan lagi pemahamanmu dengan latihan retrieval singkat.",
                en_text="Retighten your understanding with short retrieval practice.",
            ),
            due_date=tomorrow.isoformat(),
            due_label=_due_label(tomorrow, today=datetime.now(UTC).date(), language=language),
            priority=2,
        ),
        RecommendedNextActionRead(
            title=_daily_action_title("continue_learning", concept_title=None, language=language),
            action_type="continue_learning",
            reason=_daily_copy(
                language,
                id_text="Lanjutkan jalur belajarmu setelah review selesai.",
                en_text="Go to your learning path when review is complete.",
            ),
            due_date=None,
            due_label=None,
            priority=3,
        ),
    ]


def _display_name_for_user(user: UserAccount) -> str:
    if user.learner_profile and user.learner_profile.full_name.strip():
        return user.learner_profile.full_name.strip()
    return user.display_name or "Learner"


def _first_name(display_name: str) -> str:
    parts = display_name.strip().split()
    return parts[0] if parts else "Learner"


def _streak_days(session: Session, *, user: UserAccount) -> int:
    active_days = session.scalar(
        select(func.count(func.distinct(func.date(AssessmentAttempt.submitted_at))))
        .join(AssessmentSession, AssessmentAttempt.session_id == AssessmentSession.id)
        .where(AssessmentSession.user_id == user.id)
    )
    return int(active_days or 0)


def _duration_label(seconds: int) -> str:
    minutes, remainder = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{remainder:02d}"


def _current_week_label() -> str:
    start, end = _current_week_range()
    return _format_week_label(start, end)


def _current_week_range() -> tuple[date, date]:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start, end


def _format_week_label(start: date, end: date) -> str:
    return f"{start.strftime('%b')} {start.day} - {end.strftime('%b')} {end.day}, {end.year}"


def _create_assessment_session(
    session: Session,
    *,
    user: UserAccount,
    learning_goal: LearningGoal | None,
    track: LearningTrack | None,
    session_type: str,
    title: str,
    templates: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> AssessmentSession:
    assessment = AssessmentSession(
        user_id=user.id,
        learning_goal_id=learning_goal.id if learning_goal else None,
        track_id=track.id if track else None,
        session_type=session_type,
        title=title,
        status="active",
        metadata_json=metadata or {"generation": "deterministic_seed"},
    )
    session.add(assessment)
    session.flush()

    for index, template in enumerate(templates, start=1):
        concept = _find_concept_by_hints(session, template["concept_hints"])
        question = AssessmentQuestion(
            session_id=assessment.id,
            concept_id=concept.id if concept else None,
            step_label=f"{index} / {len(templates)}"
            if session_type == "pretest"
            else "Daily Evals",
            topic=str(template["topic"]),
            prompt=str(template["prompt"]),
            helper_text=str(template["helper"]),
            difficulty_label="Medium",
            sort_order=index,
            metadata_json={
                "seed_source": f"{session_type}_template",
                "correct_option_key": template["correct"],
            },
        )
        session.add(question)
        session.flush()

        for option_index, (key, text) in enumerate(template["options"], start=1):
            session.add(
                AssessmentOption(
                    question_id=question.id,
                    option_key=key,
                    label=key,
                    text=text,
                    is_correct=key == template["correct"],
                    sort_order=option_index,
                )
            )
    session.flush()
    return assessment


def _create_daily_assessment_from_bank(
    session: Session,
    *,
    user: UserAccount,
    review_date: str,
    learner_step: LearnerStep,
    selected_questions: list[SelectedQuestion],
    refresh_reason: str | None = None,
) -> AssessmentSession:
    metadata = {
        "review_date": review_date,
        "policy": "personalized_daily_ebbinghaus_v1",
        "selector_version": DAILY_SELECTOR_VERSION,
        "selection_strategy": "ebbinghaus_review_queue",
        "timestamp_sufficiency": _daily_timestamp_sufficiency(selected_questions),
        "fallback_path": _daily_fallback_path(selected_questions),
        "track_resolution_strategy": "latest_non_completed_track_updated_at_desc",
        "resolved_at": datetime.now(UTC).isoformat(),
        "selected_subject_code": learner_step.subject.code,
        "education_level": learner_step.education_level,
        "preferred_language": learner_step.preferred_language,
        "active_track_id": str(learner_step.active_track_id)
        if learner_step.active_track_id
        else None,
        "active_module_id": str(learner_step.active_module_id)
        if learner_step.active_module_id
        else None,
        "active_concept_id": str(learner_step.active_concept_id)
        if learner_step.active_concept_id
        else None,
        "selection_slots": [
            {
                "slot": selected.slot,
                "reason": selected.reason,
                "question_bank_external_id": selected.item.external_id,
                "concept_code": selected.item.concept_code,
                "assessment_types": selected.item.assessment_types_json,
                **selected.metadata_json,
            }
            for selected in selected_questions
        ],
    }
    if refresh_reason:
        metadata["refresh_reason"] = refresh_reason
    assessment = AssessmentSession(
        user_id=user.id,
        learning_goal_id=None,
        track_id=learner_step.active_track_id,
        session_type="daily_evaluation",
        title="Daily Evaluation",
        status="active",
        metadata_json=metadata,
    )
    session.add(assessment)
    session.flush()

    total = len(selected_questions)
    for index, selected in enumerate(selected_questions, start=1):
        item = selected.item
        question = AssessmentQuestion(
            session_id=assessment.id,
            concept_id=item.concept_id,
            step_label=f"{index} / {total}",
            topic=item.concept_title or item.concept_code or item.subject_code,
            prompt=item.prompt,
            helper_text=item.helper_text,
            difficulty_label=item.difficulty.title(),
            sort_order=index,
            metadata_json={
                "source": "question_bank",
                "question_bank_item_id": str(item.id),
                "question_bank_external_id": item.external_id,
                "question_bank_language": item.language,
                "assessment_types": item.assessment_types_json,
                "concept_code": item.concept_code,
                "selection_slot": selected.slot,
                "selection_reason": selected.reason,
                "selector_version": DAILY_SELECTOR_VERSION,
                **selected.metadata_json,
                "correct_option_key": item.answer_key,
            },
        )
        session.add(question)
        session.flush()
        for option_index, option in enumerate(item.options, start=1):
            session.add(
                AssessmentOption(
                    question_id=question.id,
                    option_key=option.option_key,
                    label=option.label,
                    text=option.text,
                    is_correct=option.is_correct,
                    sort_order=option_index,
                )
            )
    session.flush()
    return assessment


def _daily_timestamp_sufficiency(selected_questions: list[SelectedQuestion]) -> str:
    values = {
        str(selected.metadata_json.get("timestamp_sufficiency"))
        for selected in selected_questions
    }
    if values == {"sufficient"}:
        return "sufficient"
    if "sufficient" in values or "partial" in values:
        return "partial"
    return "insufficient"


def _daily_fallback_path(selected_questions: list[SelectedQuestion]) -> str:
    fallback_order = ["current_goal", "learning_goal", "subject_level"]
    seen = {
        str(selected.metadata_json.get("fallback_path"))
        for selected in selected_questions
        if selected.metadata_json.get("fallback_path")
    }
    for fallback in fallback_order:
        if fallback in seen:
            return fallback
    return "none"


def _create_track(
    session: Session,
    *,
    user: UserAccount,
    goal: LearningGoal,
    subject: Subject,
    concept: KnowledgeConcept | None,
) -> LearningTrack:
    language = _language_for_user(user)
    topic = _localized_topic(goal.normalized_topic, concept, language=language)
    title = (
        f"Jalur {topic}"
        if language == "id"
        else f"{topic} path"
    )
    track = LearningTrack(
        user_id=user.id,
        learning_goal_id=goal.id,
        title=title,
        subtitle=(
            f"{subject.name} | jalur adaptif mulai dari prasyarat"
            if language == "id"
            else f"{subject.name} | prerequisite-first adaptive path"
        ),
        status="pretest",
        progress_percent=0,
        metadata_json={"generation": "deterministic_seed"},
    )
    session.add(track)
    session.flush()

    modules = _module_templates(goal.normalized_topic, concept, language=language)
    for index, module in enumerate(modules, start=1):
        session.add(
            TrackModule(
                track_id=track.id,
                concept_id=concept.id if concept and index == 2 else None,
                title=module["title"],
                description=module["description"],
                estimated_minutes=module["minutes"],
                difficulty_label=module["difficulty"],
                sort_order=index,
                status="ready" if index == 1 else "locked",
                metadata_json={"seed_source": "learning_goal_track"},
            )
        )
    session.flush()
    return track


def _module_templates(
    normalized_topic: str,
    concept: KnowledgeConcept | None,
    *,
    language: str,
) -> list[dict[str, Any]]:
    target = _localized_topic(normalized_topic, concept, language=language)
    if language == "id":
        return [
            {
                "title": "Cek prasyarat",
                "description": "Perbaiki fondasi yang terdeteksi dari pretest sebelum masuk ke topik utama.",
                "minutes": 8,
                "difficulty": "Mudah",
            },
            {
                "title": target,
                "description": f"Pelajari {target} lewat chat, bukti kanvas, dan cek singkat.",
                "minutes": 14,
                "difficulty": "Sedang",
            },
            {
                "title": "Penerapan dan review",
                "description": "Terapkan konsepnya, lalu jadwalkan untuk pengulangan berspasi.",
                "minutes": 10,
                "difficulty": "Sedang",
            },
        ]

    return [
        {
            "title": "Prerequisite checkpoint",
            "description": "Repair the foundation detected by the pretest before starting the main topic.",
            "minutes": 8,
            "difficulty": "Easy",
        },
        {
            "title": target,
            "description": f"Learn {target} with chat, canvas evidence, and short checks.",
            "minutes": 14,
            "difficulty": "Medium",
        },
        {
            "title": "Application and review",
            "description": "Apply the concept, then schedule it for spaced repetition.",
            "minutes": 10,
            "difficulty": "Medium",
        },
    ]


def _localized_topic(
    normalized_topic: str,
    concept: KnowledgeConcept | None,
    *,
    language: str,
) -> str:
    if concept is None:
        return (
            normalized_topic
            if language == "id"
            else translate_curriculum_label_to_english(normalized_topic)
        )
    if language == "id":
        return concept.title
    metadata = concept.metadata_json or {}
    english_title = str(metadata.get("en_title") or metadata.get("label_en") or "").strip()
    return english_title or translate_curriculum_label_to_english(concept.title)


def _resolve_subject(
    session: Session,
    *,
    subject_code: str | None,
    user: UserAccount,
) -> Subject:
    candidates = []
    if subject_code:
        candidates.append(subject_code)
    profile = user.learner_profile
    if profile:
        candidates.extend(profile.selected_subjects)
    candidates.extend(["matematika", "math"])

    for candidate in candidates:
        normalized = canonical_subject_code(candidate)
        subject = session.scalar(
            select(Subject).where(Subject.code == normalized, Subject.is_active.is_(True))
        )
        if subject is not None:
            return subject

    subject = session.scalar(select(Subject).where(Subject.is_active.is_(True)))
    if subject is None:
        raise ValueError("Curriculum seed is empty.")
    return subject


def _pick_concept(
    session: Session,
    *,
    subject: Subject,
    raw_topic: str,
) -> KnowledgeConcept | None:
    topic = raw_topic.lower()
    concepts = list(
        session.scalars(
            select(KnowledgeConcept)
            .where(KnowledgeConcept.subject_id == subject.id)
            .order_by(KnowledgeConcept.display_order, KnowledgeConcept.title)
        )
    )
    if not concepts:
        return None
    for concept in concepts:
        haystack = f"{concept.code} {concept.title}".lower()
        if any(token in haystack for token in topic.replace("-", " ").split()):
            return concept
    for hint in ("intuitive_limits", "derivative_definition", "km_d_matematika_bilangan_rasional"):
        for concept in concepts:
            if concept.code == hint:
                return concept
    return concepts[0]


def _find_concept_by_hints(
    session: Session,
    hints: list[str],
) -> KnowledgeConcept | None:
    for hint in hints:
        concept = session.scalar(select(KnowledgeConcept).where(KnowledgeConcept.code == hint))
        if concept is not None:
            return concept
    return session.scalar(select(KnowledgeConcept).order_by(KnowledgeConcept.display_order))


def _publish_media_job_to_queue(*, job_id: UUID) -> bool:
    try:
        adapter = build_media_job_queue_adapter()
        adapter.enqueue(job_id=job_id)
        return True
    except Exception:
        logger.exception("Failed to enqueue media job %s to queue backend.", job_id)
        return False


def _load_job_with_artifact(
    session: Session,
    *,
    job_id: UUID,
) -> tuple[MediaJob, MediaArtifact] | None:
    return session.execute(
        select(MediaJob, MediaArtifact)
        .join(MediaArtifact, MediaArtifact.id == MediaJob.artifact_id)
        .where(MediaJob.id == job_id)
    ).first()


def _initial_render_meta(
    *,
    validation_result: Any | None,
    validation_error: dict[str, Any] | None,
) -> dict[str, Any]:
    render_meta: dict[str, Any] = {}
    if validation_result is not None:
        render_meta.update(
            {
                "template_path": validation_result.template_path,
                "scene_class": validation_result.scene_class,
                "schema_id": validation_result.schema_id,
                "render_engine": validation_result.render_engine,
                "engine_family": validation_result.engine_family,
                "runtime": validation_result.runtime,
                "resolved_from": validation_result.resolved_from,
                "used_alias": validation_result.used_alias,
            }
        )
    if validation_error is not None:
        render_meta["error_details"] = validation_error
        render_meta["error_code"] = validation_error.get("code")
    return render_meta


def _mark_job_processing(
    session: Session,
    *,
    job: MediaJob,
    artifact: MediaArtifact,
) -> None:
    if job.status == "processing":
        return
    if job.status != "queued":
        raise ValueError(f"Job {job.id} is not claimable from status '{job.status}'.")
    job.status = "processing"
    job.progress = max(5, int(job.progress or 0))
    job.message = "Worker claimed job and started processing."
    job.error = None
    job.attempt = int(job.attempt or 0) + 1
    if job.started_at is None:
        job.started_at = datetime.now(UTC)
    artifact.status = "processing"
    _sync_artifact_job_state(
        artifact=artifact,
        job=job,
        error_message=None,
        error_details=None,
        error_code=None,
    )
    session.commit()


def _update_job_progress(
    session: Session,
    *,
    job: MediaJob,
    artifact: MediaArtifact,
    progress: int,
    message: str,
) -> None:
    job.status = "processing"
    job.progress = max(0, min(100, int(progress)))
    job.message = message
    artifact.status = "processing"
    _sync_artifact_job_state(
        artifact=artifact,
        job=job,
        error_message=None,
        error_details=None,
        error_code=None,
    )
    session.commit()


def _mark_job_ready(
    session: Session,
    *,
    job: MediaJob,
    artifact: MediaArtifact,
    final_message: str | None = None,
) -> None:
    job.status = "ready"
    job.progress = 100
    job.message = final_message or "Render lifecycle finished. Artifact is ready."
    job.error = None
    job.finished_at = datetime.now(UTC)
    artifact.status = "ready"
    _sync_artifact_job_state(
        artifact=artifact,
        job=job,
        error_message=None,
        error_details=None,
        error_code=None,
    )
    session.commit()


def _mark_job_failed(
    session: Session,
    *,
    job: MediaJob,
    artifact: MediaArtifact,
    error_message: str,
    error_code: str | None = None,
    error_details: dict[str, Any] | None = None,
) -> None:
    cleaned_error = (error_message or "Unknown media worker error.").strip()[:2000]
    job.status = "failed"
    job.message = "Render lifecycle failed."
    job.error = cleaned_error
    job.finished_at = datetime.now(UTC)
    artifact.status = "failed"
    _sync_artifact_job_state(
        artifact=artifact,
        job=job,
        error_message=cleaned_error,
        error_details=error_details,
        error_code=error_code,
    )
    session.commit()


def _validate_job_payload(
    *,
    session: Session,
    job: MediaJob,
    artifact: MediaArtifact,
) -> None:
    if not artifact.template_id.strip():
        raise ValueError(f"Job {job.id} has empty template_id.")
    if not isinstance(artifact.spec_json, dict):
        raise ValueError(f"Job {job.id} has invalid spec_json payload.")
    validation_result = validate_template_spec(
        template_id=artifact.template_id,
        spec_json=artifact.spec_json,
    )
    artifact.template_id = validation_result.template_id
    artifact.spec_json = validation_result.normalized_spec
    render_meta = dict(artifact.render_meta_json or {})
    render_meta.update(
        {
            "template_path": validation_result.template_path,
            "scene_class": validation_result.scene_class,
            "schema_id": validation_result.schema_id,
            "render_engine": validation_result.render_engine,
            "engine_family": validation_result.engine_family,
            "runtime": validation_result.runtime,
            "resolved_from": validation_result.resolved_from,
            "used_alias": validation_result.used_alias,
            "language": artifact.language,
        }
    )
    artifact.render_meta_json = render_meta
    session.flush()


def _render_artifact_with_retry(
    session: Session,
    *,
    job: MediaJob,
    artifact: MediaArtifact,
    max_attempts: int,
    timeout_seconds: int,
    settings: Any,
) -> tuple[RenderOutput, int]:
    attempts_limit = max(1, int(max_attempts))
    render_meta = dict(artifact.render_meta_json or {})
    template_path = str(render_meta.get("template_path", "")).strip()
    scene_class = str(render_meta.get("scene_class", "GeneratedTemplate")).strip()
    render_engine = str(render_meta.get("render_engine", "")).strip().lower() or (
        "remotion" if artifact.template_id.startswith("remotion.") else "manim"
    )
    runtime = render_meta.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    if not template_path:
        raise RenderEngineError(
            code="render_error",
            message="Render metadata does not contain template_path.",
            details={"job_id": str(job.id), "template_id": artifact.template_id},
        )

    last_error: RenderEngineError | None = None
    for attempt in range(1, attempts_limit + 1):
        _update_job_progress(
            session,
            job=job,
            artifact=artifact,
            progress=60,
            message=(
                f"Running {render_engine} render attempt "
                f"{attempt}/{attempts_limit}."
            ),
        )
        try:
            output = render_template_scene(
                job_id=job.id,
                template_path=template_path,
                scene_class=scene_class,
                spec_json=artifact.spec_json,
                language=artifact.language,
                quality_profile=artifact.quality_profile,
                timeout_seconds=timeout_seconds,
                settings=settings,
                template_id=artifact.template_id,
                render_engine=render_engine,
                runtime=runtime,
            )
            return output, attempt
        except RenderEngineError as exc:
            last_error = exc
            if attempt < attempts_limit:
                _update_job_progress(
                    session,
                    job=job,
                    artifact=artifact,
                    progress=68,
                    message=f"Render attempt {attempt} failed. Retrying attempt {attempt + 1}/{attempts_limit}.",
                )
                continue
            break

    assert last_error is not None
    raise last_error


def _postprocess_output_with_retry(
    *,
    session: Session,
    job: MediaJob,
    artifact: MediaArtifact,
    render_output: RenderOutput,
    settings: Any,
) -> tuple[MediaPostprocessOutput, int]:
    attempts_limit = max(1, int(settings.media_postprocess_max_attempts))
    last_error: MediaPostprocessError | None = None

    for attempt in range(1, attempts_limit + 1):
        _update_job_progress(
            session,
            job=job,
            artifact=artifact,
            progress=92,
            message=f"Running media post-process attempt {attempt}/{attempts_limit}.",
        )
        try:
            output = postprocess_render_output(
                job_id=job.id,
                artifact=artifact,
                render_output=render_output,
                settings=settings,
            )
            return output, attempt
        except MediaPostprocessError as exc:
            last_error = exc
            if not _should_retry_postprocess_error(exc.code):
                break
            if attempt < attempts_limit:
                _update_job_progress(
                    session,
                    job=job,
                    artifact=artifact,
                    progress=94,
                    message=(
                        f"Post-process attempt {attempt} failed ({exc.code}). "
                        f"Retrying attempt {attempt + 1}/{attempts_limit}."
                    ),
                )
                continue
            break

    assert last_error is not None
    raise last_error


def _upload_media_files_with_retry(
    *,
    session: Session,
    job: MediaJob,
    artifact: MediaArtifact,
    postprocess_output: MediaPostprocessOutput,
    settings: Any,
) -> tuple[MediaStorageUploadOutput, int]:
    attempts_limit = max(1, int(settings.media_upload_max_attempts))
    last_error: MediaStorageError | None = None

    for attempt in range(1, attempts_limit + 1):
        _update_job_progress(
            session,
            job=job,
            artifact=artifact,
            progress=99,
            message=f"Uploading media files attempt {attempt}/{attempts_limit}.",
        )
        try:
            output = upload_media_artifact_files(
                job_id=job.id,
                artifact_id=artifact.id,
                local_video_path=postprocess_output.video_path,
                local_thumbnail_path=postprocess_output.thumbnail_path,
                settings=settings,
            )
            return output, attempt
        except MediaStorageError as exc:
            last_error = exc
            if attempt < attempts_limit:
                _update_job_progress(
                    session,
                    job=job,
                    artifact=artifact,
                    progress=99,
                    message=(
                        f"Upload attempt {attempt} failed ({exc.code}). "
                        f"Retrying attempt {attempt + 1}/{attempts_limit}."
                    ),
                )
                continue
            break

    assert last_error is not None
    raise last_error


def _should_retry_postprocess_error(error_code: str) -> bool:
    normalized = str(error_code or "").strip().lower()
    return normalized in {"ffmpeg_error", "tts_error"}


def _attach_render_output_to_artifact(
    session: Session,
    *,
    artifact: MediaArtifact,
    render_output: RenderOutput,
    attempts_used: int,
) -> None:
    render_meta = dict(artifact.render_meta_json or {})
    render_engine = str(render_meta.get("render_engine", "")).strip().lower() or (
        "remotion" if artifact.template_id.startswith("remotion.") else "manim"
    )
    render_meta.update(
        {
            "local_render_video_path": render_output.video_path,
            "relative_render_video_path": render_output.relative_video_path,
            "render_attempts_used": attempts_used,
            "render_completed_at": datetime.now(UTC).isoformat(),
            "render_engine": render_engine,
            "render_stdout_tail": render_output.stdout,
            "render_stderr_tail": render_output.stderr,
            "manim_stdout_tail": render_output.stdout if render_engine == "manim" else "",
            "manim_stderr_tail": render_output.stderr if render_engine == "manim" else "",
            "remotion_stdout_tail": render_output.stdout if render_engine == "remotion" else "",
            "remotion_stderr_tail": render_output.stderr if render_engine == "remotion" else "",
        }
    )
    artifact.render_meta_json = render_meta
    session.flush()


def _attach_postprocess_output_to_artifact(
    session: Session,
    *,
    artifact: MediaArtifact,
    output: MediaPostprocessOutput,
) -> None:
    artifact.duration_seconds = int(output.duration_seconds)
    artifact.transcript = output.transcript

    notes = list(artifact.notes_json or [])
    quality_message = str(output.quality_gate.get("message", "")).strip()
    quality_result = str(output.quality_gate.get("result", "")).strip().lower()
    if quality_result == "warning" and quality_message and quality_message not in notes:
        notes.append(quality_message)
    artifact.notes_json = notes

    metadata = dict(artifact.metadata_json or {})
    metadata["quality_gate"] = output.quality_gate
    artifact.metadata_json = metadata

    render_meta = dict(artifact.render_meta_json or {})
    render_meta.update(
        {
            "local_video_path": output.video_path,
            "relative_video_path": output.relative_video_path,
            "local_thumbnail_path": output.thumbnail_path,
            "relative_thumbnail_path": output.relative_thumbnail_path,
            "quality_gate": output.quality_gate,
            "tts": output.tts_meta,
            "ffmpeg": output.ffmpeg_meta,
        }
    )
    if output.audio_path:
        render_meta["voiceover_audio_path"] = output.audio_path
    else:
        render_meta.pop("voiceover_audio_path", None)
    if output.relative_audio_path:
        render_meta["relative_voiceover_audio_path"] = output.relative_audio_path
    else:
        render_meta.pop("relative_voiceover_audio_path", None)
    artifact.render_meta_json = render_meta
    session.flush()


def _attach_storage_output_to_artifact(
    *,
    session: Session,
    artifact: MediaArtifact,
    storage_output: MediaStorageUploadOutput,
) -> None:
    artifact.video_url = storage_output.video_url
    artifact.playback_url = storage_output.video_url
    artifact.thumbnail_url = storage_output.thumbnail_url

    metadata = dict(artifact.metadata_json or {})
    metadata["storage"] = {
        "backend": storage_output.storage_backend,
        "object_video_path": storage_output.object_video_path,
        "object_thumbnail_path": storage_output.object_thumbnail_path,
        "meta": storage_output.meta,
    }
    artifact.metadata_json = metadata

    render_meta = dict(artifact.render_meta_json or {})
    render_meta["storage_backend"] = storage_output.storage_backend
    render_meta["video_object_path"] = storage_output.object_video_path
    render_meta["thumbnail_object_path"] = storage_output.object_thumbnail_path
    artifact.render_meta_json = render_meta
    session.flush()


def _attach_worker_metrics_to_artifact(
    *,
    session: Session,
    artifact: MediaArtifact,
    metrics: dict[str, float],
) -> None:
    render_meta = dict(artifact.render_meta_json or {})
    render_meta["worker_metrics"] = {
        "render_seconds": metrics.get("render_seconds", 0.0),
        "postprocess_seconds": metrics.get("postprocess_seconds", 0.0),
        "upload_seconds": metrics.get("upload_seconds", 0.0),
        "total_seconds": metrics.get("total_seconds", 0.0),
    }
    artifact.render_meta_json = render_meta
    session.flush()


def _log_job_event(
    *,
    level: str,
    stage: str,
    message: str,
    context: dict[str, Any],
) -> None:
    payload = {"stage": stage, **context}
    if level == "error":
        logger.error("%s | context=%s", message, payload)
        return
    if level == "exception":
        logger.exception("%s | context=%s", message, payload)
        return
    logger.info("%s | context=%s", message, payload)


def _rollback_session_quietly(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        logger.exception("Failed to rollback session after worker error.")


def _sync_artifact_job_state(
    *,
    artifact: MediaArtifact,
    job: MediaJob,
    error_message: str | None,
    error_details: dict[str, Any] | None,
    error_code: str | None,
) -> None:
    metadata = dict(artifact.metadata_json or {})
    metadata.update(
        {
            "progress": max(0, min(100, int(job.progress or 0))),
            "job_state": job.status,
            "job_id": str(job.id),
        }
    )
    if error_message:
        metadata["error"] = error_message
        if error_code:
            metadata["error_code"] = error_code
    else:
        metadata.pop("error", None)
        metadata.pop("error_code", None)
    artifact.metadata_json = metadata

    render_meta = dict(artifact.render_meta_json or {})
    render_meta.update(
        {
            "last_job_message": job.message,
            "attempt": int(job.attempt or 0),
            "worker_status": job.status,
        }
    )
    if error_message:
        render_meta["error"] = error_message
        if error_code:
            render_meta["error_code"] = error_code
    else:
        render_meta.pop("error", None)
        render_meta.pop("error_code", None)
    if error_details:
        render_meta["error_details"] = error_details
    else:
        render_meta.pop("error_details", None)
    artifact.render_meta_json = render_meta


def _resolve_owned_workspace(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID | None,
) -> WorkspaceSession | None:
    if workspace_id is None:
        return None
    workspace = session.scalar(
        select(WorkspaceSession).where(
            WorkspaceSession.id == workspace_id,
            WorkspaceSession.user_id == user.id,
        )
    )
    if workspace is None:
        raise LookupError("Workspace session was not found.")
    return workspace


def _resolve_concept_id(
    session: Session,
    *,
    concept_id: UUID | None,
    workspace: WorkspaceSession | None,
) -> UUID | None:
    if concept_id is not None:
        concept = session.get(KnowledgeConcept, concept_id)
        if concept is None:
            raise LookupError("Concept was not found.")
        return concept.id
    if workspace is None:
        return None
    module = session.get(TrackModule, workspace.module_id)
    if module is None:
        return None
    return module.concept_id


def _queued_artifact_title(spec_json: dict[str, Any], template_id: str) -> str:
    raw_title = spec_json.get("title")
    if isinstance(raw_title, str) and raw_title.strip():
        return raw_title.strip()[:255]
    return f"Generated video ({template_id})"


def _queued_artifact_subtitle(spec_json: dict[str, Any]) -> str:
    raw_subtitle = spec_json.get("subtitle")
    if isinstance(raw_subtitle, str) and raw_subtitle.strip():
        return raw_subtitle.strip()[:255]
    return "Queued for Manim rendering"


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _coalesce_video_url(artifact: MediaArtifact) -> str | None:
    primary = _optional_text(artifact.video_url)
    if primary:
        return primary
    return _optional_text(artifact.playback_url)


def _normalize_short_label(value: str, *, fallback: str, max_length: int) -> str:
    normalized = value.strip().lower()
    if not normalized:
        return fallback
    return normalized[:max_length]


def _resolve_submission_targets(
    session: Session,
    *,
    user: UserAccount,
    assessment_session_id: UUID,
    question_id: str,
    option_id: str,
) -> tuple[AssessmentSession, AssessmentQuestion, AssessmentOption]:
    assessment = session.scalar(
        select(AssessmentSession)
        .where(AssessmentSession.id == assessment_session_id, AssessmentSession.user_id == user.id)
        .options(
            selectinload(AssessmentSession.questions).selectinload(AssessmentQuestion.options)
        )
    )
    if assessment is None:
        raise LookupError("Assessment session was not found.")

    question = _find_question(assessment.questions, question_id)
    if question is None:
        raise LookupError("Assessment question was not found.")
    option = _find_option(question.options, option_id)
    if option is None:
        raise LookupError("Assessment option was not found.")
    return assessment, question, option


def _find_question(
    questions: list[AssessmentQuestion],
    question_id: str,
) -> AssessmentQuestion | None:
    for question in questions:
        if str(question.id) == question_id:
            return question
    if len(questions) == 1:
        return questions[0]
    return None


def _find_option(
    options: list[AssessmentOption],
    option_id: str,
) -> AssessmentOption | None:
    normalized = option_id.strip()
    for option in options:
        if str(option.id) == normalized or option.option_key == normalized or option.label == normalized:
            return option
    return None


def _update_mastery(
    session: Session,
    *,
    user: UserAccount,
    question: AssessmentQuestion,
    is_correct: bool,
    session_type: str,
) -> None:
    if question.concept_id is None:
        return
    state = session.scalar(
        select(LearnerConceptState).where(
            LearnerConceptState.user_id == user.id,
            LearnerConceptState.concept_id == question.concept_id,
        )
    )
    if state is None:
        state = LearnerConceptState(
            user_id=user.id,
            concept_id=question.concept_id,
            status="ready",
            mastery_score=0.0,
            confidence_score=0.0,
            evidence_count=0,
        )
        session.add(state)
    delta = 0.18 if is_correct else -0.12
    mastery_score = state.mastery_score or 0.0
    confidence_score = state.confidence_score or 0.0
    state.mastery_score = max(0.0, min(1.0, mastery_score + delta))
    state.confidence_score = max(
        0.0,
        min(1.0, confidence_score + (0.12 if is_correct else -0.08)),
    )
    state.status = "review_due" if not is_correct else ("mastered" if state.mastery_score >= 0.7 else "ready")
    state.evidence_count = (state.evidence_count or 0) + 1
    state.last_evaluated_at = datetime.now(UTC)
    interval_days = (
        _spaced_review_interval_days(state=state, is_correct=is_correct)
        if session_type == "daily_evaluation"
        else (3 if is_correct else 1)
    )
    state.next_review_at = datetime.now(UTC) + timedelta(days=interval_days)


def _spaced_review_interval_days(
    *,
    state: LearnerConceptState,
    is_correct: bool,
) -> int:
    if not is_correct:
        return 1
    evidence_count = int(state.evidence_count or 0)
    mastery_score = float(state.mastery_score or 0.0)
    confidence_score = float(state.confidence_score or 0.0)
    if evidence_count <= 1:
        return 2
    if evidence_count <= 3 or mastery_score < 0.7 or confidence_score < 0.65:
        return 7
    if evidence_count <= 5 or mastery_score < 0.85:
        return 14
    return 30


def _daily_next_review_interval_for_attempt(
    session: Session,
    *,
    user: UserAccount,
    attempt: AssessmentAttempt,
    fallback_days: int,
) -> int:
    question = session.get(AssessmentQuestion, attempt.question_id)
    if question is None or question.concept_id is None:
        return fallback_days
    state = session.scalar(
        select(LearnerConceptState).where(
            LearnerConceptState.user_id == user.id,
            LearnerConceptState.concept_id == question.concept_id,
        )
    )
    if state is None or state.next_review_at is None:
        return fallback_days
    due_date = _as_utc(state.next_review_at).date()
    today = datetime.now(UTC).date()
    return max(1, (due_date - today).days)


def _normalize_topic(raw_topic: str) -> str:
    cleaned = " ".join(raw_topic.strip().split())
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Learning goal"
