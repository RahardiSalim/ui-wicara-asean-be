import json
from pathlib import Path

from sqlalchemy import func, select

from app.modules.curriculum.models import ConceptEdge, KnowledgeConcept, Subject
from app.modules.curriculum.seed import seed_curriculum
from app.modules.curriculum.kurikulum_merdeka import load_kurikulum_merdeka_seed_data


def test_curriculum_seed_is_idempotent_and_creates_kurikulum_graph(db_session):
    seed_data = load_kurikulum_merdeka_seed_data()

    first = seed_curriculum(db_session)
    second = seed_curriculum(db_session)

    assert first.subjects_created == len(seed_data.subjects)
    assert first.concepts_created == len(seed_data.concepts)
    assert first.edges_created == len(seed_data.edges)
    assert second.subjects_created == 0
    assert second.concepts_created == 0
    assert second.edges_created == 0

    subject_count = db_session.scalar(select(func.count()).select_from(Subject))
    concept_count = db_session.scalar(select(func.count()).select_from(KnowledgeConcept))
    edge_count = db_session.scalar(select(func.count()).select_from(ConceptEdge))
    assert subject_count == len(seed_data.subjects)
    assert concept_count == len(seed_data.concepts)
    assert edge_count == len(seed_data.edges)


def test_default_seed_contains_advanced_derivative_path(db_session):
    seed_curriculum(db_session)

    route_codes = [
        "km_f_matematika_tingkat_lanjut_turunan_secara_aljabar",
        "km_f_matematika_tingkat_lanjut_aturan_rantai",
        "km_f_matematika_tingkat_lanjut_turunan_fungsi_trigonometri",
        "km_f_matematika_tingkat_lanjut_sketsa_kurva_menggunakan_turunan",
    ]
    concepts = {
        concept.code: concept
        for concept in db_session.scalars(
            select(KnowledgeConcept).where(KnowledgeConcept.code.in_(route_codes))
        )
    }
    assert set(concepts) == set(route_codes)

    for source_code, target_code in zip(route_codes, route_codes[1:]):
        edge = db_session.scalar(
            select(ConceptEdge).where(
                ConceptEdge.from_concept_id == concepts[source_code].id,
                ConceptEdge.to_concept_id == concepts[target_code].id,
                ConceptEdge.edge_type == "prerequisite",
            )
        )
        assert edge is not None


def test_unified_seed_moves_legacy_advanced_math_concepts_without_changing_ids(
    db_session,
):
    legacy_subject = Subject(
        code="matematika_tingkat_lanjut",
        name="Matematika Tingkat Lanjut",
        is_active=True,
    )
    concept = KnowledgeConcept(
        subject=legacy_subject,
        code="km_f_matematika_tingkat_lanjut_aturan_rantai",
        title="Aturan rantai",
        display_order=1,
    )
    db_session.add_all([legacy_subject, concept])
    db_session.flush()
    original_concept_id = concept.id

    seed_curriculum(db_session)
    db_session.expire_all()

    mathematics = db_session.scalar(
        select(Subject).where(Subject.code == "matematika")
    )
    moved_concept = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.subject_id == mathematics.id,
            KnowledgeConcept.code
            == "km_f_matematika_tingkat_lanjut_aturan_rantai",
        )
    )
    legacy_subject = db_session.scalar(
        select(Subject).where(Subject.code == "matematika_tingkat_lanjut")
    )

    assert moved_concept.id == original_concept_id
    assert legacy_subject.is_active is False
    assert legacy_subject.metadata_json["superseded_by"] == "matematika"


def test_curriculum_seed_creates_required_prerequisite_edge(db_session):
    seed_curriculum(db_session)

    bilangan_bulat = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.code == "km_d_matematika_bilangan_bulat"
        )
    )
    bilangan_rasional = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.code == "km_d_matematika_bilangan_rasional"
        )
    )
    edge = db_session.scalar(
        select(ConceptEdge).where(
            ConceptEdge.from_concept_id == bilangan_bulat.id,
            ConceptEdge.to_concept_id == bilangan_rasional.id,
            ConceptEdge.edge_type == "prerequisite",
        )
    )

    assert bilangan_bulat.subject.code == "matematika"
    assert edge is not None
    assert edge.weight == 0.85


def test_curriculum_seed_preserves_bilingual_metadata(db_session):
    seed_curriculum(db_session)

    subject = db_session.scalar(select(Subject).where(Subject.code == "matematika"))
    concept = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.code == "km_d_matematika_bilangan_bulat"
        )
    )
    edge = db_session.scalar(
        select(ConceptEdge)
        .join(
            KnowledgeConcept,
            ConceptEdge.from_concept_id == KnowledgeConcept.id,
        )
        .where(KnowledgeConcept.code == "km_d_matematika_bilangan_bulat")
    )

    assert subject.metadata_json["name_id"] == "Matematika"
    assert subject.metadata_json["name_en"] == "Mathematics"
    assert subject.metadata_json["graph"]["groups"][0]["label_en"] == "Phase A / Algebra"
    assert concept.title == "Bilangan bulat"
    assert concept.metadata_json["label_id"] == "Bilangan bulat"
    assert concept.metadata_json["label_en"] == "Integers"
    assert concept.metadata_json["description_en"].startswith("Learners can represent")
    assert concept.metadata_json["domain_en"] == "Numbers"
    assert concept.metadata_json["translation_status"]["en"] == "machine_draft"
    assert edge is not None
    assert edge.metadata_json["source_curriculum_graph"].endswith(".json")


def test_curriculum_seed_generates_english_label_when_seed_copies_indonesian(
    db_session,
):
    seed_curriculum(db_session)

    concept = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.code == "km_d_matematika_bilangan_desimal"
        )
    )

    assert concept is not None
    assert concept.title == "Bilangan desimal"
    assert concept.metadata_json["label_id"] == "Bilangan desimal"
    assert concept.metadata_json["label_en"] == "Decimal numbers"
    assert concept.en_desc.startswith("Learners can represent and compare decimal numbers")


def test_default_seed_uses_revised_golden_flow_metadata(db_session):
    seed_curriculum(db_session)

    curve_sketch = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.code
            == "km_f_matematika_tingkat_lanjut_sketsa_kurva_menggunakan_turunan"
        )
    )
    chain_rule = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.code
            == "km_f_matematika_tingkat_lanjut_aturan_rantai"
        )
    )
    trig_derivative = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.code
            == "km_f_matematika_tingkat_lanjut_turunan_fungsi_trigonometri"
        )
    )
    conditional_edge = db_session.scalar(
        select(ConceptEdge).where(
            ConceptEdge.from_concept_id == chain_rule.id,
            ConceptEdge.to_concept_id == trig_derivative.id,
            ConceptEdge.edge_type == "prerequisite",
        )
    )

    assert curve_sketch.metadata_json["source_curriculum_graph"] == (
        "wicara_kurikulum_merdeka_graph_complete_revised_2026-08-08.json"
    )
    assert len(curve_sketch.metadata_json["assessment_evidence_id"]) == 4
    assert "f'(x)=0" in curve_sketch.metadata_json["common_misconceptions_id"][0]
    assert set(curve_sketch.metadata_json["question_generation_guidance_id"]) == {
        "easy",
        "medium",
        "hard",
    }
    assert conditional_edge.metadata_json["applicability"] == "conditional"


def test_reseed_removes_edges_deleted_from_revised_graph(db_session):
    backend_root = Path(__file__).resolve().parents[2]
    old_graph = (
        backend_root
        / "app"
        / "modules"
        / "curriculum"
        / "data"
        / "wicara_kurikulum_merdeka_graph_complete_matematika_digabung.json"
    )
    revised_data = load_kurikulum_merdeka_seed_data()

    seed_curriculum(db_session, graph_path=old_graph)
    result = seed_curriculum(db_session)

    edge_count = db_session.scalar(select(func.count()).select_from(ConceptEdge))
    assert result.edges_deleted == 107
    assert edge_count == len(revised_data.edges)


def test_curriculum_seed_marks_removed_concepts_as_stale(db_session, tmp_path):
    legacy_graph_path = tmp_path / "legacy_graph.json"
    legacy_graph_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "curriculum": "kurikulum_merdeka",
                    "version": "legacy-test",
                },
                "nodes": [
                    {
                        "id": "legacy_ipas_node",
                        "subject": "ipas",
                        "subject_label": "IPAS",
                        "phase": "A",
                        "school_level": "SD",
                        "grade_range": "1-2",
                        "domain": "Legacy",
                        "difficulty_order": 1,
                        "label_id": "Legacy IPAS Node",
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    seed_curriculum(db_session, graph_path=legacy_graph_path)
    legacy_concept = db_session.scalar(
        select(KnowledgeConcept).where(KnowledgeConcept.code == "legacy_ipas_node")
    )

    assert legacy_concept is not None
    assert legacy_concept.metadata_json.get("stale_seed") is not True

    seed_curriculum(db_session)
    db_session.refresh(legacy_concept)

    assert legacy_concept.metadata_json["stale_seed"] is True
    assert (
        legacy_concept.metadata_json["stale_reason"]
        == "not_present_in_current_curriculum_seed"
    )


def test_curriculum_seed_persists_bilingual_node_descriptions(db_session, tmp_path):
    graph_path = tmp_path / "bilingual_graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "curriculum": "kurikulum_merdeka",
                    "version": "bilingual-test",
                },
                "nodes": [
                    {
                        "id": "km_d_ipa_bilingual_node",
                        "subject": "ipa",
                        "subject_label": "IPA",
                        "phase": "D",
                        "school_level": "SMP/MTs",
                        "grade_range": "7-9",
                        "domain": "Sains",
                        "difficulty_order": 1,
                        "label_id": "Node bilingual",
                        "label_en": "Bilingual node",
                        "description_id": "Deskripsi Indonesia untuk node.",
                        "description_en": "English description for the node.",
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    seed_curriculum(db_session, graph_path=graph_path)

    concept = db_session.scalar(
        select(KnowledgeConcept).where(KnowledgeConcept.code == "km_d_ipa_bilingual_node")
    )
    assert concept is not None
    assert concept.description == "Deskripsi Indonesia untuk node."
    assert concept.id_desc == "Deskripsi Indonesia untuk node."
    assert concept.en_desc == "English description for the node."
