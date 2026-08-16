from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from app.modules.accounts.models import UserAccount
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.learning.models import (
    AssessmentSession,
    LearningGoal,
    LearningTrack,
    MediaArtifact,
    MediaJob,
    TrackModule,
)
from app.modules.learning.service import queue_context_animation_job
from app.modules.learning.spec_generator import WorkspaceGeneratedSpec
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession
from app.modules.workspaces.schemas import TutorResponseRead
from app.modules.workspaces.service import (
    _process_queued_posttest_generation,
    advance_workspace_phase,
    append_workspace_event,
    create_or_resume_workspace,
    queue_workspace_video_generation,
    read_workspace,
    start_posttest,
)


@pytest.mark.asyncio
async def test_workspace_runs_guided_elaborate_then_hands_to_posttest(
    db_session,
    monkeypatch,
):
    scenario = _create_workspace_scenario(db_session)
    responses = iter(
        [
            _tutor(
                tags=[],
                text=(
                    "Let u(x)=(2x + 1)^3. When x moves from 1 to 1.1, what are "
                    "u(1) and u(1.1)?"
                ),
            ),
            _tutor(tags=["exploration_attempt"]),
            _tutor(
                tags=["pattern_identified"],
                next_phase_opening_prompt=(
                    "Using the pattern you found, explain the chain rule in your own words."
                ),
            ),
            _tutor(
                tags=["learner_explanation"],
            ),
            _tutor(
                tags=["micro_check_correct"],
                next_phase_opening_prompt=(
                    "Guided application 1: differentiate (3x - 2)^5 and justify each factor."
                ),
            ),
            _tutor(
                tags=["transfer_attempt", "transfer_correct"],
            ),
            _tutor(
                tags=["transfer_attempt", "transfer_correct"],
            ),
            _tutor(
                tags=["transfer_attempt", "transfer_correct"],
            ),
        ]
    )
    monkeypatch.setattr(
        "app.modules.workspaces.service.generate_tutor_response",
        _fake_tutor(responses),
    )

    workspace_id = scenario["workspace"].id
    phases = []
    for message in (
        "I will investigate the missing factor.",
        "I know a composite function puts one function inside another.",
        "I compared the inner and outer changes.",
        "The inner change also affects the result.",
        "The outer derivative must be multiplied by the inner derivative.",
        "For guided application one I multiply both derivative layers.",
        "For guided application two I still multiply the outer and inner rates.",
        "For guided application three I can do the full chain rule without help.",
    ):
        result = await append_workspace_event(
            db_session,
            user=scenario["user"],
            workspace_id=workspace_id,
            event_type="text",
            actor_type="learner",
            text_payload=message,
            image_asset_id=None,
            media_artifact_id=None,
            metadata={},
        )
        assert result is not None
        if result.workspace.phase_transition_pending:
            advanced = advance_workspace_phase(
                db_session,
                user=scenario["user"],
                workspace_id=workspace_id,
            )
            assert advanced is not None
            phases.append(advanced.current_phase)
        else:
            phases.append(result.workspace.current_phase)

    assert phases == [
        "explore",
        "explore",
        "explain",
        "explain",
        "elaborate",
        "elaborate",
        "elaborate",
        "evaluate",
    ]
    final_workspace = read_workspace(
        db_session,
        user=scenario["user"],
        workspace_id=workspace_id,
    )
    assert final_workspace is not None
    assert final_workspace.posttest_eligible is True
    assert final_workspace.phase_evidence["explain"][0]["tags"] == [
        "learner_explanation"
    ]
    assert final_workspace.phase_evidence["explain"][1]["tags"] == [
        "micro_check_correct"
    ]
    learner_events = [
        event for event in final_workspace.events if event.actor_type == "learner"
    ]
    assert [event.metadata["phase"] for event in learner_events] == [
        "engage",
        "explore",
        "explore",
        "explain",
        "explain",
        "elaborate",
        "elaborate",
        "elaborate",
    ]
    phase_openings = [
        event
        for event in final_workspace.events
        if event.metadata.get("source") == "workspace_phase_opening"
    ]
    assert [event.metadata["phase"] for event in phase_openings] == [
        "explain",
        "elaborate",
    ]
    assert [event.text_payload for event in phase_openings] == [
        "Using the pattern you found, explain the chain rule in your own words.",
        "Guided application 1: differentiate (3x - 2)^5 and justify each factor.",
    ]
    auto_openings = [
        event
        for event in final_workspace.events
        if event.metadata.get("source") == "workspace_auto_phase_opening"
    ]
    assert [event.metadata["phase"] for event in auto_openings] == ["explore"]
    assert [event.text_payload for event in auto_openings] == [
        "Let u(x)=(2x + 1)^3. When x moves from 1 to 1.1, what are u(1) and u(1.1)?"
    ]
    assert [record["tags"] for record in final_workspace.phase_evidence["elaborate"]] == [
        ["transfer_attempt", "transfer_correct"],
        ["transfer_attempt", "transfer_correct"],
        ["transfer_attempt", "transfer_correct"],
    ]
    assert result.tutor_response is not None
    assert result.tutor_response.next_phase_ready is True
    assert result.tutor_response.next_phase_opening_prompt is None
    assert result.tutor_response.evidence_request is None


@pytest.mark.asyncio
async def test_first_engage_reply_is_generated_as_an_explore_turn(
    db_session,
    monkeypatch,
):
    scenario = _create_workspace_scenario(db_session)
    generated_phases: list[str] = []

    async def explore_tutor(**kwargs):
        generated_phases.append(kwargs["current_phase"])
        return (
            _tutor(
                tags=[],
                text=(
                    "Let u(x)=x². When x moves from 1 to 1.1, what are u(1) "
                    "and u(1.1)?"
                ),
            ),
            {"ai_source": "test_double"},
        )

    monkeypatch.setattr(
        "app.modules.workspaces.service.generate_tutor_response",
        explore_tutor,
    )

    result = await append_workspace_event(
        db_session,
        user=scenario["user"],
        workspace_id=scenario["workspace"].id,
        event_type="text",
        actor_type="learner",
        text_payload="the x²",
        image_asset_id=None,
        media_artifact_id=None,
        metadata={},
    )

    assert result is not None
    assert generated_phases == ["explore"]
    assert result.workspace.current_phase == "explore"
    assert result.tutor_response is not None
    assert result.tutor_response.text.startswith("Let u(x)=x²")
    assert result.tutor_response.next_phase_opening_prompt is None


def test_repeated_visual_request_reuses_active_media_job(db_session, monkeypatch):
    scenario = _create_workspace_scenario(db_session)
    workspace = db_session.get(WorkspaceSession, scenario["workspace"].id)
    assert workspace is not None
    workspace.metadata_json = {
        **dict(workspace.metadata_json or {}),
        "current_phase": "explore",
    }

    artifact = MediaArtifact(
        user_id=scenario["user"].id,
        track_id=workspace.track_id,
        module_id=workspace.module_id,
        workspace_id=workspace.id,
        concept_id=scenario["concept"].id,
        template_id="opaque.visual.v1",
        spec_json={},
        language="en",
        quality_profile="standard",
        title="Active visual",
        status="queued",
    )
    db_session.add(artifact)
    db_session.flush()
    job = MediaJob(
        artifact_id=artifact.id,
        status="queued",
        message="Job is queued for rendering.",
    )
    event = WorkspaceEvent(
        workspace_session_id=workspace.id,
        event_index=2,
        event_type="media_generated",
        actor_type="system",
        text_payload="",
        media_artifact_id=artifact.id,
        metadata_json={"requested_phase": "explore"},
    )
    db_session.add_all([job, event])
    db_session.commit()

    def unexpected_generation(**_kwargs):
        raise AssertionError("active request should be reused before spec generation")

    monkeypatch.setattr(
        "app.modules.workspaces.service.queue_context_animation_job",
        unexpected_generation,
    )
    response = queue_workspace_video_generation(
        db_session,
        user=scenario["user"],
        workspace_id=workspace.id,
        generation_mode="context_auto",
        template_id=None,
        spec_json={},
        language="en",
        quality_profile="standard",
        concept_id=scenario["concept"].id,
        metadata={},
    )

    assert response is not None
    assert response.queue.job_id == job.id
    assert response.queue.artifact_id == artifact.id
    assert response.event.id == event.id


def test_ready_media_adds_one_reflection_prompt_without_phase_evidence(db_session):
    scenario = _create_workspace_scenario(db_session)
    workspace = db_session.get(WorkspaceSession, scenario["workspace"].id)
    assert workspace is not None
    workspace.metadata_json = {
        **dict(workspace.metadata_json or {}),
        "current_phase": "explain",
    }
    phase_evidence_before = dict(workspace.metadata_json.get("phase_evidence") or {})

    artifact = MediaArtifact(
        user_id=scenario["user"].id,
        track_id=workspace.track_id,
        module_id=workspace.module_id,
        workspace_id=workspace.id,
        concept_id=scenario["concept"].id,
        template_id="manim.function_composition_transform.v1",
        spec_json={"title": "Outer and inner functions"},
        language="en",
        quality_profile="standard",
        title="Outer and inner functions",
        status="ready",
    )
    db_session.add(artifact)
    db_session.flush()
    db_session.add(
        WorkspaceEvent(
            workspace_session_id=workspace.id,
            event_index=2,
            event_type="media_generated",
            actor_type="system",
            text_payload="",
            media_artifact_id=artifact.id,
            metadata_json={"requested_phase": "explore"},
        )
    )
    db_session.commit()

    first = read_workspace(
        db_session,
        user=scenario["user"],
        workspace_id=workspace.id,
    )
    second = read_workspace(
        db_session,
        user=scenario["user"],
        workspace_id=workspace.id,
    )

    assert first is not None
    assert second is not None
    follow_ups = [event for event in second.events if event.event_type == "media_ready"]
    assert len(follow_ups) == 1
    assert follow_ups[0].actor_type == "tutor"
    assert follow_ups[0].media_artifact_id == artifact.id
    assert follow_ups[0].metadata["intent"] == "reflect_on_visualization"
    assert follow_ups[0].metadata["mastery_delta"] == 0.0
    assert second.current_phase == "explain"
    assert second.phase_evidence == phase_evidence_before


def test_workspace_posttest_start_only_queues_generation(db_session, monkeypatch):
    scenario = _create_workspace_scenario(db_session)
    workspace = db_session.get(WorkspaceSession, scenario["workspace"].id)
    assert workspace is not None
    workspace.metadata_json = {
        **dict(workspace.metadata_json or {}),
        "current_phase": "evaluate",
        "posttest_eligible": True,
    }
    db_session.commit()

    def unexpected_generation(*_args, **_kwargs):
        raise AssertionError("HTTP request path must not generate posttest questions")

    monkeypatch.setattr(
        "app.modules.workspaces.service._posttest_service.generation_service.create_fresh_questions",
        unexpected_generation,
    )

    result = start_posttest(
        db_session,
        user=scenario["user"],
        workspace_id=workspace.id,
    )

    assert result is not None
    assert result.posttest_trigger is not None
    assert result.posttest_trigger["status"] == "generating"
    assert result.posttest_trigger["question_count"] == 0
    assert result.posttest_eligible is True
    assessment = db_session.scalar(
        select(AssessmentSession).where(
            AssessmentSession.id
            == UUID(result.posttest_trigger["posttest_session_id"])
        )
    )
    assert assessment is not None
    assert assessment.status == "generating"
    assert assessment.metadata_json["generation_state"]["status"] == "queued"
    assert assessment.questions == []


def test_queued_posttest_worker_marks_workspace_ready_once(db_session, monkeypatch):
    scenario = _create_workspace_scenario(db_session)
    workspace = db_session.get(WorkspaceSession, scenario["workspace"].id)
    assert workspace is not None
    workspace.metadata_json = {
        **dict(workspace.metadata_json or {}),
        "current_phase": "evaluate",
        "posttest_eligible": True,
    }
    db_session.commit()
    queued = start_posttest(
        db_session,
        user=scenario["user"],
        workspace_id=workspace.id,
    )
    assert queued is not None
    assert queued.posttest_trigger is not None
    assessment_id = UUID(queued.posttest_trigger["posttest_session_id"])

    calls = 0

    def complete_generation(session, **_kwargs):
        nonlocal calls
        calls += 1
        assessment = session.get(AssessmentSession, assessment_id)
        assert assessment is not None
        assessment.status = "active"
        assessment.metadata_json = {
            **dict(assessment.metadata_json or {}),
            "generation_state": {
                "status": "ready",
                "completed_questions": 10,
                "total_questions": 10,
            },
        }
        session.commit()
        return SimpleNamespace(session_id=assessment.id, total_questions=10)

    monkeypatch.setattr(
        "app.modules.workspaces.service._posttest_service.start",
        complete_generation,
    )

    first = _process_queued_posttest_generation(
        db_session,
        user_id=scenario["user"].id,
        workspace_id=workspace.id,
        posttest_session_id=assessment_id,
    )
    second = _process_queued_posttest_generation(
        db_session,
        user_id=scenario["user"].id,
        workspace_id=workspace.id,
        posttest_session_id=assessment_id,
    )

    db_session.refresh(workspace)
    assert first is True
    assert second is False
    assert calls == 1
    assert workspace.metadata_json["posttest_eligible"] is False
    assert workspace.metadata_json["posttest_trigger"]["status"] == "ready"
    assert workspace.metadata_json["posttest_trigger"]["question_count"] == 10


def test_context_visual_request_defers_spec_generation_to_worker(db_session, monkeypatch):
    scenario = _create_workspace_scenario(db_session)
    workspace = scenario["workspace"]

    def unexpected_generation(**_kwargs):
        raise AssertionError("request path must not generate an animation spec")

    monkeypatch.setattr(
        "app.modules.learning.service.generate_spec_from_workspace_context",
        unexpected_generation,
    )
    monkeypatch.setattr(
        "app.modules.learning.service._publish_media_job_to_queue",
        lambda **_kwargs: True,
    )

    queue = queue_context_animation_job(
        db_session,
        user=scenario["user"],
        workspace_id=workspace.id,
        concept_id=scenario["concept"].id,
        language="en",
        quality_profile="standard",
    )

    artifact = db_session.get(MediaArtifact, queue.artifact_id)
    job = db_session.get(MediaJob, queue.job_id)
    assert artifact is not None
    assert job is not None
    assert queue.status == "queued"
    assert artifact.template_id == "pending.context_auto"
    assert artifact.spec_json == {}
    assert artifact.metadata_json["generation_mode"] == "context_auto"
    assert job.message == "Queued for animation spec generation."


def test_workspace_context_preserves_semantic_template_routing_signals(db_session):
    scenario = _create_workspace_scenario(db_session)
    concept = scenario["concept"]
    concept.metadata_json = {
        "concept_type": "derivative_rate_change_model",
        "concept_subtype": "derivative_rate_change_model.aturan_rantai",
        "default_template_id": "manim.function_composition_transform.v1",
        "recommended_visual_engine": "manim",
        "concept_visual_pattern": "nested function machine and outer-inner decomposition",
    }
    db_session.commit()

    workspace = create_or_resume_workspace(
        db_session,
        user=scenario["user"],
        track_id=scenario["workspace"].track_id,
        module_id=scenario["workspace"].module_id,
        content_mode="chat",
    )
    metadata = workspace.learning_context
    persisted = db_session.get(WorkspaceSession, workspace.id)
    assert persisted is not None
    route_context = persisted.metadata_json

    assert metadata["current_module"]["concept_code"] == concept.code
    assert route_context["active_concept_subtype"].endswith(".aturan_rantai")
    assert route_context["active_concept_visual_pattern"].startswith("nested function")
    assert route_context["active_visual_engine"] == "manim"
    assert route_context["active_template_id"] == (
        "manim.function_composition_transform.v1"
    )


@pytest.mark.asyncio
async def test_declining_checkpoint_stays_in_phase_until_fresh_evidence(
    db_session,
    monkeypatch,
):
    scenario = _create_workspace_scenario(db_session)
    persisted = db_session.get(WorkspaceSession, scenario["workspace"].id)
    assert persisted is not None
    persisted.metadata_json = {
        **dict(persisted.metadata_json or {}),
        "current_phase": "explore",
        "phase_history": [
            {
                "phase": "explore",
                "entered_at": persisted.created_at.isoformat(),
                "exited_at": None,
                "turn_count": 2,
            }
        ],
        "phase_transition_pending": True,
        "phase_evidence": {
            "explore": [
                {
                    "recorded_at": persisted.created_at.isoformat(),
                    "event_type": "text",
                    "tags": ["exploration_attempt", "pattern_identified"],
                    "correctness": "correct",
                    "misconception_status": "none",
                    "confidence": 0.95,
                    "evaluation_outcome": None,
                }
            ]
        },
    }
    db_session.commit()
    monkeypatch.setattr(
        "app.modules.workspaces.service.generate_tutor_response",
        _fake_tutor(
            iter(
                [
                    _tutor(tags=["exploration_attempt", "pattern_identified"]),
                    _tutor(tags=["pattern_identified"]),
                ]
            )
        ),
    )

    declined = await append_workspace_event(
        db_session,
        user=scenario["user"],
        workspace_id=persisted.id,
        event_type="text",
        actor_type="learner",
        text_payload="Not yet, I still need help with this part.",
        image_asset_id=None,
        media_artifact_id=None,
        metadata={
            "interaction_type": "phase_checkpoint",
            "checkpoint_decision": "stay",
        },
    )

    assert declined is not None
    assert declined.workspace.current_phase == "explore"
    assert declined.workspace.phase_transition_pending is False
    assert declined.tutor_response is not None
    assert declined.tutor_response.evidence_tags == []
    assert declined.mastery_update is not None
    assert declined.mastery_update.delta == 0
    assert declined.mastery_update.reason == (
        "unverified_activity_recorded_without_mastery_delta"
    )
    db_session.refresh(persisted)
    assert persisted.metadata_json["phase_readiness_recheck_required"] == "explore"

    retried = await append_workspace_event(
        db_session,
        user=scenario["user"],
        workspace_id=persisted.id,
        event_type="text",
        actor_type="learner",
        text_payload="Now I can track the change through both layers.",
        image_asset_id=None,
        media_artifact_id=None,
        metadata={},
    )

    assert retried is not None
    assert retried.workspace.current_phase == "explore"
    assert retried.workspace.phase_transition_pending is True
    db_session.refresh(persisted)
    assert persisted.metadata_json["phase_readiness_recheck_required"] is None


def test_media_worker_generates_context_spec_before_validation(db_session, monkeypatch):
    scenario = _create_workspace_scenario(db_session)
    monkeypatch.setattr(
        "app.modules.learning.service._publish_media_job_to_queue",
        lambda **_kwargs: True,
    )
    queue = queue_context_animation_job(
        db_session,
        user=scenario["user"],
        workspace_id=scenario["workspace"].id,
        concept_id=scenario["concept"].id,
        language="en",
        quality_profile="standard",
    )
    artifact = db_session.get(MediaArtifact, queue.artifact_id)
    job = db_session.get(MediaJob, queue.job_id)
    assert artifact is not None
    assert job is not None

    monkeypatch.setattr(
        "app.modules.learning.service.generate_spec_from_workspace_context",
        lambda **_kwargs: WorkspaceGeneratedSpec(
            template_id="opaque.visual.v1",
            spec_json={"title": "Composite change"},
            debug_meta={"spec_source": "test_generator"},
        ),
    )

    from app.modules.learning import service as learning_service

    learning_service._generate_context_spec_for_worker(
        session=db_session,
        job=job,
        artifact=artifact,
    )

    assert artifact.template_id == "opaque.visual.v1"
    assert artifact.spec_json == {"title": "Composite change"}
    assert artifact.title == "Composite change"
    assert artifact.metadata_json["spec_source"] == "test_generator"
    assert artifact.render_meta_json["spec_generation"] == "completed"
    assert job.progress == 8


def _tutor(
    *,
    tags: list[str],
    text: str = "Continue with the next evidence task.",
    evaluation_outcome: str | None = None,
    next_phase_opening_prompt: str | None = None,
    evidence_request: dict | None = None,
) -> TutorResponseRead:
    return TutorResponseRead(
        text=text,
        intent="probe_understanding",
        evidence_tags=tags,
        correctness="correct",
        misconception_status="none",
        confidence=0.95,
        evaluation_outcome=evaluation_outcome,
        next_phase_opening_prompt=next_phase_opening_prompt,
        evidence_request=evidence_request,
    )


def _fake_tutor(responses: Iterator[TutorResponseRead]):
    async def generate_tutor_response(**_kwargs):
        return next(responses), {"ai_source": "test_double"}

    return generate_tutor_response


def _create_workspace_scenario(db_session):
    user = UserAccount(
        supabase_user_id="workspace-5e-flow-user",
        display_name="Workspace 5E Flow",
        provider_subject="workspace-5e-flow-user",
    )
    subject = Subject(code="workspace-5e-math", name="Workspace 5E Math")
    db_session.add_all([user, subject])
    db_session.flush()

    concept = KnowledgeConcept(
        subject_id=subject.id,
        code="workspace-5e-composite-rule",
        title="Composite function rule",
        display_order=1,
    )
    db_session.add(concept)
    db_session.flush()

    goal = LearningGoal(
        user_id=user.id,
        subject_id=subject.id,
        target_concept_id=concept.id,
        raw_topic="Composite function rule",
        normalized_topic="Composite function rule",
        status="track_ready",
        metadata_json={},
    )
    db_session.add(goal)
    db_session.flush()

    track = LearningTrack(
        user_id=user.id,
        learning_goal_id=goal.id,
        title="Repair composite function rule",
        status="active",
        metadata_json={},
    )
    db_session.add(track)
    db_session.flush()

    module = TrackModule(
        track_id=track.id,
        concept_id=concept.id,
        title="Repair composite function rule",
        sort_order=1,
        status="ready",
        metadata_json={
            "module_role": "prerequisite_gap",
            "original_target": {
                "concept_id": str(concept.id),
                "concept_code": concept.code,
                "title": concept.title,
            },
            "current_module": {
                "concept_id": str(concept.id),
                "concept_code": concept.code,
                "title": concept.title,
                "role": "prerequisite_gap",
            },
            "diagnosis_reason": "A required transformation step was omitted.",
            "diagnosis_evidence": {
                "status": "gap",
                "confidence": 0.94,
            },
        },
    )
    db_session.add(module)
    db_session.commit()

    workspace = create_or_resume_workspace(
        db_session,
        user=user,
        track_id=track.id,
        module_id=module.id,
        content_mode="chat",
    )
    return {
        "user": user,
        "concept": concept,
        "workspace": workspace,
    }
