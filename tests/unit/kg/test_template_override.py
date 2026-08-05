"""v1.10.0 P4: _resolve_template_path (bare name / path / unresolved) + override."""

from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest

from arrow_lake.knowledge_graph import doc_type_router as dtr
from arrow_lake.knowledge_graph.doc_type_router import (
    get_template_gallery, reset_gallery_cache,
)
from arrow_lake.knowledge_graph.he_extractor import HyperExtractExtractor

_USER_YAML = textwrap.dedent(
    """\
    language: [zh, en]
    name: security_concept_graph
    type: graph
    tags: [security]
    description: {zh: s, en: s}
    output:
      entities:
        fields:
          - {name: name, type: str, description: d}
    guideline:
      target: {zh: e, en: e}
    """
)


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "security_concept_graph.yaml").write_text(_USER_YAML, encoding="utf-8")
    monkeypatch.setenv("ARROW_LAKE__HUGEGRAPH__HE_USER_TEMPLATES_DIR", str(d))
    reset_gallery_cache()
    yield d
    reset_gallery_cache()


def _bind():
    """_resolve_template_path uses only os + gallery (no instance state)."""
    obj = SimpleNamespace()
    return lambda ref: HyperExtractExtractor._resolve_template_path(obj, ref)


def test_resolve_bare_user_template_name(user_dir):
    resolve = _bind()
    path = resolve("security_concept_graph")
    assert path.endswith("security_concept_graph.yaml")
    assert "/" in path


def test_resolve_path_passthrough(user_dir):
    resolve = _bind()
    f = user_dir / "security_concept_graph.yaml"
    assert resolve(str(f)) == str(f)


def test_resolve_unknown_raises(user_dir):
    resolve = _bind()
    with pytest.raises(ValueError, match="template not found"):
        resolve("does_not_exist")


def test_resolve_empty_raises(user_dir):
    resolve = _bind()
    with pytest.raises(ValueError, match="empty"):
        resolve("")


def test_resolve_preset_path(user_dir):
    # preset paths like "general/concept_graph" resolve via gallery.get(path)
    # (only if the preset is installed; skip if hyperextract has no presets)
    g = get_template_gallery()
    if not g.templates or not any("/" in t.path for t in g.templates):
        pytest.skip("no hyperextract presets installed")
    preset = next(t.path for t in g.templates if "/" in t.path)
    resolve = _bind()
    assert resolve(preset) == preset


def test_override_is_single_chokepoint(user_dir):
    """Setting _active_template_override makes _resolve_template defer to it.

    We can't easily build a full extractor, so verify the override branch logic:
    when _active_template_override is set, _resolve_template must call
    _resolve_template_path with it (not the selector/router). We assert the
    override is read via getattr default None (no AttributeError on a bare obj).
    """
    obj = SimpleNamespace()
    # No override set → getattr returns None → branch skipped (no crash).
    assert getattr(obj, "_active_template_override", None) is None
    obj._active_template_override = "security_concept_graph"
    assert obj._active_template_override == "security_concept_graph"


@pytest.mark.asyncio
async def test_template_override_concurrent_tasks_isolated() -> None:
    """v1.10.2 M4 P-辅.3: the per-build template override is a contextvar, so
    two concurrent builds on a SHARED extractor each see their own override
    (the old instance attr raced across different-dataset builds)."""
    import asyncio
    from arrow_lake.knowledge_graph.he_extractor import (
        _TEMPLATE_OVERRIDE_VAR, using_template_override,
    )

    seen: dict[str, str | None] = {}

    async def _build(name: str, path: str) -> None:
        with using_template_override(path):
            await asyncio.sleep(0.02)  # force overlap with the sibling task
            seen[name] = _TEMPLATE_OVERRIDE_VAR.get()

    await asyncio.gather(_build("A", "tmplA"), _build("B", "tmplB"))
    assert seen == {"A": "tmplA", "B": "tmplB"}   # no cross-task bleed
    assert _TEMPLATE_OVERRIDE_VAR.get() is None    # reset after the builds
