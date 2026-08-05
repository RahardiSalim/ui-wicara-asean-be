from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA_PATH = Path(__file__).with_name("data") / "curated_explanations.json"


@lru_cache
def _registry() -> dict[str, Any]:
    with _DATA_PATH.open(encoding="utf-8") as source:
        payload = json.load(source)
    return payload if isinstance(payload, dict) else {}


def explanation_card_for(
    *,
    concept_subtype: str,
    language: str,
) -> dict[str, Any] | None:
    entries = _registry().get("concept_subtypes")
    if not isinstance(entries, dict):
        return None
    entry = entries.get(str(concept_subtype or "").strip().lower())
    if not isinstance(entry, dict):
        return None
    localized = entry.get("en" if str(language).lower() == "en" else "id")
    if not isinstance(localized, dict):
        return None
    return {"source": "curated_curriculum", **localized}
