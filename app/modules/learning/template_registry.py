from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TemplateRegistryEntry:
    template_id: str
    template_path: str
    scene_class: str
    schema_id: str
    aliases: tuple[str, ...]
    render_engine: str
    engine_family: str | None
    runtime: dict[str, Any]


@dataclass(frozen=True)
class ResolvedTemplateEntry:
    entry: TemplateRegistryEntry
    resolved_from: str
    used_alias: bool


class TemplateRegistryError(ValueError):
    pass


def resolve_template_entry(raw_template_id: str) -> ResolvedTemplateEntry:
    normalized = _normalize_template_token(raw_template_id)
    if not normalized:
        raise TemplateRegistryError("Template id is empty.")

    canonical_map = _registry_by_template_id()
    if normalized in canonical_map:
        entry = canonical_map[normalized]
        return ResolvedTemplateEntry(entry=entry, resolved_from=normalized, used_alias=False)

    alias_map = _registry_by_alias()
    mapped = alias_map.get(normalized)
    if mapped is None:
        raise TemplateRegistryError(f"Template '{raw_template_id}' is not registered.")
    entry = canonical_map[mapped]
    return ResolvedTemplateEntry(entry=entry, resolved_from=normalized, used_alias=True)


def registered_template_ids() -> list[str]:
    return sorted(_registry_by_template_id().keys())


@lru_cache
def _registry_by_template_id() -> dict[str, TemplateRegistryEntry]:
    payload = _load_registry_payload()
    rows = payload.get("templates")
    if not isinstance(rows, list) or not rows:
        raise TemplateRegistryError("template_registry.json must contain a non-empty 'templates' list.")

    result: dict[str, TemplateRegistryEntry] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TemplateRegistryError("Invalid registry row: must be a JSON object.")

        template_id = _normalize_template_token(row.get("template_id"))
        template_path = str(row.get("template_path", "")).strip()
        scene_class = str(row.get("scene_class", "")).strip()
        schema_id = _normalize_template_token(row.get("schema_id"))
        render_engine = _normalize_template_token(row.get("render_engine"))
        if not render_engine:
            render_engine = "remotion" if template_id.startswith("remotion.") else "manim"
        if render_engine not in {"manim", "remotion"}:
            raise TemplateRegistryError(
                f"Invalid render_engine '{render_engine}' for template_id '{template_id}'."
            )
        raw_engine_family = str(row.get("engine_family", "")).strip().lower()
        engine_family = raw_engine_family or None
        runtime_raw = row.get("runtime")
        if runtime_raw is None:
            runtime: dict[str, Any] = {}
        elif isinstance(runtime_raw, dict):
            runtime = runtime_raw
        else:
            raise TemplateRegistryError(
                f"Invalid runtime metadata for template_id '{template_id}': expected object."
            )

        if not template_id or not template_path or not scene_class or not schema_id:
            raise TemplateRegistryError(
                "Each template row must define template_id, template_path, scene_class, and schema_id."
            )
        if template_id in result:
            raise TemplateRegistryError(f"Duplicate template_id in registry: {template_id}")

        aliases = []
        raw_aliases = row.get("aliases", [])
        if isinstance(raw_aliases, list):
            for item in raw_aliases:
                normalized_alias = _normalize_template_token(item)
                if normalized_alias and normalized_alias != template_id:
                    aliases.append(normalized_alias)

        result[template_id] = TemplateRegistryEntry(
            template_id=template_id,
            template_path=template_path,
            scene_class=scene_class,
            schema_id=schema_id,
            aliases=tuple(sorted(set(aliases))),
            render_engine=render_engine,
            engine_family=engine_family,
            runtime=runtime,
        )
    return result


# When the same lesson exists for more than one renderer, the short alias has
# to land on exactly one of them. Manim comes first because those templates
# predate the Remotion set and the aliases already meant them; letting Remotion
# win would silently change the engine for every existing caller. Either
# variant is still addressable by its canonical id.
_ALIAS_ENGINE_PRECEDENCE = ("manim", "remotion")


@lru_cache
def _registry_by_alias() -> dict[str, str]:
    alias_map: dict[str, str] = {}
    registry = _registry_by_template_id()
    for template_id, entry in registry.items():
        for alias in entry.aliases:
            held_by = alias_map.get(alias)
            if held_by is None or held_by == template_id:
                alias_map[alias] = template_id
                continue
            incumbent = registry[held_by]
            if incumbent.render_engine == entry.render_engine:
                # Two templates for the same renderer claiming one alias is a
                # genuine data error — there is no principled way to pick.
                raise TemplateRegistryError(
                    f"Alias collision in template registry: {alias} maps to multiple templates."
                )
            alias_map[alias] = min(
                (held_by, template_id),
                key=lambda candidate: _ALIAS_ENGINE_PRECEDENCE.index(
                    registry[candidate].render_engine
                ),
            )
    return alias_map


def _load_registry_payload() -> dict:
    registry_path = Path(__file__).with_name("template_registry.json")
    if not registry_path.exists():
        raise TemplateRegistryError(f"Missing template registry file: {registry_path}")
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateRegistryError(f"Invalid template registry JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateRegistryError("template_registry.json root must be a JSON object.")
    return data


def _normalize_template_token(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()
