"""Dedicated ThreadPoolExecutor for KG build blocking calls (v1.10.2 M4 P-辅.4).

Mirrors ``api/utils.olap_executor``: KG's slow blocking calls (hyper-extract
``feed_text`` LLM, ``ka.load``/``dump``/``build_index``, embed) run here instead
of the shared default executor, so a long KG build can't starve OLAP / ingest /
query handlers that share the default pool. KG build concurrency is already
bounded by the builder's semaphores (``build_concurrency`` / ``write_concurrency``);
this pool only ISOLATES those bounded threads from non-KG routes.

Usage: ``await kg_to_thread(sync_fn, *args)`` — a drop-in replacement for
``asyncio.to_thread`` that targets ``kg_executor`` instead of the default pool.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable

kg_executor = ThreadPoolExecutor(
    max_workers=max(4, os.cpu_count() or 4),
    thread_name_prefix="kg-lake",
)


async def kg_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a sync ``func`` on the dedicated ``kg_executor`` (isolated from the
    shared default executor + ``olap_executor``). Signature mirrors
    ``asyncio.to_thread`` for drop-in replacement."""
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(kg_executor, partial(func, *args, **kwargs))
    return await loop.run_in_executor(kg_executor, partial(func, *args))
