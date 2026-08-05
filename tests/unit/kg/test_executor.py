"""Tests for the dedicated KG ThreadPoolExecutor (v1.10.2 M4 P-辅.4)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from arrow_lake.knowledge_graph._executor import kg_executor, kg_to_thread


def test_kg_executor_is_a_distinct_pool() -> None:
    """kg_executor exists as a real ThreadPoolExecutor (separate from the shared
    default executor + olap_executor)."""
    assert isinstance(kg_executor, ThreadPoolExecutor)
    assert kg_executor._max_workers >= 1


@pytest.mark.asyncio
async def test_kg_to_thread_runs_sync_fn_and_returns_result() -> None:
    """kg_to_thread runs a sync function and returns its value."""
    assert await kg_to_thread(lambda x: x * 2, 21) == 42


@pytest.mark.asyncio
async def test_kg_to_thread_runs_on_kg_executor() -> None:
    """kg_to_thread executes on the dedicated kg_executor (thread-name prefix
    `kg-lake`), proving isolation from the default/olap pools."""
    seen: dict[str, str] = {}

    def _capture() -> None:
        seen["name"] = threading.current_thread().name

    await kg_to_thread(_capture)
    assert seen.get("name", "").startswith("kg-lake"), seen


@pytest.mark.asyncio
async def test_kg_to_thread_passes_kwargs() -> None:
    """kg_to_thread forwards args + kwargs to the sync function."""
    def _join(a: int, b: int, *, sep: str) -> str:
        return sep.join([str(a), str(b)])
    assert await kg_to_thread(_join, 1, 2, sep="-") == "1-2"
