"""P0 security fixes (review 2026-08-26) — table-level ACL + ?table= addressing.

P0-5: table-level deny-read (``ds.table`` keys) must be creatable via the
admin ACL/deny routes AND actually fire on read paths. Previously the whole
chain was unreachable: the admin route pattern forbade dots, ``?table=``
did not participate in authorization, and rbac_sql never deny-checked
referenced tables.

P0-7: container tables are addressed via ``?table=`` on the query endpoints
(the ``{name}`` path pattern keeps forbidding dots by design).
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.api.app import create_app
from arrow_lake.api.rbac import DatasetACL, PermissionChecker
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient

SECRET = "test-secret-key-min-32-chars-for-hmac!"


@dataclass(frozen=True)
class _FakeOlapResult:
    table: pa.Table
    row_count: int = 1
    column_count: int = 1
    sql: str = "SELECT 1"


def _mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.olap_query.return_value = _FakeOlapResult(table=pa.table({"c": [1]}))
    lake.sql_query.return_value = _FakeOlapResult(table=pa.table({"c": [1]}))
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
    app.state.auth_service = AuthService(secret_key=SECRET)  # Bearer path
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


def _editor_token() -> str:
    """Mint an editor JWT (non-admin: denies actually apply)."""
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
async def client(
    checker: PermissionChecker, lake: MagicMock,
) -> AsyncClient:
    async for ac in _client(checker, lake):
        yield ac


# ---------------------------------------------------------------------------
# P0-7: ?table= addressing composes the two-part target
# ---------------------------------------------------------------------------


class TestTableQueryParam:
    @pytest.mark.asyncio
    async def test_olap_composes_two_part_target(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        await client.post(
            "/api/v1/datasets/gas/query/olap?table=segments",
            json={"sql": "SELECT COUNT(*) FROM gas.segments"},
        )
        target = lake.olap_query.call_args.args[0]
        assert target == "gas.segments"

    @pytest.mark.asyncio
    async def test_olap_without_table_keeps_plain_name(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        await client.post(
            "/api/v1/datasets/plain/query/olap",
            json={"sql": "SELECT COUNT(*) FROM plain"},
        )
        assert lake.olap_query.call_args.args[0] == "plain"

    @pytest.mark.asyncio
    async def test_metadata_composes_two_part_target(
        self, client: AsyncClient, lake: MagicMock,
    ) -> None:
        await client.post(
            "/api/v1/datasets/gas/query/metadata?table=segments",
            json={"sql": "SELECT COUNT(*) FROM gas.segments"},
        )
        assert lake.sql_query.call_args.args[0] == "gas.segments"

    @pytest.mark.asyncio
    async def test_bad_table_name_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/gas/query/olap?table=bad/name",
            json={"sql": "SELECT 1"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_dotted_path_still_422(self, client: AsyncClient) -> None:
        """The {name} route pattern must keep rejecting dots — relaxing it
        would bypass the container-keyed ACL checks (review P0-7 warning)."""
        resp = await client.post(
            "/api/v1/datasets/gas.segments/query/olap",
            json={"sql": "SELECT 1"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# P0-5: table-level deny-read enforcement
# ---------------------------------------------------------------------------


class TestTableLevelDenyRead:
    @pytest.mark.asyncio
    async def test_admin_acl_route_accepts_two_part_key(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        """Row/column ACL on a table-scoped key (``ds.table``) — the route
        pattern previously 422'd every dotted key (deny actions go through
        the /deny route, covered below)."""
        async for ac in _client(checker, lake):
            resp = await ac.put(
                "/api/v1/admin/acl/gas.segments",
                json={
                    "role": "viewer",
                    "visible_columns": ["material"],
                    "row_filter": "",
                },
            )
            assert resp.status_code == 200, resp.text
            acl = checker.get_acl("gas.segments", "viewer")
            assert acl is not None
            assert "material" in acl.visible_columns

    @pytest.mark.asyncio
    async def test_deny_route_accepts_two_part_key(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        async for ac in _client(checker, lake):
            resp = await ac.put(
                "/api/v1/admin/deny/gas.segments",
                json={"action": "read"},
            )
            assert resp.status_code == 200, resp.text
            assert "read" in checker._get_denies("gas.segments")

    @pytest.mark.asyncio
    async def test_dotted_denied_table_rejected_in_sql(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        """deny-read on ``ds.table`` must fire when the SQL references it."""
        checker.deny_action("gas.segments", "read")
        async for ac in _client(checker, lake):
            token = _editor_token()
            resp = await ac.post(
                "/api/v1/datasets/gas/query/olap?table=segments",
                json={"sql": "SELECT COUNT(*) FROM gas.segments"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code in (403, 422)
            assert "read access" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_table_acl_denied_actions_403(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        """DatasetACL.denied_actions on the dotted key → 403 at the guard."""
        checker.set_acl(DatasetACL(
            dataset="gas.segments", role="editor",
            visible_columns=frozenset(), row_filter="",
            denied_actions=frozenset({"read"}),
        ))
        async for ac in _client(checker, lake):
            token = _editor_token()
            resp = await ac.post(
                "/api/v1/datasets/gas/query/olap?table=segments",
                json={"sql": "SELECT COUNT(*) FROM gas.segments"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_bypasses_table_deny(
        self, checker: PermissionChecker, client: AsyncClient,
    ) -> None:
        """Shared admin API key sails through a table-level deny."""
        checker.deny_action("gas.segments", "read")
        resp = await client.post(
            "/api/v1/datasets/gas/query/olap?table=segments",
            json={"sql": "SELECT COUNT(*) FROM gas.segments"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_sql_referencing_denied_dataset_rejected(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        """P0-6 defense-in-depth: ANY referenced table with deny-read is
        rejected before execution (was: only row/col ACLs were consulted)."""
        checker.deny_action("secret", "read")
        async for ac in _client(checker, lake):
            token = _editor_token()
            resp = await ac.post(
                "/api/v1/datasets/publicds/query/olap",
                json={"sql": "SELECT COUNT(*) FROM secret"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code in (403, 422)

    @pytest.mark.asyncio
    async def test_undeny_allows_again(
        self, checker: PermissionChecker, lake: MagicMock,
    ) -> None:
        checker.deny_action("gas.segments", "read")
        checker.remove_deny("gas.segments", "read")
        async for ac in _client(checker, lake):
            token = _editor_token()
            resp = await ac.post(
                "/api/v1/datasets/gas/query/olap?table=segments",
                json={"sql": "SELECT COUNT(*) FROM gas.segments"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
