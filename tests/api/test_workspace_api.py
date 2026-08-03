from uuid import UUID

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import UserAccount


ACCOUNT_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_workspace_events_are_persisted_in_module_timeline(client):
    _override_account(client)
    track_id, module_id = _create_track_and_first_module(client)

    workspace_response = client.post(
        "/api/v1/workspaces",
        json={
            "track_id": track_id,
            "module_id": module_id,
            "content_mode": "chat",
        },
    )

    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["track_id"] == track_id
    assert workspace["module_id"] == module_id
    assert workspace["current_topic"] == "Prerequisite checkpoint"
    assert workspace["content_mode"] == "chat"
    assert workspace["status"] == "active"
    assert workspace["events"] == []

    resume_response = client.post(
        "/api/v1/workspaces",
        json={
            "track_id": track_id,
            "module_id": module_id,
            "content_mode": "canvas",
        },
    )
    assert resume_response.status_code == 200
    assert resume_response.json()["id"] == workspace["id"]
    assert resume_response.json()["content_mode"] == "canvas"

    text_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/events",
        json={
            "event_type": "text",
            "actor_type": "learner",
            "text_payload": "  Kenapa limit harus dicek sebelum turunan?  ",
            "metadata": {"client_event_id": "local-1"},
        },
    )

    assert text_response.status_code == 200
    text_payload = text_response.json()
    assert text_payload["event"]["event_type"] == "text"
    assert text_payload["event"]["actor_type"] == "learner"
    assert text_payload["event"]["event_index"] == 1
    assert text_payload["event"]["input_event_id"]
    assert text_payload["event"]["text_payload"] == "Kenapa limit harus dicek sebelum turunan?"
    assert text_payload["event"]["metadata"] == {"client_event_id": "local-1"}
    assert text_payload["tutor_response"]["intent"] in {"ask_followup", "spark_curiosity"}
    assert text_payload["tutor_response"]["text"]
    assert len(text_payload["workspace"]["events"]) == 2
    assert text_payload["workspace"]["events"][1]["actor_type"] == "tutor"
    assert text_payload["workspace"]["events"][1]["event_type"] == "text"
    assert text_payload["workspace"]["events"][1]["text_payload"]

    image_asset_id = "44444444-4444-4444-8444-444444444444"
    image_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/events",
        json={
            "event_type": "canvas_sent",
            "actor_type": "learner",
            "image_asset_id": image_asset_id,
            "metadata": {"element_count": 3, "has_attachment": False},
        },
    )

    assert image_response.status_code == 200
    image_payload = image_response.json()
    assert image_payload["event"]["event_index"] == 3
    assert image_payload["event"]["input_event_id"]
    assert image_payload["event"]["image_asset_id"] == image_asset_id
    assert image_payload["workspace"]["last_image_asset_id"] == image_asset_id

    load_response = client.get(f"/api/v1/workspaces/{workspace['id']}")
    assert load_response.status_code == 200
    loaded = load_response.json()
    assert [event["event_type"] for event in loaded["events"]] == [
        "text",
        "text",
        "canvas_sent",
        "text",
    ]
    assert [event["actor_type"] for event in loaded["events"]] == [
        "learner",
        "tutor",
        "learner",
        "tutor",
    ]
    assert loaded["last_image_asset_id"] == image_asset_id


def test_workspace_rejects_client_mastery_and_completion_bypass(client):
    _override_account(client)
    track_id, module_id = _create_track_and_first_module(client)

    workspace_response = client.post(
        "/api/v1/workspaces",
        json={
            "track_id": track_id,
            "module_id": module_id,
            "content_mode": "chat",
        },
    )
    assert workspace_response.status_code == 200

    modules_response = client.get(f"/api/v1/tracks/{track_id}/modules")
    assert modules_response.status_code == 200
    modules = modules_response.json()["modules"]
    active_module = next(module for module in modules if module["id"] == module_id)
    assert active_module["status"] == "active"

    event_response = client.post(
        f"/api/v1/workspaces/{workspace_response.json()['id']}/events",
        json={
            "event_type": "quiz_answer",
            "actor_type": "learner",
            "text_payload": "3",
            "metadata": {
                "question_id": "limit-graph-check",
                "selected_answer": "3",
                "correct_answer": "3",
                "is_correct": True,
            },
        },
    )

    assert event_response.status_code == 200
    payload = event_response.json()
    assert payload["tutor_response"]["text"]
    assert payload["mastery_update"]["delta"] == 0
    assert payload["mastery_update"]["reason"] in {
        "module_has_no_concept",
        "unverified_activity_recorded_without_mastery_delta",
    }

    completion_response = client.patch(
        f"/api/v1/tracks/{track_id}/modules/{module_id}/state",
        json={"status": "completed"},
    )
    assert completion_response.status_code == 422

    modules_response = client.get(f"/api/v1/tracks/{track_id}/modules")
    modules = modules_response.json()["modules"]
    current_module = next(module for module in modules if module["id"] == module_id)
    next_module = next(
        module
        for module in modules
        if module["sort_order"] == current_module["sort_order"] + 1
    )
    assert current_module["status"] == "active"
    assert next_module["status"] == "locked"


def test_workspace_rejects_event_with_unknown_type(client):
    _override_account(client)
    track_id, module_id = _create_track_and_first_module(client)
    workspace = client.post(
        "/api/v1/workspaces",
        json={"track_id": track_id, "module_id": module_id},
    ).json()

    response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/events",
        json={
            "event_type": "unsupported",
            "actor_type": "learner",
            "text_payload": "test",
        },
    )

    assert response.status_code == 422


def _create_track_and_first_module(client) -> tuple[str, str]:
    goal_response = client.post(
        "/api/v1/learning-goals",
        json={"raw_topic": "derivative rules"},
    )
    assert goal_response.status_code == 200
    track_id = goal_response.json()["track_id"]

    track_response = client.get(f"/api/v1/tracks/{track_id}/modules")
    assert track_response.status_code == 200
    track = track_response.json()
    return track_id, track["modules"][0]["id"]


def _override_account(client) -> None:
    def override_current_account(
        session: Session = Depends(get_session),
    ) -> UserAccount:
        account = session.get(UserAccount, ACCOUNT_ID)
        if account is None:
            account = UserAccount(
                id=ACCOUNT_ID,
                supabase_user_id="supabase-user-workspace",
                email="learner-workspace@example.com",
                display_name="Workspace User",
                provider_subject="supabase-user-workspace",
            )
            session.add(account)
            session.commit()
            session.refresh(account)
        return account

    client.app.dependency_overrides[get_current_account] = override_current_account
