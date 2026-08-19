import pytest

from app.modules.learning.template_registry import (
    TemplateRegistryError,
    _registry_by_alias,
    registered_template_ids,
    resolve_template_entry,
)


def test_registry_loads_despite_the_same_lesson_existing_for_two_renderers():
    """The multimodal merge gave 12 lessons both a Manim and a Remotion template.

    Both claimed the same short aliases, and _registry_by_alias raised on the
    first collision — which meant every template lookup failed and no video
    could be requested at all, whatever template was asked for.
    """

    assert len(registered_template_ids()) == 116
    assert _registry_by_alias()


@pytest.mark.parametrize(
    "alias",
    ["bio_evolution_selection", "chem_lab_safety", "sd_energy_forms"],
)
def test_a_shared_alias_keeps_the_engine_it_meant_before_the_merge(alias):
    resolved = resolve_template_entry(alias)

    assert resolved.used_alias is True
    assert resolved.entry.render_engine == "manim"
    assert resolved.entry.template_id == f"manim.{alias}.v1"


def test_the_remotion_variant_is_still_reachable_by_its_canonical_id():
    resolved = resolve_template_entry("remotion.bio_evolution_selection.v1")

    assert resolved.used_alias is False
    assert resolved.entry.render_engine == "remotion"


def test_two_templates_of_one_engine_sharing_an_alias_is_still_an_error(monkeypatch):
    """Cross-engine is resolvable; same-engine has no principled winner."""

    from app.modules.learning import template_registry as registry_module

    entries = dict(registry_module._registry_by_template_id())
    first = entries["manim.bio_evolution_selection.v1"]
    clash = entries["manim.chem_lab_safety.v1"]
    entries["manim.chem_lab_safety.v1"] = type(clash)(
        **{**clash.__dict__, "aliases": first.aliases}
    )
    monkeypatch.setattr(registry_module, "_registry_by_template_id", lambda: entries)
    registry_module._registry_by_alias.cache_clear()

    with pytest.raises(TemplateRegistryError, match="Alias collision"):
        registry_module._registry_by_alias()

    registry_module._registry_by_alias.cache_clear()


def test_an_unregistered_template_still_fails_loudly():
    with pytest.raises(TemplateRegistryError, match="is not registered"):
        resolve_template_entry("does.not.exist.v1")
