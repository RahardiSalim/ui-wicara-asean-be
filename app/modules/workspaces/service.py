from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.language import normalize_language_code, preferred_language_code
from app.modules.accounts.models import UserAccount
from app.modules.curriculum.kurikulum_merdeka import (
    translate_curriculum_label_to_english,
)
from app.modules.curriculum.models import ConceptEdge, KnowledgeConcept
from app.modules.inputs.service import create_workspace_input_event
from app.modules.learning.models import LearningTrack, MediaArtifact, MediaJob, TrackModule
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
from app.modules.workspaces.tutor import generate_tutor_response

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

_PILOT_TEMPLATE_ID = "manim.number_line_quantity.v1"
_PHASE_SEQUENCE = ("engage", "explore", "explain", "elaborate", "evaluate")
_DEFAULT_PHASE_MIN_TURNS: dict[str, int] = {
    "engage": 1,
    "explore": 1,
    "explain": 1,
    "elaborate": 1,
    "evaluate": 1,
}
_LEGACY_DEFAULT_PHASE_MIN_TURNS: dict[str, int] = {
    "engage": 1,
    "explore": 2,
    "explain": 2,
    "elaborate": 2,
    "evaluate": 1,
}
_PHASE_REQUIRED_EVIDENCE: dict[str, tuple[frozenset[str], ...]] = {
    "engage": (frozenset({"challenge_accepted", "prior_knowledge_shared"}),),
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
        frozenset({"reflection"}),
    ),
}
_PHASE_ALLOWED_EVIDENCE: dict[str, frozenset[str]] = {
    phase: frozenset().union(*requirements)
    for phase, requirements in _PHASE_REQUIRED_EVIDENCE.items()
}


def create_or_resume_workspace(
    session: Session,
    *,
    user: UserAccount,
    track_id: UUID,
    module_id: UUID,
    content_mode: str,
    workspace_session_id: UUID | None = None,
    start_new_session: bool = False,
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
        workspace = session.scalar(
            select(WorkspaceSession)
            .where(
                WorkspaceSession.user_id == user.id,
                WorkspaceSession.track_id == track.id,
                WorkspaceSession.module_id == module.id,
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
            },
        )
        session.add(workspace)
    else:
        workspace.current_topic = topic_title or module.title
        workspace.content_mode = _normalize_content_mode(content_mode)
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
) -> WorkspaceSessionHistoryRead:
    track, module = _resolve_owned_track_module(
        session,
        user=user,
        track_id=track_id,
        module_id=module_id,
    )
    workspaces = session.scalars(
        select(WorkspaceSession)
        .where(
            WorkspaceSession.user_id == user.id,
            WorkspaceSession.track_id == track.id,
            WorkspaceSession.module_id == module.id,
        )
        .order_by(WorkspaceSession.updated_at.desc(), WorkspaceSession.created_at.desc())
        .options(selectinload(WorkspaceSession.events))
    ).all()
    return WorkspaceSessionHistoryRead(
        sessions=[
            _workspace_session_summary(workspace, fallback_title=module.title)
            for workspace in workspaces
        ]
    )


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

    metadata = _advance_metadata_to_phase(metadata, next_phase=next_phase)
    workspace.metadata_json = _ensure_phase_metadata(
        metadata,
        created_at=workspace.created_at,
    )
    workspace.updated_at = datetime.now(UTC)
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
        raise ValueError("Posttest is not eligible yet. Reach Evaluate phase first.")

    track, module = _resolve_owned_track_module(
        session,
        user=user,
        track_id=workspace.track_id,
        module_id=workspace.module_id,
    )
    posttest = _posttest_service.start(
        session,
        user=user,
        workspace_session_id=workspace.id,
        learning_goal_id=track.learning_goal_id,
        track_id=track.id,
        module_id=module.id,
    )
    if posttest is None:
        raise ValueError("Posttest could not be created for this module.")
    refreshed_metadata = _ensure_phase_metadata(
        dict(workspace.metadata_json or {}),
        created_at=workspace.created_at,
    )
    refreshed_metadata["posttest_eligible"] = False
    refreshed_metadata["phase_transition_pending"] = False
    refreshed_metadata["posttest_trigger"] = {
        "status": "ready",
        "reason": "evaluate_evidence_verified",
        "learning_goal_id": str(track.learning_goal_id),
        "track_id": str(track.id),
        "module_id": str(module.id),
        "workspace_session_id": str(workspace.id),
        "posttest_session_id": str(posttest.session_id),
        "question_count": int(posttest.total_questions),
        "triggered_at": datetime.now(UTC).isoformat(),
    }
    workspace.metadata_json = refreshed_metadata
    workspace.updated_at = datetime.now(UTC)
    session.commit()

    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    if workspace is None:
        return None
    return workspace_to_schema(session, workspace, user=user)


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
    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
    if workspace is None:
        return None

    normalized_event_type = _normalize_event_type(event_type)
    normalized_actor_type = _normalize_actor_type(actor_type)
    if normalized_actor_type != "learner":
        raise ValueError("Public workspace events must be authored by the learner.")
    if media_artifact_id is not None:
        _resolve_owned_media_artifact(session, user=user, media_artifact_id=media_artifact_id)

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
    workspace.metadata_json = phase_metadata
    current_phase = str(phase_metadata.get("current_phase") or "engage")

    # Call AI before saving so audit info goes into event metadata.
    tutor_response, ai_audit = await generate_tutor_response(
        workspace=workspace,
        event_type=normalized_event_type,
        text_payload=text_payload,
        events=list(workspace.events),
        current_phase=current_phase,
        learner_language=_preferred_language(user),
    )
    tutor_response = _sanitize_tutor_response_for_phase(
        phase_metadata,
        phase=current_phase,
        event_type=normalized_event_type,
        text_payload=text_payload,
        tutor_response=tutor_response,
    )
    learner_metadata = _sanitize_learner_metadata(metadata)
    event_metadata = {
        **learner_metadata,
        "phase": current_phase,
        "ai_audit": ai_audit,
    }
    evidence_verified = bool(
        tutor_response is not None
        and tutor_response.evidence_tags
        and tutor_response.confidence >= 0.55
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
    metadata_json = _record_phase_evidence(
        metadata_json,
        phase=current_phase,
        tutor_response=tutor_response,
        event_type=normalized_event_type,
    )
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

    auto_advanced_next_phase: str | None = None
    phase_ready = _phase_is_ready(metadata_json, phase=current_phase)
    if current_phase != "evaluate":
        metadata_json["phase_transition_pending"] = phase_ready
        min_turns = int(_phase_min_turns(metadata_json).get(current_phase, 1))
        current_turns = _current_phase_turns(metadata_json)
        if phase_ready and current_turns >= min_turns:
            next_phase = _PHASE_SEQUENCE[_PHASE_SEQUENCE.index(current_phase) + 1]
            metadata_json = _advance_metadata_to_phase(
                metadata_json,
                next_phase=next_phase,
            )
            auto_advanced_next_phase = next_phase
    elif tutor_response is not None:
        outcome = tutor_response.evaluation_outcome
        if (
            phase_ready
            and outcome == "passed"
            and tutor_response.correctness == "correct"
        ):
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
        else:
            metadata_json["posttest_eligible"] = False

    if tutor_response is not None:
        tutor_response = tutor_response.model_copy(
            update={
                "next_phase_ready": phase_ready and current_phase != "evaluate",
                "scaffold_level": max(
                    0, _safe_int(metadata_json.get("hint_level"), 0)
                ),
            }
        )

    if tutor_event is not None:
        tutor_event.metadata_json = {
            **dict(tutor_event.metadata_json or {}),
            "next_phase_ready": (
                tutor_response.next_phase_ready if tutor_response is not None else False
            ),
            "scaffold_level": max(
                0, _safe_int(metadata_json.get("hint_level"), 0)
            ),
        }
        if auto_advanced_next_phase is not None:
            tutor_event.metadata_json.update(
                {
                    "auto_phase_advanced": True,
                    "advanced_from": current_phase,
                    "advanced_to": auto_advanced_next_phase,
                }
            )

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
    workspace = _load_workspace(session, user=user, workspace_id=workspace_id)
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
    )


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
) -> WorkspaceSessionSummaryRead:
    events = sorted(workspace.events, key=lambda event: event.event_index)
    text_events = [
        event
        for event in events
        if event.text_payload.strip() and event.event_type in {"text", "quiz_answer", "note"}
    ]
    preview_event = text_events[-1] if text_events else None
    preview = preview_event.text_payload.strip() if preview_event else "Belum ada pesan."
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
) -> WorkspaceSession | None:
    return session.scalar(
        select(WorkspaceSession)
        .where(WorkspaceSession.id == workspace_id, WorkspaceSession.user_id == user.id)
        .options(selectinload(WorkspaceSession.events))
        .execution_options(populate_existing=True)
    )


def _next_event_index(session: Session, *, workspace_id: UUID) -> int:
    max_index = session.scalar(
        select(func.max(WorkspaceEvent.event_index)).where(
            WorkspaceEvent.workspace_session_id == workspace_id
        )
    )
    return int(max_index or 0) + 1


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
    }
    return {
        key: value
        for key, value in dict(metadata or {}).items()
        if key not in protected
    }


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
            "hint_level": max(0, min(6, _safe_int(safe.get("hint_level"), 0))),
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
        updated["hint_level"] = min(6, max(failures, 3 if failures >= 3 else 0))
    elif tutor_response.correctness == "correct":
        updated["consecutive_failures"] = 0
        updated["hint_level"] = max(
            0, _safe_int(updated.get("hint_level"), 0) - 1
        )

    allowed_tags = _PHASE_ALLOWED_EVIDENCE.get(phase, frozenset())
    evidence_tags = [
        tag for tag in tutor_response.evidence_tags if tag in allowed_tags
    ]
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
    if tutor_response.confidence < 0.55 or not evidence_tags:
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
) -> TutorResponseRead | None:
    if tutor_response is None:
        return None

    allowed_tags = _PHASE_ALLOWED_EVIDENCE.get(phase, frozenset())
    evidence_tags = [
        tag for tag in tutor_response.evidence_tags if tag in allowed_tags
    ]
    if event_type == "media_viewed" and not text_payload.strip():
        evidence_tags = []
    if phase == "elaborate" and "transfer_correct" in evidence_tags:
        evidence_tags.insert(0, "transfer_attempt")
        evidence_tags = list(dict.fromkeys(evidence_tags))
    if (
        phase == "explain"
        and "micro_check_correct" in evidence_tags
        and not _phase_has_recorded_tag(
            metadata,
            phase="explain",
            tag="learner_explanation",
        )
    ):
        evidence_tags.remove("micro_check_correct")

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
            "evaluation_outcome": (
                tutor_response.evaluation_outcome if phase == "evaluate" else None
            ),
            "explanation_card": (
                tutor_response.explanation_card if has_explanation else None
            ),
            "tool_suggestion": (
                tutor_response.tool_suggestion if phase == "explore" else None
            ),
        }
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


def _phase_is_ready(metadata: dict[str, Any], *, phase: str) -> bool:
    evidence_by_phase = metadata.get("phase_evidence")
    if not isinstance(evidence_by_phase, dict):
        return False
    records = evidence_by_phase.get(phase)
    if not isinstance(records, list):
        return False
    tags: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        if float(record.get("confidence") or 0.0) < 0.55:
            continue
        if str(record.get("misconception_status") or "none") == "active":
            continue
        raw_tags = record.get("tags")
        if isinstance(raw_tags, list):
            tags.update(str(tag) for tag in raw_tags)
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
    updated["posttest_eligible"] = False
    return _sync_learning_context_state(updated)


def _remediate_metadata_to_phase(
    metadata: dict[str, Any],
    *,
    phase: str,
    reason: str,
) -> dict[str, Any]:
    cleared = dict(metadata)
    evidence_by_phase = dict(cleared.get("phase_evidence") or {})
    evidence_by_phase[phase] = []
    cleared["phase_evidence"] = evidence_by_phase
    updated = _advance_metadata_to_phase(cleared, next_phase=phase)
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
                str((concept.metadata_json or {}).get("concept_type") or "").strip().lower()
                if concept is not None
                else str(metadata.get("active_concept_type") or "").strip().lower()
            )
            or "general_steam",
            "active_template_id": (
                str(
                    (concept.metadata_json or {}).get("template_id")
                    or (concept.metadata_json or {}).get("default_template_id")
                    or ""
                )
                .strip()
                .lower()
                if concept is not None
                else str(metadata.get("active_template_id") or "").strip().lower()
            )
            or _PILOT_TEMPLATE_ID,
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
