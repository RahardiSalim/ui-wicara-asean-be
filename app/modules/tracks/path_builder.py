from __future__ import annotations

from collections import deque
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.accounts.models import UserAccount
from app.modules.curriculum.models import ConceptEdge, KnowledgeConcept
from app.modules.learning.models import LearningGoal, LearningTrack, TrackModule
from app.modules.learning_goal_resolution.schemas import PathModuleRead, PathSelectionResponse


PATH_OPTIONS = {
    "review_only",
    "target_reinforcement",
    "target_from_basics",
    "target_intro",
    "repair_prerequisites",
    "full_foundation_path",
}


class TrackBuilderService:
    def select_path(
        self,
        session: Session,
        *,
        user: UserAccount,
        learning_goal_id: UUID,
        path_option: str,
    ) -> PathSelectionResponse | None:
        if path_option not in PATH_OPTIONS:
            raise ValueError("Unsupported path option.")

        goal = session.scalar(
            select(LearningGoal)
            .where(LearningGoal.id == learning_goal_id, LearningGoal.user_id == user.id)
            .options(selectinload(LearningGoal.track).selectinload(LearningTrack.modules))
        )
        if goal is None:
            return None
        if goal.status in {"archived", "cancelled"}:
            raise ValueError("Path selection requires an active learning goal.")

        concepts = _concepts_for_path(session, goal=goal, path_option=path_option)
        track_context = _track_learning_context(
            goal=goal,
            concepts=concepts,
            path_option=path_option,
        )
        track = goal.track
        if track is None:
            title = f"{goal.normalized_topic} path"
            track = LearningTrack(
                user_id=user.id,
                learning_goal_id=goal.id,
                title=title,
                subtitle="Learning path from selected goal",
                status="active",
                progress_percent=0,
                metadata_json={
                    "source": "goal_path_selection",
                    "path_option": path_option,
                    "learning_context": track_context,
                },
            )
            session.add(track)
            session.flush()
        else:
            track.status = "active"
            track.metadata_json = {
                **(track.metadata_json or {}),
                "source": "goal_path_selection",
                "path_option": path_option,
                "learning_context": track_context,
            }
            for module in list(track.modules):
                session.delete(module)
            session.flush()

        modules = _module_payloads(goal=goal, concepts=concepts, path_option=path_option)
        for index, payload in enumerate(modules, start=1):
            session.add(
                TrackModule(
                    track_id=track.id,
                    concept_id=payload["concept"].id if payload.get("concept") else None,
                    title=payload["title"],
                    description=payload["description"],
                    estimated_minutes=payload["minutes"],
                    difficulty_label=payload["difficulty"],
                    sort_order=index,
                    status="ready" if index == 1 else "locked",
                    metadata_json=payload["metadata"],
                )
            )
        goal.status = "in_progress"
        session.commit()

        refreshed = session.scalar(
            select(LearningTrack)
            .where(LearningTrack.id == track.id)
            .options(selectinload(LearningTrack.modules))
        )
        assert refreshed is not None
        concept_by_id = {
            concept.id: concept
            for concept in session.scalars(
                select(KnowledgeConcept).where(
                    KnowledgeConcept.id.in_(
                        [module.concept_id for module in refreshed.modules if module.concept_id]
                    )
                )
            )
        }
        return PathSelectionResponse(
            track_id=refreshed.id,
            goal_status=goal.status,
            modules=[
                PathModuleRead(
                    id=module.id,
                    title=module.title,
                    description=module.description,
                    concept_code=concept_by_id[module.concept_id].code
                    if module.concept_id in concept_by_id
                    else None,
                    difficulty_label=module.difficulty_label,
                    sort_order=module.sort_order,
                    status=module.status,
                )
                for module in refreshed.modules
            ],
            metadata={
                "path_option": path_option,
                "learning_context": track_context,
            },
        )


def _concepts_for_path(
    session: Session,
    *,
    goal: LearningGoal,
    path_option: str,
) -> list[KnowledgeConcept]:
    target = session.get(KnowledgeConcept, goal.target_concept_id) if goal.target_concept_id else None
    if target is None:
        return []

    concept_codes = _ordered_diagnosis_codes(
        session,
        goal=goal,
        target_code=target.code,
        path_option=path_option,
    )

    seen: set[str] = set()
    ordered_codes = [code for code in concept_codes if code and not (code in seen or seen.add(code))]
    if not ordered_codes:
        ordered_codes = [target.code]

    concepts = {
        concept.code: concept
        for concept in session.scalars(select(KnowledgeConcept).where(KnowledgeConcept.code.in_(ordered_codes)))
    }
    return [concepts[code] for code in ordered_codes if code in concepts]


def _module_payloads(
    *,
    goal: LearningGoal,
    concepts: list[KnowledgeConcept],
    path_option: str,
) -> list[dict[str, object]]:
    if not concepts:
        return [
            {
                "concept": None,
                "title": goal.normalized_topic,
                "description": "Continue from the selected learning goal.",
                "minutes": 12,
                "difficulty": "Medium",
            }
        ]

    modules: list[dict[str, object]] = []
    diagnosis = _goal_diagnosis(goal)
    nodes_by_code = _diagnosis_nodes_by_code(diagnosis)
    target = _original_target(goal=goal, concepts=concepts, diagnosis=diagnosis)
    route_codes = [concept.code for concept in concepts]
    already_understood = [
        {
            "concept_code": code,
            "title": str(node.get("title") or code),
            "status": str(node.get("status") or ""),
        }
        for code, node in nodes_by_code.items()
        if str(node.get("status") or "") in {"ready", "probably_ready"}
    ]
    for index, concept in enumerate(concepts, start=1):
        node = nodes_by_code.get(concept.code, {})
        is_target = concept.code == target.get("concept_code")
        evidence_summary = node.get("evidence_summary")
        evidence = node.get("evidence") if isinstance(node.get("evidence"), list) else []
        evidence_ids = [
            str(item.get("attempt_id"))
            for item in evidence
            if isinstance(item, dict) and item.get("attempt_id")
        ]
        module_role = _module_role(
            node=node,
            is_target=is_target,
            is_first=index == 1,
        )
        if path_option == "review_only":
            title = f"Review: {concept.title}"
            description = "Short review and challenge practice for the confirmed learning goal."
            difficulty = "Hard"
        elif is_target:
            title = concept.title
            description = "Learn the target concept with guided examples and checks."
            difficulty = "Medium"
        elif module_role == "verification":
            title = f"Verify: {concept.title}"
            description = (
                "Confirm this connecting skill before returning to the original target."
            )
            difficulty = "Medium"
        else:
            title = f"Repair: {concept.title}"
            description = "Strengthen this prerequisite before returning to the target concept."
            difficulty = "Easy"
        modules.append(
            {
                "concept": concept,
                "title": title,
                "description": description,
                "minutes": 10 if difficulty == "Easy" else 14,
                "difficulty": difficulty,
                "metadata": {
                    "path_option": path_option,
                    "module_role": module_role,
                    "original_target": target,
                    "current_module": {
                        "concept_id": str(concept.id),
                        "concept_code": concept.code,
                        "title": concept.title,
                        "role": module_role,
                        "route_index": index - 1,
                        "route_length": len(concepts),
                    },
                    "diagnosis_reason": (
                        str(diagnosis.get("diagnostic_summary") or "")
                        or str(diagnosis.get("summary") or "")
                    ),
                    "diagnosis_evidence": {
                        "status": node.get("status"),
                        "confidence": node.get("confidence"),
                        "summary": evidence_summary if isinstance(evidence_summary, dict) else {},
                        "source_attempt_ids": evidence_ids,
                    },
                    "already_understood": already_understood,
                    "route": route_codes,
                    "returns_to_original_target": not is_target,
                },
            }
        )
    return modules


_REMEDIATION_OPTIONS = {
    "target_from_basics",
    "repair_prerequisites",
    "full_foundation_path",
}
_ACTIONABLE_STATUSES = {"gap", "probably_gap", "fragile", "partial"}


def _goal_diagnosis(goal: LearningGoal) -> dict[str, Any]:
    metadata = goal.metadata_json or {}
    diagnosis = metadata.get("diagnosis")
    return diagnosis if isinstance(diagnosis, dict) else {}


def _diagnosis_nodes_by_code(diagnosis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = diagnosis.get("nodes")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("concept_code")): row
        for row in rows
        if isinstance(row, dict) and str(row.get("concept_code") or "").strip()
    }


def _ordered_diagnosis_codes(
    session: Session,
    *,
    goal: LearningGoal,
    target_code: str,
    path_option: str,
) -> list[str]:
    if path_option not in _REMEDIATION_OPTIONS:
        return [target_code]

    diagnosis = _goal_diagnosis(goal)
    nodes = _diagnosis_nodes_by_code(diagnosis)
    candidates = [
        node
        for code, node in nodes.items()
        if code != target_code
        and str(node.get("role") or "") == "prerequisite"
        and str(node.get("status") or "") in _ACTIONABLE_STATUSES
    ]
    if not candidates:
        return [target_code]

    candidates.sort(key=_diagnosis_anchor_sort_key)
    anchor_code = str(candidates[0]["concept_code"])
    route = _route_from_diagnosis_parents(
        nodes=nodes,
        anchor_code=anchor_code,
        target_code=target_code,
    )
    if route[-1] != target_code:
        route = _shortest_graph_route(
            session,
            anchor_code=anchor_code,
            target_code=target_code,
            preferred_codes=set(nodes),
        )
    if not route or route[-1] != target_code:
        route = [anchor_code, target_code]
    return route


def _diagnosis_anchor_sort_key(node: dict[str, Any]) -> tuple[int, int, int, float, str]:
    summary = node.get("evidence_summary")
    attempt_count = (
        int(summary.get("attempt_count") or 0)
        if isinstance(summary, dict)
        else 0
    )
    severity = {
        "gap": 0,
        "probably_gap": 1,
        "fragile": 2,
        "partial": 3,
    }.get(str(node.get("status") or ""), 4)
    return (
        0 if attempt_count else 1,
        severity,
        -int(node.get("depth") or 0),
        -float(node.get("confidence") or 0.0),
        str(node.get("concept_code") or ""),
    )


def _route_from_diagnosis_parents(
    *,
    nodes: dict[str, dict[str, Any]],
    anchor_code: str,
    target_code: str,
) -> list[str]:
    route = [anchor_code]
    seen = {anchor_code}
    current = anchor_code
    while current != target_code:
        parent = str(nodes.get(current, {}).get("parent") or "")
        if not parent or parent in seen:
            break
        route.append(parent)
        seen.add(parent)
        current = parent
    return route


def _shortest_graph_route(
    session: Session,
    *,
    anchor_code: str,
    target_code: str,
    preferred_codes: set[str],
) -> list[str]:
    concepts = {
        concept.code: concept
        for concept in session.scalars(
            select(KnowledgeConcept).where(
                KnowledgeConcept.code.in_(preferred_codes | {anchor_code, target_code})
            )
        )
    }
    anchor = concepts.get(anchor_code)
    target = concepts.get(target_code)
    if anchor is None or target is None:
        return []

    edges = list(
        session.scalars(
            select(ConceptEdge)
            .where(
                ConceptEdge.edge_type == "prerequisite",
                ConceptEdge.from_concept_id.in_([concept.id for concept in concepts.values()]),
                ConceptEdge.to_concept_id.in_([concept.id for concept in concepts.values()]),
            )
            .order_by(ConceptEdge.weight.desc(), ConceptEdge.created_at)
        )
    )
    code_by_id = {concept.id: concept.code for concept in concepts.values()}
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        source = code_by_id.get(edge.from_concept_id)
        destination = code_by_id.get(edge.to_concept_id)
        if source and destination:
            adjacency.setdefault(source, []).append(destination)

    queue: deque[list[str]] = deque([[anchor_code]])
    visited = {anchor_code}
    while queue:
        route = queue.popleft()
        current = route[-1]
        if current == target_code:
            return route
        for destination in adjacency.get(current, []):
            if destination in visited:
                continue
            visited.add(destination)
            queue.append([*route, destination])
    return []


def _original_target(
    *,
    goal: LearningGoal,
    concepts: list[KnowledgeConcept],
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    target = diagnosis.get("target")
    if isinstance(target, dict) and target.get("concept_code"):
        return {
            "concept_id": target.get("concept_id"),
            "concept_code": target.get("concept_code"),
            "title": target.get("title") or goal.normalized_topic,
        }
    concept = next(
        (item for item in concepts if item.id == goal.target_concept_id),
        concepts[-1] if concepts else None,
    )
    return {
        "concept_id": str(concept.id) if concept else (
            str(goal.target_concept_id) if goal.target_concept_id else None
        ),
        "concept_code": concept.code if concept else None,
        "title": concept.title if concept else goal.normalized_topic,
    }


def _module_role(
    *,
    node: dict[str, Any],
    is_target: bool,
    is_first: bool,
) -> str:
    if is_target:
        return "original_target"
    status = str(node.get("status") or "")
    if is_first and status in _ACTIONABLE_STATUSES:
        return "prerequisite_gap"
    return "verification"


def _track_learning_context(
    *,
    goal: LearningGoal,
    concepts: list[KnowledgeConcept],
    path_option: str,
) -> dict[str, Any]:
    diagnosis = _goal_diagnosis(goal)
    target = _original_target(goal=goal, concepts=concepts, diagnosis=diagnosis)
    return {
        "original_target": target,
        "selected_path": path_option,
        "route": [concept.code for concept in concepts],
        "diagnosis_reason": (
            str(diagnosis.get("diagnostic_summary") or "")
            or str(diagnosis.get("summary") or "")
        ),
        "returns_to_original_target": bool(concepts)
        and concepts[-1].code == target.get("concept_code"),
    }
