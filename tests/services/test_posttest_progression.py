from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.modules.accounts.models import UserAccount
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.learning.models import (
    AssessmentQuestion,
    AssessmentSession,
    LearnerConceptState,
    LearningGoal,
    LearningTrack,
    TrackModule,
)
from app.modules.posttests.service import (
    AdaptivePosttestService,
    _create_posttest_questions,
    _target_concept_for_goal_or_workspace,
)
from app.modules.workspaces.models import WorkspaceSession


def test_workspace_or_module_concept_takes_precedence_over_goal_target(db_session):
    scenario = _create_scenario(db_session)

    workspace_target = _target_concept_for_goal_or_workspace(
        db_session,
        goal=scenario["goal"],
        workspace=scenario["workspace"],
        module_id=None,
    )
    module_target = _target_concept_for_goal_or_workspace(
        db_session,
        goal=scenario["goal"],
        workspace=None,
        module_id=scenario["modules"][1].id,
    )

    assert workspace_target is not None
    assert workspace_target.id == scenario["concepts"][0].id
    assert module_target is not None
    assert module_target.id == scenario["concepts"][1].id
    assert workspace_target.id != scenario["goal"].target_concept_id


def test_passing_module_posttest_completes_only_current_and_unlocks_immediate_next(
    db_session,
):
    scenario = _create_scenario(db_session, correct_count=7)

    result = AdaptivePosttestService().finalize(
        db_session,
        user=scenario["user"],
        session_id=scenario["assessment"].id,
    )

    assert result is not None
    assert result.progression is not None
    assert result.progression.passed is True
    assert result.progression.module_id == scenario["modules"][0].id
    assert result.progression.module_status == "completed"
    assert result.progression.next_module_id == scenario["modules"][1].id
    assert result.progression.next_module_status == "ready"
    assert result.progression.track_status == "active"
    assert result.progression.track_progress_percent == 33
    assert result.progression.workspace_status == "completed"
    assert result.progression.goal_status == "in_progress"

    modules = _modules(db_session, scenario["track"].id)
    assert [module.status for module in modules] == ["completed", "ready", "locked"]
    assert scenario["track"].status == "active"
    assert scenario["track"].progress_percent == 33
    assert scenario["goal"].status == "in_progress"
    assert scenario["goal"].completed_at is None
    assert scenario["workspace"].status == "completed"
    assert scenario["workspace"].metadata_json["posttest_eligible"] is False
    assert scenario["workspace"].metadata_json["posttest_trigger"]["status"] == "completed"
    assert scenario["workspace"].metadata_json["posttest_progression"]["module_id"] == str(
        scenario["modules"][0].id
    )

    concept_state = db_session.scalar(
        select(LearnerConceptState).where(
            LearnerConceptState.user_id == scenario["user"].id,
            LearnerConceptState.concept_id == scenario["concepts"][0].id,
        )
    )
    assert concept_state is not None
    assert concept_state.status == "mastered"


@pytest.mark.parametrize(
    ("diagnostic_signal", "expected_phase", "expected_reason"),
    [
        (
            "misconception_detected",
            "explore",
            "posttest_misconception_still_active",
        ),
        (
            "concept_gap_likely",
            "elaborate",
            "posttest_transfer_needs_practice",
        ),
    ],
)
def test_failing_posttest_keeps_module_active_and_routes_remediation(
    db_session,
    diagnostic_signal,
    expected_phase,
    expected_reason,
):
    scenario = _create_scenario(
        db_session,
        statuses=("completed", "ready", "locked"),
        workspace_status="completed",
        correct_count=6,
        diagnostic_signal=diagnostic_signal,
    )

    result = AdaptivePosttestService().finalize(
        db_session,
        user=scenario["user"],
        session_id=scenario["assessment"].id,
    )

    assert result is not None
    assert result.progression is not None
    assert result.progression.passed is False
    assert result.progression.module_status == "active"
    assert result.progression.next_module_status == "locked"
    assert result.progression.workspace_status == "active"
    assert result.progression.goal_status == "in_progress"
    assert result.progression.remediation_phase == expected_phase
    assert result.progression.remediation_reason == expected_reason

    modules = _modules(db_session, scenario["track"].id)
    assert [module.status for module in modules] == ["active", "locked", "locked"]
    assert scenario["track"].progress_percent == 0
    assert scenario["goal"].status == "in_progress"
    assert scenario["workspace"].status == "active"
    workspace_metadata = scenario["workspace"].metadata_json
    assert workspace_metadata["posttest_eligible"] is False
    assert workspace_metadata["current_phase"] == expected_phase
    assert workspace_metadata["phase_history"][-1]["phase"] == expected_phase
    assert expected_phase in workspace_metadata["visited_5e_phases"]
    assert workspace_metadata["posttest_trigger"]["status"] == "needs_remediation"
    assert workspace_metadata["posttest_remediation"] == {
        "assessment_session_id": str(scenario["assessment"].id),
        "phase": expected_phase,
        "reason": expected_reason,
        "weak_question_types": ["error_analysis"],
        "routed_at": workspace_metadata["posttest_remediation"]["routed_at"],
    }


def test_passing_original_target_completes_goal_only_after_every_module_is_done(
    db_session,
):
    scenario = _create_scenario(
        db_session,
        current_index=2,
        statuses=("completed", "completed", "active"),
        correct_count=7,
    )

    result = AdaptivePosttestService().finalize(
        db_session,
        user=scenario["user"],
        session_id=scenario["assessment"].id,
    )

    assert result is not None
    assert result.progression is not None
    assert result.progression.passed is True
    assert result.progression.module_id == scenario["modules"][2].id
    assert result.progression.next_module_id is None
    assert result.progression.track_status == "completed"
    assert result.progression.track_progress_percent == 100
    assert result.progression.goal_status == "completed"
    assert [module.status for module in _modules(db_session, scenario["track"].id)] == [
        "completed",
        "completed",
        "completed",
    ]
    assert scenario["track"].status == "completed"
    assert scenario["goal"].status == "completed"
    assert scenario["goal"].completed_at is not None


def test_posttest_generation_resumes_after_cached_batch(db_session, monkeypatch):
    scenario = _create_scenario(db_session)
    assessment = scenario["assessment"]
    assessment.status = "active"
    assessment.decision_state_json = {}
    concept = scenario["concepts"][0]
    assessment.target_concept_id = concept.id

    for sort_order in range(1, 4):
        db_session.add(
            AssessmentQuestion(
                session_id=assessment.id,
                concept_id=concept.id,
                step_label="Posttest",
                topic=concept.title,
                prompt=f"Cached question {sort_order}",
                difficulty_label="Medium",
                sort_order=sort_order,
            )
        )
    db_session.commit()

    generated_batches = []

    def fake_generate(
        _generation_service,
        session,
        *,
        assessment,
        concept,
        difficulties,
        **_kwargs,
    ):
        generated_batches.append(list(difficulties))
        current_count = session.scalar(
            select(func.count())
            .select_from(AssessmentQuestion)
            .where(AssessmentQuestion.session_id == assessment.id)
        )
        created = []
        for offset, difficulty in enumerate(difficulties, start=1):
            question = AssessmentQuestion(
                session_id=assessment.id,
                concept_id=concept.id,
                step_label="Posttest",
                topic=concept.title,
                prompt=f"Generated question {current_count + offset}",
                difficulty_label=difficulty.title(),
                sort_order=current_count + offset,
            )
            session.add(question)
            created.append(question)
        session.flush()
        return created

    monkeypatch.setattr(
        "app.modules.posttests.service._generate_posttest_question_chunk",
        fake_generate,
    )

    questions = _create_posttest_questions(
        object(),
        db_session,
        assessment=assessment,
        concept=concept,
        language="en",
        diagnosis_context="cached generation test",
    )

    assert len(questions) == 10
    assert generated_batches == [
        ["hard", "hard", "hard"],
        ["hard", "hard"],
        ["hard", "hard"],
    ]
    assert assessment.metadata_json["generation_state"]["completed_questions"] == 10


def _create_scenario(
    db_session,
    *,
    current_index: int = 0,
    statuses: tuple[str, str, str] = ("active", "locked", "locked"),
    workspace_status: str = "active",
    correct_count: int = 7,
    diagnostic_signal: str = "concept_gap_likely",
):
    user = UserAccount(
        supabase_user_id="posttest-progression-user",
        display_name="Posttest Progression",
        provider_subject="posttest-progression-user",
    )
    subject = Subject(code="posttest-math", name="Posttest Math")
    db_session.add_all([user, subject])
    db_session.flush()

    concepts = [
        KnowledgeConcept(
            subject_id=subject.id,
            code=code,
            title=title,
            display_order=index,
        )
        for index, (code, title) in enumerate(
            [
                ("derivative.chain-rule", "Aturan Rantai"),
                ("derivative.trigonometric", "Turunan Trigonometri"),
                ("derivative.curve-sketch", "Sketsa Kurva"),
            ],
            start=1,
        )
    ]
    db_session.add_all(concepts)
    db_session.flush()

    goal = LearningGoal(
        user_id=user.id,
        subject_id=subject.id,
        target_concept_id=concepts[-1].id,
        raw_topic="Sketsa kurva menggunakan turunan",
        normalized_topic="Sketsa kurva menggunakan turunan",
        status="in_progress",
    )
    db_session.add(goal)
    db_session.flush()
    track = LearningTrack(
        user_id=user.id,
        learning_goal_id=goal.id,
        title="Repair aturan rantai menuju sketsa kurva",
        status="active",
        progress_percent=0,
    )
    db_session.add(track)
    db_session.flush()

    modules = [
        TrackModule(
            track_id=track.id,
            concept_id=concept.id,
            title=concept.title,
            sort_order=index,
            status=status,
        )
        for index, (concept, status) in enumerate(zip(concepts, statuses), start=1)
    ]
    db_session.add_all(modules)
    db_session.flush()

    current_concept = concepts[current_index]
    current_module = modules[current_index]
    workspace = WorkspaceSession(
        user_id=user.id,
        track_id=track.id,
        module_id=current_module.id,
        current_topic=current_concept.title,
        status=workspace_status,
        metadata_json={
            "current_phase": "evaluate",
            "phase_transition_pending": False,
            "posttest_eligible": True,
            "phase_history": [
                {
                    "phase": "evaluate",
                    "entered_at": "2026-08-04T00:00:00+00:00",
                    "exited_at": None,
                    "turn_count": 2,
                }
            ],
            "visited_5e_phases": ["engage", "explore", "explain", "elaborate", "evaluate"],
            "posttest_trigger": {"status": "ready"},
        },
    )
    db_session.add(workspace)
    db_session.flush()

    node_result = {
        "concept_id": str(current_concept.id),
        "concept_title": current_concept.title,
        "total_questions": 10,
        "answered_count": 10,
        "correct_count": correct_count,
        "answer_score_sum": float(correct_count),
        "evidence_score_sum": float(correct_count),
        "confidence_sum": 7.0,
        "attempts": [
            {
                "question_type": "error_analysis",
                "is_correct": False,
                "diagnostic_signal": diagnostic_signal,
                "reasoning_signal": "not_applicable",
            }
        ],
    }
    assessment = AssessmentSession(
        user_id=user.id,
        learning_goal_id=goal.id,
        track_id=track.id,
        target_concept_id=current_concept.id,
        session_type="posttest",
        title=f"Posttest: {current_concept.title}",
        status="completed",
        metadata_json={
            "workspace_session_id": str(workspace.id),
            "learning_goal_id": str(goal.id),
            "track_id": str(track.id),
            "module_id": str(current_module.id),
            "target_concept_id": str(current_concept.id),
            "target_concept_code": current_concept.code,
            "target_concept_title": current_concept.title,
            "language": "id",
        },
        decision_state_json={
            "current_index": 10,
            "node_results": {current_concept.code: node_result},
        },
        max_questions=10,
        max_depth=0,
        max_nodes_visited=1,
    )
    db_session.add(assessment)
    db_session.commit()
    return {
        "user": user,
        "concepts": concepts,
        "goal": goal,
        "track": track,
        "modules": modules,
        "workspace": workspace,
        "assessment": assessment,
    }


def _modules(db_session, track_id):
    return list(
        db_session.scalars(
            select(TrackModule)
            .where(TrackModule.track_id == track_id)
            .order_by(TrackModule.sort_order)
        )
    )
