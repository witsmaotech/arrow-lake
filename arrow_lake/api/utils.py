"""Shared utilities for API layer."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 300


async def run_sync(
    func: Any,
    *args: Any,
    timeout: float = _DEFAULT_TIMEOUT,
    label: str = "",
    **kwargs: Any,
) -> Any:
    loop = asyncio.get_running_loop()
    coro = loop.run_in_executor(None, partial(func, *args, **kwargs))
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        name = label or getattr(func, "__name__", str(func))
        _log.warning("run_sync_timeout", name=name, timeout=timeout)
        raise
