from uuid import UUID

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import UserAccount
from app.modules.workspaces.models import WorkspaceSession


ACCOUNT_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_workspace_events_are_persisted_in_module_timeline(client, monkeypatch):
    monkeypatch.setenv("WICARA_WORKSPACE_TUTOR_TIMEOUT_SECONDS", "0.1")
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
    assert len(workspace["events"]) == 1
    assert workspace["events"][0]["actor_type"] == "tutor"
    assert workspace["events"][0]["event_index"] == 1
    assert workspace["events"][0]["metadata"]["source"] == "workspace_initial_opening"

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
    assert text_payload["event"]["event_index"] == 2
    assert text_payload["event"]["input_event_id"]
    assert text_payload["event"]["text_payload"] == "Kenapa limit harus dicek sebelum turunan?"
    assert text_payload["event"]["metadata"] == {
        "client_event_id": "local-1",
        "phase": "engage",
    }
    assert text_payload["tutor_response"]["intent"] == "phase_opening"
    assert text_payload["tutor_response"]["text"]
    assert text_payload["workspace"]["current_phase"] == "explore"
    assert text_payload["workspace"]["phase_transition_pending"] is False
    assert len(text_payload["workspace"]["events"]) == 3
    assert text_payload["workspace"]["events"][2]["actor_type"] == "tutor"
    assert text_payload["workspace"]["events"][2]["event_type"] == "text"
    assert (
        text_payload["workspace"]["events"][2]["metadata"]["source"]
        == "workspace_auto_phase_opening"
    )
    assert text_payload["workspace"]["events"][2]["text_payload"]

    # Workspace events may only reference an image asset the caller owns, so mint
    # a real one instead of inventing an id.
    asset_response = client.post(
        "/api/v1/evidence/image-assets",
        json={"storage_path": "evidence/test/canvas.png", "mime_type": "image/png"},
    )
    assert asset_response.status_code == 200
    image_asset_id = asset_response.json()["id"]
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
    assert image_payload["event"]["event_index"] == 4
    assert image_payload["event"]["input_event_id"]
    assert image_payload["event"]["image_asset_id"] == image_asset_id
    assert image_payload["workspace"]["last_image_asset_id"] == image_asset_id

    load_response = client.get(f"/api/v1/workspaces/{workspace['id']}")
    assert load_response.status_code == 200
    loaded = load_response.json()
    assert [event["event_type"] for event in loaded["events"]] == [
        "text",
        "text",
        "text",
        "canvas_sent",
        "text",
    ]
    assert [event["actor_type"] for event in loaded["events"]] == [
        "tutor",
        "learner",
        "tutor",
        "learner",
        "tutor",
    ]
    assert loaded["last_image_asset_id"] == image_asset_id


def test_workspace_rejects_client_mastery_and_completion_bypass(client, monkeypatch):
    monkeypatch.setenv("WICARA_WORKSPACE_TUTOR_TIMEOUT_SECONDS", "0.1")
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


def test_opening_a_locked_module_is_a_conflict_not_a_server_error(client):
    _override_account(client)
    track_id, first_module_id = _create_track_and_first_module(client)
    modules = client.get(f"/api/v1/tracks/{track_id}/modules").json()["modules"]
    locked_module = next(
        module
        for module in modules
        if module["id"] != first_module_id and module["status"] == "locked"
    )

    response = client.post(
        "/api/v1/workspaces",
        json={"track_id": track_id, "module_id": locked_module["id"]},
    )

    assert response.status_code == 409
    assert "prerequisite" in response.json()["detail"].lower()


def test_workspace_rejects_visualization_outside_explore(client):
    _override_account(client)
    track_id, module_id = _create_track_and_first_module(client)
    workspace = client.post(
        "/api/v1/workspaces",
        json={"track_id": track_id, "module_id": module_id},
    ).json()

    response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/generate-video",
        json={"generation_mode": "context_auto", "language": "id"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Visualization can only be requested during the Explore phase."
    )


def test_workspace_event_rejects_an_image_asset_the_caller_does_not_own(client):
    _override_account(client)
    track_id, module_id = _create_track_and_first_module(client)
    workspace = client.post(
        "/api/v1/workspaces",
        json={"track_id": track_id, "module_id": module_id},
    ).json()

    response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/events",
        json={
            "event_type": "canvas_sent",
            "actor_type": "learner",
            "image_asset_id": "44444444-4444-4444-8444-444444444444",
        },
    )

    assert response.status_code == 404


def test_session_history_paginates_and_supports_deletion(client):
    _override_account(client)
    track_id, module_id = _create_track_and_first_module(client)
    created = [
        client.post(
            "/api/v1/workspaces",
            json={
                "track_id": track_id,
                "module_id": module_id,
                "start_new_session": True,
            },
        ).json()["id"]
        for _ in range(3)
    ]

    first_page = client.get(
        "/api/v1/workspaces",
        params={
            "track_id": track_id,
            "module_id": module_id,
            "limit": 2,
            "offset": 0,
        },
    ).json()
    assert len(first_page["sessions"]) == 2
    assert first_page["total"] == 3
    assert first_page["has_more"] is True

    second_page = client.get(
        "/api/v1/workspaces",
        params={
            "track_id": track_id,
            "module_id": module_id,
            "limit": 2,
            "offset": 2,
        },
    ).json()
    assert len(second_page["sessions"]) == 1
    assert second_page["has_more"] is False

    assert client.delete(f"/api/v1/workspaces/{created[0]}").status_code == 204
    assert client.get(f"/api/v1/workspaces/{created[0]}").status_code == 404
    remaining = client.get(
        "/api/v1/workspaces",
        params={"track_id": track_id, "module_id": module_id},
    ).json()
    assert remaining["total"] == 2


def test_a_completed_workspace_is_not_resumed_or_reopened(client):
    _override_account(client)
    track_id, module_id = _create_track_and_first_module(client)
    workspace_id = client.post(
        "/api/v1/workspaces",
        json={"track_id": track_id, "module_id": module_id},
    ).json()["id"]

    session = next(client.app.dependency_overrides[get_session]())
    completed = session.get(WorkspaceSession, UUID(workspace_id))
    completed.status = "completed"
    session.commit()

    resumed = client.post(
        "/api/v1/workspaces",
        json={"track_id": track_id, "module_id": module_id},
    ).json()

    # Auto-resume must start a fresh session rather than reviving the finished
    # one, and the finished one must stay completed.
    assert resumed["id"] != workspace_id
    assert client.get(f"/api/v1/workspaces/{workspace_id}").json()["status"] == (
        "completed"
    )


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
