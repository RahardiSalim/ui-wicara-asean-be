from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.accounts.models import UserAccount
from app.modules.assessments.metrics import PASS_PERCENT
from app.modules.curriculum.models import ConceptEdge, KnowledgeConcept, Subject
from app.modules.curriculum.schemas import (
    ConceptDetailResponse,
    ConceptRelation,
    KnowledgeMapEdge,
    KnowledgeMapGraph,
    KnowledgeMapGroup,
    KnowledgeMapNode,
    KnowledgeMapResponse,
    SubjectRead,
)
from app.modules.curriculum.kurikulum_merdeka import (
    GROUP_X_GAP,
    GROUP_X_START,
    NODE_Y_GAP,
    NODE_Y_START,
    PHASE_ORDER,
    SUBJECT_LABEL_EN,
    SUBJECT_DISPLAY_ORDER,
    canonical_subject_code,
    translate_curriculum_domain_to_english,
    translate_curriculum_label_to_english,
)
from app.modules.learning.models import (
    AssessmentAttempt,
    AssessmentQuestion,
    AssessmentSession,
    LearnerConceptState,
    LearningGoal,
)

STATUS_LABELS = {
    "mastered": "MASTERED",
    "active": "IN PROGRESS",
    "review": "REVIEW",
    "review_due": "REVIEW",
    "ready": "READY",
    "gap": "GAP",
    "locked": "LOCKED",
}

SUPPORTED_LOCALES = {"id", "en"}

KNOWLEDGE_MAP_SUBJECT_SCOPES = {
    "matematika": {"matematika"},
    "ipas": {"ipas"},
    "ipa": {"ipas", "ipa"},
    "fisika": {"ipas", "ipa", "fisika"},
    "kimia": {"ipas", "ipa", "kimia"},
    "biologi": {"ipas", "ipa", "biologi"},
    "matematika_tingkat_lanjut": {"matematika_tingkat_lanjut"},
}

KNOWLEDGE_GRAPH_ENABLED_SUBJECTS = {
    "matematika",
    "matematika_tingkat_lanjut",
}


@dataclass(frozen=True)
class _NodeLayout:
    x: float | None
    y: float | None
    group: str | None


@dataclass(frozen=True)
class _PrerequisiteGate:
    prerequisite_count: int
    satisfied_prerequisite_count: int

    @property
    def has_prerequisites(self) -> bool:
        return self.prerequisite_count > 0

    @property
    def is_satisfied(self) -> bool:
        return self.satisfied_prerequisite_count >= self.prerequisite_count


def list_active_subjects(session: Session) -> list[Subject]:
    return list(
        session.scalars(
            select(Subject)
            .where(Subject.is_active.is_(True))
            .order_by(Subject.display_order, Subject.name)
        )
    )


def subject_to_schema(
    subject: Subject,
    *,
    locale: str = "id",
    for_knowledge_graph_selector: bool = False,
) -> SubjectRead:
    metadata = subject.metadata_json or {}
    name_id = _subject_display_name(subject, locale="id", fallback=subject.name)
    name_en = _subject_display_name(
        subject,
        locale="en",
        fallback=subject.name,
        for_knowledge_graph_selector=for_knowledge_graph_selector,
    )
    response_metadata = {
        **metadata,
        "locale": _normalize_locale(locale),
        "name_id": name_id,
        "name_en": name_en,
        "is_available_in_knowledge_graph": (
            subject.code in KNOWLEDGE_GRAPH_ENABLED_SUBJECTS
        ),
        "is_locked_in_knowledge_graph": (
            subject.code not in KNOWLEDGE_GRAPH_ENABLED_SUBJECTS
        ),
    }
    return SubjectRead(
        id=subject.id,
        code=subject.code,
        name=_subject_display_name(
            subject,
            locale=locale,
            fallback=subject.name,
            for_knowledge_graph_selector=for_knowledge_graph_selector,
        ),
        description=_localized(
            metadata,
            "description",
            locale,
            fallback=subject.description,
        ),
        is_active=subject.is_active,
        display_order=subject.display_order,
        metadata=response_metadata,
    )


def _active_subject_by_code(session: Session, code: str) -> Subject | None:
    return session.scalar(
        select(Subject).where(
            Subject.code == canonical_subject_code(code),
            Subject.is_active.is_(True),
        )
    )


def _knowledge_map_concepts_for_subject(
    session: Session,
    subject: Subject,
) -> list[KnowledgeConcept]:
    scope_codes = KNOWLEDGE_MAP_SUBJECT_SCOPES.get(subject.code, {subject.code})
    concepts = list(
        session.scalars(
            select(KnowledgeConcept)
            .join(KnowledgeConcept.subject)
            .where(Subject.is_active.is_(True))
            .options(selectinload(KnowledgeConcept.subject))
        )
    )
    scoped_concepts = [
        concept
        for concept in concepts
        if concept.subject.code in scope_codes and not _is_stale_seed_concept(concept)
    ]

    return sorted(
        scoped_concepts,
        key=_concept_map_sort_key,
    )


def _concept_for_detail_context(
    session: Session,
    *,
    concept_code: str,
    subject_code: str | None,
) -> KnowledgeConcept | None:
    normalized_subject = canonical_subject_code(subject_code) if subject_code else None
    base_statement = (
        select(KnowledgeConcept)
        .join(KnowledgeConcept.subject)
        .options(selectinload(KnowledgeConcept.subject))
        .where(KnowledgeConcept.code == concept_code)
    )
    if normalized_subject:
        exact_match = session.scalar(
            base_statement.where(Subject.code == normalized_subject)
        )
        if exact_match is not None and not _is_stale_seed_concept(exact_match):
            return exact_match

    candidates = [
        concept
        for concept in session.scalars(
            base_statement.order_by(Subject.display_order, Subject.name)
        )
        if not _is_stale_seed_concept(concept)
    ]
    if not candidates:
        return None
    if not normalized_subject:
        return candidates[0]

    subject = _active_subject_by_code(session, normalized_subject)
    if subject is None:
        return None

    scoped_concept_ids = {
        concept.id for concept in _knowledge_map_concepts_for_subject(session, subject)
    }
    for candidate in candidates:
        if candidate.id in scoped_concept_ids:
            return candidate
    return None


def get_knowledge_map(
    session: Session,
    *,
    subject_code: str,
    locale: str = "id",
    user: UserAccount | None = None,
) -> KnowledgeMapResponse | None:
    locale = _normalize_locale(locale)
    normalized_code = canonical_subject_code(subject_code)
    subject = _active_subject_by_code(session, normalized_code)
    if subject is None:
        return None

    concepts = _knowledge_map_concepts_for_subject(session, subject)
    if not concepts:
        return None

    concept_by_id = {concept.id: concept for concept in concepts}
    edges = list(
        session.scalars(
            select(ConceptEdge)
            .where(
                ConceptEdge.from_concept_id.in_(concept_by_id),
                ConceptEdge.to_concept_id.in_(concept_by_id),
            )
            .order_by(ConceptEdge.edge_type, ConceptEdge.created_at)
        )
    )
    groups, node_layouts, graph = _knowledge_map_layout(
        concepts,
        subject,
        locale=locale,
    )
    state_by_concept = _learner_states_for_concepts(
        session,
        user=user,
        concept_ids=list(concept_by_id),
    )
    posttest_required_concept_ids = _posttest_required_concept_ids(
        session,
        user=user,
        concept_ids=list(concept_by_id),
    )
    latest_posttest_pass_by_concept = _latest_posttest_pass_by_concept(
        session,
        user=user,
        concept_ids=list(concept_by_id),
    )
    prerequisite_ids_by_concept: dict[UUID, list[UUID]] = defaultdict(list)
    for edge in edges:
        if edge.edge_type != "prerequisite":
            continue
        prerequisite_ids_by_concept[edge.to_concept_id].append(edge.from_concept_id)
    prerequisite_gates = {
        concept_id: _prerequisite_gate(prerequisite_ids, state_by_concept)
        for concept_id, prerequisite_ids in prerequisite_ids_by_concept.items()
    }
    empty_gate = _PrerequisiteGate(
        prerequisite_count=0,
        satisfied_prerequisite_count=0,
    )

    return KnowledgeMapResponse(
        subject=subject_to_schema(subject, locale=locale),
        graph=graph,
        groups=groups,
        nodes=[
            _concept_to_node(
                concept,
                groups,
                layout=node_layouts.get(concept.id),
                state=state_by_concept.get(concept.id),
                is_personalized=user is not None,
                prerequisite_gate=prerequisite_gates.get(concept.id, empty_gate),
                posttest_required=(concept.id in posttest_required_concept_ids),
                latest_posttest_pass=latest_posttest_pass_by_concept.get(concept.id),
                locale=locale,
            )
            for concept in concepts
        ],
        edges=[
            KnowledgeMapEdge(
                id=edge.id,
                from_node=concept_by_id[edge.from_concept_id].code,
                to=concept_by_id[edge.to_concept_id].code,
                edge_type=edge.edge_type,
                weight=edge.weight,
                metadata=edge.metadata_json or {},
            )
            for edge in edges
        ],
    )


def get_concept_detail(
    session: Session,
    *,
    concept_code: str,
    subject_code: str | None = None,
    locale: str = "id",
    user: UserAccount | None = None,
) -> ConceptDetailResponse | None:
    locale = _normalize_locale(locale)
    concept = _concept_for_detail_context(
        session,
        concept_code=concept_code,
        subject_code=subject_code,
    )
    if concept is None:
        return None

    incoming_edges = list(
        session.scalars(
            select(ConceptEdge)
            .where(ConceptEdge.to_concept_id == concept.id)
            .options(
                selectinload(ConceptEdge.from_concept).selectinload(
                    KnowledgeConcept.subject
                )
            )
            .order_by(ConceptEdge.edge_type, ConceptEdge.weight.desc())
        )
    )
    outgoing_edges = list(
        session.scalars(
            select(ConceptEdge)
            .where(ConceptEdge.from_concept_id == concept.id)
            .options(
                selectinload(ConceptEdge.to_concept).selectinload(
                    KnowledgeConcept.subject
                )
            )
            .order_by(ConceptEdge.edge_type, ConceptEdge.weight.desc())
        )
    )

    prerequisite_concepts = [
        edge.from_concept
        for edge in incoming_edges
        if edge.from_concept.subject_id == concept.subject_id
    ]
    related_concept_models = [
        edge.to_concept
        for edge in outgoing_edges
        if edge.to_concept.subject_id == concept.subject_id
    ]
    cross_subject_concepts = [
        *[
            edge.from_concept
            for edge in incoming_edges
            if edge.from_concept.subject_id != concept.subject_id
        ],
        *[
            edge.to_concept
            for edge in outgoing_edges
            if edge.to_concept.subject_id != concept.subject_id
        ],
    ]
    state_by_concept = _learner_states_for_concepts(
        session,
        user=user,
        concept_ids=[
            concept.id,
            *[item.id for item in prerequisite_concepts],
            *[item.id for item in related_concept_models],
            *[item.id for item in cross_subject_concepts],
        ],
    )
    concept_ids_for_detail = [
        concept.id,
        *[item.id for item in prerequisite_concepts],
        *[item.id for item in related_concept_models],
        *[item.id for item in cross_subject_concepts],
    ]
    posttest_required_concept_ids = _posttest_required_concept_ids(
        session,
        user=user,
        concept_ids=concept_ids_for_detail,
    )
    latest_posttest_pass_by_concept = _latest_posttest_pass_by_concept(
        session,
        user=user,
        concept_ids=concept_ids_for_detail,
    )
    concept_state = state_by_concept.get(concept.id)
    concept_prerequisite_gate = _prerequisite_gate(
        [item.id for item in prerequisite_concepts],
        state_by_concept,
    )

    prerequisites = [
        _concept_relation(
            item,
            state=state_by_concept.get(item.id),
            is_personalized=user is not None,
            prerequisite_gate=_PrerequisiteGate(
                prerequisite_count=0,
                satisfied_prerequisite_count=0,
            ),
            posttest_required=(item.id in posttest_required_concept_ids),
            latest_posttest_pass=latest_posttest_pass_by_concept.get(item.id),
            locale=locale,
        )
        for item in prerequisite_concepts
    ]
    related_concepts = [
        _concept_relation(
            item,
            state=state_by_concept.get(item.id),
            is_personalized=user is not None,
            prerequisite_gate=_PrerequisiteGate(
                prerequisite_count=1,
                satisfied_prerequisite_count=0,
            ),
            posttest_required=(item.id in posttest_required_concept_ids),
            latest_posttest_pass=latest_posttest_pass_by_concept.get(item.id),
            locale=locale,
        )
        for item in related_concept_models
    ]
    cross_subject_connections = [
        _concept_relation(
            item,
            state=state_by_concept.get(item.id),
            is_personalized=user is not None,
            prerequisite_gate=_PrerequisiteGate(
                prerequisite_count=1,
                satisfied_prerequisite_count=0,
            ),
            posttest_required=(item.id in posttest_required_concept_ids),
            latest_posttest_pass=latest_posttest_pass_by_concept.get(item.id),
            locale=locale,
        )
        for item in cross_subject_concepts
    ]

    return ConceptDetailResponse(
        concept=_concept_to_node(
            concept,
            _groups_for_subject(concept.subject, locale=locale),
            state=concept_state,
            is_personalized=user is not None,
            prerequisite_gate=concept_prerequisite_gate,
            posttest_required=(concept.id in posttest_required_concept_ids),
            latest_posttest_pass=latest_posttest_pass_by_concept.get(concept.id),
            locale=locale,
        ),
        subject=subject_to_schema(concept.subject, locale=locale),
        mastery_confidence=_mastery_confidence_for_detail(
            concept,
            state=concept_state,
            is_personalized=user is not None,
        ),
        prerequisites=prerequisites[:5],
        related_concepts=related_concepts[:5],
        cross_subject_connections=cross_subject_connections[:5],
        metadata=_concept_detail_metadata(
            concept,
            state=concept_state,
            is_personalized=user is not None,
            prerequisite_gate=concept_prerequisite_gate,
        ),
    )


def _concept_to_node(
    concept: KnowledgeConcept,
    groups: list[KnowledgeMapGroup],
    *,
    layout: _NodeLayout | None = None,
    state: LearnerConceptState | None = None,
    is_personalized: bool = False,
    prerequisite_gate: _PrerequisiteGate | None = None,
    posttest_required: bool = False,
    latest_posttest_pass: bool | None = None,
    locale: str = "id",
) -> KnowledgeMapNode:
    metadata: dict[str, Any] = concept.metadata_json or {}
    gate = prerequisite_gate or _PrerequisiteGate(
        prerequisite_count=0,
        satisfied_prerequisite_count=0,
    )
    status, reason = _status_for_concept(
        concept,
        state=state,
        is_personalized=is_personalized,
        prerequisite_gate=gate,
        posttest_required=posttest_required,
        latest_posttest_pass=latest_posttest_pass,
    )
    response_metadata = _node_metadata(
        concept,
        state=state,
        is_personalized=is_personalized,
        status_reason=reason,
        prerequisite_gate=gate,
    )
    response_metadata["locale"] = _normalize_locale(locale)
    label = _concept_display_label(concept, locale=locale)
    description = _concept_display_description(concept, locale=locale, label=label)
    return KnowledgeMapNode(
        id=concept.code,
        concept_id=concept.id,
        code=concept.code,
        label=label,
        title=label,
        description=description,
        id_desc=concept.id_desc or concept.description,
        en_desc=description if _normalize_locale(locale) == "en" else concept.en_desc,
        grade_band=concept.grade_band,
        status=status,
        status_label=(
            _personalized_status_label(status)
            if is_personalized
            else _concept_status_label(metadata, status, locale=locale)
        ),
        x=layout.x if layout else concept.layout_x,
        y=layout.y if layout else concept.layout_y,
        group=layout.group if layout else _nearest_group_label(concept.layout_x, groups),
        metadata=response_metadata,
    )


def _concept_relation(
    concept: KnowledgeConcept,
    *,
    state: LearnerConceptState | None = None,
    is_personalized: bool = False,
    prerequisite_gate: _PrerequisiteGate | None = None,
    posttest_required: bool = False,
    latest_posttest_pass: bool | None = None,
    locale: str = "id",
) -> ConceptRelation:
    metadata: dict[str, Any] = concept.metadata_json or {}
    gate = prerequisite_gate or _PrerequisiteGate(
        prerequisite_count=0,
        satisfied_prerequisite_count=0,
    )
    status, _reason = _status_for_concept(
        concept,
        state=state,
        is_personalized=is_personalized,
        prerequisite_gate=gate,
        posttest_required=posttest_required,
        latest_posttest_pass=latest_posttest_pass,
    )
    return ConceptRelation(
        id=concept.code,
        code=concept.code,
        label=_concept_display_label(concept, locale=locale),
        subject_code=concept.subject.code,
        subject_name=_localized(
            concept.subject.metadata_json or {},
            "name",
            locale,
            fallback=concept.subject.name,
        ),
        status=status,
        status_label=(
            _personalized_status_label(status)
            if is_personalized
            else _concept_status_label(metadata, status, locale=locale)
        ),
    )


def _groups_for_subject(
    subject: Subject,
    *,
    locale: str = "id",
) -> list[KnowledgeMapGroup]:
    graph_metadata = subject.metadata_json.get("graph", {}) if subject.metadata_json else {}
    groups_payload = graph_metadata.get("groups", [])
    return [
        KnowledgeMapGroup(
            label=_group_display_label(group, locale=locale),
            x=float(group["x"]),
        )
        for group in groups_payload
    ]


def _knowledge_map_layout(
    concepts: list[KnowledgeConcept],
    selected_subject: Subject,
    *,
    locale: str = "id",
) -> tuple[list[KnowledgeMapGroup], dict[UUID, _NodeLayout], KnowledgeMapGraph]:
    subject_ids = {concept.subject_id for concept in concepts}
    if subject_ids == {selected_subject.id}:
        return _single_subject_knowledge_map_layout(
            concepts,
            selected_subject,
            locale=locale,
        )

    return _integrated_knowledge_map_layout(concepts, selected_subject, locale=locale)


def _single_subject_knowledge_map_layout(
    concepts: list[KnowledgeConcept],
    subject: Subject,
    *,
    locale: str = "id",
) -> tuple[list[KnowledgeMapGroup], dict[UUID, _NodeLayout], KnowledgeMapGraph]:
    graph_metadata = subject.metadata_json.get("graph", {}) if subject.metadata_json else {}
    groups = _groups_for_subject(subject, locale=locale)
    node_layouts = {
        concept.id: _NodeLayout(
            x=concept.layout_x,
            y=concept.layout_y,
            group=_nearest_group_label(concept.layout_x, groups),
        )
        for concept in concepts
    }

    return (
        groups,
        node_layouts,
        KnowledgeMapGraph(
            title=_knowledge_map_title(subject, graph_metadata, locale=locale),
            width=float(graph_metadata.get("width", 1200)),
            height=float(graph_metadata.get("height", 600)),
            top_down=bool(graph_metadata.get("top_down", True)),
        ),
    )


def _integrated_knowledge_map_layout(
    concepts: list[KnowledgeConcept],
    selected_subject: Subject,
    *,
    locale: str = "id",
) -> tuple[list[KnowledgeMapGroup], dict[UUID, _NodeLayout], KnowledgeMapGraph]:
    ordered_concepts = sorted(concepts, key=_concept_map_sort_key)
    ordered_group_keys: list[tuple[str, str, str, str]] = []
    known_group_keys: set[tuple[str, str, str, str]] = set()
    for concept in ordered_concepts:
        key = _layout_group_key(concept)
        if key in known_group_keys:
            continue
        known_group_keys.add(key)
        ordered_group_keys.append(key)

    ordered_group_keys.sort(key=_layout_group_sort_key)
    groups = [
        KnowledgeMapGroup(
            label=_layout_group_label(key, locale=locale),
            x=GROUP_X_START + (index * GROUP_X_GAP),
        )
        for index, key in enumerate(ordered_group_keys)
    ]
    group_by_key = dict(zip(ordered_group_keys, groups, strict=True))
    local_counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
    node_layouts: dict[UUID, _NodeLayout] = {}
    max_group_count = 0

    for concept in ordered_concepts:
        key = _layout_group_key(concept)
        group = group_by_key[key]
        local_index = local_counts[key]
        local_counts[key] = local_index + 1
        max_group_count = max(max_group_count, local_counts[key])
        node_layouts[concept.id] = _NodeLayout(
            x=group.x,
            y=NODE_Y_START + (local_index * NODE_Y_GAP),
            group=group.label,
        )

    width = (groups[-1].x + 260.0) if groups else 1200.0
    height = max(600.0, NODE_Y_START + (max_group_count * NODE_Y_GAP) + 80.0)
    return (
        groups,
        node_layouts,
        KnowledgeMapGraph(
            title=(
                f"{_localized(selected_subject.metadata_json or {}, 'name', locale, fallback=selected_subject.name)} "
                "Integrated Knowledge Map"
            ),
            width=width,
            height=height,
            top_down=True,
        ),
    )


def _layout_group_key(concept: KnowledgeConcept) -> tuple[str, str, str, str]:
    metadata: dict[str, Any] = concept.metadata_json or {}
    return (
        concept.subject.code,
        _localized(
            metadata,
            "subject_label",
            "id",
            fallback=concept.subject.name,
        ).strip(),
        str(metadata.get("phase") or "").strip(),
        _localized(metadata, "domain", "id", fallback="General").strip(),
    )


def _layout_group_sort_key(key: tuple[str, str, str, str]) -> tuple[int, int, str, str]:
    subject_code, _subject_label, phase, domain = key
    return (
        PHASE_ORDER.get(phase, 999),
        SUBJECT_DISPLAY_ORDER.get(subject_code, 999),
        domain,
        subject_code,
    )


def _layout_group_label(key: tuple[str, str, str, str], *, locale: str = "id") -> str:
    subject_code, subject_label, phase, domain = key
    is_english = _normalize_locale(locale) == "en"
    phase_label = "Phase" if is_english else "Fase"
    localized_subject_label = (
        SUBJECT_LABEL_EN.get(subject_code, subject_label) if is_english else subject_label
    )
    localized_domain = (
        translate_curriculum_domain_to_english(domain) if is_english else domain
    )
    if phase and domain:
        return f"{localized_subject_label} - {phase_label} {phase} / {localized_domain}"
    if phase:
        return f"{localized_subject_label} - {phase_label} {phase}"
    return f"{localized_subject_label} - {localized_domain}"


def _concept_map_sort_key(concept: KnowledgeConcept) -> tuple[int, int, str, int, int, str]:
    metadata: dict[str, Any] = concept.metadata_json or {}
    phase = str(metadata.get("phase") or "").strip()
    domain = str(metadata.get("domain") or "").strip()
    return (
        PHASE_ORDER.get(phase, 999),
        SUBJECT_DISPLAY_ORDER.get(concept.subject.code, 999),
        domain,
        _safe_int(metadata.get("difficulty_order"), fallback=9999),
        concept.display_order,
        concept.title,
    )


def _is_stale_seed_concept(concept: KnowledgeConcept) -> bool:
    metadata: dict[str, Any] = concept.metadata_json or {}
    return metadata.get("stale_seed") is True


def _learner_states_for_concepts(
    session: Session,
    *,
    user: UserAccount | None,
    concept_ids: list[UUID],
) -> dict[UUID, LearnerConceptState]:
    if user is None or not concept_ids:
        return {}

    return {
        state.concept_id: state
        for state in session.scalars(
            select(LearnerConceptState).where(
                LearnerConceptState.user_id == user.id,
                LearnerConceptState.concept_id.in_(concept_ids),
            )
        )
    }


def _posttest_required_concept_ids(
    session: Session,
    *,
    user: UserAccount | None,
    concept_ids: list[UUID],
) -> set[UUID]:
    return set()


def _latest_posttest_pass_by_concept(
    session: Session,
    *,
    user: UserAccount | None,
    concept_ids: list[UUID],
) -> dict[UUID, bool]:
    if user is None or not concept_ids:
        return {}
    concept_id_set = set(concept_ids)
    result: dict[UUID, bool] = {}
    completed_sessions = list(
        session.scalars(
            select(AssessmentSession)
            .where(
                AssessmentSession.user_id == user.id,
                AssessmentSession.session_type == "posttest",
                AssessmentSession.status == "completed",
            )
            .order_by(AssessmentSession.completed_at.desc().nullslast(), AssessmentSession.created_at.desc())
            .limit(50)
        )
    )
    for assessment in completed_sessions:
        node_results = (assessment.metadata_json or {}).get("node_results")
        if not isinstance(node_results, dict):
            node_results = (assessment.decision_state_json or {}).get("node_results")
        if not isinstance(node_results, dict):
            continue
        for payload in node_results.values():
            if not isinstance(payload, dict):
                continue
            try:
                concept_uuid = UUID(str(payload.get("concept_id")))
            except (TypeError, ValueError):
                continue
            if concept_uuid not in concept_id_set or concept_uuid in result:
                continue
            answered_count = _safe_int(payload.get("answered_count"), fallback=0)
            total_questions = max(1, _safe_int(payload.get("total_questions"), fallback=3))
            answer_percent = _safe_float(payload.get("answer_percent"), fallback=0.0)
            score_percent = _safe_float(
                payload.get("score_percent"),
                fallback=_safe_float(payload.get("scaled_score"), fallback=0.0) * 10,
            )
            result[concept_uuid] = (
                answered_count >= total_questions
                and answer_percent >= PASS_PERCENT
                and score_percent >= PASS_PERCENT
            )

    rows = list(
        session.execute(
            select(
                AssessmentQuestion.concept_id,
                AssessmentAttempt.is_correct,
                AssessmentAttempt.submitted_at,
            )
            .join(AssessmentAttempt, AssessmentAttempt.question_id == AssessmentQuestion.id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentAttempt.session_id)
            .where(
                AssessmentSession.user_id == user.id,
                AssessmentSession.session_type == "posttest",
                AssessmentQuestion.concept_id.in_(concept_ids),
            )
            .order_by(AssessmentQuestion.concept_id, AssessmentAttempt.submitted_at.desc())
        )
    )
    latest: dict[UUID, dict[str, int]] = {}
    for concept_id, is_correct, _submitted_at in rows:
        if concept_id is None or concept_id in result:
            continue
        payload = latest.setdefault(concept_id, {"answered": 0, "correct": 0})
        if payload["answered"] >= 3:
            continue
        payload["answered"] += 1
        payload["correct"] += 1 if is_correct else 0
    for concept_id, payload in latest.items():
        answered = payload["answered"]
        if answered <= 0:
            continue
        score_percent = (payload["correct"] / max(1, answered)) * 100
        result[concept_id] = score_percent >= PASS_PERCENT and answered >= 3
    return result


def _status_for_concept(
    concept: KnowledgeConcept,
    *,
    state: LearnerConceptState | None,
    is_personalized: bool,
    prerequisite_gate: _PrerequisiteGate,
    posttest_required: bool,
    latest_posttest_pass: bool | None,
) -> tuple[str, str]:
    metadata: dict[str, Any] = concept.metadata_json or {}
    curriculum_status = _curriculum_default_status(
        metadata,
        has_prerequisites=prerequisite_gate.has_prerequisites,
    )
    if not is_personalized:
        return curriculum_status, "curriculum_default"

    if state is None:
        if prerequisite_gate.has_prerequisites and not prerequisite_gate.is_satisfied:
            return "locked", "no_learner_evidence_prerequisites_unmet"
        return "ready", "no_learner_evidence_curriculum_available"

    evidence_count = state.evidence_count or 0
    mastery_score = _clamp_score(state.mastery_score)
    stored_status = _normalize_status(state.status)
    if evidence_count <= 0:
        if prerequisite_gate.has_prerequisites and not prerequisite_gate.is_satisfied:
            return "locked", "learner_state_without_evidence_prerequisites_unmet"
        return "ready", "learner_state_without_evidence"
    if mastery_score < 0.4:
        return "gap", "low_mastery_score"
    if _is_review_due(state.next_review_at):
        return "review", "review_due"
    if stored_status in {"review", "review_due"}:
        return "review", "stored_review_status"
    if stored_status == "active":
        return "active", "stored_active_status"
    if stored_status == "mastered" or mastery_score >= 0.7:
        if posttest_required and latest_posttest_pass is not True:
            return "review", "posttest_mastery_gate_unmet"
        return "mastered", "strong_mastery_score"
    if mastery_score < 0.55:
        return "review", "moderate_low_mastery_score"
    return "ready", "developing_mastery_score"


def _curriculum_default_status(
    metadata: dict[str, Any],
    *,
    has_prerequisites: bool,
) -> str:
    status = _normalize_status(str(metadata.get("default_status", "ready")))
    if status == "locked" and not has_prerequisites:
        return "ready"
    return status


def _node_metadata(
    concept: KnowledgeConcept,
    *,
    state: LearnerConceptState | None,
    is_personalized: bool,
    status_reason: str,
    prerequisite_gate: _PrerequisiteGate,
) -> dict[str, Any]:
    metadata: dict[str, Any] = dict(concept.metadata_json or {})
    if not is_personalized:
        return metadata

    metadata.update(
        {
            "personalization_source": (
                "learner_concept_state" if state is not None else "no_learner_state"
            ),
            "learner_state_present": state is not None,
            "status_reason": status_reason,
            "has_prerequisites": prerequisite_gate.has_prerequisites,
            "prerequisite_count": prerequisite_gate.prerequisite_count,
            "satisfied_prerequisite_count": (
                prerequisite_gate.satisfied_prerequisite_count
            ),
            "prerequisites_satisfied": prerequisite_gate.is_satisfied,
            "curriculum_default_status": _curriculum_default_status(
                metadata,
                has_prerequisites=prerequisite_gate.has_prerequisites,
            ),
            "mock_mastery": False,
        }
    )
    if state is None:
        metadata.update(
            {
                "mastery_score": None,
                "confidence_score": None,
                "evidence_count": 0,
                "last_evaluated_at": None,
                "next_review_at": None,
            }
        )
        return metadata

    metadata.update(
        {
            "stored_status": state.status,
            "mastery_score": round(_clamp_score(state.mastery_score), 4),
            "confidence_score": round(_clamp_score(state.confidence_score), 4),
            "evidence_count": state.evidence_count or 0,
            "last_evaluated_at": _datetime_to_iso(state.last_evaluated_at),
            "next_review_at": _datetime_to_iso(state.next_review_at),
        }
    )
    return metadata


def _concept_detail_metadata(
    concept: KnowledgeConcept,
    *,
    state: LearnerConceptState | None,
    is_personalized: bool,
    prerequisite_gate: _PrerequisiteGate,
) -> dict[str, Any]:
    if not is_personalized:
        return {
            "mock_mastery": True,
            "source": "curriculum_graph",
        }

    _status, reason = _status_for_concept(
        concept,
        state=state,
        is_personalized=True,
        prerequisite_gate=prerequisite_gate,
        posttest_required=False,
        latest_posttest_pass=None,
    )
    return {
        "mock_mastery": False,
        "source": "learner_concept_state" if state is not None else "no_learner_state",
        "learner_state_present": state is not None,
        "status_reason": reason,
        "has_prerequisites": prerequisite_gate.has_prerequisites,
        "prerequisite_count": prerequisite_gate.prerequisite_count,
        "satisfied_prerequisite_count": prerequisite_gate.satisfied_prerequisite_count,
        "prerequisites_satisfied": prerequisite_gate.is_satisfied,
        "mastery_score": round(_clamp_score(state.mastery_score), 4) if state else None,
        "confidence_score": round(_clamp_score(state.confidence_score), 4) if state else None,
        "evidence_count": (state.evidence_count or 0) if state else 0,
        "last_evaluated_at": _datetime_to_iso(state.last_evaluated_at) if state else None,
        "next_review_at": _datetime_to_iso(state.next_review_at) if state else None,
    }


def _mastery_confidence_for_detail(
    concept: KnowledgeConcept,
    *,
    state: LearnerConceptState | None,
    is_personalized: bool,
) -> float:
    if not is_personalized:
        return _mock_mastery_confidence(concept)
    if state is None:
        return 0.0
    confidence = _clamp_score(state.confidence_score)
    return round(confidence if confidence > 0 else _clamp_score(state.mastery_score), 4)


def _mock_mastery_confidence(concept: KnowledgeConcept) -> float:
    metadata: dict[str, Any] = concept.metadata_json or {}
    status = str(metadata.get("default_status", "ready"))
    return {
        "mastered": 0.92,
        "active": 0.62,
        "review": 0.48,
        "ready": 0.34,
        "gap": 0.18,
        "locked": 0.08,
    }.get(status, 0.34)


def _prerequisite_gate(
    prerequisite_ids: list[UUID],
    state_by_concept: dict[UUID, LearnerConceptState],
) -> _PrerequisiteGate:
    return _PrerequisiteGate(
        prerequisite_count=len(prerequisite_ids),
        satisfied_prerequisite_count=sum(
            1
            for prerequisite_id in prerequisite_ids
            if _is_prerequisite_satisfied(state_by_concept.get(prerequisite_id))
        ),
    )


def _is_prerequisite_satisfied(state: LearnerConceptState | None) -> bool:
    if state is None or (state.evidence_count or 0) <= 0:
        return False
    mastery_score = _clamp_score(state.mastery_score)
    stored_status = _normalize_status(state.status)
    return stored_status == "mastered" or mastery_score >= 0.7


def _nearest_group_label(
    x: float | None,
    groups: list[KnowledgeMapGroup],
) -> str | None:
    if x is None or not groups:
        return None
    return min(groups, key=lambda group: abs(group.x - x)).label


def _concept_status_label(
    metadata: dict[str, Any],
    status: str,
    *,
    locale: str = "id",
) -> str:
    if metadata.get("preview_status_only"):
        return STATUS_LABELS.get(status, status.upper())

    if metadata.get("source_curriculum_graph"):
        return STATUS_LABELS.get(status, status.upper())

    return STATUS_LABELS.get(status, status.upper())


def _personalized_status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status.upper())


def _normalize_status(status: str) -> str:
    normalized = status.strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "review_due": "review_due",
        "in_progress": "active",
        "unknown": "locked",
    }.get(normalized, normalized if normalized in STATUS_LABELS else "ready")


def _safe_int(value: Any, *, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: Any, *, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _is_review_due(value: datetime | None) -> bool:
    if value is None:
        return False
    candidate = value if value.tzinfo else value.replace(tzinfo=UTC)
    return candidate <= datetime.now(UTC)


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    candidate = value if value.tzinfo else value.replace(tzinfo=UTC)
    return candidate.isoformat()


def _normalize_locale(locale: str | None) -> str:
    normalized = (locale or "id").strip().lower()
    return normalized if normalized in SUPPORTED_LOCALES else "id"


def _localized(
    metadata: dict[str, Any],
    base_key: str,
    locale: str,
    *,
    fallback: str | None = None,
) -> str:
    normalized_locale = _normalize_locale(locale)
    locale_key = f"{base_key}_{normalized_locale}"
    id_key = f"{base_key}_id"
    candidates = [
        metadata.get(locale_key),
        metadata.get(id_key),
        metadata.get(base_key),
        fallback,
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return text
    return ""


def _subject_display_name(
    subject: Subject,
    *,
    locale: str,
    fallback: str,
    for_knowledge_graph_selector: bool = False,
) -> str:
    if for_knowledge_graph_selector and _normalize_locale(locale) == "en":
        if subject.code == "matematika":
            return "Math"
    return _localized(subject.metadata_json or {}, "name", locale, fallback=fallback)


def _knowledge_map_title(
    subject: Subject,
    graph_metadata: dict[str, Any],
    *,
    locale: str,
) -> str:
    localized_title = _localized(
        graph_metadata,
        "title",
        locale,
        fallback="",
    )
    cleaned_title = _strip_kurikulum_merdeka_label(localized_title)
    if cleaned_title:
        return cleaned_title
    subject_name = _localized(
        subject.metadata_json or {},
        "name",
        locale,
        fallback=subject.name,
    )
    if _normalize_locale(locale) == "id":
        return f"Peta Pengetahuan {subject_name}"
    return f"{subject_name} Knowledge Map"


def _strip_kurikulum_merdeka_label(value: str) -> str:
    cleaned = str(value or "").strip()
    for prefix in ("Kurikulum Merdeka ", "Graph Kurikulum Merdeka "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    return cleaned


def _concept_display_label(
    concept: KnowledgeConcept,
    *,
    locale: str = "id",
) -> str:
    metadata: dict[str, Any] = concept.metadata_json or {}
    if _normalize_locale(locale) == "en":
        source_label = _localized(metadata, "label", "id", fallback=concept.title)
        explicit_label = str(metadata.get("label_en") or "").strip()
        if explicit_label and explicit_label.casefold() != source_label.casefold():
            return explicit_label
        translated_label = translate_curriculum_label_to_english(source_label)
        if translated_label:
            return translated_label
    return _localized(metadata, "label", locale, fallback=concept.title)


def _concept_display_description(
    concept: KnowledgeConcept,
    *,
    locale: str = "id",
    label: str,
) -> str | None:
    metadata: dict[str, Any] = concept.metadata_json or {}
    if _normalize_locale(locale) != "en":
        return _localized(
            metadata,
            "description",
            locale,
            fallback=concept.description,
        )

    if not label:
        return _localized(
            metadata,
            "description",
            locale,
            fallback=concept.description,
        )

    domain_id = _localized(metadata, "domain", "id")
    domain = translate_curriculum_domain_to_english(domain_id) if domain_id else ""
    domain_suffix = f" within {domain}" if domain else ""
    return f"Build understanding of {label}{domain_suffix}."


def _group_display_label(group: dict[str, Any], *, locale: str = "id") -> str:
    if _normalize_locale(locale) == "en":
        phase = str(group.get("phase") or "").strip()
        domain_id = str(group.get("domain_id") or group.get("domain") or "").strip()
        domain_label = (
            translate_curriculum_domain_to_english(domain_id) if domain_id else ""
        )
        if phase and domain_label:
            return f"Phase {phase} / {domain_label}"
        if phase:
            return f"Phase {phase}"
        if domain_label:
            return domain_label
    return _localized(group, "label", locale, fallback=str(group["label"]))
