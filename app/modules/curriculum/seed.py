from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.modules.curriculum.models import ConceptEdge, KnowledgeConcept, Subject
from app.modules.curriculum.kurikulum_merdeka import (
    SUBJECT_ALIASES,
    canonical_subject_code,
    load_kurikulum_merdeka_seed_data,
)


@dataclass(frozen=True)
class CurriculumSeedResult:
    subjects_created: int = 0
    subjects_updated: int = 0
    concepts_created: int = 0
    concepts_updated: int = 0
    edges_created: int = 0
    edges_updated: int = 0
    edges_deleted: int = 0


def seed_curriculum(
    session: Session,
    *,
    commit: bool = True,
    graph_path: str | Path | None = None,
) -> CurriculumSeedResult:
    seed_data = load_kurikulum_merdeka_seed_data(graph_path)
    counts = {
        "subjects_created": 0,
        "subjects_updated": 0,
        "concepts_created": 0,
        "concepts_updated": 0,
        "edges_created": 0,
        "edges_updated": 0,
        "edges_deleted": 0,
    }

    subjects_by_code: dict[str, Subject] = {}
    for subject_data in seed_data.subjects:
        subject = session.scalar(select(Subject).where(Subject.code == subject_data.code))
        if subject is None:
            subject = Subject(code=subject_data.code)
            session.add(subject)
            counts["subjects_created"] += 1
        else:
            counts["subjects_updated"] += 1

        subject.name = subject_data.name
        subject.description = subject_data.description
        subject.display_order = subject_data.display_order
        subject.is_active = True
        subject.metadata_json = subject_data.metadata
        subjects_by_code[subject.code] = subject

    session.flush()
    _deactivate_legacy_subject_aliases(session, subjects_by_code)
    session.flush()

    concepts_by_code: dict[str, KnowledgeConcept] = {}
    current_concept_keys: set[tuple[str, str]] = set()
    for concept_data in seed_data.concepts:
        subject = subjects_by_code[concept_data.subject_code]
        current_concept_keys.add((subject.code, concept_data.code))
        concept = session.scalar(
            select(KnowledgeConcept).where(
                KnowledgeConcept.subject_id == subject.id,
                KnowledgeConcept.code == concept_data.code,
            )
        )
        if concept is None:
            concept = KnowledgeConcept(
                subject_id=subject.id,
                code=concept_data.code,
            )
            session.add(concept)
            counts["concepts_created"] += 1
        else:
            counts["concepts_updated"] += 1

        concept.title = concept_data.title
        concept.description = concept_data.description
        concept.id_desc = concept_data.id_desc
        concept.en_desc = concept_data.en_desc
        concept.grade_band = concept_data.grade_band
        concept.display_order = concept_data.display_order
        concept.layout_x = concept_data.layout_x
        concept.layout_y = concept_data.layout_y
        metadata = dict(concept_data.metadata)
        metadata.pop("stale_seed", None)
        metadata.pop("stale_reason", None)
        concept.metadata_json = metadata
        concepts_by_code[concept.code] = concept

    _mark_stale_concepts(session, subjects_by_code, current_concept_keys)
    session.flush()

    current_edge_keys: set[tuple[object, object, str]] = set()
    for edge_data in seed_data.edges:
        from_concept = concepts_by_code[edge_data.from_code]
        to_concept = concepts_by_code[edge_data.to_code]
        current_edge_keys.add(
            (from_concept.id, to_concept.id, edge_data.edge_type)
        )
        edge = session.scalar(
            select(ConceptEdge).where(
                ConceptEdge.from_concept_id == from_concept.id,
                ConceptEdge.to_concept_id == to_concept.id,
                ConceptEdge.edge_type == edge_data.edge_type,
            )
        )
        if edge is None:
            edge = ConceptEdge(
                from_concept_id=from_concept.id,
                to_concept_id=to_concept.id,
                edge_type=edge_data.edge_type,
            )
            session.add(edge)
            counts["edges_created"] += 1
        else:
            counts["edges_updated"] += 1

        edge.weight = edge_data.weight
        edge.metadata_json = edge_data.metadata

    counts["edges_deleted"] = _remove_stale_seed_edges(
        session,
        current_edge_keys=current_edge_keys,
    )

    if commit:
        session.commit()

    return CurriculumSeedResult(**counts)


def _remove_stale_seed_edges(
    session: Session,
    *,
    current_edge_keys: set[tuple[object, object, str]],
) -> int:
    deleted = 0
    for edge in session.scalars(select(ConceptEdge)):
        metadata = edge.metadata_json or {}
        if not metadata.get("source_curriculum_graph"):
            continue
        edge_key = (edge.from_concept_id, edge.to_concept_id, edge.edge_type)
        if edge_key in current_edge_keys:
            continue
        session.delete(edge)
        deleted += 1
    return deleted


def _deactivate_legacy_subject_aliases(
    session: Session,
    subjects_by_code: dict[str, Subject],
) -> None:
    canonical_codes = set(subjects_by_code)
    for alias, canonical_code in SUBJECT_ALIASES.items():
        if canonical_code not in canonical_codes or alias == canonical_code:
            continue
        legacy_subject = session.scalar(select(Subject).where(Subject.code == alias))
        if legacy_subject is None:
            continue

        canonical_subject = subjects_by_code[canonical_code]
        for legacy_concept in session.scalars(
            select(KnowledgeConcept).where(
                KnowledgeConcept.subject_id == legacy_subject.id
            )
        ):
            existing = session.scalar(
                select(KnowledgeConcept).where(
                    KnowledgeConcept.subject_id == canonical_subject.id,
                    KnowledgeConcept.code == legacy_concept.code,
                )
            )
            if existing is None:
                legacy_concept.subject_id = canonical_subject.id
            else:
                legacy_concept.metadata_json = {
                    **(legacy_concept.metadata_json or {}),
                    "stale_seed": True,
                    "stale_reason": "duplicate_from_legacy_subject_alias",
                }

        legacy_subject.is_active = False
        legacy_subject.metadata_json = {
            **(legacy_subject.metadata_json or {}),
            "superseded_by": canonical_subject.code,
        }


def _mark_stale_concepts(
    session: Session,
    subjects_by_code: dict[str, Subject],
    current_concept_keys: set[tuple[str, str]],
) -> None:
    for subject in subjects_by_code.values():
        concepts = session.scalars(
            select(KnowledgeConcept).where(KnowledgeConcept.subject_id == subject.id)
        )
        for concept in concepts:
            if (subject.code, concept.code) in current_concept_keys:
                continue
            concept.metadata_json = {
                **(concept.metadata_json or {}),
                "stale_seed": True,
                "stale_reason": "not_present_in_current_curriculum_seed",
            }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed WICARA curriculum data.")
    parser.add_argument(
        "--graph-path",
        default=None,
        help="Path to wicara_kurikulum_merdeka_graph_complete.json.",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        result = seed_curriculum(session, graph_path=args.graph_path)
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
