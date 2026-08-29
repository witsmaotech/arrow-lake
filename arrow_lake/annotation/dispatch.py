"""F4.1/F4.4 — Label Studio REST client(v1.11.3 MS4 W1.3)。

旁路模块。PAT 认证(D1):``POST /api/token/refresh`` 把 personal access
token 换成 ~5min 短时 access token,业务请求带 ``Authorization: Bearer``;
遇 401 丢缓存重换一次(仅一次,防循环)。HTTP 失败统一 ``LSClientError``
携带 status + body 摘要(排障面,不吞)。

零新依赖(version-plan 红线):urllib.request;``opener`` 可注入供测试
mock(FakeOpener 模式,与 tests/unit/annotation/test_dispatch_client.py 对应)。
LS 是 transient 工作区——本 client 不持有任何业务状态,重启即重建。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

__all__ = ["LSClient", "LSClientError"]

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_BODY_SNIPPET = 400


class LSClientError(RuntimeError):
    """LS REST 调用失败(网络/HTTP/解码);message 带 status 与 body 摘要。"""


class LSClient:
    """Label Studio REST API 薄封装(project CRUD + task import)。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = _TIMEOUT_SECONDS,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self._access: str | None = None

    # --- low-level ---------------------------------------------------------

    def _open(self, method: str, path: str, body: Any, auth: str | None) -> Any:
        url = f"{self._base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", auth)
        try:
            return self._opener(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:_BODY_SNIPPET].decode("utf-8", "replace")
            raise LSClientError(f"LS {method} {path} → {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LSClientError(f"LS {method} {path} unreachable: {exc.reason}") from exc

    @staticmethod
    def _read_json(resp: Any) -> Any:
        raw = resp.read()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LSClientError(f"LS returned non-JSON body: {raw[:_BODY_SNIPPET]!r}") from exc

    def _refresh_access(self) -> str:
        resp = self._open("POST", "/api/token/refresh", {"refresh": self._token}, auth=None)
        payload = self._read_json(resp)
        access = (payload or {}).get("access")
        if not access:
            raise LSClientError("LS token refresh returned no access token")
        self._access = str(access)
        return self._access

    def _api(self, method: str, path: str, body: Any = None) -> Any:
        """带 PAT 认证的 API 调用;401 时重换 token 重试一次。"""
        if not self._access:
            self._refresh_access()
        resp = self._open(method, path, body, f"Bearer {self._access}")
        if getattr(resp, "status", 200) == 401:
            logger.debug("LS 401 on %s %s — refreshing access token", method, path)
            self._access = None
            self._refresh_access()
            resp = self._open(method, path, body, f"Bearer {self._access}")
        return self._read_json(resp)

    # --- projects ----------------------------------------------------------

    def create_project(self, title: str, labeling_config: str) -> dict[str, Any]:
        """POST /api/projects LS 1.13 returns 201 with the project record."""
        out = self._api(
            "POST", "/api/projects", {"title": title, "label_config": labeling_config}
        )
        return out if isinstance(out, dict) else {}

    def get_project(self, project_id: int) -> dict[str, Any]:
        out = self._api("GET", f"/api/projects/{project_id}")
        return out if isinstance(out, dict) else {}

    def list_projects(self) -> list[dict[str, Any]]:
        out = self._api("GET", "/api/projects.json?page_size=100")
        if isinstance(out, dict) and isinstance(out.get("results"), list):
            return out["results"]
        return out if isinstance(out, list) else []

    # --- tasks -------------------------------------------------------------

    def import_tasks(self, project_id: int, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /api/projects/{id}/import — tasks 可内嵌静态 predictions(S4)。"""
        out = self._api("POST", f"/api/projects/{project_id}/import", tasks)
        return out if isinstance(out, dict) else {}

    def list_tasks(self, project_id: int, *, page: int = 1, page_size: int = 100) -> Any:
        return self._api(
            "GET", f"/api/tasks?project={project_id}&page={page}&page_size={page_size}"
        )
