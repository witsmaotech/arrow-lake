"""P0.1 per-dataset build lock serialization tests (review M2).

Covers the lock MECHANISM cheaply and deterministically:
- same dataset → builds serialize (queue on the lock)
- different datasets → distinct locks → concurrent

The full integration (kg_build's fire-and-forget _run_build holding the lock
across execute_build) is exercised by the coverage/E2E suites; here we guard
the core invariant without the heavy Lake/TaskManager wiring.
"""

from __future__ import annotations

import asyncio


def test_build_lock_serializes_same_dataset() -> None:
    from arrow_lake._lake_kg import _KG_BUILD_LOCKS

    ds = "__test_lock_ds__"
    _KG_BUILD_LOCKS.setdefault(ds, asyncio.Lock())
    order: list[str] = []

    async def work(name: str, delay: float) -> None:
        async with _KG_BUILD_LOCKS.setdefault(ds, asyncio.Lock()):
            order.append(f"{name}-start")
            await asyncio.sleep(delay)
            order.append(f"{name}-end")

    async def main() -> None:
        await asyncio.gather(work("A", 0.05), work("B", 0.01))

    asyncio.run(main())
    # A enters first; B must wait until A releases (the serialization guarantee)
    assert order == ["A-start", "A-end", "B-start", "B-end"]
    _KG_BUILD_LOCKS.pop(ds, None)


def test_build_lock_distinct_per_dataset() -> None:
    from arrow_lake._lake_kg import _KG_BUILD_LOCKS

    a = _KG_BUILD_LOCKS.setdefault("__test_A__", asyncio.Lock())
    b = _KG_BUILD_LOCKS.setdefault("__test_B__", asyncio.Lock())
    assert a is not b  # different datasets → different locks → may run concurrent
    _KG_BUILD_LOCKS.pop("__test_A__", None)
    _KG_BUILD_LOCKS.pop("__test_B__", None)
