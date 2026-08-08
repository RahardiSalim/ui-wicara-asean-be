from __future__ import annotations

from collections import deque
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.curriculum.models import ConceptEdge, KnowledgeConcept, Subject


class GraphScopeBuilder:
    def build(
        self,
        session: Session,
        *,
        target_concept_id: UUID,
        max_depth: int = 2,
    ) -> dict[str, object]:
        target = session.get(KnowledgeConcept, target_concept_id)
        if target is None:
            raise LookupError("Target concept was not found.")

        subject = session.get(Subject, target.subject_id)
        nodes_by_code: dict[str, dict[str, object]] = {
            target.code: _node_payload(target, depth=0, role="target", parent=None)
        }
        edges: list[dict[str, object]] = []
        queue: deque[tuple[KnowledgeConcept, int]] = deque([(target, 0)])

        while queue:
            concept, depth = queue.popleft()
            if depth >= max_depth:
                continue
            incoming_edges = list(
                session.scalars(
                    select(ConceptEdge)
                    .where(
                        ConceptEdge.to_concept_id == concept.id,
                        ConceptEdge.edge_type == "prerequisite",
                    )
                    .options(selectinload(ConceptEdge.from_concept))
                    .order_by(ConceptEdge.weight.desc(), ConceptEdge.created_at)
                )
            )
            for edge in incoming_edges:
                prerequisite = edge.from_concept
                if prerequisite is None:
                    continue
                edge_metadata = edge.metadata_json or {}
                next_depth = depth + 1
                if prerequisite.code not in nodes_by_code:
                    nodes_by_code[prerequisite.code] = _node_payload(
                        prerequisite,
                        depth=next_depth,
                        role="prerequisite",
                        parent=concept.code,
                    )
                    queue.append((prerequisite, next_depth))
                edges.append(
                    {
                        "from": concept.code,
                        "to": prerequisite.code,
                        "edge_type": edge.edge_type,
                        "weight": float(edge.weight or 1.0),
                        "depth": next_depth,
                        "reason_id": str(edge_metadata.get("reason_id") or ""),
                        "reason_en": str(edge_metadata.get("reason_en") or ""),
                    }
                )

        nodes = sorted(
            nodes_by_code.values(),
            key=lambda item: (int(item["depth"]), 0 if item["role"] == "target" else 1, str(item["title"])),
        )
        return {
            "target": target.code,
            "target_concept_id": str(target.id),
            "subject_code": subject.code if subject else "",
            "max_depth": max_depth,
            "nodes": nodes,
            "edges": edges,
        }

def _node_payload(
    concept: KnowledgeConcept,
    *,
    depth: int,
    role: str,
    parent: str | None,
) -> dict[str, object]:
    metadata = concept.metadata_json or {}
    return {
        "concept_id": str(concept.id),
        "concept_code": concept.code,
        "title": concept.title,
        "description": concept.description,
        "depth": depth,
        "role": role,
        "parent": parent,
        "assessment_evidence_id": metadata.get("assessment_evidence_id", []),
        "assessment_evidence_en": metadata.get("assessment_evidence_en", []),
        "common_misconceptions_id": metadata.get("common_misconceptions_id", []),
        "common_misconceptions_en": metadata.get("common_misconceptions_en", []),
    }
