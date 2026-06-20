from __future__ import annotations

import json

from app.core.language import is_indonesian_language, language_display_name
from app.modules.curriculum.kurikulum_merdeka import (
    translate_curriculum_label_to_english,
)
from app.modules.learning_goal_resolution.candidate_retriever import ConceptCandidate
from app.modules.learning_goal_resolution.description_cleaner import (
    first_course_description,
)


PROMPT_VERSION = "goal_resolver_v5_localized_nodes"


def build_goal_resolution_prompt(
    *,
    raw_query: str,
    candidates: list[ConceptCandidate],
    language: str,
    search_scope: str,
) -> str:
    # Node content is localized binary (id/en seed columns); the AI's *response*
    # is generated in the full target language (e.g. "Vietnamese").
    content_language = "id" if is_indonesian_language(language) else "en"
    response_language = language_display_name(language)
    node_payload = [
        _node_payload(candidate, language=content_language) for candidate in candidates
    ]
    return f"""
You resolve a learner's free-text learning goal to an existing knowledge_concepts node.

Rules:
- Choose only from the provided nodes.
- Do not invent concept_code values.
- The learner query may be written in English or Indonesian.
- Available nodes are already localized to the learner response language.
- Use the localized node title and description to understand each candidate.
- Return status="exact_match" only when one node directly teaches the requested learning goal.
- Return status="ambiguous" when multiple nodes are plausible, the query is too broad, or the query could mean different curriculum concepts.
- Return status="no_match" when no node directly represents the goal in this node list.
- Never pick a weakly related node just because it is the closest available.
- confidence must be a number from 0 to 1.
- If status="exact_match", selected_concept_code must be from the node list.
- If status is "ambiguous" or "no_match", selected_concept_code must be null.
- alternatives must contain only concept_code values from the node list.
- Write clarification_question in the learner response language.
- Return compact JSON only with:
  status, selected_concept_code, confidence, alternatives, reason, should_expand_scope, clarification_question.

User query:
{raw_query}

Learner response language:
{response_language}

Search scope:
{search_scope}

Available nodes:
{json.dumps(node_payload, ensure_ascii=False)}
""".strip()


def _node_payload(candidate: ConceptCandidate, *, language: str) -> dict[str, str]:
    concept = candidate.concept
    title = _localized_title(candidate, language=language)
    return {
        "concept_code": concept.code,
        "title": title,
        "grade_band": concept.grade_band or "",
        "description": _compact(
            _localized_description(candidate, language=language, title=title)
        ),
    }


def _localized_title(candidate: ConceptCandidate, *, language: str) -> str:
    concept = candidate.concept
    label_id = _metadata_text(candidate, "label_id") or concept.title
    if is_indonesian_language(language):
        return label_id
    explicit = _metadata_text(candidate, "label_en")
    if explicit and explicit.casefold() != label_id.casefold():
        return explicit
    return translate_curriculum_label_to_english(label_id) or explicit or label_id


def _localized_description(
    candidate: ConceptCandidate,
    *,
    language: str,
    title: str,
) -> str:
    concept = candidate.concept
    if is_indonesian_language(language):
        return first_course_description(
            concept.id_desc,
            _metadata_text(candidate, "description_id"),
            concept.description,
        )
    return first_course_description(
        concept.en_desc,
        _metadata_text(candidate, "description_en"),
        _metadata_text(candidate, "en_desc"),
    )


def _compact(value: str, *, max_length: int = 180) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1].rstrip() + "..."


def _metadata_text(candidate: ConceptCandidate, key: str) -> str:
    value = (candidate.concept.metadata_json or {}).get(key)
    return str(value).strip() if value is not None else ""
