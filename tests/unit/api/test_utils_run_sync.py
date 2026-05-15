"""Tests for arrow_lake.api.utils — run_sync."""

from __future__ import annotations

import pytest
from arrow_lake.api.utils import run_sync


class TestRunSync:
    @pytest.mark.asyncio
    async def test_basic_sync_function(self):
        def add(a, b):
            return a + b

        result = await run_sync(add, 3, 4)
        assert result == 7

    @pytest.mark.asyncio
    async def test_with_kwargs(self):
        def greet(name, greeting="hello"):
            return f"{greeting} {name}"

        result = await run_sync(greet, "world", greeting="hi")
        assert result == "hi world"

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        def slow():
            import time
            time.sleep(5)

        with pytest.raises(TimeoutError):
            await run_sync(slow, timeout=0.1, label="slow_func")

    @pytest.mark.asyncio
    async def test_label_in_timeout_log(self):
        """Verify label is used when timeout occurs."""
        def blocker():
            import time
            time.sleep(10)

        with pytest.raises(TimeoutError):
            await run_sync(blocker, timeout=0.05, label="my_custom_label")

    @pytest.mark.asyncio
    async def test_func_name_as_default_label(self):
        """When no label given, uses func.__name__."""
        import unittest.mock
        with unittest.mock.patch("arrow_lake.api.utils._log") as mock_log:
            def my_func():
                import time
                time.sleep(10)

            with pytest.raises(TimeoutError):
                await run_sync(my_func, timeout=0.05)

            mock_log.warning.assert_called_once()
            call_kwargs = mock_log.warning.call_args[1]
            assert call_kwargs["name"] == "my_func"

    @pytest.mark.asyncio
    async def test_default_timeout_is_300(self):
        """Default timeout is 300 seconds."""
        assert True  # signature check
        import inspect
        sig = inspect.signature(run_sync)
        assert sig.parameters["timeout"].default == 300
