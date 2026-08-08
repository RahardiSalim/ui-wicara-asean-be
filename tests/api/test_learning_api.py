from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import LearnerProfile, UserAccount
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.curriculum.seed import seed_curriculum
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentOption,
    AssessmentQuestion,
    AssessmentSession,
    LearnerConceptState,
    LearningGoal,
    LearningTrack,
    TrackModule,
)
from app.modules.question_bank.models import QuestionBankItem
from app.modules.question_bank.service import import_seed_directory


ACCOUNT_ID = UUID("22222222-2222-4222-8222-222222222222")


def test_learning_goal_bootstraps_seeded_pretest_and_track(client):
    _override_account(client)

    goal_response = client.post(
        "/api/v1/learning-goals",
        json={"raw_topic": "derivative rules"},
    )

    assert goal_response.status_code == 200
    goal = goal_response.json()
    assert goal["status"] == "pretest_ready"
    assert goal["pretest_session_id"]
    assert goal["track_id"]

    pretest_response = client.get(f"/api/v1/pretests/{goal['learning_goal_id']}")
    assert pretest_response.status_code == 200
    pretest = pretest_response.json()
    assert pretest["session_id"] == goal["pretest_session_id"]
    assert pretest["questions"]

    question = pretest["questions"][0]
    correct_option = next(option for option in question["options"] if option["label"] == "B")
    answer_response = client.post(
        f"/api/v1/pretests/{pretest['session_id']}/answers",
        json={
            "question_id": question["id"],
            "option_id": correct_option["id"],
            "confidence": 7,
        },
    )
    assert answer_response.status_code == 200
    assert answer_response.json()["is_correct"] is True

    reasoning_response = client.post(
        f"/api/v1/pretests/{pretest['session_id']}/reasoning",
        json={
            "question_id": question["id"],
            "option_id": correct_option["id"],
            "confidence": 7,
            "explanation": "Limits are the prerequisite signal.",
            "used_canvas": False,
        },
    )
    assert reasoning_response.status_code == 200
    assert reasoning_response.json()["path_title"] == "Personalized path generated"

    tracks_response = client.get("/api/v1/tracks")
    assert tracks_response.status_code == 200
    tracks = tracks_response.json()["items"]
    assert tracks[0]["id"] == goal["track_id"]
    assert len(tracks[0]["modules"]) == 3


def test_daily_evaluation_returns_seeded_review_questions_and_persists_answer(client):
    _override_account(client, seed_question_bank=True)

    response = client.get("/api/v1/daily-evaluations/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"]
    assert payload["review_policy"]["strategy"] == "personalized_daily_ebbinghaus_v1"
    assert payload["language"] == "en"
    assert payload["source"] == "question_bank_personalized_daily_ebbinghaus_v1"
    assert payload["review_due"]["due_count"] == 3
    assert payload["progress"] == {
        "current": 1,
        "total": 3,
        "completed": 0,
        "label": "1 of 3",
    }
    assert payload["question"]["id"] == payload["questions"][0]["id"]
    assert payload["retention_forecast"]["points"][0] == {
        "label": "Today",
        "retention_percent": 100,
        "projected": False,
    }
    assert payload["recommendation_callout"]["action_label"] == "Review now"
    assert len(payload["questions"]) == 3

    for index, question in enumerate(payload["questions"]):
        selected_option = question["options"][0]
        answer_response = client.post(
            f"/api/v1/daily-evaluations/{payload['session_id']}/answers",
            json={
                "question_id": question["id"],
                "option_id": selected_option["id"],
                "confidence": 6,
            },
        )

        assert answer_response.status_code == 200
        answer_payload = answer_response.json()
        assert isinstance(answer_payload["is_correct"], bool)
        assert answer_payload["next_review_label"] in {
            "Review tomorrow",
            "Review in 2 days",
            "Review in 7 days",
            "Review in 14 days",
            "Review in 30 days",
        }
        assert answer_payload["completed"] == (index == len(payload["questions"]) - 1)

    result_response = client.get(f"/api/v1/daily-evaluations/{payload['session_id']}/result")
    assert result_response.status_code == 200
    result = result_response.json()
    assert 0 <= result["score_percent"] <= 100
    assert result["reviewed_count"] == 3
    assert 0 <= result["correct_count"] <= 3
    assert 0 <= result["review_again_count"] <= 3
    assert len(result["reviewed_concepts"]) == 3
    assert {item["status_label"] for item in result["reviewed_concepts"]} <= {"Good", "Strong", "Review"}
    assert result["spaced_repetition_impact"]["retention_lift_percent"] >= 0
    assert result["next_review"]["interval_days"] in {3, 7}
    assert result["recommended_next_actions"][0]["action_type"] == "review"
    assert result["back_to_home"] == {
        "label": "Back to Home",
        "action_type": "navigate",
        "target": "/home",
    }


def test_daily_evaluation_correct_answer_uses_ebbinghaus_review_interval(client):
    _override_account(client, seed_question_bank=True)

    response = client.get("/api/v1/daily-evaluations/today")
    assert response.status_code == 200
    payload = response.json()
    question_payload = payload["questions"][0]

    with _session_for_client(client) as session:
        question = session.get(AssessmentQuestion, UUID(question_payload["id"]))
        assert question is not None
        assert question.concept_id is not None
        correct_option = next(option for option in question.options if option.is_correct)
        correct_option_id = str(correct_option.id)
        concept_id = question.concept_id

    answer_response = client.post(
        f"/api/v1/daily-evaluations/{payload['session_id']}/answers",
        json={
            "question_id": question_payload["id"],
            "option_id": correct_option_id,
            "confidence": 8,
        },
    )

    assert answer_response.status_code == 200
    answer_payload = answer_response.json()
    assert answer_payload["is_correct"] is True
    assert answer_payload["next_review_label"] == "Review in 2 days"
    with _session_for_client(client) as session:
        state = session.scalar(
            select(LearnerConceptState).where(
                LearnerConceptState.user_id == ACCOUNT_ID,
                LearnerConceptState.concept_id == concept_id,
            )
        )
        assert state is not None
        assert state.evidence_count == 1
        assert state.last_evaluated_at is not None
        assert state.next_review_at is not None
        next_review_at = (
            state.next_review_at.replace(tzinfo=UTC)
            if state.next_review_at.tzinfo is None
            else state.next_review_at
        )
        assert datetime.now(UTC) + timedelta(days=1, hours=20) <= next_review_at
        assert next_review_at <= datetime.now(UTC) + timedelta(days=2, minutes=5)


def test_daily_evaluation_uses_indonesian_question_bank_after_profile_switch(client):
    _override_account(client, seed_question_bank=True, preferred_language="en")

    english_response = client.get("/api/v1/daily-evaluations/today")
    assert english_response.status_code == 200
    assert english_response.json()["language"] == "en"

    _override_account(client, seed_question_bank=True, preferred_language="id")

    indonesian_response = client.get("/api/v1/daily-evaluations/today")

    assert indonesian_response.status_code == 200
    payload = indonesian_response.json()
    assert payload["language"] == "id"
    assert payload["review_due"]["title"] == "Review yang jatuh tempo"
    assert payload["progress"]["label"] == "1 dari 3"
    assert "Which topic" not in payload["question"]["prompt"]
    assert "A quick review" not in payload["question"]["prompt"]


def test_daily_evaluation_refreshes_unanswered_session_when_latest_track_changes(client):
    _override_account(client, seed_question_bank=True, preferred_language="en")

    first_response = client.get("/api/v1/daily-evaluations/today")
    assert first_response.status_code == 200
    first_session_id = first_response.json()["session_id"]

    with _session_for_client(client) as session:
        track = _create_test_track(session, raw_topic="latest daily track")
        track_id = str(track.id)

    refreshed_response = client.get("/api/v1/daily-evaluations/today")

    assert refreshed_response.status_code == 200
    refreshed_payload = refreshed_response.json()
    assert refreshed_payload["session_id"] != first_session_id
    with _session_for_client(client) as session:
        assessment = session.get(AssessmentSession, UUID(refreshed_payload["session_id"]))
        assert assessment is not None
        assert str(assessment.track_id) == track_id
        assert assessment.metadata_json["active_track_id"] == track_id
        assert assessment.metadata_json["track_resolution_strategy"] == (
            "latest_non_completed_track_updated_at_desc"
        )
        assert assessment.metadata_json["refresh_reason"] == "active_track_changed"
        assert assessment.metadata_json["resolved_at"]


def test_daily_evaluation_preserves_answered_session_when_latest_track_changes(client):
    _override_account(client, seed_question_bank=True, preferred_language="en")

    first_response = client.get("/api/v1/daily-evaluations/today")
    assert first_response.status_code == 200
    first_payload = first_response.json()
    question = first_payload["questions"][0]
    answer_response = client.post(
        f"/api/v1/daily-evaluations/{first_payload['session_id']}/answers",
        json={
            "question_id": question["id"],
            "option_id": question["options"][0]["id"],
            "confidence": 6,
        },
    )
    assert answer_response.status_code == 200

    with _session_for_client(client) as session:
        _create_test_track(session, raw_topic="new track after answer")

    preserved_response = client.get("/api/v1/daily-evaluations/today")

    assert preserved_response.status_code == 200
    assert preserved_response.json()["session_id"] == first_payload["session_id"]


def test_weekly_report_returns_explicit_no_data_payload_for_fresh_user(client):
    _override_account(client)

    response = client.get("/api/v1/reports/weekly/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["range_label"]
    assert payload["range_start"]
    assert payload["range_end"]
    assert payload["status"] == "no_data"
    assert payload["source"] == "no_assessment_or_mastery_data"
    assert payload["score"] == 0
    assert [item["label"] for item in payload["performance_groups"]] == [
        "Overall",
        "Application",
        "Analysis",
    ]
    assert payload["gap_metrics"]["fixed"]["delta_label"]
    assert payload["gap_metrics"]["remaining"]["delta_label"]
    assert payload["unlocked_this_week"] == {"count": 0, "concepts": []}
    assert payload["upcoming_recommendations"] == []
    assert payload["consistency_summary"]["title"] == "No activity in selected range."
    assert payload["consistency_summary"]["signal"] == "no_activity"
    assert payload["data_quality"]["coverage_status"] == "no_data"
    assert payload["data_quality"]["confidence_label"] == "low"
    assert payload["effort_impact"]["efficiency_label"] == "no_signal"
    assert len(payload["weekly_timeline"]) == 4
    assert set(payload["weekly_narrative"]) == {"improved", "stagnant", "focus"}
    assert isinstance(payload["concept_movers"], list)


def test_weekly_report_range_uses_selected_dates_and_attempt_scores(client):
    _override_account(client, seed_question_bank=True)

    daily_response = client.get("/api/v1/daily-evaluations/today")
    assert daily_response.status_code == 200
    daily = daily_response.json()
    today = datetime.now(UTC).date()

    for question in daily["questions"]:
        selected_option = question["options"][0]
        response = client.post(
            f"/api/v1/daily-evaluations/{daily['session_id']}/answers",
            json={
                "question_id": question["id"],
                "option_id": selected_option["id"],
                "confidence": 8,
            },
        )
        assert response.status_code == 200

    report_response = client.get(
        f"/api/v1/reports/weekly?start={today.isoformat()}&end={today.isoformat()}"
    )

    assert report_response.status_code == 200
    payload = report_response.json()
    assert payload["range_start"] == today.isoformat()
    assert payload["range_end"] == today.isoformat()
    assert payload["source"] == "derived_from_range_assessments_no_baseline"
    assert 0 <= payload["score"] <= 100
    assert payload["performance_groups"][0]["label"] == "Overall"
    assert payload["upcoming_recommendations"][0]["title"].startswith("Review:")
    assert payload["effort_impact"]["attempt_count"] == len(daily["questions"])
    assert payload["data_quality"]["attempts_covered"] == len(daily["questions"])


def test_weekly_report_exposes_paired_pretest_posttest_gain(client):
    _override_account(client)
    today = datetime.now(UTC).date()
    _create_paired_pre_post_attempts(client)

    report_response = client.get(
        f"/api/v1/reports/weekly?start={today.isoformat()}&end={today.isoformat()}"
    )

    assert report_response.status_code == 200
    payload = report_response.json()
    assert payload["pretest_score_percent"] == 0
    assert payload["posttest_score_percent"] == 100
    assert payload["learning_gain_percent"] == 100
    assert payload["paired_concept_count"] == 1
    assert payload["data_quality"]["paired_concepts"] == 1


def test_weekly_report_range_rejects_invalid_dates(client):
    _override_account(client)

    response = client.get("/api/v1/reports/weekly?start=2026-05-17&end=2026-05-11")

    assert response.status_code == 422
    assert "start date" in response.json()["detail"]


def test_daily_result_recommends_missed_concept_review(client):
    _override_account(client, seed_question_bank=True)

    daily_response = client.get("/api/v1/daily-evaluations/today")
    assert daily_response.status_code == 200
    daily = daily_response.json()

    for question in daily["questions"]:
        selected_option = question["options"][0]
        answer_response = client.post(
            f"/api/v1/daily-evaluations/{daily['session_id']}/answers",
            json={
                "question_id": question["id"],
                "option_id": selected_option["id"],
                "confidence": 5,
            },
        )
        assert answer_response.status_code == 200

    result_response = client.get(f"/api/v1/daily-evaluations/{daily['session_id']}/result")

    assert result_response.status_code == 200
    result = result_response.json()
    assert result["review_again_count"] >= 1
    assert result["recommended_next_actions"][0]["action_type"] == "review"
    assert result["recommended_next_actions"][0]["title"].startswith("Review:")
    assert result["recommended_next_actions"][0]["reason"] == (
        "You missed this concept in today's evaluation."
    )


def test_media_artifacts_contains_demo_supabase_videos(client):
    _override_account(client)

    response = client.get("/api/v1/media-artifacts")

    assert response.status_code == 200
    payload = response.json()
    playback_urls = {item["playback_url"] for item in payload["items"]}
    assert (
        "https://gwbqhirtkgkghnpahtgt.supabase.co/storage/v1/object/public/video/perkalian.mp4"
        in playback_urls
    )
    assert (
        "https://gwbqhirtkgkghnpahtgt.supabase.co/storage/v1/object/public/video/aljabar.mp4"
        in playback_urls
    )


def _create_paired_pre_post_attempts(client) -> None:
    with _session_for_client(client) as session:
        account = session.get(UserAccount, ACCOUNT_ID)
        if account is None:
            account = UserAccount(
                id=ACCOUNT_ID,
                supabase_user_id="supabase-user-learning",
                email="learner-learning@example.com",
                display_name="Learning User",
                provider_subject="supabase-user-learning",
            )
            session.add(account)
            session.flush()
        subject = Subject(code="metric-test", name="Metric Test", description="", is_active=True)
        session.add(subject)
        session.flush()
        concept = KnowledgeConcept(
            subject_id=subject.id,
            code="metric.test.concept",
            title="Metric Test Concept",
            description="Metric Test Concept",
            grade_band="primary",
            display_order=1,
        )
        session.add(concept)
        session.flush()
        for session_type, is_correct in [("pretest", False), ("posttest", True)]:
            assessment = AssessmentSession(
                user_id=ACCOUNT_ID,
                session_type=session_type,
                title=f"{session_type} metric test",
                status="completed",
                metadata_json={"source": "test"},
            )
            session.add(assessment)
            session.flush()
            question = AssessmentQuestion(
                session_id=assessment.id,
                concept_id=concept.id,
                step_label="Metric",
                topic="Metric Test",
                prompt="Metric prompt?",
                helper_text="",
                difficulty_label="Medium",
                sort_order=1,
                metadata_json={"correct_option_key": "A"},
            )
            session.add(question)
            session.flush()
            option = AssessmentOption(
                question_id=question.id,
                option_key="A",
                label="A",
                text="Answer",
                is_correct=is_correct,
                sort_order=1,
            )
            session.add(option)
            session.flush()
            score = 1.0 if is_correct else 0.0
            session.add(
                AssessmentAttempt(
                    session_id=assessment.id,
                    question_id=question.id,
                    selected_option_id=option.id,
                    confidence=8,
                    score=score,
                    is_correct=is_correct,
                    answer_score=score,
                    evidence_score=score,
                    diagnostic_signal="correct_mcq_only" if is_correct else "concept_gap_likely",
                )
            )
        session.commit()


@contextmanager
def _session_for_client(client):
    override = client.app.dependency_overrides[get_session]
    generator = override()
    session = next(generator)
    try:
        yield session
    finally:
        generator.close()


def _override_account(
    client,
    *,
    seed_question_bank: bool = False,
    preferred_language: str | None = None,
) -> None:
    def override_current_account(
        session: Session = Depends(get_session),
    ) -> UserAccount:
        account = session.get(UserAccount, ACCOUNT_ID)
        if account is None:
            account = UserAccount(
                id=ACCOUNT_ID,
                supabase_user_id="supabase-user-learning",
                email="learner-learning@example.com",
                display_name="Learning User",
                provider_subject="supabase-user-learning",
            )
            session.add(account)
            session.commit()
            session.refresh(account)
        if preferred_language is not None and account.learner_profile is None:
            session.add(
                LearnerProfile(
                    user_id=account.id,
                    full_name="Learning User",
                    education_level="SMP",
                    grade_level="Kelas 7",
                    preferred_language=preferred_language,
                    selected_subjects=["mathematics"],
                    onboarding_completed=True,
                )
            )
            session.commit()
            session.refresh(account)
        elif preferred_language is not None and account.learner_profile is not None:
            account.learner_profile.preferred_language = preferred_language
            session.commit()
        if seed_question_bank:
            existing_bank_item = session.scalar(select(QuestionBankItem.id).limit(1))
            if existing_bank_item is None:
                seed_curriculum(session)
                import_seed_directory(session)
        return account

    client.app.dependency_overrides[get_current_account] = override_current_account


def _create_test_track(session: Session, *, raw_topic: str) -> LearningTrack:
    account = session.get(UserAccount, ACCOUNT_ID)
    assert account is not None
    subject = session.scalar(select(Subject).where(Subject.code == "matematika"))
    concept = session.scalar(
        select(KnowledgeConcept).where(KnowledgeConcept.code == "km_d_matematika_bilangan_rasional")
    )
    assert subject is not None
    assert concept is not None
    goal = LearningGoal(
        user_id=account.id,
        subject_id=subject.id,
        target_concept_id=concept.id,
        raw_topic=raw_topic,
        normalized_topic=raw_topic,
        status="pretest_ready",
    )
    session.add(goal)
    session.flush()
    track = LearningTrack(
        user_id=account.id,
        learning_goal_id=goal.id,
        title=raw_topic,
        subtitle="test track",
        status="in_progress",
        progress_percent=0,
    )
    session.add(track)
    session.flush()
    session.add(
        TrackModule(
            track_id=track.id,
            concept_id=concept.id,
            title="Active module",
            description="",
            estimated_minutes=10,
            difficulty_label="Medium",
            sort_order=1,
            status="ready",
        )
    )
    session.commit()
    session.refresh(track)
    return track
