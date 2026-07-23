"""Unit tests for the security event logging helper (v1.9.2 批3).

Locks in two design contracts from plan §5 Review B4:
1. Persistence runs via ``asyncio.to_thread`` (NOT ``create_task``) so the audit
   write can never be GC'd mid-flight (same trap that killed kg_build tasks).
2. The helper is fail-soft: a broken/missing lake never breaks the request.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from arrow_lake.api._security_log import (
    LOGIN_SUCCESS,
    actor_of,
    log_security_event,
)


class _RecordingLake:
    """Fake Lake facade that records audit_record calls."""

    def __init__(self, *, raise_on_record: bool = False):
        self.calls: list[tuple] = []
        self._raise = raise_on_record

    def audit_record(self, event_type, dataset_name="", actor="system", **kw):
        if self._raise:
            raise RuntimeError("simulated turso outage")
        self.calls.append((event_type, dataset_name, actor, kw))


@pytest.mark.asyncio
async def test_persists_via_audit_record_with_actor_and_payload():
    lake = _RecordingLake()

    await log_security_event(
        LOGIN_SUCCESS, "alice", lake=lake,
        detail={"user_id": 7, "ip": "10.0.0.1"},
    )

    assert len(lake.calls) == 1
    event_type, _dataset, actor, kw = lake.calls[0]
    assert event_type == LOGIN_SUCCESS
    assert actor == "alice"
    payload = kw["payload"]
    assert payload["actor"] == "alice"
    assert payload["user_id"] == 7
    assert payload["ip"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_no_lake_logs_only_and_never_raises():
    # lake=None must be tolerated (e.g. tests without app.state.lake).
    await log_security_event(LOGIN_SUCCESS, "alice", lake=None)
    # No assertion needed — not raising is the contract.


@pytest.mark.asyncio
async def test_audit_record_failure_is_swallowed():
    lake = _RecordingLake(raise_on_record=True)

    # Must NOT raise even though audit_record blows up.
    await log_security_event(LOGIN_SUCCESS, "alice", lake=lake)


@pytest.mark.asyncio
async def test_runs_off_event_loop_via_to_thread():
    # The persistence call must dispatch + await via to_thread (not create_task,
    # which can be GC'd mid-flight — plan §5 Review B4). We assert the worker
    # call completed (the to_thread path ran and resolved) rather than a specific
    # thread name, which varies by Python/pytest-asyncio default executor naming.
    captured: dict = {}

    class _ThreadLake:
        def audit_record(self, *a, **kw):
            import threading
            captured["thread"] = threading.current_thread().name
            captured["ran"] = True

    await log_security_event(LOGIN_SUCCESS, "alice", lake=_ThreadLake())
    assert captured.get("ran") is True
    # Must not be the main thread name ("MainThread"), proving off-loop dispatch.
    assert captured["thread"] != "MainThread"


def test_actor_of_handles_dict_and_object():
    assert actor_of({"username": "bob"}) == "bob"
    assert actor_of({"sub": "u9"}) == "u9"
    assert actor_of({}) == "admin"  # admin-protected endpoints default
    assert actor_of(SimpleNamespace(username="carol")) == "carol"
    assert actor_of(SimpleNamespace(sub="u1")) == "u1"
    assert actor_of(SimpleNamespace()) == "unknown"
