from sqlalchemy import select

from app.modules.accounts.models import UserAccount
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.curriculum.seed import seed_curriculum
from app.modules.learning.models import LearningGoal, MediaArtifact, TrackModule
from app.modules.tracks.path_builder import TrackBuilderService
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession
from app.modules.workspaces.service import create_or_resume_workspace, read_workspace


def test_repair_path_materializes_diagnosed_gap_connectors_and_target(db_session):
    seed_curriculum(db_session)
    codes = {
        "algebraic": "km_f_matematika_tingkat_lanjut_turunan_secara_aljabar",
        "gap": "km_f_matematika_tingkat_lanjut_aturan_rantai",
        "connector": "km_f_matematika_tingkat_lanjut_turunan_fungsi_trigonometri",
        "target": "km_f_matematika_tingkat_lanjut_sketsa_kurva_menggunakan_turunan",
    }
    concepts = {
        concept.code: concept
        for concept in db_session.scalars(
            select(KnowledgeConcept).where(KnowledgeConcept.code.in_(codes.values()))
        )
    }
    subject = db_session.scalar(
        select(Subject).where(Subject.code == "matematika_tingkat_lanjut")
    )
    user = UserAccount(
        supabase_user_id="track-builder-user",
        display_name="Track Builder",
    )
    db_session.add(user)
    db_session.flush()
    goal = LearningGoal(
        user_id=user.id,
        subject_id=subject.id,
        target_concept_id=concepts[codes["target"]].id,
        raw_topic="opaque requested target",
        normalized_topic="Opaque requested target",
        status="diagnosed",
        metadata_json={
            "diagnosis": {
                "summary": "A process gap was confirmed.",
                "diagnostic_summary": "Attempt evidence points to the prerequisite.",
                "target": {
                    "concept_id": str(concepts[codes["target"]].id),
                    "concept_code": codes["target"],
                    "title": concepts[codes["target"]].title,
                },
                "nodes": [
                    {
                        "concept_code": codes["target"],
                        "concept_id": str(concepts[codes["target"]].id),
                        "title": "Opaque target",
                        "role": "target",
                        "depth": 0,
                        "parent": None,
                        "status": "gap",
                    },
                    {
                        "concept_code": codes["connector"],
                        "concept_id": str(concepts[codes["connector"]].id),
                        "title": "Opaque connector",
                        "role": "prerequisite",
                        "depth": 1,
                        "parent": codes["target"],
                        "status": "not_tested",
                    },
                    {
                        "concept_code": codes["gap"],
                        "concept_id": str(concepts[codes["gap"]].id),
                        "title": "Opaque gap",
                        "role": "prerequisite",
                        "depth": 2,
                        "parent": codes["connector"],
                        "status": "gap",
                        "confidence": 0.91,
                        "evidence": [{"attempt_id": "attempt-source-1"}],
                        "evidence_summary": {
                            "attempt_count": 2,
                            "misconception_detected": True,
                        },
                    },
                    {
                        "concept_code": codes["algebraic"],
                        "concept_id": str(concepts[codes["algebraic"]].id),
                        "title": "Opaque control",
                        "role": "prerequisite",
                        "depth": 2,
                        "parent": codes["connector"],
                        "status": "ready",
                        "evidence_summary": {"attempt_count": 1},
                    },
                ],
            }
        },
    )
    db_session.add(goal)
    db_session.commit()

    response = TrackBuilderService().select_path(
        db_session,
        user=user,
        learning_goal_id=goal.id,
        path_option="repair_prerequisites",
    )

    assert response is not None
    assert [module.concept_code for module in response.modules] == [
        codes["gap"],
        codes["connector"],
        codes["target"],
    ]
    modules = list(
        db_session.scalars(
            select(TrackModule)
            .where(TrackModule.track_id == response.track_id)
            .order_by(TrackModule.sort_order)
        )
    )
    assert [module.status for module in modules] == ["ready", "locked", "locked"]
    assert [module.metadata_json["module_role"] for module in modules] == [
        "prerequisite_gap",
        "verification",
        "original_target",
    ]
    first_context = modules[0].metadata_json
    assert first_context["original_target"]["concept_code"] == codes["target"]
    assert first_context["diagnosis_evidence"]["source_attempt_ids"] == [
        "attempt-source-1"
    ]
    assert first_context["already_understood"][0]["concept_code"] == codes["algebraic"]
    assert first_context["route"] == [
        codes["gap"],
        codes["connector"],
        codes["target"],
    ]

    workspace = create_or_resume_workspace(
        db_session,
        user=user,
        track_id=response.track_id,
        module_id=modules[0].id,
        content_mode="chat",
    )
    assert workspace.learning_context["original_target"]["concept_code"] == codes["target"]
    assert workspace.learning_context["current_module"]["role"] == "prerequisite_gap"
    assert workspace.learning_context["diagnosis"]["evidence"]["source_attempt_ids"] == [
        "attempt-source-1"
    ]
    assert workspace.learning_context["route"] == [
        codes["gap"],
        codes["connector"],
        codes["target"],
    ]

    artifact = MediaArtifact(
        user_id=user.id,
        track_id=response.track_id,
        module_id=modules[0].id,
        workspace_id=workspace.id,
        concept_id=concepts[codes["gap"]].id,
        template_id="opaque.visual.v1",
        spec_json={},
        title="Opaque visual",
        status="ready",
    )
    db_session.add(artifact)
    db_session.flush()
    db_session.add(
        WorkspaceEvent(
            workspace_session_id=workspace.id,
            event_index=1,
            event_type="media_generated",
            actor_type="system",
            text_payload="",
            media_artifact_id=artifact.id,
            metadata_json={"generation_mode": "context_auto"},
        )
    )
    db_session.commit()

    first_read = read_workspace(
        db_session,
        user=user,
        workspace_id=workspace.id,
    )
    second_read = read_workspace(
        db_session,
        user=user,
        workspace_id=workspace.id,
    )
    assert first_read is not None
    assert second_read is not None
    followups = [
        event
        for event in second_read.events
        if event.metadata.get("follow_up_for_media_artifact_id") == str(artifact.id)
    ]
    assert len(followups) == 1
    assert followups[0].metadata["mastery_delta"] == 0.0
