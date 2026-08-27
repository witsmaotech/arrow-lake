"""Container-table support for the tidy/clean & data-prep pages (DR14 follow-up).

The cleaning + quality endpoints accept ``?table=`` so the two prep pages can
operate on one table inside a multi-table container — the same addressing the
query endpoints got in P0-7. Storage already understood ``table=``; this batch
threads it through the Lake facade and the routers, and layers the table-level
deny override (P0-5) on the manual-call auth path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.api.app import create_app
from arrow_lake.api.rbac import PermissionChecker
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient

SECRET = "test-secret-key-min-32-chars-for-hmac!"

_TABLE = pa.table({"seg_id": ["a", "b"], "pressure": [1.0, 2.0]})


@dataclass
class _FakeDedupResult:
    """Duck-typed DedupResult: dataclass path exercises asdict + .table read."""

    total_rows: int = 2
    unique_rows: int = 2
    duplicates_found: int = 0
    strategy: str = "exact"
    action: str = "flag"
    table: pa.Table = field(default_factory=lambda: _TABLE)


@dataclass
class _FakeQualityReport:
    """Duck-typed QualityReport (filter endpoint asdicts it; report uses to_json)."""

    passed_count: int = 2
    rejected_count: int = 0
    filter_results: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {"passed": self.passed_count, "rejected": self.rejected_count}


def _mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.read_dataset.return_value = _TABLE
    lake.restore_dataset.return_value = None
    lake.quality_filter.return_value = _FakeQualityReport()
    lake.deduplicate.return_value = _FakeDedupResult()
    return lake


async def _client(checker: PermissionChecker, lake: MagicMock) -> AsyncClient:
    from arrow_lake.api.auth_service import AuthService

    config = ArrowLakeConfig()
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"  # shared key = admin (deny bypass)
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = lake
    app.state.checker = checker
    app.state.auth_service = AuthService(secret_key=SECRET)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


def _editor_token() -> str:
    from arrow_lake.api.auth_models import Role
    from arrow_lake.api.auth_service import AuthService

    svc = AuthService(secret_key=SECRET)
    payload = svc.create_access_token("42", role=Role.EDITOR)
    return svc._encode(payload)


@pytest.fixture
def checker() -> PermissionChecker:
    return PermissionChecker()


@pytest.fixture
def lake() -> MagicMock:
    return _mock_lake()


@pytest.fixture
async def client(checker: PermissionChecker, lake: MagicMock) -> AsyncClient:
    async for ac in _client(checker, lake):
        yield ac


# ---------------------------------------------------------------------------
# ?table= threads through to the facade
# ---------------------------------------------------------------------------


class TestTableThreading:
    @pytest.mark.asyncio
    async def test_clean_reads_and_writes_the_addressed_table(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        resp = await client.post(
            "/api/v1/datasets/gas/clean?table=segments",
            json={
                "steps": [{"type": "cast", "column": "pressure",
                           "params": {"dtype": "double"}}],
                "filters": [], "write_back": True,
            },
        )
        assert resp.status_code == 200, resp.text
        assert lake.read_dataset.call_args.kwargs.get("table") == "segments"
        assert lake.restore_dataset.call_args.kwargs.get("table") == "segments"

    @pytest.mark.asyncio
    async def test_clean_without_table_stays_bare(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        resp = await client.post(
            "/api/v1/datasets/plain/clean",
            json={"steps": [], "filters": [], "write_back": False},
        )
        assert resp.status_code == 200, resp.text
        assert lake.read_dataset.call_args.kwargs.get("table") is None
        lake.restore_dataset.assert_not_called()

    @pytest.mark.asyncio
    async def test_quality_profile_addresses_table(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        resp = await client.get(
            "/api/v1/datasets/gas/quality/profile?table=segments")
        assert resp.status_code == 200, resp.text
        assert lake.read_dataset.call_args.kwargs.get("table") == "segments"

    @pytest.mark.asyncio
    async def test_quality_report_addresses_table(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        resp = await client.get(
            "/api/v1/datasets/gas/quality/report?table=segments")
        assert resp.status_code == 200, resp.text
        assert lake.quality_filter.call_args.kwargs.get("table") == "segments"

    @pytest.mark.asyncio
    async def test_quality_filter_addresses_table(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        resp = await client.post(
            "/api/v1/datasets/gas/quality/filter?table=segments",
            json={"active_filters": "", "mode": "all"},
        )
        assert resp.status_code == 200, resp.text
        assert lake.quality_filter.call_args.kwargs.get("table") == "segments"

    @pytest.mark.asyncio
    async def test_quality_rules_addresses_table(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        resp = await client.post(
            "/api/v1/datasets/gas/quality/rules?table=segments",
            json={"rules": [{"name": "r", "column": "seg_id",
                             "check": "length", "params": {"min": 1},
                             "action": "flag"}]},
        )
        assert resp.status_code == 200, resp.text
        assert lake.read_dataset.call_args.kwargs.get("table") == "segments"

    @pytest.mark.asyncio
    async def test_deduplicate_addresses_table(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        resp = await client.post(
            "/api/v1/datasets/gas/quality/deduplicate?table=segments",
            json={"strategy": "exact", "action": "flag"},
        )
        assert resp.status_code == 200, resp.text
        assert lake.deduplicate.call_args.kwargs.get("table") == "segments"

    @pytest.mark.asyncio
    async def test_llm_ops_accept_table_param(
        self, client: AsyncClient,
    ) -> None:
        """llm_label/extract take ?table= (async 202; deep threading is
        unit-tested on the enrich functions below)."""
        resp = await client.post(
            "/api/v1/datasets/gas/quality/llm_label?table=segments",
            json={"column": "seg_id", "new_column": "tag",
                  "prompt_template": "{text}"},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["task_id"]

    @pytest.mark.asyncio
    async def test_bad_table_name_422(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/datasets/gas/quality/profile?table=bad/name")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_migrate_addresses_table(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        """schema/migrate (data-prep 文本规整算子) hits the container table."""
        from types import SimpleNamespace

        lake.catalog.return_value = SimpleNamespace(
            datasets=[SimpleNamespace(name="gas")])
        lake._storage.open_dataset.return_value = MagicMock(
            schema=pa.schema([("material", pa.string())]))
        resp = await client.post(
            "/api/v1/datasets/gas/schema/migrate?table=segments",
            json={"actions": [{
                "operation": "add_column", "column_name": "mat2",
                "sql_expr": '"material"',
            }], "dry_run": False},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied_count"] == 1
        assert lake.add_column.call_args.kwargs.get("table") == "segments"
        assert lake._storage.open_dataset.call_args.kwargs.get("table") == "segments"


# ---------------------------------------------------------------------------
# Table-level deny override on the manual-call auth path
# ---------------------------------------------------------------------------


class TestTableDenyAuth:
    @pytest.mark.asyncio
    async def test_deny_read_blocks_profile(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        checker.deny_action("gas.segments", "read")
        async for ac in _client(checker, lake):
            resp = await ac.get(
                "/api/v1/datasets/gas/quality/profile?table=segments",
                headers={"Authorization": f"Bearer {_editor_token()}"},
            )
            assert resp.status_code == 403
            assert "read" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_deny_read_blocks_clean_write(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        """A write op implies reading — deny-read on the table blocks it too."""
        checker.deny_action("gas.segments", "read")
        async for ac in _client(checker, lake):
            resp = await ac.post(
                "/api/v1/datasets/gas/clean?table=segments",
                json={"steps": [], "filters": [], "write_back": True},
                headers={"Authorization": f"Bearer {_editor_token()}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_deny_write_blocks_clean_but_read_ops_pass(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        checker.deny_action("gas.segments", "write")
        async for ac in _client(checker, lake):
            token = {"Authorization": f"Bearer {_editor_token()}"}
            prof = await ac.get(
                "/api/v1/datasets/gas/quality/profile?table=segments",
                headers=token,
            )
            assert prof.status_code == 200
            clean = await ac.post(
                "/api/v1/datasets/gas/clean?table=segments",
                json={"steps": [], "filters": [], "write_back": True},
                headers=token,
            )
            assert clean.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_bypasses_table_deny(
        self, checker: PermissionChecker, client: AsyncClient,
    ) -> None:
        checker.deny_action("gas.segments", "read")
        resp = await client.get(
            "/api/v1/datasets/gas/quality/profile?table=segments")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# llm_enrich threading (pure logic, mock lake)
# ---------------------------------------------------------------------------


class TestLlmEnrichTableThreading:
    @pytest.mark.asyncio
    async def test_label_column_reads_and_adds_columns_on_table(self) -> None:
        from arrow_lake.quality.llm_enrich import label_column

        lake = MagicMock()
        lake.read_dataset.return_value = pa.table(
            {"text_content": ["a", "b"]})

        class _Prov:
            async def generate(self, msgs: Any) -> Any:
                return MagicMock(content="x")

        out = await label_column(
            lake, "gas", "text_content", "tag", "{text}",
            provider=_Prov(), table="segments",
        )
        assert out["new_columns"] == ["tag"]
        assert lake.read_dataset.call_args.kwargs.get("table") == "segments"
        assert lake.add_columns_table.call_args.kwargs.get("table") == "segments"
