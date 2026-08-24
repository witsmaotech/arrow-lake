"""MS1 前置批测试(v1.10.7 post-release review B-1~B-4 + M-7)。

B-1: 同步 ingest 端点 run_sync 走 ingest_executor(不再与 auth IO 共享默认池)
B-2: _get_row_col_acl 控制面故障 fail-closed(AclStoreUnavailable→503)+
     tva provider 故障默认 fail-closed(显式开关恢复 fail-open)
B-3: 死信表移入 internal 命名空间(_{ds}_dead_letter)+ ADMIN-only + 旧命名同守卫
B-4: ingest/index/write 端点加 deny(write) 守卫(authorize_dataset write=True)
M-7: verify_token 超时→503(非 401);登录 identity store 超时→503(非裸 500)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from arrow_lake.api.auth_models import Role


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _Checker:
    """PermissionChecker stand-in: allows everything except listed actions."""

    def __init__(self, *, deny_actions: set[str] | None = None) -> None:
        self._deny = deny_actions or set()
        self.calls: list[tuple[str, str, str]] = []

    def check_dataset_access(self, *, role, dataset, action) -> bool:  # noqa: ANN001
        self.calls.append((str(role), dataset, action))
        return action not in self._deny


def _make_request(*, role: Role = Role.EDITOR, checker: _Checker | None = None) -> MagicMock:
    """Request with state.user / app.state.checker wired for authorize_dataset."""
    request = MagicMock()
    request.state.user = SimpleNamespace(role=role, sub="1", user_id=1)
    request.app.state.checker = checker or _Checker()
    return request


# ---------------------------------------------------------------------------
# B-1: sync ingest endpoints dispatch to ingest_executor
# ---------------------------------------------------------------------------


class TestSyncIngestExecutor:
    @pytest.fixture
    def executor_spy(self, monkeypatch):
        """Wrap datasets.run_sync to capture the executor kwarg."""
        from arrow_lake.api.routers import datasets as ds_mod
        from arrow_lake.api.utils import ingest_executor

        captured: dict[str, object] = {}
        real = ds_mod.run_sync

        async def spy(func, *args, **kwargs):
            if kwargs.get("label", "").startswith("ingest"):
                captured[kwargs["label"]] = kwargs.get("executor")
            return await real(func, *args, **kwargs)

        monkeypatch.setattr(ds_mod, "run_sync", spy)
        return captured, ingest_executor

    async def test_ingest_files_uses_ingest_executor(self, executor_spy, monkeypatch) -> None:
        captured, ingest_executor = executor_spy
        from arrow_lake.api.models.dataset import IngestFilesRequest
        from arrow_lake.api.routers import datasets as ds_mod

        monkeypatch.setattr(ds_mod.IngestResponse, "from_report", classmethod(lambda cls, r: MagicMock()))
        from arrow_lake.api.routers.datasets import ingest_files

        lake = MagicMock()
        request = _make_request()
        await ingest_files(
            request, "ds",
            req=IngestFilesRequest(file_paths=["/tmp/f.parquet"]),
            lake=lake,
            _user=MagicMock(user_id=1),
        )
        assert captured.get("ingest_files") is ingest_executor

    async def test_ingest_documents_uses_ingest_executor(self, executor_spy, monkeypatch) -> None:
        captured, ingest_executor = executor_spy
        from arrow_lake.api.models.dataset import IngestDocumentsRequest
        from arrow_lake.api.routers import datasets as ds_mod

        monkeypatch.setattr(ds_mod.IngestResponse, "from_report", classmethod(lambda cls, r: MagicMock()))
        from arrow_lake.api.routers.datasets import ingest_documents

        lake = MagicMock()
        request = _make_request()
        # B-3 死信守卫不拦普通数据集名;B-4 守卫对 allow checker 放行
        await ingest_documents(
            request, "ds",
            req=IngestDocumentsRequest(pdf_paths=["f.pdf"]),
            lake=lake,
            _user=MagicMock(user_id=1),
        )
        assert captured.get("ingest_documents") is ingest_executor


# ---------------------------------------------------------------------------
# B-4: deny(write) guards on ingest/write endpoints
# ---------------------------------------------------------------------------


class TestWriteDenyGuard:
    async def test_sync_ingest_denied_write_403(self) -> None:
        from arrow_lake.api.models.dataset import IngestFilesRequest
        from arrow_lake.api.routers.datasets import ingest_files

        request = _make_request(checker=_Checker(deny_actions={"write"}))
        with pytest.raises(HTTPException) as ei:
            await ingest_files(
                request, "locked_ds",
                req=IngestFilesRequest(file_paths=["/tmp/f.parquet"]),
                lake=MagicMock(),
                _user=MagicMock(user_id=1),
            )
        assert ei.value.status_code == 403

    async def test_delete_dataset_denied_write_403(self) -> None:
        from arrow_lake.api.routers.datasets import delete_dataset

        request = _make_request(checker=_Checker(deny_actions={"write"}))
        with pytest.raises(HTTPException) as ei:
            await delete_dataset(request, "locked_ds", True, lake=MagicMock(), _user=MagicMock())
        assert ei.value.status_code == 403

    async def test_async_ingest_denied_write_403(self) -> None:
        from arrow_lake.api.routers.async_tasks import AsyncIngestRequest, ingest_files_async

        request = _make_request(checker=_Checker(deny_actions={"write"}))
        with pytest.raises(HTTPException) as ei:
            await ingest_files_async(
                "locked_ds",
                req=AsyncIngestRequest(file_paths=["/tmp/f.parquet"]),
                request=request,
                lake=MagicMock(),
                _user=SimpleNamespace(user_id=1, role=Role.EDITOR),
            )
        assert ei.value.status_code == 403

    async def test_async_vector_index_denied_write_403(self) -> None:
        from arrow_lake.api.routers.async_tasks import (
            AsyncVectorIndexRequest,
            create_vector_index_async,
        )

        request = _make_request(checker=_Checker(deny_actions={"write"}))
        with pytest.raises(HTTPException) as ei:
            await create_vector_index_async(
                request, "locked_ds",
                req=AsyncVectorIndexRequest(),
                lake=MagicMock(),
                _user=SimpleNamespace(user_id=1, role=Role.EDITOR),
            )
        assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# B-3: dead-letter table namespace + ADMIN-only guard
# ---------------------------------------------------------------------------


class TestDeadLetterNamespace:
    def test_writer_uses_internal_prefix(self) -> None:
        import pyarrow as pa

        from arrow_lake.quality.dead_letter import DeadLetterWriter

        storage = MagicMock()
        storage.write.return_value = 2
        writer = DeadLetterWriter(storage)
        n = writer.write("orders", pa.table({"a": [1, 2]}), "f1")
        assert n == 2
        written_name = storage.write.call_args[0][0]
        assert written_name == "_orders_dead_letter"

    def test_is_internal_table_recognizes_both_spellings(self) -> None:
        from arrow_lake._system_tables import is_internal_table

        assert is_internal_table("_orders_dead_letter")  # new
        assert is_internal_table("orders_dead_letter")  # legacy pre-rename
        assert not is_internal_table("orders")
        assert not is_internal_table("dead_letter_like")  # 后缀必须精确

    async def test_dead_letter_table_admin_only(self) -> None:
        from arrow_lake.api.deps import authorize_dataset

        # 非 ADMIN 一律 403(新旧命名同守卫),checker 不再被咨询
        for name in ("_orders_dead_letter", "orders_dead_letter"):
            request = _make_request(role=Role.VIEWER, checker=_Checker())
            with pytest.raises(HTTPException) as ei:
                authorize_dataset(request, name)
            assert ei.value.status_code == 403

    async def test_dead_letter_table_admin_allowed_without_acl_consult(self) -> None:
        from arrow_lake.api.deps import authorize_dataset

        checker = _Checker()
        request = _make_request(role=Role.ADMIN, checker=checker)
        authorize_dataset(request, "_orders_dead_letter")
        assert checker.calls == []  # admin bypass,无 ACL 查询


# ---------------------------------------------------------------------------
# B-2: row/col ACL control-plane failure is fail-closed; tva explicit switch
# ---------------------------------------------------------------------------


class TestRowColAclFailClosed:
    def test_store_failure_raises_unavailable(self) -> None:
        from arrow_lake.api.rbac import AclStoreUnavailable, PermissionChecker

        store = MagicMock()
        store.get_row_col_acl.side_effect = RuntimeError("libSQL down")
        checker = PermissionChecker()
        checker.set_system_store(store)
        with pytest.raises(AclStoreUnavailable):
            checker.get_acl("orders", "editor")

    def test_store_healthy_none_acl_still_none(self) -> None:
        from arrow_lake.api.rbac import PermissionChecker

        store = MagicMock()
        store.get_row_col_acl.return_value = None
        checker = PermissionChecker()
        checker.set_system_store(store)
        assert checker.get_acl("orders", "editor") is None


class TestTvaFailClosed:
    @staticmethod
    def _svc_with_failing_provider(*, fail_open: bool):
        from arrow_lake.api.auth_service import AuthService

        svc = AuthService(secret_key="test-secret-key-min-32-chars-for-hmac!")
        token = svc.create_refresh_token(user_id="42", role=Role.VIEWER)

        def _boom(sub: str):
            raise RuntimeError("identity store down")

        svc.set_token_valid_after_provider(_boom, fail_open=fail_open)
        return svc, token

    def test_default_fail_closed_rejects_token(self) -> None:
        svc, token = self._svc_with_failing_provider(fail_open=False)
        with pytest.raises(ValueError, match="unreachable"):
            svc.verify_token(token)

    def test_explicit_fail_open_skips_check(self) -> None:
        svc, token = self._svc_with_failing_provider(fail_open=True)
        payload = svc.verify_token(token)  # provider 异常被跳过
        assert payload.sub == "42"

    def test_config_default_is_fail_closed(self) -> None:
        from arrow_lake.config.api import AuthConfig

        assert AuthConfig().auth_tva_fail_open is False


# ---------------------------------------------------------------------------
# M-7: auth timeouts surface as 503
# ---------------------------------------------------------------------------


class TestAuthTimeout503:
    async def test_jwt_middleware_timeout_503(self) -> None:
        from arrow_lake.api.jwt_auth import jwt_auth_middleware_fn

        auth_service = MagicMock()
        auth_service.verify_token.side_effect = TimeoutError("redis EXISTS timed out")

        async def call_next(request):  # noqa: ANN001, ANN202
            return MagicMock()

        resp = await jwt_auth_middleware_fn(
            _make_request_role_bearer(), call_next, auth_service
        )
        assert resp.status_code == 503
        assert b"AUTH_STORE_UNAVAILABLE" in resp.body

    async def test_login_identity_timeout_503(self, monkeypatch) -> None:
        from arrow_lake.api.routers import auth as auth_mod

        async def hang(*args, **kwargs):
            raise TimeoutError("libSQL timeout")

        monkeypatch.setattr(auth_mod, "run_sync", hang)
        request = _make_request()
        request.app.state.identity_store = MagicMock()
        request.app.state.redis_rate_limiter = None  # 走 in-memory lockout,别让 mock rl 进 run_sync
        request.headers = {"x-forwarded-for": "127.0.0.1"}

        class _Creds:
            username = "u"
            password = "p"

        with pytest.raises(HTTPException) as ei:
            await auth_mod.login_with_password(request, _Creds())
        assert ei.value.status_code == 503


def _make_request_role_bearer() -> MagicMock:
    """Request carrying a Bearer header for the JWT middleware path."""
    request = MagicMock()
    request.headers = {"Authorization": "Bearer faketoken"}
    request.url.path = "/api/v1/datasets"
    return request
