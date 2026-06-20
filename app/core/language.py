from __future__ import annotations

from typing import Any

# Supported ASEAN languages. English is the universal fallback.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "id", "ms", "vi", "th", "fil")

# Back-compat: kept for callers that import these sets directly.
INDONESIAN_LANGUAGE_ALIASES = {
    "id",
    "id-id",
    "ind",
    "indo",
    "indonesian",
    "bahasa",
    "bahasa indonesia",
}
ENGLISH_LANGUAGE_ALIASES = {"en", "en-us", "en-gb", "eng", "english"}

_ALIASES: dict[str, str] = {
    # English
    "en": "en", "en-us": "en", "en-gb": "en", "eng": "en", "english": "en",
    # Indonesian
    "id": "id", "id-id": "id", "ind": "id", "indo": "id", "indonesian": "id",
    "bahasa": "id", "bahasa indonesia": "id",
    # Malay
    "ms": "ms", "ms-my": "ms", "msa": "ms", "may": "ms", "malay": "ms",
    "melayu": "ms", "bahasa melayu": "ms",
    # Vietnamese
    "vi": "vi", "vi-vn": "vi", "vie": "vi", "vietnamese": "vi", "tieng viet": "vi",
    # Thai
    "th": "th", "th-th": "th", "tha": "th", "thai": "th",
    # Filipino / Tagalog
    "fil": "fil", "fil-ph": "fil", "tl": "fil", "tl-ph": "fil", "tgl": "fil",
    "filipino": "fil", "tagalog": "fil",
}

# Native names (for UI display).
_ENDONYMS: dict[str, str] = {
    "en": "English",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
    "vi": "Tiếng Việt",
    "th": "ภาษาไทย",
    "fil": "Filipino",
}

# English names (for AI prompts / back-compat display).
_ENGLISH_NAMES: dict[str, str] = {
    "en": "English",
    "id": "Indonesian",
    "ms": "Malay",
    "vi": "Vietnamese",
    "th": "Thai",
    "fil": "Filipino",
}


def normalize_language_code(language: str | None, *, fallback: str = "en") -> str:
    """Normalize any language string to one of SUPPORTED_LANGUAGES (fallback en)."""
    normalized = str(language or "").strip().lower().replace("_", "-")
    if not normalized:
        return fallback if fallback in SUPPORTED_LANGUAGES else "en"
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    base = normalized.split("-", 1)[0]
    if base in _ALIASES:
        return _ALIASES[base]
    if "indo" in normalized:
        return "id"
    return fallback if fallback in SUPPORTED_LANGUAGES else "en"


def is_supported_language(language: str | None) -> bool:
    return normalize_language_code(language, fallback="") in SUPPORTED_LANGUAGES


def is_indonesian_language(language: str | None) -> bool:
    return normalize_language_code(language) == "id"


def language_display_name(language: str | None) -> str:
    """English name of the language (e.g. 'Vietnamese'). Used in AI prompts."""
    return _ENGLISH_NAMES.get(normalize_language_code(language), "English")


def language_endonym(language: str | None) -> str:
    """Native name of the language (e.g. 'Tiếng Việt'). Used for UI labels."""
    return _ENDONYMS.get(normalize_language_code(language), "English")


def preferred_language_code(user: Any) -> str:
    profile = getattr(user, "learner_profile", None)
    preferred_language = getattr(profile, "preferred_language", None)
    return normalize_language_code(preferred_language)
