from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.language import normalize_language_code, preferred_language_code
from app.db.session import SessionLocal
from app.modules.accounts.models import UserAccount
from app.modules.curriculum.kurikulum_merdeka import (
    translate_curriculum_label_to_english,
)
from app.modules.curriculum.models import ConceptEdge, KnowledgeConcept
from app.modules.evidence.canvas_upload_service import (
    image_asset_file_path,
    load_owned_image_asset,
)
from app.modules.inputs.service import create_workspace_input_event
from app.modules.learning.models import (
    AssessmentSession,
    LearningTrack,
    MediaArtifact,
    MediaJob,
    TrackModule,
)
from app.modules.learning.schemas import AnimationQueueResponse
from app.modules.learning.service import (
    media_artifact_to_schema,
    queue_animation_job,
    queue_context_animation_job,
)
from app.modules.posttests.service import AdaptivePosttestService
from app.modules.workspaces.mastery import WorkspaceMasteryService
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession
from app.modules.workspaces.schemas import (
    TutorResponseRead,
    WorkspaceEventCreateResponse,
    WorkspaceEventRead,
    WorkspaceGenerateVideoResponse,
    WorkspaceRead,
    WorkspaceSessionHistoryRead,
    WorkspaceSessionSummaryRead,
)
from app.modules.workspaces.tutor import (
    TutorImageInput,
    demo_phase_opening_prompt,
    generate_tutor_response,
)

VALID_EVENT_TYPES = {
    "text",
    "quiz_answer",
    "canvas_sent",
    "media_generated",
    "media_viewed",
    "media_ready",
    "system",
    "note",
}
VALID_ACTOR_TYPES = {"learner", "tutor", "system"}
_mastery_service = WorkspaceMasteryService()
_posttest_service = AdaptivePosttestService()
logger = logging.getLogger(__name__)

_PHASE_SEQUENCE = ("engage", "explore", "explain", "elaborate", "evaluate")
_MAX_HINT_LEVEL = 6
_HINT_DECAY_PER_SUCCESS = 2
_EVIDENCE_CONFIDENCE_FLOOR = 0.55
_CHECKPOINT_INTERACTION_TYPE = "phase_checkpoint"
_CHECKPOINT_STAY_DECISION = "stay"
_DEFAULT_PHASE_MIN_TURNS: dict[str, int] = {
    "engage": 2,
    "explore": 1,
    "explain": 1,
    "elaborate": 3,
    "evaluate": 1,
}
_ELABORATE_REQUIRED_APPLICATIONS = 3
_LEGACY_DEFAULT_PHASE_MIN_TURNS: dict[str, int] = {
    "engage": 1,
    "explore": 2,
    "explain": 2,
    "elaborate": 2,
    "evaluate": 1,
}
_PHASE_REQUIRED_EVIDENCE: dict[str, tuple[frozenset[str], ...]] = {
    "engage": (
        frozenset({"challenge_accepted"}),
        frozenset({"prior_knowledge_shared"}),
    ),
    "explore": (
        frozenset({"exploration_attempt"}),
        frozenset({"pattern_identified", "misconception_shifted"}),
    ),
    "explain": (
        frozenset({"learner_explanation"}),
        frozenset({"micro_check_correct"}),
    ),
    "elaborate": (
        frozenset({"transfer_attempt"}),
        frozenset({"transfer_correct"}),
    ),
    "evaluate": (
        frozenset({"independent_attempt"}),
        frozenset({"error_analysis"}),
    ),
}
_PHASE_ALLOWED_EVIDENCE: dict[str, frozenset[str]] = {
    phase: frozenset().union(*requirements)
    for phase, requirements in _PHASE_REQUIRED_EVIDENCE.items()
}
_PHASE_ALLOWED_EVIDENCE["evaluate"] = (
    _PHASE_ALLOWED_EVIDENCE["evaluate"] | frozenset({"reflection"})
)


def create_or_resume_workspace(
    session: Session,
    *,
    user: UserAccount,
    track_id: UUID,
    module_id: UUID,
    content_mode: str,
    workspace_session_id: UUID | None = None,
    start_new_session: bool = False,
    demo_session: bool = False,
) -> WorkspaceRead:
    track, module = _resolve_owned_track_module(
        session,
        user=user,
        track_id=track_id,
        module_id=module_id,
    )
    if module.status == "locked":
        raise ValueError("Locked modules cannot be opened before prerequisites pass.")

    language = _preferred_language(user)
    topic_title, _topic_description = _workspace_topic_display(
        session,
        module=module,
        language=language,
    )
    workspace: WorkspaceSession | None = None
    if workspace_session_id is not None:
        workspace = _load_workspace(session, user=user, workspace_id=workspace_session_id)
        if workspace is None:
            raise LookupError("Workspace session was not found.")
        if workspace.track_id != track.id or workspace.module_id != module.id:
            raise LookupError("Workspace session does not belong to the requested module.")
    elif not start_new_session:
        # A completed workspace is a finished record, not somewhere to drop the
        # learner back into: auto-resume only ever picks up unfinished work.
        workspace = session.scalar(
            select(WorkspaceSession)
            .where(
                WorkspaceSession.user_id == user.id,
                WorkspaceSession.track_id == track.id,
                WorkspaceSession.module_id == module.id,
                WorkspaceSession.status != "completed",
            )
            .order_by(WorkspaceSession.updated_at.desc(), WorkspaceSession.created_at.desc())
            .options(selectinload(WorkspaceSession.events))
        )

    if workspace is None:
        now_iso = datetime.now(UTC).isoformat()
        workspace = WorkspaceSession(
            user_id=user.id,
            track_id=track.id,
            module_id=module.id,
            current_topic=topic_title or module.title,
            content_mode=_normalize_content_mode(content_mode),
            status="active",
            metadata_json={
                "source": "workspace_api",
                "current_phase": "engage",
                "phase_transition_pending": False,
                "posttest_eligible": False,
                "phase_evidence": {},
                "hint_level": 0,
                "consecutive_failures": 0,
                "phase_min_turns": dict(_DEFAULT_PHASE_MIN_TURNS),
                "phase_history": [
                    {
                        "phase": "engage",
                        "entered_at": now_iso,
                        "exited_at": None,
                        "turn_count": 0,
                    }
                ],
                "visited_5e_phases": ["engage"],
                "demo_script": bool(demo_session),
            },
        )
        session.add(workspace)
    else:
        workspace.current_topic = topic_title or module.title
        workspace.content_mode = _normalize_content_mode(content_mode)
        # Never demote a completed session back to active; the posttest owns that
        # transition and reopening a finished record must not undo it.
        if workspace.status != "completed":
            workspace.status = "active"
        workspace.updated_at = datetime.now(UTC)
    _apply_workspace_context(
        session,
        workspace=workspace,
        module=module,
        user=user,
    )
    workspace.metadata_json = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    # A newly opened workspace starts with the tutor's Engage invitation. The
    # learner should not have to manufacture a synthetic "I'm ready" message
    # just to make the conversation appear.
    if (
        not workspace.events
        and str(workspace.metadata_json.get("current_phase") or "engage") == "engage"
    ):
        session.flush()
        opening_prompt = demo_phase_opening_prompt(
            phase="engage",
            topic=workspace.current_topic or "this module",
            learner_language=language,
            learning_context=workspace.metadata_json.get("learning_context"),
            force_demo=bool(workspace.metadata_json.get("demo_script", False)),
        )
        session.add(
            WorkspaceEvent(
                workspace_session_id=workspace.id,
                event_index=1,
                event_type="text",
                actor_type="tutor",
                text_payload=opening_prompt,
                image_asset_id=None,
                media_artifact_id=None,
                input_event_id=None,
                metadata_json={
                    "source": "workspace_initial_opening",
                    "intent": "phase_opening",
                    "next_actions": [],
                    "next_phase_ready": False,
                    "phase_reasoning": "initial_engage_opening",
                    "phase_checkpoint_question": None,
                    "next_phase_opening_prompt": None,
                    "evidence_tags": [],
                    "correctness": "unknown",
                    "misconception_status": "none",
                    "confidence": 1.0,
                    "evaluation_outcome": None,
                    "evidence_request": None,
                    "explanation_card": None,
                    "tool_suggestion": None,
                    "phase": "engage",
                },
            )
        )
    module.status = "active"
    track.status = "active"

    session.commit()
    workspace = _load_workspace(session, user=user, workspace_id=workspace.id)
    assert workspace is not None
    return workspace_to_schema(session, workspace, user=user)


def read_workspace(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID,
) -> WorkspaceRead | None:
    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    if workspace is not None and _sync_ready_media_followups(
        session,
        workspace=workspace,
        language=_preferred_language(user),
    ):
        session.commit()
        workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    return workspace_to_schema(session, workspace, user=user) if workspace else None


def list_workspace_sessions(
    session: Session,
    *,
    user: UserAccount,
    track_id: UUID,
    module_id: UUID,
    limit: int = 20,
    offset: int = 0,
) -> WorkspaceSessionHistoryRead:
    track, module = _resolve_owned_track_module(
        session,
        user=user,
        track_id=track_id,
        module_id=module_id,
    )
    scope = (
        WorkspaceSession.user_id == user.id,
        WorkspaceSession.track_id == track.id,
        WorkspaceSession.module_id == module.id,
    )
    total = int(session.scalar(select(func.count()).select_from(WorkspaceSession).where(*scope)) or 0)
    workspaces = session.scalars(
        select(WorkspaceSession)
        .where(*scope)
        .order_by(WorkspaceSession.updated_at.desc(), WorkspaceSession.created_at.desc())
        .offset(offset)
        .limit(limit)
        .options(selectinload(WorkspaceSession.events))
    ).all()
    language = _preferred_language(user)
    return WorkspaceSessionHistoryRead(
        sessions=[
            _workspace_session_summary(
                workspace,
                fallback_title=module.title,
                language=language,
            )
            for workspace in workspaces
        ],
        total=total,
        has_more=(offset + len(workspaces)) < total,
    )


def delete_workspace(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID,
) -> bool:
    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    if workspace is None:
        return False
    session.delete(workspace)
    session.commit()
    return True


def advance_workspace_phase(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID,
    force: bool = False,
) -> WorkspaceRead | None:
    if force:
        raise ValueError("Learner phase transitions cannot be forced.")
    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    if workspace is None:
        return None

    metadata = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    current_phase = str(metadata.get("current_phase") or "engage")
    if current_phase == "evaluate":
        raise ValueError("Already at the final 5E phase.")

    phase_index = _PHASE_SEQUENCE.index(current_phase)
    next_phase = _PHASE_SEQUENCE[phase_index + 1]
    phase_min_turns = _phase_min_turns(metadata)
    min_turns = int(phase_min_turns.get(current_phase, 1))
    current_turns = _current_phase_turns(metadata)
    pending = bool(metadata.get("phase_transition_pending", False))
    if not pending or not _phase_is_ready(metadata, phase=current_phase):
        raise ValueError(
            "Phase transition requires verified phase-specific evidence."
        )
    if current_turns < min_turns:
        raise ValueError(
            f"Minimum {min_turns} learner turns required for phase '{current_phase}'."
        )

    posttest_handoff = next_phase == "evaluate"
    opening_prompt = None
    if not posttest_handoff:
        opening_prompt = _pending_phase_opening_prompt(
            workspace,
            current_phase=current_phase,
        )
        if not opening_prompt:
            opening_prompt = demo_phase_opening_prompt(
                phase=next_phase,
                topic=workspace.current_topic or "this module",
                learner_language=_preferred_language(user),
                learning_context=metadata.get("learning_context"),
            )
    metadata = _advance_metadata_to_phase(metadata, next_phase=next_phase)
    workspace.metadata_json = _ensure_phase_metadata(
        metadata,
        created_at=workspace.created_at,
    )
    if posttest_handoff:
        workspace.metadata_json["posttest_eligible"] = True
        workspace.metadata_json["posttest_handoff_reason"] = (
            "guided_elaborate_complete"
        )
    workspace.updated_at = datetime.now(UTC)
    if opening_prompt:
        session.add(
            WorkspaceEvent(
                workspace_session_id=workspace.id,
                event_index=_next_event_index(session, workspace_id=workspace.id),
                event_type="text",
                actor_type="tutor",
                text_payload=opening_prompt,
                image_asset_id=None,
                media_artifact_id=None,
                input_event_id=None,
                metadata_json={
                    "source": "workspace_phase_opening",
                    "intent": "phase_opening",
                    "next_actions": [],
                    "next_phase_ready": False,
                    "phase_reasoning": f"learner_confirmed_transition_from_{current_phase}",
                    "phase_checkpoint_question": None,
                    "next_phase_opening_prompt": None,
                    "evidence_tags": [],
                    "correctness": "unknown",
                    "misconception_status": "none",
                    "confidence": 1.0,
                    "evaluation_outcome": None,
                    "evidence_request": None,
                    "explanation_card": None,
                    "tool_suggestion": None,
                    "phase": next_phase,
                },
            )
        )
    session.commit()

    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    if workspace is None:
        return None
    return workspace_to_schema(session, workspace, user=user)


def start_posttest(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID,
) -> WorkspaceRead | None:
    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    if workspace is None:
        return None

    metadata = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    if not bool(metadata.get("posttest_eligible", False)):
        raise ValueError(
            "Posttest is not eligible yet. Complete the guided Elaborate practice first."
        )

    track, module = _resolve_owned_track_module(
        session,
        user=user,
        track_id=workspace.track_id,
        module_id=workspace.module_id,
    )
    language = _preferred_language(user)
    concept = session.get(KnowledgeConcept, module.concept_id) if module.concept_id else None
    concept_title = (
        _concept_display_title(concept, language=language)
        if concept is not None
        else _module_display_title(module, language=language)
    )

    def _record_trigger(payload: dict[str, Any]) -> None:
        refreshed = _ensure_phase_metadata(
            dict(workspace.metadata_json or {}),
            created_at=workspace.created_at,
        )
        refreshed["posttest_trigger"] = {
            "learning_goal_id": str(track.learning_goal_id),
            "track_id": str(track.id),
            "module_id": str(module.id),
            "workspace_session_id": str(workspace.id),
            "concept_code": concept.code if concept is not None else None,
            "concept_title": concept_title,
            "triggered_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        workspace.metadata_json = refreshed
        workspace.updated_at = datetime.now(UTC)
        session.commit()

    try:
        # The prepared Chain Rule demo owns a fixed, local question pack. Do
        # not queue it behind the normal async LLM post-test pipeline: the
        # answer set is already known and can be made active in this request.
        is_demo_posttest = bool(metadata.get("demo_script", False))
        posttest = _posttest_service.start(
            session,
            user=user,
            workspace_session_id=workspace.id,
            learning_goal_id=track.learning_goal_id,
            track_id=track.id,
            module_id=module.id,
            generate_questions=is_demo_posttest,
        )
    except ValueError as exc:
        # Drop anything the failed generation left pending before recording the
        # error, so a partial posttest is never committed as a side effect.
        session.rollback()
        _record_trigger({"status": "error", "reason": "posttest_start_failed", "error": str(exc)})
        raise
    if posttest is None:
        message = "Posttest could not be created for this module."
        _record_trigger({"status": "error", "reason": "posttest_unavailable", "error": message})
        raise ValueError(message)
    generation_ready = (
        posttest.status in {"active", "awaiting_answer"}
        and int(posttest.total_questions) > 0
    )
    refreshed_metadata = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    refreshed_metadata["posttest_eligible"] = not generation_ready
    if generation_ready:
        refreshed_metadata["phase_transition_pending"] = False
    refreshed_metadata["posttest_trigger"] = {
        "status": "ready" if generation_ready else "generating",
        "reason": "guided_elaborate_complete",
        "learning_goal_id": str(track.learning_goal_id),
        "track_id": str(track.id),
        "module_id": str(module.id),
        "workspace_session_id": str(workspace.id),
        "concept_code": concept.code if concept is not None else None,
        "concept_title": concept_title,
        "posttest_session_id": str(posttest.session_id),
        "question_count": int(posttest.total_questions),
        "error": None,
        "triggered_at": datetime.now(UTC).isoformat(),
    }
    workspace.metadata_json = refreshed_metadata
    workspace.updated_at = datetime.now(UTC)
    session.commit()

    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    if workspace is None:
        return None
    return workspace_to_schema(session, workspace, user=user)


def process_queued_posttest_generation(
    *,
    user_id: UUID,
    workspace_id: UUID,
    posttest_session_id: UUID,
) -> None:
    """Finish a queued workspace post-test outside the request lifecycle."""
    with SessionLocal() as session:
        _process_queued_posttest_generation(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            posttest_session_id=posttest_session_id,
        )


def _process_queued_posttest_generation(
    session: Session,
    *,
    user_id: UUID,
    workspace_id: UUID,
    posttest_session_id: UUID,
) -> bool:
    user = session.get(UserAccount, user_id)
    workspace = session.get(WorkspaceSession, workspace_id)
    assessment = session.get(AssessmentSession, posttest_session_id)
    if user is None or workspace is None or assessment is None:
        logger.warning(
            "Queued posttest context disappeared: user_id=%s workspace_id=%s session_id=%s",
            user_id,
            workspace_id,
            posttest_session_id,
        )
        return False

    generation_state = dict(
        (assessment.metadata_json or {}).get("generation_state") or {}
    )
    if str(generation_state.get("status") or "") != "queued":
        return False
    assessment.metadata_json = {
        **dict(assessment.metadata_json or {}),
        "generation_state": {**generation_state, "status": "processing"},
    }
    session.commit()

    trigger = dict((workspace.metadata_json or {}).get("posttest_trigger") or {})
    try:
        posttest = _posttest_service.start(
            session,
            user=user,
            workspace_session_id=workspace.id,
            learning_goal_id=UUID(str(trigger["learning_goal_id"])),
            track_id=workspace.track_id,
            module_id=workspace.module_id,
            generate_questions=True,
        )
        if posttest is None:
            raise ValueError("Posttest could not be created for this module.")
    except Exception as exc:  # Background task boundary: persist a retryable failure.
        session.rollback()
        workspace = session.get(WorkspaceSession, workspace_id)
        assessment = session.get(AssessmentSession, posttest_session_id)
        if assessment is not None:
            failed_state = dict(
                (assessment.metadata_json or {}).get("generation_state") or {}
            )
            assessment.metadata_json = {
                **dict(assessment.metadata_json or {}),
                "generation_state": {**failed_state, "status": "error"},
            }
        if workspace is not None:
            metadata = dict(workspace.metadata_json or {})
            current_trigger = dict(metadata.get("posttest_trigger") or trigger)
            metadata["posttest_trigger"] = {
                **current_trigger,
                "status": "error",
                "reason": "posttest_generation_failed",
                "error": str(exc)[:1000],
            }
            metadata["posttest_eligible"] = True
            workspace.metadata_json = metadata
            workspace.updated_at = datetime.now(UTC)
        session.commit()
        logger.exception(
            "Queued posttest generation failed: workspace_id=%s session_id=%s",
            workspace_id,
            posttest_session_id,
        )
        return False

    workspace = session.get(WorkspaceSession, workspace_id)
    if workspace is None:
        return False
    metadata = dict(workspace.metadata_json or {})
    current_trigger = dict(metadata.get("posttest_trigger") or trigger)
    metadata["posttest_eligible"] = False
    metadata["phase_transition_pending"] = False
    metadata["posttest_trigger"] = {
        **current_trigger,
        "status": "ready",
        "posttest_session_id": str(posttest.session_id),
        "question_count": int(posttest.total_questions),
        "error": None,
    }
    workspace.metadata_json = metadata
    workspace.updated_at = datetime.now(UTC)
    session.commit()
    return True


async def append_workspace_event(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID,
    event_type: str,
    actor_type: str,
    text_payload: str,
    image_asset_id: UUID | None,
    media_artifact_id: UUID | None,
    metadata: dict[str, Any],
) -> WorkspaceEventCreateResponse | None:
    workspace = _load_workspace(
        session, user=user, workspace_id=workspace_id, for_update=True
    )
    if workspace is None:
        return None

    normalized_event_type = _normalize_event_type(event_type)
    normalized_actor_type = _normalize_actor_type(actor_type)
    if normalized_actor_type != "learner":
        raise ValueError("Public workspace events must be authored by the learner.")
    learner_metadata = _sanitize_learner_metadata(metadata)
    checkpoint_declined = _is_checkpoint_stay_event(learner_metadata)
    if media_artifact_id is not None:
        _resolve_owned_media_artifact(session, user=user, media_artifact_id=media_artifact_id)
    tutor_image = _resolve_tutor_image_input(session, user=user, image_asset_id=image_asset_id)

    module = session.get(TrackModule, workspace.module_id)
    if module is not None:
        topic_title, _topic_description = _workspace_topic_display(
            session,
            module=module,
            language=_preferred_language(user),
        )
        if topic_title:
            workspace.current_topic = topic_title

    phase_metadata = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    current_phase = str(phase_metadata.get("current_phase") or "engage")
    is_demo_script = bool(phase_metadata.get("demo_script", False))
    if current_phase == "evaluate" and bool(phase_metadata.get("posttest_eligible")):
        raise ValueError("Guided workspace is complete. Start the posttest.")
    if checkpoint_declined:
        if not bool(phase_metadata.get("phase_transition_pending", False)):
            raise ValueError("There is no pending phase checkpoint to decline.")
        phase_metadata["phase_transition_pending"] = False
        phase_metadata["phase_readiness_recheck_required"] = current_phase
    workspace.metadata_json = phase_metadata
    first_engage_reply = (
        current_phase == "engage"
        and normalized_event_type == "text"
        and _is_substantive_engage_reply(text_payload)
        and not checkpoint_declined
        and _current_phase_turns(phase_metadata) == 0
    )
    # Demo has its own fixed state machine. It must be generated against the
    # actual server phase, never the normal Engage shortcut.
    tutor_generation_phase = (
        current_phase
        if is_demo_script
        else ("explore" if first_engage_reply else current_phase)
    )

    # Call AI before saving so audit info goes into event metadata.
    tutor_response, ai_audit = await generate_tutor_response(
        workspace=workspace,
        event_type=normalized_event_type,
        text_payload=text_payload,
        events=list(workspace.events),
        current_phase=tutor_generation_phase,
        learner_language=_preferred_language(user),
        image_input=tutor_image,
        learner_event_metadata=learner_metadata,
    )
    tutor_response = _sanitize_tutor_response_for_phase(
        phase_metadata,
        phase=tutor_generation_phase,
        event_type=normalized_event_type,
        text_payload=text_payload,
        tutor_response=tutor_response,
        checkpoint_declined=checkpoint_declined,
    )
    event_metadata = {
        **learner_metadata,
        "phase": current_phase,
        "ai_audit": ai_audit,
    }
    evidence_verified = bool(
        tutor_response is not None
        and tutor_response.evidence_tags
        and tutor_response.confidence >= _EVIDENCE_CONFIDENCE_FLOOR
    )
    audit_metadata = {
        **learner_metadata,
        **ai_audit,
        "track_id": str(workspace.track_id),
        "module_id": str(workspace.module_id),
        "phase": current_phase,
        "tutor_policy": ai_audit.get("ai_source", "unknown"),
        "evidence_verified": evidence_verified,
        "evidence_correctness": (
            tutor_response.correctness if tutor_response is not None else "unknown"
        ),
        "evidence_tags": (
            list(tutor_response.evidence_tags) if tutor_response is not None else []
        ),
    }
    mastery_result = _mastery_service.apply_event(
        session,
        user=user,
        module=module,
        event_type=normalized_event_type,
        text_payload=text_payload,
        metadata=audit_metadata,
    )
    if mastery_result.concept_id is not None:
        audit_metadata["concept_id"] = str(mastery_result.concept_id)
    if mastery_result.update is not None:
        audit_metadata["mastery_update_reason"] = mastery_result.update.reason

    input_event = create_workspace_input_event(
        session,
        user=user,
        workspace_session_id=workspace.id,
        concept_id=mastery_result.concept_id,
        source_event_type=normalized_event_type,
        actor_type=normalized_actor_type,
        text_payload=text_payload,
        image_asset_id=image_asset_id,
        media_artifact_id=media_artifact_id,
        metadata=audit_metadata,
    )
    event = WorkspaceEvent(
        workspace_session_id=workspace.id,
        event_index=_next_event_index(session, workspace_id=workspace.id),
        event_type=normalized_event_type,
        actor_type=normalized_actor_type,
        text_payload=text_payload.strip(),
        image_asset_id=image_asset_id,
        media_artifact_id=media_artifact_id,
        input_event_id=input_event.id,
        metadata_json=event_metadata,
    )
    tutor_event: WorkspaceEvent | None = None
    if tutor_response is not None and tutor_response.text.strip():
        tutor_event = WorkspaceEvent(
            workspace_session_id=workspace.id,
            event_index=event.event_index + 1,
            event_type="text",
            actor_type="tutor",
            text_payload=tutor_response.text.strip(),
            image_asset_id=None,
            media_artifact_id=None,
            input_event_id=None,
            metadata_json={
                "source": "workspace_tutor_response",
                "intent": tutor_response.intent,
                "next_actions": list(tutor_response.next_actions),
                "next_phase_ready": tutor_response.next_phase_ready,
                "phase_reasoning": tutor_response.phase_reasoning,
                "phase_checkpoint_question": (
                    tutor_response.phase_checkpoint_question
                ),
                "next_phase_opening_prompt": (
                    tutor_response.next_phase_opening_prompt
                ),
                "evidence_tags": list(tutor_response.evidence_tags),
                "correctness": tutor_response.correctness,
                "misconception_status": tutor_response.misconception_status,
                "confidence": tutor_response.confidence,
                "evaluation_outcome": tutor_response.evaluation_outcome,
                "evidence_request": tutor_response.evidence_request,
                "explanation_card": tutor_response.explanation_card,
                "tool_suggestion": (
                    tutor_response.tool_suggestion.model_dump()
                    if tutor_response.tool_suggestion is not None
                    else None
                ),
                "phase": current_phase,
            },
        )

    metadata_json = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    if "demo_script_next_step" in ai_audit:
        metadata_json["demo_script_step"] = ai_audit["demo_script_next_step"]
    metadata_json = _record_phase_evidence(
        metadata_json,
        phase=current_phase,
        tutor_response=tutor_response,
        event_type=normalized_event_type,
    )
    if (
        not checkpoint_declined
        and metadata_json.get("phase_readiness_recheck_required") == current_phase
        and _response_reestablishes_phase_readiness(
            phase=current_phase,
            tutor_response=tutor_response,
        )
    ):
        metadata_json["phase_readiness_recheck_required"] = None
    history = list(metadata_json.get("phase_history", []))
    if normalized_actor_type == "learner" and normalized_event_type != "system":
        if history:
            history[-1]["turn_count"] = int(history[-1].get("turn_count", 0)) + 1
        else:
            now_iso = datetime.now(UTC).isoformat()
            history = [
                {
                    "phase": current_phase,
                    "entered_at": now_iso,
                    "exited_at": None,
                    "turn_count": 1,
                }
            ]
        metadata_json["phase_history"] = history

    phase_ready = _phase_is_ready(metadata_json, phase=current_phase)
    phase_min_turns = int(_phase_min_turns(metadata_json).get(current_phase, 1))
    phase_transition_ready = (
        phase_ready and _current_phase_turns(metadata_json) >= phase_min_turns
    )
    auto_advance_engage = (
        not is_demo_script and first_engage_reply and tutor_response is not None
    )
    if auto_advance_engage:
        next_phase = "explore"
        metadata_json = _advance_metadata_to_phase(
            metadata_json,
            next_phase=next_phase,
        )
        if tutor_response is not None:
            tutor_response = tutor_response.model_copy(
                update={
                    "next_phase_ready": False,
                    "phase_reasoning": "automatic_transition_from_engage",
                    "phase_checkpoint_question": None,
                    "next_phase_opening_prompt": None,
                    "evidence_tags": [],
                    "evidence_request": None,
                    "explanation_card": None,
                }
            )
        if tutor_event is not None:
            tutor_event.metadata_json = {
                **dict(tutor_event.metadata_json or {}),
                "source": "workspace_auto_phase_opening",
                "next_phase_ready": False,
                "phase_reasoning": "automatic_transition_from_engage",
                "phase_checkpoint_question": None,
                "next_phase_opening_prompt": None,
                "evidence_tags": [],
                "phase": next_phase,
            }
        phase_ready = False
    elif (
        ai_audit.get("ai_source") == "demo_script"
        and tutor_response is not None
        and tutor_response.next_phase_ready
        and current_phase != "evaluate"
    ):
        # The presentation script is a continuous, fixed sequence.  It must
        # not depend on a learner pressing a hidden checkpoint between turns:
        # an extra typed reply previously left the script cursor and server
        # phase out of sync, which made the flow fall back to live AI.
        phase_index = _PHASE_SEQUENCE.index(current_phase)
        next_phase = _PHASE_SEQUENCE[phase_index + 1]
        metadata_json = _advance_metadata_to_phase(
            metadata_json,
            next_phase=next_phase,
        )
        if current_phase == "elaborate":
            metadata_json["posttest_eligible"] = True
            metadata_json["posttest_handoff_reason"] = "demo_elaborate_complete"
        tutor_response = tutor_response.model_copy(
            update={
                "next_phase_ready": False,
                "phase_checkpoint_question": None,
                "next_phase_opening_prompt": None,
                "evidence_request": None,
            }
        )
        if tutor_event is not None:
            tutor_event.metadata_json = {
                **dict(tutor_event.metadata_json or {}),
                "source": "workspace_demo_auto_transition",
                "next_phase_ready": False,
                "phase_checkpoint_question": None,
                "next_phase_opening_prompt": None,
                "phase": next_phase,
            }
        phase_transition_ready = False
    elif current_phase != "evaluate":
        # Every phase after Engage remains learner-confirmed through its
        # contextual checkpoint.
        metadata_json["phase_transition_pending"] = phase_transition_ready
    elif tutor_response is not None:
        outcome = tutor_response.evaluation_outcome
        if phase_ready and (
            tutor_response.correctness != "incorrect"
            and tutor_response.misconception_status != "active"
        ):
            outcome = "passed"
        elif (
            tutor_response.correctness == "correct"
            and tutor_response.misconception_status == "none"
        ):
            # A correct staged Evaluate response is incomplete until the later
            # error-analysis and reflection turns arrive. Providers sometimes
            # label that incompleteness as "partial"; it is not a transfer gap.
            outcome = "continue"
        elif outcome == "passed":
            outcome = "continue"
        tutor_response = tutor_response.model_copy(
            update={
                "evaluation_outcome": outcome,
                "evidence_request": (
                    None if outcome == "passed" else tutor_response.evidence_request
                ),
                "next_actions": (
                    ["continue_next_module"]
                    if outcome == "passed"
                    else tutor_response.next_actions
                ),
            }
        )
        if phase_ready and outcome == "passed":
            metadata_json["posttest_eligible"] = True
            metadata_json["phase_transition_pending"] = False
        elif outcome == "misconception":
            metadata_json = _remediate_metadata_to_phase(
                metadata_json,
                phase="explore",
                reason="evaluate_misconception",
            )
        elif outcome == "partial":
            metadata_json = _remediate_metadata_to_phase(
                metadata_json,
                phase="elaborate",
                reason="evaluate_partial_transfer",
            )
        # Otherwise leave posttest_eligible untouched: once the learner has earned
        # eligibility it stays earned until a remediation explicitly revokes it.

    tutor_degraded = bool(ai_audit.get("degraded", False))
    if tutor_response is not None:
        next_phase_opening_prompt = tutor_response.next_phase_opening_prompt
        if (
            phase_transition_ready
            and current_phase != "evaluate"
            and next_phase_opening_prompt is None
            and _PHASE_SEQUENCE[_PHASE_SEQUENCE.index(current_phase) + 1]
            != "evaluate"
        ):
            phase_index = _PHASE_SEQUENCE.index(current_phase)
            next_phase_opening_prompt = demo_phase_opening_prompt(
                phase=_PHASE_SEQUENCE[phase_index + 1],
                topic=workspace.current_topic or "this module",
                learner_language=_preferred_language(user),
                learning_context=metadata_json.get("learning_context"),
            )
        if phase_transition_ready and current_phase == "elaborate":
            # Elaborate now hands directly to the independent posttest. Do not
            # expose or persist a synthetic Evaluate exercise in the workspace.
            next_phase_opening_prompt = None
        tutor_response = tutor_response.model_copy(
            update={
                "next_phase_ready": phase_transition_ready and current_phase != "evaluate",
                "phase_checkpoint_question": (
                    tutor_response.phase_checkpoint_question
                    if phase_transition_ready and current_phase != "evaluate"
                    else None
                ),
                "next_phase_opening_prompt": (
                    next_phase_opening_prompt
                    if phase_transition_ready and current_phase != "evaluate"
                    else None
                ),
                "evidence_request": (
                    None
                    if phase_transition_ready or tutor_response.evaluation_outcome == "passed"
                    else tutor_response.evidence_request
                ),
                "scaffold_level": max(
                    0, _safe_int(metadata_json.get("hint_level"), 0)
                ),
                "degraded": tutor_degraded,
            }
        )

    if tutor_event is not None:
        tutor_event.metadata_json = {
            **dict(tutor_event.metadata_json or {}),
            "next_phase_ready": (
                tutor_response.next_phase_ready if tutor_response is not None else False
            ),
            "next_actions": (
                list(tutor_response.next_actions) if tutor_response is not None else []
            ),
            "phase_checkpoint_question": (
                tutor_response.phase_checkpoint_question
                if tutor_response is not None
                else None
            ),
            "next_phase_opening_prompt": (
                tutor_response.next_phase_opening_prompt
                if tutor_response is not None
                else None
            ),
            "evidence_tags": (
                list(tutor_response.evidence_tags) if tutor_response is not None else []
            ),
            "evaluation_outcome": (
                tutor_response.evaluation_outcome
                if tutor_response is not None
                else None
            ),
            "evidence_request": (
                tutor_response.evidence_request if tutor_response is not None else None
            ),
            "scaffold_level": max(
                0, _safe_int(metadata_json.get("hint_level"), 0)
            ),
            "degraded": tutor_degraded,
        }

    metadata_json = _sync_learning_context_state(metadata_json)
    workspace.metadata_json = _ensure_phase_metadata(
        metadata_json,
        created_at=workspace.created_at,
    )

    workspace.updated_at = datetime.now(UTC)
    session.add(event)
    if tutor_event is not None:
        session.add(tutor_event)
    session.commit()
    session.refresh(event)

    workspace = _load_workspace(session, user=user, workspace_id=workspace.id)
    assert workspace is not None
    return WorkspaceEventCreateResponse(
        event=event_to_schema(event),
        tutor_response=tutor_response,
        mastery_update=mastery_result.update,
        workspace=workspace_to_schema(session, workspace, user=user),
    )


def queue_workspace_video_generation(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID,
    generation_mode: str,
    template_id: str | None,
    spec_json: dict[str, Any],
    language: str,
    quality_profile: str,
    concept_id: UUID | None,
    metadata: dict[str, Any],
) -> WorkspaceGenerateVideoResponse | None:
    workspace = _load_workspace(
        session, user=user, workspace_id=workspace_id, for_update=True
    )
    if workspace is None:
        return None

    workspace_metadata = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    requested_phase = str(workspace_metadata.get("current_phase") or "engage")
    if requested_phase != "explore":
        raise ValueError("Visualization can only be requested during the Explore phase.")

    module = session.get(TrackModule, workspace.module_id)
    resolved_concept_id = concept_id or (module.concept_id if module is not None else None)
    active_request = _active_workspace_media_request(
        session,
        workspace=workspace,
        concept_id=resolved_concept_id,
        requested_phase=requested_phase,
    )
    if active_request is not None:
        artifact, job, existing_event = active_request
        return WorkspaceGenerateVideoResponse(
            queue=AnimationQueueResponse(
                job_id=job.id,
                artifact_id=artifact.id,
                status=job.status,
                error_details=None,
            ),
            event=event_to_schema(existing_event),
            workspace=workspace_to_schema(session, workspace, user=user),
        )

    normalized_generation_mode = _normalize_generation_mode(generation_mode)
    if normalized_generation_mode == "context_auto":
        queue = queue_context_animation_job(
            session,
            user=user,
            workspace_id=workspace.id,
            concept_id=resolved_concept_id,
            language=language,
            quality_profile=quality_profile,
        )
        resolved_template_id = "pending.context_auto"
        resolved_metadata = {"spec_source": "context_auto_worker"}
    else:
        resolved_template_id = (template_id or "").strip().lower()
        if not resolved_template_id:
            raise ValueError("template_id must not be empty when generation_mode is 'manual'.")
        resolved_spec_json = dict(spec_json)
        resolved_metadata = {
            "spec_source": "manual_payload",
            "resolved_template_id": resolved_template_id,
        }

        queue = queue_animation_job(
            session,
            user=user,
            workspace_id=workspace.id,
            concept_id=resolved_concept_id,
            template_id=resolved_template_id,
            spec_json=resolved_spec_json,
            language=language,
            quality_profile=quality_profile,
        )
    event_metadata = {
        **metadata,
        "source": "workspace_generate_video_api",
        "generation_mode": normalized_generation_mode,
        "template_id": resolved_template_id,
        "job_id": str(queue.job_id),
        "artifact_id": str(queue.artifact_id),
        "queue_status": queue.status,
        "requested_phase": requested_phase,
        **resolved_metadata,
    }
    if queue.error_details is not None:
        event_metadata["error_details"] = queue.error_details

    event = WorkspaceEvent(
        workspace_session_id=workspace.id,
        event_index=_next_event_index(session, workspace_id=workspace.id),
        event_type="media_generated",
        actor_type="system",
        text_payload="",
        image_asset_id=None,
        media_artifact_id=queue.artifact_id,
        input_event_id=None,
        metadata_json=event_metadata,
    )
    workspace.updated_at = datetime.now(UTC)
    session.add(event)
    session.commit()
    session.refresh(event)

    workspace = _load_workspace(session, user=user, workspace_id=workspace.id)
    if workspace is None:
        return None

    return WorkspaceGenerateVideoResponse(
        queue=queue,
        event=event_to_schema(event),
        workspace=workspace_to_schema(session, workspace, user=user),
    )


def workspace_to_schema(
    session: Session,
    workspace: WorkspaceSession,
    *,
    user: UserAccount | None = None,
) -> WorkspaceRead:
    events = sorted(
        workspace.events,
        key=lambda event: event.event_index,
    )
    latest_media = _latest_media_artifact(session, events)
    language = _preferred_language(user) if user is not None else "en"
    module = session.get(TrackModule, workspace.module_id)
    topic_title = workspace.current_topic
    topic_description = ""
    if module is not None:
        localized_title, localized_description = _workspace_topic_display(
            session,
            module=module,
            language=language,
        )
        topic_title = localized_title or topic_title
        topic_description = localized_description
    metadata = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    return WorkspaceRead(
        id=workspace.id,
        track_id=workspace.track_id,
        module_id=workspace.module_id,
        current_topic=topic_title,
        current_topic_description=topic_description,
        learner_language=_normalize_language_code(language),
        content_mode=workspace.content_mode,
        status=workspace.status,
        events=[event_to_schema(event) for event in events],
        last_image_asset_id=_latest_image_asset_id(events),
        latest_media=media_artifact_to_schema(latest_media) if latest_media else None,
        posttest_trigger=_posttest_trigger_payload(workspace),
        current_phase=str(metadata.get("current_phase") or "engage"),
        phase_transition_pending=bool(metadata.get("phase_transition_pending", False)),
        posttest_eligible=bool(metadata.get("posttest_eligible", False)),
        learning_context=dict(metadata.get("learning_context") or {}),
        phase_evidence=dict(metadata.get("phase_evidence") or {}),
        hint_level=max(0, _safe_int(metadata.get("hint_level"), 0)),
        tutor_degraded=_latest_tutor_degraded(events),
    )


def _latest_tutor_degraded(events: list[WorkspaceEvent]) -> bool:
    """True when the most recent tutor turn came from the deterministic fallback."""
    for event in reversed(events):
        if event.actor_type != "tutor":
            continue
        metadata = event.metadata_json or {}
        if metadata.get("source") != "workspace_tutor_response":
            continue
        return bool(metadata.get("degraded", False))
    return False


def event_to_schema(event: WorkspaceEvent) -> WorkspaceEventRead:
    public_metadata = dict(event.metadata_json or {})
    public_metadata.pop("ai_audit", None)
    return WorkspaceEventRead(
        id=event.id,
        workspace_id=event.workspace_session_id,
        event_index=event.event_index,
        event_type=event.event_type,
        actor_type=event.actor_type,
        text_payload=event.text_payload,
        image_asset_id=event.image_asset_id,
        media_artifact_id=event.media_artifact_id,
        input_event_id=event.input_event_id,
        metadata=public_metadata,
        created_at=event.created_at.isoformat() if event.created_at else "",
    )


def _workspace_session_summary(
    workspace: WorkspaceSession,
    *,
    fallback_title: str,
    language: str = "en",
) -> WorkspaceSessionSummaryRead:
    events = sorted(workspace.events, key=lambda event: event.event_index)
    text_events = [
        event
        for event in events
        if event.text_payload.strip() and event.event_type in {"text", "quiz_answer", "note"}
    ]
    preview_event = text_events[-1] if text_events else None
    empty_preview = (
        "Belum ada pesan."
        if _normalize_language_code(language) == "id"
        else "No messages yet."
    )
    preview = preview_event.text_payload.strip() if preview_event else empty_preview
    first_learner_event = next(
        (event for event in text_events if event.actor_type == "learner"),
        None,
    )
    title = first_learner_event.text_payload.strip() if first_learner_event else fallback_title
    if len(title) > 48:
        title = f"{title[:45].rstrip()}..."
    if len(preview) > 96:
        preview = f"{preview[:93].rstrip()}..."
    return WorkspaceSessionSummaryRead(
        id=workspace.id,
        track_id=workspace.track_id,
        module_id=workspace.module_id,
        title=title,
        preview=preview,
        message_count=len(text_events),
        created_at=workspace.created_at.isoformat() if workspace.created_at else "",
        updated_at=workspace.updated_at.isoformat() if workspace.updated_at else "",
    )


def _resolve_owned_track_module(
    session: Session,
    *,
    user: UserAccount,
    track_id: UUID,
    module_id: UUID,
) -> tuple[LearningTrack, TrackModule]:
    track = session.scalar(
        select(LearningTrack)
        .where(LearningTrack.id == track_id, LearningTrack.user_id == user.id)
        .options(selectinload(LearningTrack.modules))
    )
    if track is None:
        raise LookupError("Track was not found.")

    module = next((item for item in track.modules if item.id == module_id), None)
    if module is None:
        raise LookupError("Track module was not found.")
    return track, module


def _resolve_tutor_image_input(
    session: Session,
    *,
    user: UserAccount,
    image_asset_id: UUID | None,
) -> TutorImageInput | None:
    """Resolve a learner-owned image asset into something the tutor can actually see."""
    if image_asset_id is None:
        return None
    asset = load_owned_image_asset(session, user=user, asset_id=image_asset_id)
    if asset is None:
        raise LookupError("Image asset was not found.")
    path = image_asset_file_path(asset)
    if path is None:
        # The row exists but the bytes are gone; degrade to a text-only turn rather
        # than failing the whole append.
        return None
    return TutorImageInput(file_path=str(path), mime_type=asset.mime_type)


def _resolve_owned_media_artifact(
    session: Session,
    *,
    user: UserAccount,
    media_artifact_id: UUID,
) -> MediaArtifact:
    artifact = session.scalar(
        select(MediaArtifact).where(
            MediaArtifact.id == media_artifact_id,
            MediaArtifact.user_id == user.id,
        )
    )
    if artifact is None:
        raise LookupError("Media artifact was not found.")
    return artifact


def _load_workspace(
    session: Session,
    *,
    user: UserAccount,
    workspace_id: UUID,
    for_update: bool = False,
) -> WorkspaceSession | None:
    statement = (
        select(WorkspaceSession)
        .where(WorkspaceSession.id == workspace_id, WorkspaceSession.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        # Serialise concurrent appends against the same workspace. Without this two
        # in-flight events both read the same max(event_index) and the second commit
        # trips uq_workspace_events_session_index with a 500.
        statement = statement.with_for_update()
    return session.scalar(statement.options(selectinload(WorkspaceSession.events)))


def _next_event_index(session: Session, *, workspace_id: UUID) -> int:
    max_index = session.scalar(
        select(func.max(WorkspaceEvent.event_index)).where(
            WorkspaceEvent.workspace_session_id == workspace_id
        )
    )
    return int(max_index or 0) + 1


def _pending_phase_opening_prompt(
    workspace: WorkspaceSession,
    *,
    current_phase: str,
) -> str | None:
    for event in sorted(
        workspace.events,
        key=lambda item: item.event_index,
        reverse=True,
    ):
        if event.actor_type != "tutor":
            continue
        metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
        if str(metadata.get("phase") or "") != current_phase:
            continue
        if not bool(metadata.get("next_phase_ready", False)):
            continue
        prompt = str(metadata.get("next_phase_opening_prompt") or "").strip()
        if prompt:
            return prompt
    return None


def _normalize_event_type(event_type: str) -> str:
    normalized = event_type.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in VALID_EVENT_TYPES:
        raise ValueError(
            "Event type must be text, quiz_answer, canvas_sent, media_generated, system, or note."
        )
    return normalized


def _normalize_actor_type(actor_type: str) -> str:
    normalized = actor_type.strip().lower()
    if normalized not in VALID_ACTOR_TYPES:
        raise ValueError("Actor type must be learner, tutor, or system.")
    return normalized


def _sanitize_learner_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    protected = {
        "is_correct",
        "correct_answer",
        "evidence_verified",
        "evidence_correctness",
        "evidence_tags",
        "mastery_score",
        "phase_transition_pending",
        "posttest_eligible",
        "client_5e_state",
        "client_tutor_override",
        "skip_server_tutor",
        "phase_readiness_recheck_required",
    }
    return {
        key: value
        for key, value in dict(metadata or {}).items()
        if key not in protected
    }


def _is_checkpoint_stay_event(metadata: dict[str, Any]) -> bool:
    return (
        str(metadata.get("interaction_type") or "").strip().lower()
        == _CHECKPOINT_INTERACTION_TYPE
        and str(metadata.get("checkpoint_decision") or "").strip().lower()
        == _CHECKPOINT_STAY_DECISION
    )


def _normalize_content_mode(content_mode: str) -> str:
    normalized = content_mode.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized or "chat"


def _normalize_generation_mode(generation_mode: str) -> str:
    normalized = generation_mode.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"auto": "context_auto", "context": "context_auto"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"manual", "context_auto"}:
        raise ValueError("generation_mode must be either 'manual' or 'context_auto'.")
    return normalized


def _ensure_phase_metadata(
    metadata: dict[str, Any],
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    safe = dict(metadata or {})
    now_iso = (created_at or datetime.now(UTC)).isoformat()
    current_phase = _normalize_phase(str(safe.get("current_phase") or "engage"))
    min_turns = _phase_min_turns(safe)

    history: list[dict[str, Any]] = []
    raw_history = safe.get("phase_history")
    if isinstance(raw_history, list):
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            phase = _normalize_phase(str(item.get("phase") or current_phase))
            turn_count = max(0, _safe_int(item.get("turn_count"), 0))
            entered_at = str(item.get("entered_at") or now_iso).strip() or now_iso
            exited_raw = item.get("exited_at")
            exited_at = str(exited_raw).strip() if exited_raw is not None else None
            if exited_at == "":
                exited_at = None
            history.append(
                {
                    "phase": phase,
                    "entered_at": entered_at,
                    "exited_at": exited_at,
                    "turn_count": turn_count,
                }
            )

    if not history:
        visited = safe.get("visited_5e_phases")
        if isinstance(visited, list):
            for value in visited:
                phase = _normalize_phase(str(value or current_phase))
                history.append(
                    {
                        "phase": phase,
                        "entered_at": now_iso,
                        "exited_at": None,
                        "turn_count": 0,
                    }
                )

    if not history:
        history = [
            {
                "phase": current_phase,
                "entered_at": now_iso,
                "exited_at": None,
                "turn_count": 0,
            }
        ]
    else:
        if history[-1]["phase"] != current_phase:
            history.append(
                {
                    "phase": current_phase,
                    "entered_at": now_iso,
                    "exited_at": None,
                    "turn_count": 0,
                }
            )
        for item in history[:-1]:
            if item.get("exited_at") is None:
                item["exited_at"] = now_iso
        history[-1]["exited_at"] = None

    visited_phases: list[str] = []
    for item in history:
        phase = _normalize_phase(str(item.get("phase") or "engage"))
        if phase not in visited_phases:
            visited_phases.append(phase)

    posttest_eligible = bool(safe.get("posttest_eligible", False))
    phase_evidence = safe.get("phase_evidence")
    if not isinstance(phase_evidence, dict):
        phase_evidence = {}
    recheck_phase = str(
        safe.get("phase_readiness_recheck_required") or ""
    ).strip().lower()
    if recheck_phase not in _PHASE_SEQUENCE or recheck_phase != current_phase:
        recheck_phase = None
    safe.update(
        {
            "current_phase": current_phase,
            "phase_history": history,
            "phase_transition_pending": bool(safe.get("phase_transition_pending", False))
            and current_phase != "evaluate",
            "posttest_eligible": posttest_eligible,
            "phase_min_turns": min_turns,
            "visited_5e_phases": visited_phases,
            "phase_evidence": phase_evidence,
            "phase_readiness_recheck_required": recheck_phase,
            "hint_level": max(
                0, min(_MAX_HINT_LEVEL, _safe_int(safe.get("hint_level"), 0))
            ),
            "consecutive_failures": max(
                0, _safe_int(safe.get("consecutive_failures"), 0)
            ),
        }
    )
    return safe


def _phase_min_turns(metadata: dict[str, Any]) -> dict[str, int]:
    resolved = dict(_DEFAULT_PHASE_MIN_TURNS)
    value = metadata.get("phase_min_turns")
    if isinstance(value, dict):
        normalized_input = {
            phase: _safe_int(value.get(phase), _DEFAULT_PHASE_MIN_TURNS[phase])
            for phase in _PHASE_SEQUENCE
        }
        if normalized_input == _LEGACY_DEFAULT_PHASE_MIN_TURNS:
            return dict(_DEFAULT_PHASE_MIN_TURNS)
        for phase in _PHASE_SEQUENCE:
            candidate = _safe_int(value.get(phase), resolved[phase])
            resolved[phase] = max(1, candidate)
    return resolved


def _normalize_phase(value: str) -> str:
    phase = value.strip().lower()
    return phase if phase in _PHASE_SEQUENCE else "engage"


def _current_phase_turns(metadata: dict[str, Any]) -> int:
    history = metadata.get("phase_history")
    if not isinstance(history, list) or not history:
        return 0
    last_entry = history[-1]
    if not isinstance(last_entry, dict):
        return 0
    return max(0, _safe_int(last_entry.get("turn_count"), 0))


def _is_substantive_engage_reply(text: str) -> bool:
    """Keep a terse first reply conversationally in Engage before exploration."""
    normalized = str(text or "").strip()
    return len(normalized) >= 24 or len(normalized.split()) >= 4


def _record_phase_evidence(
    metadata: dict[str, Any],
    *,
    phase: str,
    tutor_response: TutorResponseRead | None,
    event_type: str,
) -> dict[str, Any]:
    updated = dict(metadata)
    if tutor_response is None:
        return updated

    failed = (
        tutor_response.correctness in {"incorrect", "partial"}
        or tutor_response.misconception_status in {"suspected", "active"}
    )
    if failed:
        failures = max(0, _safe_int(updated.get("consecutive_failures"), 0)) + 1
        updated["consecutive_failures"] = failures
        updated["hint_level"] = max(
            max(0, _safe_int(updated.get("hint_level"), 0)),
            min(_MAX_HINT_LEVEL, failures),
        )
    elif tutor_response.correctness == "correct":
        # Unwind at the same rate the ladder can climb, so a learner who recovers
        # is not stuck at a high scaffold level for the rest of the session.
        updated["consecutive_failures"] = 0
        updated["hint_level"] = max(
            0, _safe_int(updated.get("hint_level"), 0) - _HINT_DECAY_PER_SUCCESS
        )

    allowed_tags = _PHASE_ALLOWED_EVIDENCE.get(phase, frozenset())
    evidence_tags = [
        tag for tag in tutor_response.evidence_tags if tag in allowed_tags
    ]
    if (
        phase == "engage"
        and _current_phase_turns(metadata) == 0
        and "prior_knowledge_shared" in evidence_tags
    ):
        evidence_tags.remove("prior_knowledge_shared")
    if phase == "elaborate" and "transfer_correct" in evidence_tags:
        evidence_tags.insert(0, "transfer_attempt")
        evidence_tags = list(dict.fromkeys(evidence_tags))
    if (
        phase == "explain"
        and "micro_check_correct" in evidence_tags
        and not _phase_has_recorded_tag(
            updated,
            phase="explain",
            tag="learner_explanation",
        )
    ):
        evidence_tags.remove("micro_check_correct")
    if phase == "evaluate":
        evidence_tags = _current_evaluate_evidence_tags(metadata, evidence_tags)
    if tutor_response.confidence < _EVIDENCE_CONFIDENCE_FLOOR or not evidence_tags:
        return updated
    evidence_by_phase = dict(updated.get("phase_evidence") or {})
    records = list(evidence_by_phase.get(phase, []))
    records.append(
        {
            "recorded_at": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "tags": list(dict.fromkeys(evidence_tags)),
            "correctness": tutor_response.correctness,
            "misconception_status": tutor_response.misconception_status,
            "confidence": round(float(tutor_response.confidence), 4),
            "evaluation_outcome": tutor_response.evaluation_outcome,
        }
    )
    evidence_by_phase[phase] = records[-20:]
    updated["phase_evidence"] = evidence_by_phase
    return updated


def _sanitize_tutor_response_for_phase(
    metadata: dict[str, Any],
    *,
    phase: str,
    event_type: str,
    text_payload: str,
    tutor_response: TutorResponseRead | None,
    checkpoint_declined: bool = False,
) -> TutorResponseRead | None:
    if tutor_response is None:
        return None

    allowed_tags = _PHASE_ALLOWED_EVIDENCE.get(phase, frozenset())
    evidence_tags = [
        tag for tag in tutor_response.evidence_tags if tag in allowed_tags
    ]
    if checkpoint_declined:
        evidence_tags = []
    if (
        phase == "engage"
        and _current_phase_turns(metadata) == 0
        and "prior_knowledge_shared" in evidence_tags
    ):
        evidence_tags.remove("prior_knowledge_shared")
    if event_type == "media_viewed" and not text_payload.strip():
        evidence_tags = []
    if phase == "elaborate" and "transfer_correct" in evidence_tags:
        evidence_tags.insert(0, "transfer_attempt")
        evidence_tags = list(dict.fromkeys(evidence_tags))
    demo_script = tutor_response.phase_reasoning == "demo_script"
    if (
        phase == "explain"
        and "micro_check_correct" in evidence_tags
        and not _phase_has_recorded_tag(
            metadata,
            phase="explain",
            tag="learner_explanation",
        )
        and not demo_script
    ):
        evidence_tags.remove("micro_check_correct")
    if phase == "evaluate":
        evidence_tags = _current_evaluate_evidence_tags(metadata, evidence_tags)

    has_explanation = (
        phase == "explain"
        and (
            "learner_explanation" in evidence_tags
            or _phase_has_recorded_tag(
                metadata,
                phase="explain",
                tag="learner_explanation",
            )
        )
    )
    return tutor_response.model_copy(
        update={
            "evidence_tags": evidence_tags,
            "correctness": (
                "unknown" if checkpoint_declined else tutor_response.correctness
            ),
            "next_phase_ready": (
                False if checkpoint_declined else tutor_response.next_phase_ready
            ),
            "phase_checkpoint_question": (
                None
                if checkpoint_declined
                else tutor_response.phase_checkpoint_question
            ),
            "next_phase_opening_prompt": (
                None
                if checkpoint_declined
                else tutor_response.next_phase_opening_prompt
            ),
            "evaluation_outcome": (
                tutor_response.evaluation_outcome if phase == "evaluate" else None
            ),
            "explanation_card": (
                tutor_response.explanation_card if has_explanation else None
            ),
            "tool_suggestion": (
                tutor_response.tool_suggestion
                if phase == "explore"
                or (
                    phase == "engage"
                    and tutor_response.tool_suggestion is not None
                    and tutor_response.tool_suggestion.tool
                    == "interactive_function_flow"
                )
                or (
                    phase == "explain"
                    and tutor_response.tool_suggestion is not None
                    and tutor_response.tool_suggestion.tool == "demo_chain_rule_video"
                )
                else None
            ),
        }
    )


def _response_reestablishes_phase_readiness(
    *,
    phase: str,
    tutor_response: TutorResponseRead | None,
) -> bool:
    if tutor_response is None:
        return False
    allowed_tags = _PHASE_ALLOWED_EVIDENCE.get(phase, frozenset())
    return bool(allowed_tags.intersection(tutor_response.evidence_tags)) and (
        tutor_response.correctness == "correct"
        and tutor_response.misconception_status in {"none", "resolved"}
        and tutor_response.confidence >= _EVIDENCE_CONFIDENCE_FLOOR
    )


def _active_workspace_media_request(
    session: Session,
    *,
    workspace: WorkspaceSession,
    concept_id: UUID | None,
    requested_phase: str,
) -> tuple[MediaArtifact, MediaJob, WorkspaceEvent] | None:
    statement = (
        select(MediaArtifact)
        .where(
            MediaArtifact.user_id == workspace.user_id,
            MediaArtifact.workspace_id == workspace.id,
            MediaArtifact.status.in_(("queued", "processing")),
        )
        .order_by(MediaArtifact.created_at.desc())
    )
    if concept_id is not None:
        statement = statement.where(MediaArtifact.concept_id == concept_id)
    artifacts = list(session.scalars(statement))
    if not artifacts:
        return None

    events_by_artifact = {
        event.media_artifact_id: event
        for event in workspace.events
        if event.event_type == "media_generated" and event.media_artifact_id is not None
    }
    for artifact in artifacts:
        event = events_by_artifact.get(artifact.id)
        if event is None:
            continue
        event_phase = str((event.metadata_json or {}).get("requested_phase") or "")
        if event_phase and event_phase != requested_phase:
            continue
        job = session.scalar(
            select(MediaJob)
            .where(
                MediaJob.artifact_id == artifact.id,
                MediaJob.status.in_(("queued", "processing")),
            )
            .order_by(MediaJob.created_at.desc())
        )
        if job is not None:
            return artifact, job, event
    return None


def _phase_has_recorded_tag(
    metadata: dict[str, Any],
    *,
    phase: str,
    tag: str,
) -> bool:
    evidence_by_phase = metadata.get("phase_evidence")
    if not isinstance(evidence_by_phase, dict):
        return False
    records = evidence_by_phase.get(phase)
    if not isinstance(records, list):
        return False
    return any(
        isinstance(record, dict)
        and isinstance(record.get("tags"), list)
        and tag in record["tags"]
        for record in records
    )


def _recorded_phase_tags(metadata: dict[str, Any], *, phase: str) -> set[str]:
    evidence_by_phase = metadata.get("phase_evidence")
    if not isinstance(evidence_by_phase, dict):
        return set()
    records = evidence_by_phase.get(phase)
    if not isinstance(records, list):
        return set()
    tags: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        if float(record.get("confidence") or 0.0) < _EVIDENCE_CONFIDENCE_FLOOR:
            continue
        raw_tags = record.get("tags")
        if not isinstance(raw_tags, list):
            continue
        if str(record.get("misconception_status") or "none") == "active":
            # An Explore attempt remains useful evidence that the learner engaged
            # with the task after a later response resolves the misconception.
            # It cannot by itself satisfy the pattern requirement.
            if phase == "explore" and "exploration_attempt" in raw_tags:
                tags.add("exploration_attempt")
            continue
        tags.update(str(tag) for tag in raw_tags)
    return tags


def _current_evaluate_evidence_tags(
    metadata: dict[str, Any],
    proposed_tags: list[str],
) -> list[str]:
    recorded_tags = _recorded_phase_tags(metadata, phase="evaluate")
    if "independent_attempt" not in recorded_tags:
        required_tag = "independent_attempt"
    elif "error_analysis" not in recorded_tags:
        required_tag = "error_analysis"
    elif "reflection" not in recorded_tags:
        return [tag for tag in proposed_tags if tag == "reflection"]
    else:
        return []
    return [
        tag for tag in proposed_tags if tag in {required_tag, "reflection"}
    ]


def _phase_is_ready(metadata: dict[str, Any], *, phase: str) -> bool:
    if metadata.get("phase_readiness_recheck_required") == phase:
        return False
    if phase == "elaborate":
        evidence_by_phase = metadata.get("phase_evidence")
        records = (
            evidence_by_phase.get("elaborate", [])
            if isinstance(evidence_by_phase, dict)
            else []
        )
        if not isinstance(records, list):
            return False
        valid_records = [
            record
            for record in records
            if isinstance(record, dict)
            and float(record.get("confidence") or 0.0) >= _EVIDENCE_CONFIDENCE_FLOOR
            and str(record.get("misconception_status") or "none") != "active"
            and isinstance(record.get("tags"), list)
        ]
        attempts = sum("transfer_attempt" in record["tags"] for record in valid_records)
        successes = sum("transfer_correct" in record["tags"] for record in valid_records)
        return (
            attempts >= _ELABORATE_REQUIRED_APPLICATIONS
            and successes >= _ELABORATE_REQUIRED_APPLICATIONS
        )
    tags = _recorded_phase_tags(metadata, phase=phase)
    requirements = _PHASE_REQUIRED_EVIDENCE.get(phase, ())
    return bool(requirements) and all(bool(tags & alternatives) for alternatives in requirements)


def _advance_metadata_to_phase(
    metadata: dict[str, Any],
    *,
    next_phase: str,
) -> dict[str, Any]:
    updated = dict(metadata)
    now_iso = datetime.now(UTC).isoformat()
    history = list(updated.get("phase_history", []))
    if history:
        history[-1]["exited_at"] = now_iso
    history.append(
        {
            "phase": next_phase,
            "entered_at": now_iso,
            "exited_at": None,
            "turn_count": 0,
        }
    )
    updated["current_phase"] = next_phase
    updated["phase_history"] = history
    updated["phase_transition_pending"] = False
    updated["phase_readiness_recheck_required"] = None
    updated["posttest_eligible"] = False
    return _sync_learning_context_state(updated)


def _clear_phase_evidence_from(metadata: dict[str, Any], *, phase: str) -> dict[str, Any]:
    """Drop recorded evidence for `phase` and every phase after it.

    Without this a remediated learner re-satisfies the gate immediately from the
    evidence they banked on their first pass, and ping-pongs back to evaluate.
    """
    updated = dict(metadata)
    evidence = dict(updated.get("phase_evidence") or {})
    start_index = _PHASE_SEQUENCE.index(_normalize_phase(phase))
    for stale_phase in _PHASE_SEQUENCE[start_index:]:
        evidence.pop(stale_phase, None)
    updated["phase_evidence"] = evidence
    return updated


def _remediate_metadata_to_phase(
    metadata: dict[str, Any],
    *,
    phase: str,
    reason: str,
) -> dict[str, Any]:
    updated = _advance_metadata_to_phase(
        _clear_phase_evidence_from(metadata, phase=phase),
        next_phase=phase,
    )
    updated["remediation_reason"] = reason
    updated["posttest_eligible"] = False
    updated["phase_transition_pending"] = False
    updated["consecutive_failures"] = 0
    updated["remediation_cycle"] = max(
        0,
        _safe_int(metadata.get("remediation_cycle"), 0),
    ) + 1
    return updated


def _sync_learning_context_state(metadata: dict[str, Any]) -> dict[str, Any]:
    updated = dict(metadata)
    context = updated.get("learning_context")
    if isinstance(context, dict):
        updated["learning_context"] = {
            **context,
            "current_phase": str(updated.get("current_phase") or "engage"),
            "phase_evidence": dict(updated.get("phase_evidence") or {}),
            "hint_level": max(0, _safe_int(updated.get("hint_level"), 0)),
        }
    return updated


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _preferred_language(user: UserAccount) -> str:
    return preferred_language_code(user)


def _normalize_language_code(language: str | None) -> str:
    return normalize_language_code(language)


def _metadata_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return str(value).strip() if value is not None else ""


def _workspace_topic_display(
    session: Session,
    *,
    module: TrackModule,
    language: str,
) -> tuple[str, str]:
    concept = session.get(KnowledgeConcept, module.concept_id) if module.concept_id else None
    if concept is not None:
        title = _concept_display_title(concept, language=language)
        description = _concept_display_description(
            concept,
            language=language,
            title=title,
        )
        return title or module.title, description
    return _module_display_title(module, language=language), _module_display_description(
        module,
        language=language,
    )


def _concept_display_title(concept: KnowledgeConcept, *, language: str) -> str:
    metadata = concept.metadata_json or {}
    if _normalize_language_code(language) == "en":
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
) -> str:
    metadata = concept.metadata_json or {}
    if _normalize_language_code(language) == "en":
        description = (
            concept.en_desc
            or _metadata_text(metadata, "description_en")
            or _metadata_text(metadata, "en_desc")
        )
        if description:
            return description
        return f"Understand and apply {title}."
    return (
        concept.id_desc
        or _metadata_text(metadata, "description_id")
        or concept.description
        or f"Memahami dan menerapkan {title}."
    )


def _module_display_title(module: TrackModule, *, language: str) -> str:
    metadata = module.metadata_json or {}
    language_code = _normalize_language_code(language)
    localized = _metadata_text(metadata, f"title_{language_code}")
    if localized:
        return localized
    if language_code == "id":
        return {
            "Prerequisite checkpoint": "Cek prasyarat",
            "Application and review": "Aplikasi dan ulasan",
        }.get(module.title, module.title)
    return {
        "Cek prasyarat": "Prerequisite checkpoint",
        "Aplikasi dan ulasan": "Application and review",
    }.get(module.title, module.title)


def _module_display_description(module: TrackModule, *, language: str) -> str:
    metadata = module.metadata_json or {}
    language_code = _normalize_language_code(language)
    localized = _metadata_text(metadata, f"description_{language_code}")
    if localized:
        return localized
    if language_code == "id":
        return {
            "Prerequisite checkpoint": (
                "Perbaiki fondasi yang terdeteksi dari pretest sebelum masuk ke topik utama."
            ),
            "Application and review": (
                "Terapkan konsepnya, lalu jadwalkan untuk pengulangan terarah."
            ),
        }.get(module.title, module.description)
    return {
        "Cek prasyarat": (
            "Repair the foundation detected by the pretest before starting the main topic."
        ),
        "Aplikasi dan ulasan": "Apply the concept, then schedule it for spaced repetition.",
    }.get(module.title, module.description)


def _apply_workspace_context(
    session: Session,
    *,
    workspace: WorkspaceSession,
    module: TrackModule,
    user: UserAccount,
) -> None:
    metadata = dict(workspace.metadata_json or {})
    language = _preferred_language(user)
    concept = session.get(KnowledgeConcept, module.concept_id) if module.concept_id else None
    concept_metadata = dict(concept.metadata_json or {}) if concept is not None else {}
    prerequisite_codes: list[str] = []
    if concept is not None:
        prerequisite_codes = list(
            session.scalars(
                select(KnowledgeConcept.code)
                .join(ConceptEdge, ConceptEdge.from_concept_id == KnowledgeConcept.id)
                .where(
                    ConceptEdge.to_concept_id == concept.id,
                    ConceptEdge.edge_type == "prerequisite",
                )
            )
        )
    module_metadata = dict(module.metadata_json or {})
    original_target = module_metadata.get("original_target")
    if not isinstance(original_target, dict):
        original_target = {
            "concept_id": str(concept.id) if concept is not None else None,
            "concept_code": concept.code if concept is not None else None,
            "title": (
                _concept_display_title(concept, language=language)
                if concept is not None
                else _module_display_title(module, language=language)
            ),
        }
    current_module = module_metadata.get("current_module")
    if not isinstance(current_module, dict):
        current_module = {
            "concept_id": str(concept.id) if concept is not None else None,
            "concept_code": concept.code if concept is not None else None,
            "title": (
                _concept_display_title(concept, language=language)
                if concept is not None
                else _module_display_title(module, language=language)
            ),
            "role": str(module_metadata.get("module_role") or "original_target"),
        }
    learning_context = {
        "learner": {
            "language": _normalize_language_code(language),
        },
        "original_target": original_target,
        "current_module": current_module,
        "diagnosis": {
            "reason": str(module_metadata.get("diagnosis_reason") or ""),
            "evidence": (
                module_metadata.get("diagnosis_evidence")
                if isinstance(module_metadata.get("diagnosis_evidence"), dict)
                else {}
            ),
        },
        "already_understood": (
            module_metadata.get("already_understood")
            if isinstance(module_metadata.get("already_understood"), list)
            else []
        ),
        "route": (
            module_metadata.get("route")
            if isinstance(module_metadata.get("route"), list)
            else []
        ),
        "returns_to_original_target": bool(
            module_metadata.get("returns_to_original_target", False)
        ),
        "current_phase": str(metadata.get("current_phase") or "engage"),
        "phase_evidence": dict(metadata.get("phase_evidence") or {}),
        "hint_level": max(0, _safe_int(metadata.get("hint_level"), 0)),
        "tools": {
            "chat": True,
            "canvas": True,
            "visualization": True,
            "video": True,
        },
    }
    metadata.update(
        {
            "active_node_id": concept.code if concept is not None else metadata.get("active_node_id"),
            "active_concept_type": (
                str(concept_metadata.get("concept_type") or "").strip().lower()
                if concept is not None
                else str(metadata.get("active_concept_type") or "").strip().lower()
            )
            or "general_steam",
            "active_concept_subtype": (
                str(concept_metadata.get("concept_subtype") or "").strip().lower()
                if concept is not None
                else str(metadata.get("active_concept_subtype") or "").strip().lower()
            ),
            "active_concept_visual_pattern": (
                str(concept_metadata.get("concept_visual_pattern") or "").strip()
                if concept is not None
                else str(metadata.get("active_concept_visual_pattern") or "").strip()
            ),
            "active_visual_engine": (
                str(
                    concept_metadata.get("recommended_visual_engine")
                    or concept_metadata.get("media_engine_family")
                    or ""
                )
                .strip()
                .lower()
                if concept is not None
                else str(metadata.get("active_visual_engine") or "").strip().lower()
            ),
            "active_template_id": (
                str(
                    concept_metadata.get("template_id")
                    or concept_metadata.get("default_template_id")
                    or ""
                )
                .strip()
                .lower()
                if concept is not None
                else str(metadata.get("active_template_id") or "").strip().lower()
            ),
            "active_prerequisites": prerequisite_codes,
            "context_source": "module_concept_context" if concept is not None else "workspace_module_fallback",
            "learning_flow": "5e_steam",
            "learner_language": _normalize_language_code(language),
            "original_target_concept_id": original_target.get("concept_id"),
            "original_target_concept_code": original_target.get("concept_code"),
            "original_target_concept_title": original_target.get("title"),
            "module_role": current_module.get("role"),
            "diagnosis_reason": learning_context["diagnosis"]["reason"],
            "learning_context": learning_context,
            "session_goal_concept_id": str(concept.id) if concept is not None else None,
            "session_goal_concept_title": (
                _concept_display_title(concept, language=language)
                if concept is not None
                else _module_display_title(module, language=language)
            ),
            "session_goal_concept_description": (
                _concept_display_description(
                    concept,
                    language=language,
                    title=_concept_display_title(concept, language=language),
                )
                if concept is not None
                else _module_display_description(module, language=language)
            ),
        }
    )
    workspace.metadata_json = {
        key: value for key, value in metadata.items() if value is not None
    }


def _posttest_trigger_payload(workspace: WorkspaceSession) -> dict[str, Any] | None:
    payload = (workspace.metadata_json or {}).get("posttest_trigger")
    if isinstance(payload, dict):
        return payload
    return None


# _deterministic_tutor_response removed: AI generation is now the primary tutor via tutor.py
# Fallback logic lives in app.modules.workspaces.tutor._fallback_response


def _latest_image_asset_id(events: list[WorkspaceEvent]) -> UUID | None:
    for event in reversed(events):
        if event.image_asset_id is not None:
            return event.image_asset_id
    return None


def _latest_media_artifact(
    session: Session,
    events: list[WorkspaceEvent],
) -> MediaArtifact | None:
    for event in reversed(events):
        if event.media_artifact_id is not None:
            return session.get(MediaArtifact, event.media_artifact_id)
    return None


def _sync_ready_media_followups(
    session: Session,
    *,
    workspace: WorkspaceSession,
    language: str,
) -> bool:
    events = sorted(workspace.events, key=lambda item: item.event_index)
    followed_ids = {
        str((event.metadata_json or {}).get("follow_up_for_media_artifact_id"))
        for event in events
        if (event.metadata_json or {}).get("follow_up_for_media_artifact_id")
    }
    ready_artifacts: list[MediaArtifact] = []
    requested_phases: dict[UUID, str] = {}
    for event in events:
        if event.media_artifact_id is None:
            continue
        artifact = session.get(MediaArtifact, event.media_artifact_id)
        if artifact is None or artifact.status != "ready":
            continue
        if str(artifact.id) in followed_ids:
            continue
        ready_artifacts.append(artifact)
        requested_phases[artifact.id] = str(
            (event.metadata_json or {}).get("requested_phase") or "explore"
        )

    if not ready_artifacts:
        return False
    context = (workspace.metadata_json or {}).get("learning_context")
    context = context if isinstance(context, dict) else {}
    current_module = context.get("current_module")
    current_module = current_module if isinstance(current_module, dict) else {}
    topic = str(current_module.get("title") or workspace.current_topic)
    next_index = max((event.event_index for event in events), default=0) + 1
    for artifact in ready_artifacts:
        if _normalize_language_code(language) == "id":
            follow_up = (
                f"Visualisasi untuk {topic} sudah siap. Setelah melihatnya, jelaskan "
                "satu hubungan yang kamu amati dan langkah mana yang ingin kamu revisi."
            )
        else:
            follow_up = (
                f"The visualization for {topic} is ready. After viewing it, explain "
                "one relationship you noticed and which step you would revise."
            )
        session.add(
            WorkspaceEvent(
                workspace_session_id=workspace.id,
                event_index=next_index,
                event_type="media_ready",
                actor_type="tutor",
                text_payload=follow_up,
                media_artifact_id=artifact.id,
                metadata_json={
                    "source": "workspace_media_ready_follow_up",
                    "follow_up_for_media_artifact_id": str(artifact.id),
                    "intent": "reflect_on_visualization",
                    "next_actions": ["play_media", "explain_observation"],
                    "mastery_delta": 0.0,
                    "requested_phase": requested_phases.get(artifact.id, "explore"),
                    "current_phase_at_ready": str(
                        (workspace.metadata_json or {}).get("current_phase") or "engage"
                    ),
                },
            )
        )
        next_index += 1
    workspace.updated_at = datetime.now(UTC)
    return True
