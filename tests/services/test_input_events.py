from uuid import UUID

import pytest
from sqlalchemy import select

from app.modules.accounts.models import UserAccount
from app.modules.evidence.models import ImageAsset
from app.modules.inputs.models import InputEvent
from app.modules.learning import service as learning_service
from app.modules.workspaces import service as workspace_service


ACCOUNT_ID = UUID("55555555-5555-4555-8555-555555555555")
IMAGE_ASSET_ID = UUID("66666666-6666-4666-8666-666666666666")


@pytest.mark.asyncio
async def test_workspace_event_creates_canonical_input_event(db_session):
    account = UserAccount(
        id=ACCOUNT_ID,
        supabase_user_id="supabase-user-input",
        email="learner-input@example.com",
        display_name="Input User",
        provider_subject="supabase-user-input",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    # Workspace events may only reference an image asset the learner owns.
    db_session.add(
        ImageAsset(
            id=IMAGE_ASSET_ID,
            user_id=account.id,
            storage_path="evidence/test/canvas.png",
            mime_type="image/png",
        )
    )
    db_session.commit()

    goal = learning_service.create_learning_goal(
        db_session,
        user=account,
        raw_topic="derivative rules",
        subject_code=None,
    )
    track = learning_service.get_track_modules(db_session, user=account, track_id=goal.track_id)
    module_id = track.modules[0].id
    workspace = workspace_service.create_or_resume_workspace(
        db_session,
        user=account,
        track_id=goal.track_id,
        module_id=module_id,
        content_mode="canvas",
    )

    response = await workspace_service.append_workspace_event(
        db_session,
        user=account,
        workspace_id=workspace.id,
        event_type="canvas_sent",
        actor_type="learner",
        text_payload="Ini langkah saya dari canvas.",
        image_asset_id=IMAGE_ASSET_ID,
        media_artifact_id=None,
        metadata={"confidence": 8, "client_event_id": "canvas-1"},
    )

    assert response is not None
    assert response.event.input_event_id is not None

    input_event = db_session.scalar(
        select(InputEvent).where(InputEvent.id == response.event.input_event_id)
    )
    assert input_event is not None
    assert input_event.user_id == account.id
    assert input_event.workspace_session_id == workspace.id
    assert input_event.event_type == "mixed"
    assert input_event.source_type == "workspace"
    assert input_event.text_payload == "Ini langkah saya dari canvas."
    assert input_event.image_asset_id == IMAGE_ASSET_ID
    assert input_event.confidence == 8
    assert input_event.raw_payload["source_event_type"] == "canvas_sent"
    assert input_event.raw_payload["metadata"]["client_event_id"] == "canvas-1"
