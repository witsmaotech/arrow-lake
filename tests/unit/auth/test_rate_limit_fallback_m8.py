"""M-8(v1.10.7 review,发版前清偿):fail-open 回落限额均摊。

Redis 不可用回落进程内计数时,gunicorn N worker = 全站限额 ×N——
修法:每 worker 预算 = limit // N(近似全局口径;恢复后回精确)。
"""

from __future__ import annotations

import pytest
from collections import defaultdict

from arrow_lake.api import rate_limit as rl
from arrow_lake.api.rate_limit import _Counter


class _FakeCallNext:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, request):  # noqa: ANN001
        from types import SimpleNamespace

        self.calls += 1
        return SimpleNamespace(headers={})


class _FakeRequest:
    def __init__(self, path: str) -> None:
        class _URL:
            pass

        class _App:
            state = type("S", (), {"redis_rate_limiter": None})()

        self.url = _URL()
        self.url.path = path
        self.method = "POST"
        self.headers = {}
        self.client = type("C", (), {"host": "10.0.0.1"})()
        self.app = _App()


@pytest.mark.asyncio
async def test_fallback_limit_amortized_by_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "_WORKER_COUNT", 2)
    monkeypatch.setattr(rl, "_counters", defaultdict(_Counter))
    call_next = _FakeCallNext()
    # rpm=4, N=2 → 每 worker 预算 2:第 3 次应 429
    for _ in range(2):
        resp = await rl.rate_limit_middleware_fn(
            _FakeRequest("/x"), call_next, rpm=4, burst=10,
            trusted_proxies=set(),
        )
        assert getattr(resp, "status_code", None) is None  # 透传
    resp3 = await rl.rate_limit_middleware_fn(
        _FakeRequest("/x"), call_next, rpm=4, burst=10,
        trusted_proxies=set(),
    )
    assert getattr(resp3, "status_code", None) == 429
    assert call_next.calls == 2  # 被拒请求未透传


@pytest.mark.asyncio
async def test_no_redis_uses_amortized_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 Redis(rl=None)同样走均摊预算(语义一致)。"""
    monkeypatch.setattr(rl, "_WORKER_COUNT", 4)
    monkeypatch.setattr(rl, "_counters", defaultdict(_Counter))
    call_next = _FakeCallNext()
    # rpm=8, N=4 → 预算 2
    for _ in range(2):
        assert getattr(await rl.rate_limit_middleware_fn(
            _FakeRequest("/y"), call_next, rpm=8, burst=10,
            trusted_proxies=set()), "status_code", None) is None  # 透传
    assert getattr(await rl.rate_limit_middleware_fn(
        _FakeRequest("/y"), call_next, rpm=8, burst=10,
        trusted_proxies=set()), "status_code", None) == 429
