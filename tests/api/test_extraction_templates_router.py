"""v1.10.0 P6: extraction-templates admin router (CRUD + validate)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.knowledge_graph.doc_type_router import reset_gallery_cache

_YAML = (
    "language: [zh, en]\n"
    "name: security_concept_graph\n"
    "type: graph\n"
    "category: general\n"  # M5: category required + ∈ doc_type dictionary
    "tags: [security]\n"
    "description: {zh: s, en: s}\n"
    "output:\n"
    "  entities:\n"
    "    fields:\n"
    "      - {name: name, type: str, description: d}\n"
    "      - {name: type, type: str, description: d}\n"
    "  relations:\n"
    "    fields:\n"
    "      - {name: source, type: str, description: d}\n"
    "      - {name: target, type: str, description: d}\n"
    "      - {name: type, type: str, description: d}\n"
    "guideline:\n"
    "  target: {zh: e, en: e}\n"
)


@pytest.fixture
async def client(tmp_path, monkeypatch) -> AsyncClient:
    user_dir = tmp_path / "templates"
    user_dir.mkdir()
    monkeypatch.setenv("ARROW_LAKE__HUGEGRAPH__HE_USER_TEMPLATES_DIR", str(user_dir))
    reset_gallery_cache()
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"
    config.api.docs_enabled = False
    app = create_app(config=config)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac
    reset_gallery_cache()


@pytest.mark.asyncio
async def test_create_list_get_delete(client: AsyncClient) -> None:
    # create
    resp = await client.post("/api/v1/admin/extraction-templates", json={
        "name": "security_concept_graph", "yaml": _YAML})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["source"] == "user"

    # list contains it
    resp = await client.get("/api/v1/admin/extraction-templates")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()["data"]]
    assert "security_concept_graph" in names

    # detail returns yaml
    resp = await client.get("/api/v1/admin/extraction-templates/security_concept_graph")
    assert resp.status_code == 200
    assert resp.json()["data"]["yaml"] is not None

    # delete
    resp = await client.delete("/api/v1/admin/extraction-templates/security_concept_graph")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True

    # gone
    resp = await client.get("/api/v1/admin/extraction-templates/security_concept_graph")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_validate_ok(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/admin/extraction-templates/validate", json={"yaml": _YAML})
    assert resp.status_code == 200
    assert resp.json()["data"]["valid"] is True


@pytest.mark.asyncio
async def test_validate_bad_returns_errors(client: AsyncClient) -> None:
    bad = _YAML.replace("name: security_concept_graph", "name: Bad-Name")
    resp = await client.post("/api/v1/admin/extraction-templates/validate", json={"yaml": bad})
    assert resp.status_code == 200
    assert resp.json()["data"]["valid"] is False


@pytest.mark.asyncio
async def test_create_hard_error_returns_422(client: AsyncClient) -> None:
    # a HARD error (invalid name — name == filename safety) blocks save → 422.
    # (Soft/quality errors like a missing guideline no longer block: they save
    # as a draft and are surfaced via the /validate endpoint.)
    bad = _YAML.replace("name: security_concept_graph", "name: Bad-Name")
    resp = await client.post("/api/v1/admin/extraction-templates", json={
        "name": "security_concept_graph", "yaml": bad})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "TEMPLATE_INVALID"


@pytest.mark.asyncio
async def test_update_then_reflects(client: AsyncClient) -> None:
    await client.post("/api/v1/admin/extraction-templates", json={
        "name": "security_concept_graph", "yaml": _YAML})
    updated = _YAML.replace("target: {zh: e, en: e}", "target: {zh: expert2, en: expert2}")
    resp = await client.put("/api/v1/admin/extraction-templates/security_concept_graph",
                            json={"yaml": updated})
    assert resp.status_code == 200
    resp = await client.get("/api/v1/admin/extraction-templates/security_concept_graph")
    assert "expert2" in resp.json()["data"]["yaml"]


@pytest.mark.asyncio
async def test_system_template_read_only(client: AsyncClient) -> None:
    # entity_graph is a project/system template → PUT/DELETE 403
    resp = await client.put("/api/v1/admin/extraction-templates/entity_graph",
                            json={"yaml": _YAML})
    # entity_graph may or may not be indexed depending on install; if present, must 403
    if resp.status_code != 404:
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_with_doc_type_injects_category(client: AsyncClient) -> None:
    # M5: a YAML without `category` + a `doc_type` param → the router injects
    # `category: <doc_type>` into the saved YAML (Layer-2 routing keys on it).
    yaml_no_cat = _YAML.replace("category: general\n", "")
    resp = await client.post("/api/v1/admin/extraction-templates", json={
        "name": "security_concept_graph", "yaml": yaml_no_cat, "doc_type": "finance"})
    assert resp.status_code == 201, resp.text
    resp = await client.get("/api/v1/admin/extraction-templates/security_concept_graph")
    assert "category: finance" in resp.json()["data"]["yaml"]


@pytest.mark.asyncio
async def test_doc_type_categories_static_fallback(client: AsyncClient) -> None:
    # M5: with system_db disabled (the test fixture), GET /admin/doc-type-categories
    # degrades to the static code-level taxonomy (the 11 canonical doc_types).
    resp = await client.get("/api/v1/admin/doc-type-categories")
    assert resp.status_code == 200
    names = {c["name"] for c in resp.json()["data"]}
    assert {"finance", "legal", "general"} <= names


def test_inject_category_only_matches_top_level_key() -> None:
    # review HIGH: a NESTED `category:` (e.g. under guideline) must NOT be
    # rewritten; only a column-0 top-level category is replaced/inserted.
    from arrow_lake.api.routers.extraction_templates import _inject_category
    yaml_nested = (
        "language: [zh]\nname: foo\n"
        "guideline:\n  category: internal_note\n  target: {zh: x}\n")
    out = _inject_category(yaml_nested, "finance")
    assert "category: finance" in out          # top-level inserted
    assert "category: internal_note" in out      # nested key preserved
    # idempotent + replace-branch anchored to column 0
    out2 = _inject_category("name: foo\ncategory: old\n", "finance")
    assert out2.count("category:") == 1
    assert "category: finance" in out2
    assert "category: old" not in out2


def test_inject_category_rejects_yaml_special_chars() -> None:
    # review defense-in-depth: a doc_type with YAML-significant chars is
    # rejected before splicing (no key/value injection into the YAML).
    from arrow_lake.api.routers.extraction_templates import _inject_category
    from arrow_lake.knowledge_graph.template_registry import TemplateValidationError
    with pytest.raises(TemplateValidationError):
        _inject_category("name: foo\n", "finance\nbogus: true")
    with pytest.raises(TemplateValidationError):
        _inject_category("name: foo\n", "has space")
