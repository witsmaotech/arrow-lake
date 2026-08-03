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
async def test_create_invalid_returns_422(client: AsyncClient) -> None:
    # valid name + structurally invalid YAML (no guideline) → reaches handler
    bad = _YAML.split("guideline:")[0]
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
