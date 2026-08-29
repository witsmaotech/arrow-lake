"""W1.3 — annotation/dispatch.LSClient:Label Studio REST client(urllib,零新依赖)。

契约(version-plan W1.3 / D1):
* PAT 认证(D1):``POST /api/token/refresh`` 换短时 access token,
  ``Authorization: Bearer``;401 自动重换一次;
* project CRUD(create/list/get)+ tasks import(带 predictions 内嵌);
* 全部经可注入 opener(mock);HTTPError → LSClientError 带 status/body。
"""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.parse import urlparse

import pytest
from arrow_lake.annotation.dispatch import LSClient, LSClientError


def _path_of(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


class FakeOpener:
    """按 (method, path) 队列返回响应;记录每次请求供断言。"""

    def __init__(self, script: list[tuple[str, str, int, Any]]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def __call__(self, req: Any, timeout: float = 0) -> Any:
        method = req.get_method()
        path = _path_of(req.full_url)
        body = req.data.decode("utf-8") if req.data else None
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append({"method": method, "path": path, "body": body, "headers": headers})
        if not self.script:
            raise AssertionError(f"unexpected request: {method} {path}")
        want_method, want_path, status, payload = self.script.pop(0)
        assert method == want_method, f"{method} != {want_method}"
        assert path == want_path, f"{path} != {want_path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else b"{}"
        resp = io.BytesIO(data)
        resp.status = status
        return resp


def _client(opener: FakeOpener) -> LSClient:
    return LSClient("http://ls:8080", "pat-token", opener=opener)


class TestAuth:
    def test_first_call_refreshes_pat_then_bearer(self):
        opener = FakeOpener([
            ("POST", "/api/token/refresh", 200, {"access": "acc-1"}),
            ("GET", "/api/projects.json?page_size=100", 200, [{"id": 7}]),
        ])
        client = _client(opener)
        assert client.list_projects() == [{"id": 7}]
        refresh, call = opener.calls
        assert json.loads(refresh["body"]) == {"refresh": "pat-token"}
        assert call["headers"]["authorization"] == "Bearer acc-1"

    def test_access_token_cached_across_calls(self):
        opener = FakeOpener([
            ("POST", "/api/token/refresh", 200, {"access": "acc-1"}),
            ("GET", "/api/projects.json?page_size=100", 200, []),
            ("GET", "/api/projects.json?page_size=100", 200, []),
        ])
        client = _client(opener)
        client.list_projects()
        client.list_projects()
        assert len([c for c in opener.calls if "refresh" in c["path"]]) == 1

    def test_401_refreshes_once_and_retries(self):
        opener = FakeOpener([
            ("POST", "/api/token/refresh", 200, {"access": "stale"}),
            ("GET", "/api/projects.json?page_size=100", 401, {"detail": "expired"}),
            ("POST", "/api/token/refresh", 200, {"access": "fresh"}),
            ("GET", "/api/projects.json?page_size=100", 200, [{"id": 1}]),
        ])
        client = _client(opener)
        assert client.list_projects() == [{"id": 1}]

    def test_refresh_failure_raises_with_detail(self):
        import urllib.error

        def failing(req: Any, timeout: float = 0) -> Any:
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"detail": "bad token"}')
            )

        client = LSClient("http://ls:8080", "bad", opener=failing)
        with pytest.raises(LSClientError, match="401"):
            client.list_projects()


class TestProjects:
    def test_create_project_payload(self):
        opener = FakeOpener([
            ("POST", "/api/token/refresh", 200, {"access": "a"}),
            ("POST", "/api/projects", 201, {"id": 42, "title": "demo"}),
        ])
        client = _client(opener)
        rec = client.create_project("demo", "<View/>")
        assert rec == {"id": 42, "title": "demo"}
        body = json.loads(opener.calls[1]["body"])
        assert body == {"title": "demo", "label_config": "<View/>"}

    def test_get_project(self):
        opener = FakeOpener([
            ("POST", "/api/token/refresh", 200, {"access": "a"}),
            ("GET", "/api/projects/42", 200, {"id": 42}),
        ])
        assert _client(opener).get_project(42) == {"id": 42}

    def test_http_error_surfaces_status_and_body(self):
        import urllib.error

        counters = {"refresh": 0}

        def opener_fn(req: Any, timeout: float = 0) -> Any:
            path = req.full_url.split("/api/")[-1]
            if "refresh" in path:
                counters["refresh"] += 1
                if counters["refresh"] == 1:
                    resp = io.BytesIO(b'{"access": "a"}')
                    resp.status = 200
                    return resp
                raise AssertionError("refresh should not repeat here")
            raise urllib.error.HTTPError(
                req.full_url, 500, "Server Error", {}, io.BytesIO(b'{"detail": "boom"}')
            )

        client = LSClient("http://ls:8080", "t", opener=opener_fn)
        with pytest.raises(LSClientError) as exc_info:
            client.get_project(1)
        assert "500" in str(exc_info.value)
        assert "boom" in str(exc_info.value)


class TestTasks:
    def test_import_tasks_posts_to_project_import(self):
        opener = FakeOpener([
            ("POST", "/api/token/refresh", 200, {"access": "a"}),
            ("POST", "/api/projects/42/import", 201, {"task_ids": [1, 2]}),
        ])
        tasks = [
            {"data": {"text": "hello"}, "predictions": [{"result": []}]},
            {"data": {"text": "world"}},
        ]
        client = _client(opener)
        out = client.import_tasks(42, tasks)
        assert out == {"task_ids": [1, 2]}
        assert json.loads(opener.calls[1]["body"]) == tasks

    def test_list_tasks_pagination_params(self):
        opener = FakeOpener([
            ("POST", "/api/token/refresh", 200, {"access": "a"}),
            ("GET", "/api/tasks?project=42&page=1&page_size=100", 200, {"tasks": []}),
        ])
        assert _client(opener).list_tasks(42) == {"tasks": []}
