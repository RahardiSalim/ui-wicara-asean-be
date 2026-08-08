from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import UserAccount
from app.modules.curriculum.models import ConceptEdge, KnowledgeConcept, Subject
from app.modules.evidence.models import ImageAsset
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentOption,
    AssessmentQuestion,
    AssessmentSession,
    LearnerConceptState,
    LearningGoal,
    TrackModule,
)
from app.modules.learning_goal_resolution.models import LearningGoalResolution

ACCOUNT_ID = UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture(autouse=True)
def _allow_dev_assessment_generation_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WICARA_ASSESSMENT_DEV_FALLBACK_QUESTIONS", "1")


def test_resolve_is_read_only_and_confirm_allows_repeated_targets(client):
    _override_account(client)

    resolve_response = client.post(
        "/api/v1/learning-goals/resolve",
        json={
            "raw_query": "aku mau belajar kali-kalian",
            "subject_code": "math",
            "education_level": "sd",
            "grade_level": "3",
            "language": "id",
        },
    )

    assert resolve_response.status_code == 200
    resolved = resolve_response.json()
    assert resolved["status"] == "needs_confirmation"
    assert resolved["suggested_concept"]["concept_code"] == "math.multiplication"

    with _session_for_client(client) as session:
        assert session.scalar(select(LearningGoal).where(LearningGoal.user_id == ACCOUNT_ID)) is None

    confirm_response = client.post(
        f"/api/v1/learning-goals/resolve/{resolved['resolution_id']}/confirm"
    )
    assert confirm_response.status_code == 200
    goal = confirm_response.json()
    assert goal["status"] == "confirmed"

    second = client.post(
        "/api/v1/learning-goals/resolve",
        json={"raw_query": "belajar penjumlahan", "subject_code": "math"},
    )
    assert second.status_code == 200
    distinct_target = client.post(
        f"/api/v1/learning-goals/resolve/{second.json()['resolution_id']}/confirm"
    )
    assert distinct_target.status_code == 200

    third = client.post(
        "/api/v1/learning-goals/resolve",
        json={"raw_query": "belajar perkalian lagi", "subject_code": "math"},
    )
    assert third.status_code == 200
    repeated_target = client.post(
        f"/api/v1/learning-goals/resolve/{third.json()['resolution_id']}/confirm"
    )
    assert repeated_target.status_code == 200

    with _session_for_client(client) as session:
        active_goals = list(
            session.scalars(
                select(LearningGoal).where(
                    LearningGoal.user_id == ACCOUNT_ID,
                    LearningGoal.status == "confirmed",
                )
            )
        )
        assert len(active_goals) == 3
        assert len({goal.target_concept_id for goal in active_goals}) == 2


def test_resolve_tolerates_null_llm_confidence(client, monkeypatch):
    _override_account(client)

    async def fake_resolve_with_ai(*, raw_query, candidates, **_kwargs):
        return {
            "status": "needs_confirmation",
            "concept_code": candidates[0].concept.code,
            "confidence": None,
            "provider": "test",
            "model": "test",
        }

    from app.modules.learning_goal_resolution.router import service

    monkeypatch.setattr(service, "_resolve_with_ai", fake_resolve_with_ai)
    response = client.post(
        "/api/v1/learning-goals/resolve",
        json={"raw_query": "aku mau belajar kali-kalian", "subject_code": "math"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_confirmation"
    assert payload["confidence"] > 0


def test_resolve_needs_clarification_when_query_has_no_candidate_signal(client):
    _override_account(client)

    response = client.post(
        "/api/v1/learning-goals/resolve",
        json={
            "raw_query": "zzzzzz topik tidak ada",
            "subject_code": "math",
            "language": "en",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_clarification"
    assert payload["suggested_concept"] is None
    assert payload["clarification_question"]


def test_resolve_defaults_to_math_scope_when_subject_missing(client, monkeypatch):
    _override_account(client)
    from app.modules.learning_goal_resolution.router import service

    captured_kwargs: dict[str, object] = {}

    async def fake_resolve_progressively(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return None

    monkeypatch.setattr(service, "_resolve_progressively", fake_resolve_progressively)

    response = client.post(
        "/api/v1/learning-goals/resolve",
        json={
            "raw_query": "aku mau belajar gaya",
            "education_level": "senior_high",
            "grade_level": "11",
            "language": "id",
        },
    )

    assert response.status_code == 200
    assert captured_kwargs["subject_code"] == "math"
    assert captured_kwargs["allow_cross_subject"] is False
    assert response.json()["search_scope"] == "no_match"


def test_resolve_allows_foundational_node_for_higher_grade_user(client):
    _override_account(client)

    response = client.post(
        "/api/v1/learning-goals/resolve",
        json={
            "raw_query": "aku mau refresh perkalian dasar",
            "subject_code": "math",
            "education_level": "senior_high",
            "grade_level": "11",
            "language": "id",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_confirmation"
    assert payload["search_scope"] == "same_subject_all_grades"
    assert payload["suggested_concept"]["concept_code"] == "math.multiplication"
    assert payload["suggested_concept"]["grade_relation"] == "below_current_level"
    assert "fondasi" in payload["suggested_concept"]["level_note"]


def test_pretest_start_is_idempotent_and_generates_fresh_target_node_set(client):
    _override_account(client)
    learning_goal_id = _confirmed_goal_id(client)

    first = client.post(
        "/api/v1/pretests/start",
        json={"learning_goal_id": learning_goal_id, "depth": 2},
    )
    second = client.post(
        "/api/v1/pretests/start",
        json={"learning_goal_id": learning_goal_id, "depth": 2},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["session_id"] == second.json()["session_id"]
    assert first.json()["current_question"]["difficulty"] == "medium"
    assert first.json()["current_question"]["pack_id"] is None

    with _session_for_client(client) as session:
        questions = first.json()["decision_state"]["generated_questions"]
        target_code = first.json()["target_concept"]["concept_code"]
        assert set(questions[target_code]) == {"easy", "medium", "hard"}
        assert questions[target_code]["medium"] == first.json()["current_question"]["id"]
        stored_question = session.get(AssessmentQuestion, UUID(first.json()["current_question"]["id"]))
        assert stored_question is not None
        assert stored_question.metadata_json["non_reusable"] is True


def test_pretest_start_returns_503_when_ai_is_unavailable(client, monkeypatch):
    _override_account(client)
    monkeypatch.delenv("WICARA_ASSESSMENT_DEV_FALLBACK_QUESTIONS")
    monkeypatch.setattr(
        "app.modules.pretests.generation_service.get_ai_settings",
        lambda: SimpleNamespace(openrouter_api_key=""),
    )

    response = client.post(
        "/api/v1/pretests/start",
        json={"learning_goal_id": _confirmed_goal_id(client)},
    )

    assert response.status_code == 503
    assert "AI question generation requires" in response.json()["detail"]


def test_pretest_start_returns_503_after_ai_generation_timeout(client, monkeypatch):
    _override_account(client)
    monkeypatch.delenv("WICARA_ASSESSMENT_DEV_FALLBACK_QUESTIONS")
    monkeypatch.setenv("WICARA_PRETEST_LLM_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(
        "app.modules.pretests.generation_service.get_ai_settings",
        lambda: SimpleNamespace(openrouter_api_key="test-key", ai_request_timeout_seconds=30.0),
    )

    async def slow_generate(**_kwargs):
        await asyncio.sleep(2)

    monkeypatch.setattr(
        "app.modules.pretests.generation_service.ai_client.generate",
        slow_generate,
    )

    response = client.post(
        "/api/v1/pretests/start",
        json={"learning_goal_id": _confirmed_goal_id(client)},
    )

    assert response.status_code == 503
    assert "failed validation after bounded retries" in response.json()["detail"]
    assert "attempt 2" in response.json()["detail"]


def test_answers_reuse_node_set_generate_prerequisite_set_and_reject_duplicate(client):
    _override_account(client)
    learning_goal_id = _confirmed_goal_id(client)
    start = client.post("/api/v1/pretests/start", json={"learning_goal_id": learning_goal_id})
    payload = start.json()
    question = payload["current_question"]
    wrong_option = next(option for option in question["options"] if option["label"] != "B")

    answer = client.post(
        f"/api/v1/pretests/{payload['session_id']}/answers",
        json={
            "question_id": question["id"],
            "selected_option_id": wrong_option["id"],
            "typed_reasoning": "",
        },
    )
    duplicate = client.post(
        f"/api/v1/pretests/{payload['session_id']}/answers",
        json={
            "question_id": question["id"],
            "selected_option_id": wrong_option["id"],
        },
    )

    assert answer.status_code == 200
    assert answer.json()["next_question"]["difficulty"] == "easy"
    assert answer.json()["next_question"]["pack_id"] is None
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["error"] == "QUESTION_ALREADY_ANSWERED"

    easy_question = answer.json()["next_question"]
    easy_wrong = next(option for option in easy_question["options"] if option["label"] != "B")
    prereq_answer = client.post(
        f"/api/v1/pretests/{payload['session_id']}/answers",
        json={
            "question_id": easy_question["id"],
            "selected_option_id": easy_wrong["id"],
        },
    )

    assert prereq_answer.status_code == 200
    next_question = prereq_answer.json()["next_question"]
    assert next_question["difficulty"] == "medium"
    assert next_question["concept_code"] != question["concept_code"]

    with _session_for_client(client) as session:
        questions = list(session.scalars(select(AssessmentQuestion).where(AssessmentQuestion.session_id == UUID(payload["session_id"]))))
        assert len(questions) == 6
        assert all(question.metadata_json.get("non_reusable") is True for question in questions)
        difficulties_by_concept: dict[str, set[str]] = {}
        for stored_question in questions:
            concept_code = str(stored_question.metadata_json["concept_code"])
            difficulties_by_concept.setdefault(concept_code, set()).add(
                stored_question.difficulty_label.lower()
            )
        assert len(difficulties_by_concept) == 2
        assert all(
            difficulties == {"easy", "medium", "hard"}
            for difficulties in difficulties_by_concept.values()
        )


def test_finalize_and_path_selection_create_track(client):
    _override_account(client)
    learning_goal_id = _confirmed_goal_id(client)
    start = client.post("/api/v1/pretests/start", json={"learning_goal_id": learning_goal_id})
    session_id = start.json()["session_id"]
    question = start.json()["current_question"]
    correct = next(option for option in question["options"] if option["label"] == "B")

    first = client.post(
        f"/api/v1/pretests/{session_id}/answers",
        json={"question_id": question["id"], "selected_option_id": correct["id"]},
    )
    hard_question = first.json()["next_question"]
    hard_correct = next(option for option in hard_question["options"] if option["label"] == "B")
    done = client.post(
        f"/api/v1/pretests/{session_id}/answers",
        json={"question_id": hard_question["id"], "selected_option_id": hard_correct["id"]},
    )

    assert done.status_code == 200
    assert done.json()["next_action"]["type"] == "finalize"
    assert done.json()["diagnosis"]["recommended_path"] == "review_only"
    target_metric = done.json()["diagnosis"]["target"]
    assert target_metric["answer_percent"] == 100
    assert target_metric["evidence_percent"] == 100
    assert target_metric["score_percent"] == 100
    assert target_metric["mastery_estimate_percent"] == 90
    assert target_metric["confidence_percent"] == 68
    assert target_metric["metric_source"] == "adaptive_pretest_diagnosis"
    assert target_metric["answer_metric_source"] == "official_mcq"

    dashboard = client.get(f"/api/v1/learning-goals/{learning_goal_id}/assessment-dashboard")
    assert dashboard.status_code == 200
    dashboard_payload = dashboard.json()
    assert dashboard_payload["state"] == "diagnosed"
    assert dashboard_payload["pretest"]["recommended_path"] == "review_only"
    assert dashboard_payload["pretest"]["nodes"][0]["metric_source"] == (
        "adaptive_pretest_diagnosis"
    )
    assert dashboard_payload["pretest"]["nodes"][0]["answer_metric_source"] == (
        "official_mcq"
    )
    assert dashboard_payload["comparison"]["available"] is False

    path = client.post(
        f"/api/v1/learning-goals/{learning_goal_id}/path-selection",
        json={"path_option": "review_only"},
    )

    assert path.status_code == 200
    assert path.json()["goal_status"] == "in_progress"
    assert path.json()["modules"]

    with _session_for_client(client) as session:
        goal = session.get(LearningGoal, UUID(learning_goal_id))
        assert goal.status == "in_progress"
        assessment = session.get(AssessmentSession, UUID(session_id))
        assert "diagnosis" in assessment.metadata_json
        assert session.scalar(select(TrackModule).where(TrackModule.track_id == goal.track.id)) is not None


def test_target_hard_wrong_probes_prerequisite_before_finalizing(client):
    _override_account(client)
    learning_goal_id = _confirmed_goal_id(client)
    start = client.post("/api/v1/pretests/start", json={"learning_goal_id": learning_goal_id})
    session_id = start.json()["session_id"]
    medium_question = start.json()["current_question"]
    medium_correct = next(option for option in medium_question["options"] if option["label"] == "B")

    hard_response = client.post(
        f"/api/v1/pretests/{session_id}/answers",
        json={"question_id": medium_question["id"], "selected_option_id": medium_correct["id"]},
    )
    hard_question = hard_response.json()["next_question"]
    hard_wrong = next(option for option in hard_question["options"] if option["label"] != "B")

    probe_response = client.post(
        f"/api/v1/pretests/{session_id}/answers",
        json={"question_id": hard_question["id"], "selected_option_id": hard_wrong["id"]},
    )

    assert probe_response.status_code == 200
    payload = probe_response.json()
    assert payload["next_action"] == {
        "type": "next_question",
        "concept_code": payload["next_question"]["concept_code"],
        "difficulty": "medium",
        "reason": "enter_prerequisite_node",
    }
    assert payload["next_question"]["concept_code"] != medium_question["concept_code"]
    assert payload["diagnosis"] is None


def test_assessment_dashboard_without_pretest_returns_start_state(client):
    _override_account(client)
    assert client.get("/api/v1/home").status_code == 200
    with _session_for_client(client) as session:
        subject = session.scalar(select(Subject).where(Subject.code == "matematika"))
        assert subject is not None
        concept = session.scalar(select(KnowledgeConcept).where(KnowledgeConcept.subject_id == subject.id))
        assert concept is not None
        goal = LearningGoal(
            user_id=ACCOUNT_ID,
            subject_id=subject.id,
            target_concept_id=concept.id,
            raw_topic="dashboard no pretest",
            normalized_topic="dashboard no pretest",
            status="pretest_ready",
            metadata_json={"source": "test"},
        )
        session.add(goal)
        session.commit()
        learning_goal_id = str(goal.id)

    dashboard = client.get(f"/api/v1/learning-goals/{learning_goal_id}/assessment-dashboard")

    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["state"] == "needs_pretest"
    assert payload["pretest"] is None
    assert payload["posttest"] is None
    assert payload["primary_action"]["action_type"] == "start_pretest"


def test_cancel_abandons_active_pretest_and_releases_lock(client):
    _override_account(client)
    learning_goal_id = _confirmed_goal_id(client)
    start = client.post("/api/v1/pretests/start", json={"learning_goal_id": learning_goal_id})

    cancel = client.post(f"/api/v1/learning-goals/{learning_goal_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"

    with _session_for_client(client) as session:
        assessment = session.get(AssessmentSession, UUID(start.json()["session_id"]))
        assert assessment.status == "cancelled"

    resolve = client.post(
        "/api/v1/learning-goals/resolve",
        json={"raw_query": "belajar penjumlahan", "subject_code": "math"},
    )
    confirm = client.post(f"/api/v1/learning-goals/resolve/{resolve.json()['resolution_id']}/confirm")
    assert confirm.status_code == 200


def test_posttest_six_of_ten_does_not_pass_with_unverified_written_evidence(client, monkeypatch):
    monkeypatch.setenv("WICARA_PRETEST_LLM_EVALUATION", "0")
    _override_account(client)
    learning_goal_id, concept_id, concept_code = _goal_with_posttest_node(client)

    start = client.post("/api/v1/posttests/start", json={"learning_goal_id": learning_goal_id})
    assert start.status_code == 200
    payload = start.json()
    assert payload["total_questions"] == 10

    answer = None
    for index, question in enumerate(payload["questions"]):
        is_correct = index < 6
        answer_payload = {
            "question_id": question["id"],
            "selected_option_id": _option_id_for_question(client, question["id"], correct=is_correct),
            "confidence": 8,
        }
        if not is_correct:
            answer_payload["typed_reasoning"] = "The multiplication model has six groups of four then subtract two."
        answer = client.post(
            f"/api/v1/posttests/{payload['session_id']}/answers",
            json=answer_payload,
        )
        assert answer.status_code == 200

    assert answer is not None
    last = answer.json()
    assert last["completed"] is True
    assert last["is_correct"] is False
    assert last["evaluation"]["is_correct"] is False
    assert last["evaluation"]["diagnostic_signal"] == "concept_gap_likely"
    assert last["node_result"]["answer_percent"] == 60
    assert last["node_result"]["passed"] is False

    final = client.post(f"/api/v1/posttests/{payload['session_id']}/finalize")
    assert final.status_code == 200
    assert final.json()["retake_required_concepts"] == [concept_code]

    dashboard = client.get(f"/api/v1/learning-goals/{learning_goal_id}/assessment-dashboard")
    assert dashboard.status_code == 200
    dashboard_payload = dashboard.json()
    assert dashboard_payload["state"] == "needs_retake"
    assert dashboard_payload["posttest"]["passed"] is False
    assert dashboard_payload["posttest"]["answer_percent"] == 60
    assert dashboard_payload["posttest"]["retake_required_concepts"] == [concept_code]

    with _session_for_client(client) as session:
        state = session.scalar(
            select(LearnerConceptState).where(
                LearnerConceptState.user_id == ACCOUNT_ID,
                LearnerConceptState.concept_id == concept_id,
            )
        )
        assert state is not None
        assert state.status == "review_due"


def test_posttest_seven_of_ten_passes_and_marks_concept_mastered(client):
    _override_account(client)
    learning_goal_id, concept_id, _concept_code = _goal_with_posttest_node(client)

    start = client.post("/api/v1/posttests/start", json={"learning_goal_id": learning_goal_id})
    assert start.status_code == 200
    payload = start.json()
    assert payload["total_questions"] == 10

    answer = None
    for index, question in enumerate(payload["questions"]):
        is_correct = index < 7
        answer = client.post(
            f"/api/v1/posttests/{payload['session_id']}/answers",
            json={
                "question_id": question["id"],
                "selected_option_id": _option_id_for_question(client, question["id"], correct=is_correct),
                "confidence": 9,
            },
        )
        assert answer.status_code == 200

    assert answer is not None
    node_result = answer.json()["node_result"]
    assert node_result["answer_percent"] == 70
    assert node_result["score_percent"] == 70
    assert node_result["scaled_score"] == 7
    assert node_result["passed"] is True

    final = client.post(f"/api/v1/posttests/{payload['session_id']}/finalize")
    assert final.status_code == 200
    assert final.json()["retake_required_concepts"] == []

    dashboard = client.get(f"/api/v1/learning-goals/{learning_goal_id}/assessment-dashboard")
    assert dashboard.status_code == 200
    dashboard_payload = dashboard.json()
    assert dashboard_payload["state"] == "mastered"
    assert dashboard_payload["posttest"]["passed"] is True
    assert dashboard_payload["posttest"]["passed_node_count"] == 1
    assert dashboard_payload["posttest"]["nodes"][0]["score_percent"] == 70

    with _session_for_client(client) as session:
        state = session.scalar(
            select(LearnerConceptState).where(
                LearnerConceptState.user_id == ACCOUNT_ID,
                LearnerConceptState.concept_id == concept_id,
            )
        )
        assert state is not None
        assert state.status == "mastered"
        assert state.mastery_score == 0.7


def test_posttest_accepts_canvas_asset_without_numeric_canvas_score(client):
    _override_account(client)
    learning_goal_id, _concept_id, _concept_code = _goal_with_posttest_node(client)
    canvas_asset_id = _create_canvas_asset(client)

    start = client.post("/api/v1/posttests/start", json={"learning_goal_id": learning_goal_id})
    assert start.status_code == 200
    question = start.json()["current_question"]

    answer = client.post(
        f"/api/v1/posttests/{start.json()['session_id']}/answers",
        json={
            "question_id": question["id"],
            "selected_option_id": _option_id_for_question(client, question["id"], correct=True),
            "confidence": 7,
            "canvas_asset_id": canvas_asset_id,
            "used_canvas": True,
        },
    )

    assert answer.status_code == 200
    evaluation = answer.json()["evaluation"]
    assert evaluation["canvas_status"] == "stored_not_evaluated"
    assert evaluation["canvas_score"] is None
    assert evaluation["evidence_score"] == 1.0

    with _session_for_client(client) as session:
        attempt = session.get(AssessmentAttempt, UUID(answer.json()["attempt_id"]))
        assert str(attempt.canvas_asset_id) == canvas_asset_id


def _confirmed_goal_id(client) -> str:
    response = client.post(
        "/api/v1/learning-goals/resolve",
        json={"raw_query": "aku mau belajar kali-kalian", "subject_code": "math"},
    )
    assert response.status_code == 200
    confirm = client.post(
        f"/api/v1/learning-goals/resolve/{response.json()['resolution_id']}/confirm"
    )
    assert confirm.status_code == 200
    return confirm.json()["learning_goal_id"]


def _goal_with_posttest_node(client) -> tuple[str, UUID, str]:
    learning_goal_id = _confirmed_goal_id(client)
    with _session_for_client(client) as session:
        goal = session.get(LearningGoal, UUID(learning_goal_id))
        assert goal is not None
        concept = session.get(KnowledgeConcept, goal.target_concept_id)
        assert concept is not None
        goal.status = "in_progress"
        goal.metadata_json = {
            **(goal.metadata_json or {}),
            "diagnosis": {
                "nodes": [
                    {
                        "concept_id": str(concept.id),
                        "concept_code": concept.code,
                        "title": concept.title,
                        "role": "target",
                        "status": "fragile",
                        "depth": 0,
                    }
                ]
            },
        }
        session.commit()
        return learning_goal_id, concept.id, concept.code


def _option_id_for_question(client, question_id: str, *, correct: bool) -> str:
    with _session_for_client(client) as session:
        option = session.scalar(
            select(AssessmentOption)
            .where(
                AssessmentOption.question_id == UUID(question_id),
                AssessmentOption.is_correct.is_(correct),
            )
            .order_by(AssessmentOption.sort_order)
        )
        assert option is not None
        return str(option.id)


def _create_canvas_asset(client) -> str:
    with _session_for_client(client) as session:
        asset = ImageAsset(
            user_id=ACCOUNT_ID,
            storage_path="tests/canvas/posttest.png",
            mime_type="image/png",
            width=320,
            height=240,
            checksum="posttest-canvas",
        )
        session.add(asset)
        session.commit()
        return str(asset.id)


def _override_account(client) -> None:
    def override_current_account(
        session: Session = Depends(get_session),
    ) -> UserAccount:
        account = session.get(UserAccount, ACCOUNT_ID)
        if account is None:
            account = UserAccount(
                id=ACCOUNT_ID,
                supabase_user_id="supabase-user-adaptive",
                email="learner-adaptive@example.com",
                display_name="Adaptive User",
                provider_subject="supabase-user-adaptive",
            )
            session.add(account)
            _seed_math_graph(session)
            session.commit()
        return account

    client.app.dependency_overrides[get_current_account] = override_current_account


def _seed_math_graph(session: Session) -> None:
    subject = session.scalar(select(Subject).where(Subject.code == "matematika"))
    if subject is None:
        subject = Subject(code="matematika", name="Matematika", description="", is_active=True)
        session.add(subject)
        session.flush()
    concepts = {}
    for index, (code, title) in enumerate(
        [
            ("math.addition", "Penjumlahan"),
            ("math.subtraction", "Pengurangan"),
            ("math.multiplication", "Perkalian"),
        ],
        start=1,
    ):
        concept = session.scalar(
            select(KnowledgeConcept).where(
                KnowledgeConcept.subject_id == subject.id,
                KnowledgeConcept.code == code,
            )
        )
        if concept is None:
            concept = KnowledgeConcept(
                subject_id=subject.id,
                code=code,
                title=title,
                description=title,
                grade_band="primary",
                display_order=index,
            )
            session.add(concept)
            session.flush()
        else:
            concept.grade_band = "primary"
        concepts[code] = concept
    for from_code, to_code in [
        ("math.addition", "math.subtraction"),
        ("math.subtraction", "math.multiplication"),
    ]:
        if session.scalar(
            select(ConceptEdge).where(
                ConceptEdge.from_concept_id == concepts[from_code].id,
                ConceptEdge.to_concept_id == concepts[to_code].id,
            )
        ) is None:
            session.add(
                ConceptEdge(
                    from_concept_id=concepts[from_code].id,
                    to_concept_id=concepts[to_code].id,
                    edge_type="prerequisite",
                    weight=0.9,
                )
            )


@contextmanager
def _session_for_client(client):
    override = client.app.dependency_overrides[get_session]
    generator = override()
    session = next(generator)
    try:
        yield session
    finally:
        generator.close()
