"""On-demand, cached localization of concept content into supported languages.

`id`/`en` come straight from the seed columns (`title`/`id_desc`/`en_desc`).
`ms`/`vi`/`th`/`fil` are served from the `concept_translations` cache, or
AI-translated on first request and cached. Best-effort: any failure falls back
to English and is not cached.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.language import language_display_name, normalize_language_code
from app.modules.ai.client import ai_client
from app.modules.curriculum.kurikulum_merdeka import translate_curriculum_label_to_english
from app.modules.curriculum.models import KnowledgeConcept
from app.modules.curriculum.translation_models import ConceptTranslation

logger = logging.getLogger(__name__)


def _indonesian(concept: KnowledgeConcept) -> tuple[str, str]:
    return (concept.title or "", concept.id_desc or concept.description or "")


def _english(concept: KnowledgeConcept) -> tuple[str, str]:
    title = translate_curriculum_label_to_english(concept.title) if concept.title else ""
    return (title or concept.title or "", concept.en_desc or "")


def _ai_translate(title: str, description: str, lang_code: str) -> dict[str, str] | None:
    target = language_display_name(lang_code)  # English name, e.g. "Vietnamese"
    system = "You translate educational STEM content. Return strict JSON only, no prose."
    user = (
        f"Translate the concept below into {target}. Keep it natural for a school learner. "
        'Return JSON exactly as {"title": "...", "description": "..."}.\n\n'
        f"title: {title}\ndescription: {description}"
    )
    try:
        response = asyncio.run(
            ai_client.generate(
                system_instruction=system,
                user_instruction=user,
                params={"temperature": 0.0, "response_format": {"type": "json_object"}},
            )
        )
        data = json.loads(response.text)
        return {
            "title": str(data.get("title") or title),
            "description": str(data.get("description") or description),
        }
    except Exception:  # noqa: BLE001 - translation must never break the request
        logger.exception("AI translation failed for lang=%s", lang_code)
        return None


def localize_concept(
    session: Session, concept: KnowledgeConcept, lang: str | None
) -> dict[str, Any]:
    """Return ``{lang, title, description, source}`` for the concept in ``lang``."""
    code = normalize_language_code(lang)
    if code == "id":
        title, description = _indonesian(concept)
        return {"lang": "id", "title": title, "description": description, "source": "seed"}
    if code == "en":
        title, description = _english(concept)
        return {"lang": "en", "title": title, "description": description, "source": "seed"}

    cached = session.scalar(
        select(ConceptTranslation).where(
            ConceptTranslation.concept_id == concept.id,
            ConceptTranslation.lang == code,
        )
    )
    if cached is not None:
        return {
            "lang": code,
            "title": cached.title,
            "description": cached.description,
            "source": cached.source,
        }

    src_title, src_desc = _english(concept)
    if not src_title and not src_desc:
        src_title, src_desc = _indonesian(concept)

    translated = _ai_translate(src_title, src_desc, code)
    if translated is None:
        return {"lang": "en", "title": src_title, "description": src_desc, "source": "fallback_en"}

    row = ConceptTranslation(
        concept_id=concept.id,
        lang=code,
        title=translated["title"][:255],
        description=translated["description"],
        source="ai",
    )
    session.add(row)
    try:
        session.commit()
        session.refresh(row)
    except Exception:  # noqa: BLE001 - cache write is best-effort
        session.rollback()
    return {"lang": code, "title": translated["title"][:255], "description": translated["description"], "source": "ai"}
