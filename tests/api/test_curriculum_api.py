from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_session
from app.modules.accounts.dependencies import get_optional_current_account
from app.modules.accounts.models import LearnerProfile, UserAccount
from app.modules.curriculum.models import KnowledgeConcept, Subject
from app.modules.learning.models import LearnerConceptState


ACCOUNT_ID = UUID("33333333-3333-4333-8333-333333333333")
TARGET_CONCEPT = "km_d_matematika_bilangan_rasional"


def test_get_subjects_returns_seeded_subject_catalog(client, seeded_curriculum):
    response = client.get("/api/v1/subjects")

    assert response.status_code == 200
    payload = response.json()
    assert [subject["code"] for subject in payload["items"]] == [
        "matematika",
        "ipas",
        "ipa",
        "fisika",
        "kimia",
        "biologi",
        "matematika_tingkat_lanjut",
    ]
    subjects_by_code = {subject["code"]: subject for subject in payload["items"]}
    matematika = subjects_by_code["matematika"]
    assert matematika["name"] == "Matematika"
    assert matematika["metadata"]["name_id"] == "Matematika"
    assert matematika["metadata"]["name_en"] == "Math"
    assert matematika["metadata"]["curriculum"] == "kurikulum_merdeka"
    assert matematika["metadata"]["is_available_in_knowledge_graph"] is True
    assert matematika["metadata"]["is_locked_in_knowledge_graph"] is False
    advanced_math = subjects_by_code["matematika_tingkat_lanjut"]
    assert advanced_math["name"] == "Matematika Tingkat Lanjut"
    assert advanced_math["metadata"]["name_en"] == "Advanced Mathematics"
    assert advanced_math["metadata"]["is_available_in_knowledge_graph"] is True
    assert advanced_math["metadata"]["is_locked_in_knowledge_graph"] is False
    enabled_codes = {"matematika", "matematika_tingkat_lanjut"}
    assert all(
        subject["metadata"]["is_available_in_knowledge_graph"] is False
        for code, subject in subjects_by_code.items()
        if code not in enabled_codes
    )
    assert all(
        subject["metadata"]["is_locked_in_knowledge_graph"] is True
        for code, subject in subjects_by_code.items()
        if code not in enabled_codes
    )


def test_get_subjects_uses_profile_language_for_button_labels(
    client,
    seeded_curriculum,
):
    _override_optional_account(client, preferred_language="en")

    response = client.get("/api/v1/subjects")

    assert response.status_code == 200
    payload = response.json()
    subjects_by_code = {subject["code"]: subject for subject in payload["items"]}
    assert subjects_by_code["matematika"]["name"] == "Math"
    assert subjects_by_code["fisika"]["name"] == "Physics"
    assert subjects_by_code["biologi"]["name"] == "Biology"
    assert subjects_by_code["matematika"]["metadata"]["locale"] == "en"


def test_get_knowledge_map_returns_mobile_ready_kurikulum_graph(client, seeded_curriculum):
    response = client.get("/api/v1/knowledge-map?subject=matematika")

    assert response.status_code == 200
    payload = response.json()

    assert payload["subject"]["code"] == "matematika"
    assert payload["graph"]["title"] == "Peta Pengetahuan Matematika"
    assert payload["graph"]["top_down"] is True
    assert payload["groups"][0] == {"label": "Fase A / Aljabar", "x": 28.0}

    nodes_by_id = {node["id"]: node for node in payload["nodes"]}
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["status"] == "active"
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["status_label"] == "IN PROGRESS"
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["metadata"]["preview_status_only"] is True
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["group"] == "Fase D / Bilangan"
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["description"] == (
        nodes_by_id["km_d_matematika_bilangan_bulat"]["id_desc"]
    )
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["id_desc"].startswith(
        "Memahami dan menerapkan konsep Bilangan bulat"
    )
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["en_desc"].startswith(
        "Understand and apply integer concepts"
    )
    assert (
        nodes_by_id["km_d_matematika_bilangan_bulat"]["en_desc"]
        != nodes_by_id["km_d_matematika_bilangan_bulat"]["id_desc"]
    )

    edges = {(edge["from"], edge["to"], edge["edge_type"]) for edge in payload["edges"]}
    assert (
        "km_d_matematika_bilangan_bulat",
        "km_d_matematika_bilangan_rasional",
        "prerequisite",
    ) in edges


def test_get_knowledge_map_localizes_english_graph_fields(client, seeded_curriculum):
    response = client.get("/api/v1/knowledge-map?subject=matematika&locale=en")

    assert response.status_code == 200
    payload = response.json()
    nodes_by_id = {node["id"]: node for node in payload["nodes"]}

    assert payload["subject"]["name"] == "Mathematics"
    assert payload["graph"]["title"] == "Mathematics Knowledge Map"
    assert payload["groups"][0] == {"label": "Phase A / Algebra", "x": 28.0}
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["label"] == "Integers"
    assert (
        nodes_by_id["km_d_matematika_bilangan_bulat"]["description"]
        == "Build understanding of Integers within Numbers."
    )
    assert nodes_by_id["km_d_matematika_bilangan_desimal"]["label"] == (
        "Decimal numbers"
    )
    assert nodes_by_id["km_d_matematika_bilangan_bulat"]["metadata"]["locale"] == "en"


def test_advanced_math_graph_exposes_chain_rule_route_in_english(
    client,
    seeded_curriculum,
):
    response = client.get(
        "/api/v1/knowledge-map"
        "?subject=matematika_tingkat_lanjut&locale=en"
    )

    assert response.status_code == 200
    payload = response.json()
    nodes = {node["id"]: node for node in payload["nodes"]}
    edges = {(edge["from"], edge["to"]) for edge in payload["edges"]}

    algebraic = "km_f_matematika_tingkat_lanjut_turunan_secara_aljabar"
    chain_rule = "km_f_matematika_tingkat_lanjut_aturan_rantai"
    trig = "km_f_matematika_tingkat_lanjut_turunan_fungsi_trigonometri"
    curve_sketch = (
        "km_f_matematika_tingkat_lanjut_sketsa_kurva_menggunakan_turunan"
    )

    assert payload["subject"]["name"] == "Advanced Mathematics"
    assert payload["graph"]["title"] == "Advanced Mathematics Knowledge Map"
    assert nodes[chain_rule]["label"] == "Chain rule"
    assert nodes[trig]["label"] == "Derivatives of trigonometric functions"
    assert nodes[curve_sketch]["label"] == "Curve sketching using derivatives"
    assert (algebraic, chain_rule) in edges
    assert (chain_rule, trig) in edges
    assert (trig, curve_sketch) in edges


def test_get_knowledge_map_rejects_unsupported_locale(client, seeded_curriculum):
    response = client.get("/api/v1/knowledge-map?subject=matematika&locale=fr")

    assert response.status_code == 422


def test_get_knowledge_map_supports_math_alias(client, seeded_curriculum):
    response = client.get("/api/v1/knowledge-map?subject=math")

    assert response.status_code == 200
    assert response.json()["subject"]["code"] == "matematika"


def test_get_knowledge_map_returns_science_subject_graph(client, seeded_curriculum):
    response = client.get("/api/v1/knowledge-map?subject=kimia")

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"]["code"] == "kimia"
    assert payload["nodes"]
    assert payload["groups"]


def test_get_knowledge_map_integrates_science_foundation_phases(
    client,
    seeded_curriculum,
):
    response = client.get("/api/v1/knowledge-map?subject=fisika")

    assert response.status_code == 200
    payload = response.json()
    nodes = {node["id"]: node for node in payload["nodes"]}
    edges = {(edge["from"], edge["to"]) for edge in payload["edges"]}
    phases = {node["metadata"]["phase"] for node in payload["nodes"]}

    assert {"A", "B", "C", "D", "E", "F"}.issubset(phases)
    assert "km_a_ipas_benda_dan_sifat_sederhana" in nodes
    assert "km_d_ipa_pengukuran_dalam_ipa" in nodes
    assert "km_e_fisika_besaran_dan_satuan" in nodes
    assert (
        "km_c_ipas_metode_ilmiah_data_tabel_dan_grafik_sederhana",
        "km_d_ipa_pengukuran_dalam_ipa",
    ) in edges
    assert (
        "km_d_ipa_pengukuran_dalam_ipa",
        "km_e_fisika_besaran_dan_satuan",
    ) in edges
    assert payload["groups"][0]["label"].startswith("IPAS")
    assert not any(node_id.startswith("km_a_matematika") for node_id in nodes)


def test_get_knowledge_map_ignores_stale_seed_concepts(
    client,
    seeded_curriculum,
    test_engine,
):
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    with TestingSessionLocal() as session:
        subject = session.scalar(select(Subject).where(Subject.code == "ipas"))
        assert subject is not None
        session.add(
            KnowledgeConcept(
                subject_id=subject.id,
                code="legacy_stale_ipas_node",
                title="Legacy Stale IPAS Node",
                display_order=99999,
                layout_x=28,
                layout_y=82,
                metadata_json={
                    "phase": "A",
                    "domain": "Legacy",
                    "stale_seed": True,
                },
            )
        )
        session.commit()

    response = client.get("/api/v1/knowledge-map?subject=ipas")

    assert response.status_code == 200
    node_ids = {node["id"] for node in response.json()["nodes"]}
    assert "legacy_stale_ipas_node" not in node_ids


def test_get_knowledge_map_does_not_lock_root_nodes_without_prerequisites(
    client,
    seeded_curriculum,
):
    response = client.get("/api/v1/knowledge-map?subject=kimia")

    assert response.status_code == 200
    payload = response.json()
    incoming_ids = {edge["to"] for edge in payload["edges"]}
    root_nodes = [
        node for node in payload["nodes"] if node["id"] not in incoming_ids
    ]

    assert root_nodes
    assert all(node["status"] != "locked" for node in root_nodes)
    assert _node_by_id(payload, "km_e_kimia_hakikat_ilmu_kimia")["status"] != "locked"


def test_get_knowledge_map_uses_local_phase_order_for_fisika_preview_status(
    client,
    seeded_curriculum,
):
    response = client.get("/api/v1/knowledge-map?subject=fisika")

    assert response.status_code == 200
    payload = response.json()
    besaran = _node_by_id(payload, "km_e_fisika_besaran_dan_satuan")
    alat_ukur = _node_by_id(payload, "km_e_fisika_alat_ukur_dan_ketelitian")

    assert besaran["metadata"]["local_group_order"] == 1
    assert besaran["status"] == "active"
    assert alat_ukur["metadata"]["local_group_order"] == 2
    assert alat_ukur["status"] == "active"


def test_get_concept_detail_returns_mock_mastery_and_relations(
    client,
    seeded_curriculum,
):
    response = client.get(
        "/api/v1/knowledge-map/concepts/km_d_matematika_bilangan_rasional"
        "?subject=matematika"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["concept"]["id"] == "km_d_matematika_bilangan_rasional"
    assert payload["subject"]["code"] == "matematika"
    assert payload["metadata"]["mock_mastery"] is True
    assert isinstance(payload["mastery_confidence"], float)

    prerequisite_ids = {item["id"] for item in payload["prerequisites"]}
    related_ids = {item["id"] for item in payload["related_concepts"]}
    assert "km_d_matematika_bilangan_bulat" in prerequisite_ids
    assert "km_d_matematika_bilangan_irasional" in related_ids


def test_get_concept_detail_localizes_english_relations(client, seeded_curriculum):
    response = client.get(
        "/api/v1/knowledge-map/concepts/km_d_matematika_bilangan_rasional"
        "?subject=matematika&locale=en"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["concept"]["label"] == "Rational numbers"
    assert payload["subject"]["name"] == "Mathematics"
    prerequisites = {item["id"]: item for item in payload["prerequisites"]}
    assert prerequisites["km_d_matematika_bilangan_bulat"]["label"] == "Integers"


def test_get_concept_detail_allows_foundation_node_in_integrated_subject_context(
    client,
    seeded_curriculum,
):
    response = client.get(
        "/api/v1/knowledge-map/concepts/km_a_ipas_benda_dan_sifat_sederhana"
        "?subject=fisika"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["concept"]["id"] == "km_a_ipas_benda_dan_sifat_sederhana"
    assert payload["subject"]["code"] == "ipas"


def test_authenticated_knowledge_map_uses_low_learner_mastery_as_gap(
    client,
    seeded_curriculum,
):
    _override_optional_account(
        client,
        concept_code=TARGET_CONCEPT,
        mastery_score=0.25,
        confidence_score=0.2,
        evidence_count=2,
        status="review_due",
    )

    response = client.get("/api/v1/knowledge-map?subject=matematika")

    assert response.status_code == 200
    node = _node_by_id(response.json(), TARGET_CONCEPT)
    assert node["status"] == "gap"
    assert node["status_label"] == "GAP"
    assert node["metadata"]["personalization_source"] == "learner_concept_state"
    assert node["metadata"]["learner_state_present"] is True
    assert node["metadata"]["mock_mastery"] is False
    assert node["metadata"]["mastery_score"] == 0.25
    assert node["metadata"]["status_reason"] == "low_mastery_score"


def test_authenticated_concept_detail_uses_real_mastery_confidence(
    client,
    seeded_curriculum,
):
    _override_optional_account(
        client,
        concept_code=TARGET_CONCEPT,
        mastery_score=0.82,
        confidence_score=0.74,
        evidence_count=4,
        status="mastered",
    )

    response = client.get(
        f"/api/v1/knowledge-map/concepts/{TARGET_CONCEPT}?subject=matematika"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["concept"]["status"] == "mastered"
    assert payload["concept"]["status_label"] == "MASTERED"
    assert payload["mastery_confidence"] == 0.74
    assert payload["metadata"]["mock_mastery"] is False
    assert payload["metadata"]["source"] == "learner_concept_state"
    assert payload["metadata"]["evidence_count"] == 4


def test_authenticated_knowledge_map_marks_due_review_from_next_review_at(
    client,
    seeded_curriculum,
):
    _override_optional_account(
        client,
        concept_code=TARGET_CONCEPT,
        mastery_score=0.81,
        confidence_score=0.75,
        evidence_count=3,
        status="mastered",
        next_review_at=datetime.now(UTC) - timedelta(days=1),
    )

    response = client.get("/api/v1/knowledge-map?subject=matematika")

    assert response.status_code == 200
    node = _node_by_id(response.json(), TARGET_CONCEPT)
    assert node["status"] == "review"
    assert node["status_label"] == "REVIEW"
    assert node["metadata"]["status_reason"] == "review_due"


def test_authenticated_knowledge_map_locks_unmeasured_prerequisite_concept(
    client,
    seeded_curriculum,
):
    _override_optional_account(client)

    response = client.get("/api/v1/knowledge-map?subject=matematika")

    assert response.status_code == 200
    node = _node_by_id(response.json(), TARGET_CONCEPT)
    assert node["status"] == "locked"
    assert node["metadata"]["personalization_source"] == "no_learner_state"
    assert node["metadata"]["learner_state_present"] is False
    assert node["metadata"]["evidence_count"] == 0
    assert node["metadata"]["prerequisite_count"] == 2
    assert node["metadata"]["satisfied_prerequisite_count"] == 0
    assert node["metadata"]["status_reason"] == (
        "no_learner_evidence_prerequisites_unmet"
    )


def test_authenticated_knowledge_map_unlocks_concept_after_prerequisite_mastery(
    client,
    seeded_curriculum,
):
    _override_optional_account(
        client,
        concept_states=[
            {
                "concept_code": "km_d_matematika_bilangan_bulat",
                "mastery_score": 0.82,
                "confidence_score": 0.73,
                "evidence_count": 3,
                "status": "mastered",
            },
            {
                "concept_code": "km_c_matematika_pecahan_desimal_dan_persen",
                "mastery_score": 0.79,
                "confidence_score": 0.7,
                "evidence_count": 2,
                "status": "mastered",
            }
        ],
    )

    response = client.get("/api/v1/knowledge-map?subject=matematika")

    assert response.status_code == 200
    node = _node_by_id(response.json(), TARGET_CONCEPT)
    assert node["status"] == "ready"
    assert node["metadata"]["prerequisite_count"] == 2
    assert node["metadata"]["satisfied_prerequisite_count"] == 2
    assert node["metadata"]["prerequisites_satisfied"] is True
    assert node["metadata"]["status_reason"] == (
        "no_learner_evidence_curriculum_available"
    )


def test_authenticated_knowledge_map_locks_fisika_until_ipa_prerequisite_mastered(
    client,
    seeded_curriculum,
):
    _override_optional_account(client)

    response = client.get("/api/v1/knowledge-map?subject=fisika")

    assert response.status_code == 200
    node = _node_by_id(response.json(), "km_e_fisika_besaran_dan_satuan")
    assert node["status"] == "locked"
    assert node["metadata"]["prerequisite_count"] == 1
    assert node["metadata"]["satisfied_prerequisite_count"] == 0
    assert node["metadata"]["status_reason"] == (
        "no_learner_evidence_prerequisites_unmet"
    )


def test_get_concept_detail_unknown_concept_returns_404(client, seeded_curriculum):
    response = client.get("/api/v1/knowledge-map/concepts/unknown")

    assert response.status_code == 404


def test_get_knowledge_map_unknown_subject_returns_404(client, seeded_curriculum):
    response = client.get("/api/v1/knowledge-map?subject=history")

    assert response.status_code == 404


def _node_by_id(payload: dict, node_id: str) -> dict:
    return next(node for node in payload["nodes"] if node["id"] == node_id)


def _override_optional_account(
    client,
    *,
    preferred_language: str | None = None,
    concept_code: str | None = None,
    mastery_score: float = 0.0,
    confidence_score: float = 0.0,
    evidence_count: int = 0,
    status: str = "ready",
    next_review_at: datetime | None = None,
    concept_states: list[dict] | None = None,
) -> None:
    def override_optional_current_account(
        session: Session = Depends(get_session),
    ) -> UserAccount:
        account = session.get(UserAccount, ACCOUNT_ID)
        if account is None:
            account = UserAccount(
                id=ACCOUNT_ID,
                supabase_user_id="supabase-user-curriculum",
                email="learner-curriculum@example.com",
                display_name="Curriculum User",
                provider_subject="supabase-user-curriculum",
            )
            session.add(account)
            session.flush()

        if preferred_language is not None:
            profile = account.learner_profile
            if profile is None:
                profile = LearnerProfile(
                    user_id=account.id,
                    full_name=account.display_name,
                    selected_subjects=["matematika"],
                    onboarding_completed=True,
                )
                session.add(profile)
            profile.preferred_language = preferred_language

        requested_states = list(concept_states or [])
        if concept_code is not None:
            requested_states.append(
                {
                    "concept_code": concept_code,
                    "mastery_score": mastery_score,
                    "confidence_score": confidence_score,
                    "evidence_count": evidence_count,
                    "status": status,
                    "next_review_at": next_review_at,
                }
            )

        for requested_state in requested_states:
            requested_concept_code = requested_state["concept_code"]
            concept = session.scalar(
                select(KnowledgeConcept).where(
                    KnowledgeConcept.code == requested_concept_code
                )
            )
            assert concept is not None
            learner_state = session.scalar(
                select(LearnerConceptState).where(
                    LearnerConceptState.user_id == account.id,
                    LearnerConceptState.concept_id == concept.id,
                )
            )
            if learner_state is None:
                learner_state = LearnerConceptState(
                    user_id=account.id,
                    concept_id=concept.id,
                )
                session.add(learner_state)
            learner_state.status = requested_state.get("status", status)
            learner_state.mastery_score = requested_state.get(
                "mastery_score",
                mastery_score,
            )
            learner_state.confidence_score = requested_state.get(
                "confidence_score",
                confidence_score,
            )
            learner_state.evidence_count = requested_state.get(
                "evidence_count",
                evidence_count,
            )
            learner_state.last_evaluated_at = datetime.now(UTC)
            learner_state.next_review_at = requested_state.get(
                "next_review_at",
                next_review_at,
            )

        session.commit()
        session.refresh(account)
        return account

    client.app.dependency_overrides[get_optional_current_account] = (
        override_optional_current_account
    )
