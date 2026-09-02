"""Tests for arrow_lake/_lake_lineage.py — _LakeLineageMixin.

Targets: all uncovered lines to reach 80%+ coverage.
Originally: 20, 79-90, 94-105. Extended to: 34-48, 53-57, 61-75.

LineageStore and LineageQueryBridge are imported locally inside mixin methods,
so we patch them at ``arrow_lake.catalog.lineage``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake._lake_lineage import _LakeLineageMixin


# ---------------------------------------------------------------------------
# Helper: build a mixin instance with required mocks
# ---------------------------------------------------------------------------


def _make_mixin(
    *,
    has_auth_provider: bool = False,
) -> _LakeLineageMixin:
    """Create a _LakeLineageMixin with mocked internal methods."""
    mixin = _LakeLineageMixin()
    mixin._get_storage = MagicMock(return_value=MagicMock())  # type: ignore[assignment]
    mixin.get_session_manager = MagicMock(return_value=MagicMock())  # type: ignore[assignment]
    if has_auth_provider:
        mixin._gravitino_auth_provider = MagicMock()  # type: ignore[assignment]
    return mixin


def _make_get_component(store: MagicMock, bridge: MagicMock):
    """Build a _get_component mock that returns *store* for 'lineage' and
    invokes the factory for 'lineage_bridge' (so the lambda body executes)."""

    def _get_component(name: str, factory=None):
        if name == "lineage":
            return store
        if name == "lineage_bridge" and factory is not None:
            return factory()
        return MagicMock()

    return _get_component


# ---------------------------------------------------------------------------
# _create_lineage_store — with auth provider (line 20)
# ---------------------------------------------------------------------------


class TestCreateLineageStore:
    def test_creates_store_and_injects_auth_provider(self) -> None:
        mixin = _make_mixin(has_auth_provider=True)
        mock_store = MagicMock()

        with patch(
            "arrow_lake.catalog.lineage.LineageStore",
            return_value=mock_store,
        ):
            result = mixin._create_lineage_store()

        mock_store.set_auth_provider.assert_called_once_with(
            mixin._gravitino_auth_provider
        )
        assert result is mock_store

    def test_no_auth_provider_when_absent(self) -> None:
        mixin = _make_mixin(has_auth_provider=False)
        mock_store = MagicMock()

        with patch(
            "arrow_lake.catalog.lineage.LineageStore",
            return_value=mock_store,
        ):
            result = mixin._create_lineage_store()

        mock_store.set_auth_provider.assert_not_called()
        assert result is mock_store


# ---------------------------------------------------------------------------
# lineage_graph (lines 79-90)
# ---------------------------------------------------------------------------


class TestLineageGraph:
    def test_lineage_graph_delegates_to_bridge(self) -> None:
        mixin = _make_mixin()
        mock_store = MagicMock()
        mock_bridge = MagicMock()

        mixin._get_component = _make_get_component(mock_store, mock_bridge)  # type: ignore[assignment]

        with patch(
            "arrow_lake.catalog.lineage.LineageQueryBridge",
            return_value=mock_bridge,
        ):
            graph: dict[str, Any] = mixin.lineage_graph("ds_a", max_depth=5)

        assert graph is mock_bridge.trace_full_graph.return_value
        mock_bridge.trace_full_graph.assert_called_once_with("ds_a", max_depth=5, max_nodes=500)

    def test_lineage_graph_default_max_depth(self) -> None:
        mixin = _make_mixin()
        mock_store = MagicMock()
        mock_bridge = MagicMock()

        mixin._get_component = _make_get_component(mock_store, mock_bridge)  # type: ignore[assignment]

        with patch(
            "arrow_lake.catalog.lineage.LineageQueryBridge",
            return_value=mock_bridge,
        ):
            mixin.lineage_graph("ds_b")

        mock_bridge.trace_full_graph.assert_called_once_with("ds_b", max_depth=10, max_nodes=500)


# ---------------------------------------------------------------------------
# lineage_impact (lines 94-105)
# ---------------------------------------------------------------------------


class TestLineageImpact:
    def test_lineage_impact_delegates_to_bridge(self) -> None:
        mixin = _make_mixin()
        mock_store = MagicMock()
        mock_bridge = MagicMock()

        mixin._get_component = _make_get_component(mock_store, mock_bridge)  # type: ignore[assignment]

        with patch(
            "arrow_lake.catalog.lineage.LineageQueryBridge",
            return_value=mock_bridge,
        ):
            impact: list[dict[str, Any]] = mixin.lineage_impact("upstream_ds")

        assert impact is mock_bridge.trace_impact.return_value
        mock_bridge.trace_impact.assert_called_once_with("upstream_ds")


# ---------------------------------------------------------------------------
# lineage_record_event (lines 34-48)
# ---------------------------------------------------------------------------


class TestLineageRecordEvent:
    @patch("arrow_lake.catalog.lineage.create_lineage_event")
    def test_record_event_delegates_to_store(self, mock_create: MagicMock) -> None:
        mixin = _make_mixin()
        mock_store = MagicMock()
        mock_event = MagicMock()
        mock_create.return_value = mock_event

        mixin._get_component = _make_get_component(mock_store, MagicMock())  # type: ignore[assignment]

        mixin.lineage_record_event(
            "target_ds",
            "transform",
            source_datasets=["src_a"],
            transform_type="etl",
            actor="user",
        )

        mock_create.assert_called_once_with(
            "target_ds",
            "transform",
            source_datasets=["src_a"],
            transform_type="etl",
            lance_version=None,
            actor="user",
            metadata=None,
        )
        mock_store.record_event.assert_called_once_with(mock_event)

    @patch("arrow_lake.catalog.lineage.create_lineage_event")
    def test_record_event_default_args(self, mock_create: MagicMock) -> None:
        mixin = _make_mixin()
        mock_store = MagicMock()
        mock_event = MagicMock()
        mock_create.return_value = mock_event

        mixin._get_component = _make_get_component(mock_store, MagicMock())  # type: ignore[assignment]

        mixin.lineage_record_event("ds1", "ingest")

        mock_create.assert_called_once_with(
            "ds1",
            "ingest",
            source_datasets=None,
            transform_type="",
            lance_version=None,
            actor="system",
            metadata=None,
        )
        mock_store.record_event.assert_called_once()


# ---------------------------------------------------------------------------
# lineage_history (lines 53-57)
# ---------------------------------------------------------------------------


class TestLineageHistory:
    def test_history_delegates_to_store(self) -> None:
        mixin = _make_mixin()
        mock_store = MagicMock()
        expected = [{"event": "e1"}]
        mock_store.get_dataset_history.return_value = expected

        mixin._get_component = _make_get_component(mock_store, MagicMock())  # type: ignore[assignment]

        result = mixin.lineage_history("my_ds")

        assert result == expected
        mock_store.get_dataset_history.assert_called_once_with("my_ds")


# ---------------------------------------------------------------------------
# lineage_query (lines 61-75)
# ---------------------------------------------------------------------------


class TestLineageQuery:
    def test_query_delegates_to_bridge(self) -> None:
        mixin = _make_mixin()
        mock_store = MagicMock()
        mock_bridge = MagicMock()
        mock_table = MagicMock()
        mock_bridge.query.return_value = mock_table

        mixin._get_component = _make_get_component(mock_store, mock_bridge)  # type: ignore[assignment]

        with patch(
            "arrow_lake.catalog.lineage.LineageQueryBridge",
            return_value=mock_bridge,
        ):
            result = mixin.lineage_query("SELECT * FROM lineage")

        assert result is mock_table
        mock_bridge.query.assert_called_once_with("SELECT * FROM lineage")
