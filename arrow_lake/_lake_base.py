"""Base mixin — shared utilities for all Lake mixin classes."""

from __future__ import annotations

from typing import Any


class _LakeBaseMixin:
    """Provides shared utilities used across all Lake mixin classes."""

    def _trace_span(self, name: str, **attrs: Any) -> Any:
        from arrow_lake.api.telemetry import get_tracer

        return get_tracer().start_as_current_span(name, attributes=attrs)
