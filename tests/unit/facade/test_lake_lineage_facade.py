"""Tests for _LakeLineageMixin facade methods — lineage_record_event, lineage_history, lineage_query."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake._lake_lineage import _LakeLineageMixin
from arrow_lake.config import ArrowLakeConfig


def _make_config() -> ArrowLakeConfig:
    return ArrowLakeConfig()


class _TestLake(_LakeLineageMixin):
    """Thin wrapper to expose _LakeLineageMixin without full Lake.__init__."""

    def __init__(self, config: ArrowLakeConfig | None = None) -> None:
        self._config = config or _make_config()
        self._components: dict[str, object] = {}
        self._storage: object | None = None

    def _get_component(self, key: str, factory) -> object:
        if key not in self._components:
            self._components[key] = factory()
        return self._components[key]

    def _get_storage(self) -> object:
        if self._storage is None:
            self._storage = MagicMock()
        return self._storage

    def get_session_manager(self) -> object:
        return self._get_component(
            "session_manager",
            lambda: MagicMock(),
        )


@pytest.fixture()
def lake() -> _TestLake:
    return _TestLake()


class TestLineageRecordEvent:
    """Test lineage_record_event delegates to LineageStore."""

    @patch("arrow_lake.catalog.lineage.create_lineage_event")
    @patch("arrow_lake.catalog.lineage.LineageStore")
    def test_records_event_with_defaults(self, mock_store_cls: MagicMock, mock_create: MagicMock, lake: _TestLake) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_event = MagicMock()
        mock_create.return_value = mock_event

        lake.lineage_record_event("my_ds", "create")

        mock_create.assert_called_once_with(
            "my_ds",
            "create",
            source_datasets=None,
            transform_type="",
            actor="system",
            metadata=None,
        )
        mock_store.record_event.assert_called_once_with(mock_event)

    @patch("arrow_lake.catalog.lineage.create_lineage_event")
    @patch("arrow_lake.catalog.lineage.LineageStore")
    def test_records_event_with_all_params(self, mock_store_cls: MagicMock, mock_create: MagicMock, lake: _TestLake) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_event = MagicMock()
        mock_create.return_value = mock_event

        lake.lineage_record_event(
            "target_ds",
            "transform",
            source_datasets=["src_a", "src_b"],
            transform_type="map",
            actor="alice",
            metadata={"env": "prod"},
        )

        mock_create.assert_called_once_with(
            "target_ds",
            "transform",
            source_datasets=["src_a", "src_b"],
            transform_type="map",
            actor="alice",
            metadata={"env": "prod"},
        )
        mock_store.record_event.assert_called_once_with(mock_event)

    @patch("arrow_lake.catalog.lineage.create_lineage_event")
    @patch("arrow_lake.catalog.lineage.LineageStore")
    def test_caches_store_component(self, mock_store_cls: MagicMock, mock_create: MagicMock, lake: _TestLake) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_create.return_value = MagicMock()

        lake.lineage_record_event("ds1", "create")
        lake.lineage_record_event("ds2", "append")

        # LineageStore factory should only be called once (cached)
        assert mock_store_cls.call_count == 1


class TestLineageHistory:
    """Test lineage_history delegates to LineageStore."""

    @patch("arrow_lake.catalog.lineage.LineageStore")
    def test_returns_history(self, mock_store_cls: MagicMock, lake: _TestLake) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        expected = [{"event_id": "e1"}, {"event_id": "e2"}]
        mock_store.get_dataset_history.return_value = expected

        result = lake.lineage_history("my_ds")

        mock_store.get_dataset_history.assert_called_once_with("my_ds")
        assert result == expected

    @patch("arrow_lake.catalog.lineage.LineageStore")
    def test_returns_empty_for_unknown_dataset(self, mock_store_cls: MagicMock, lake: _TestLake) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_store.get_dataset_history.return_value = []

        result = lake.lineage_history("unknown_ds")
        assert result == []


class TestLineageQuery:
    """Test lineage_query delegates to LineageQueryBridge."""

    @patch("arrow_lake.catalog.lineage.LineageQueryBridge")
    @patch("arrow_lake.catalog.lineage.LineageStore")
    def test_query_returns_arrow_table(self, mock_store_cls: MagicMock, mock_bridge_cls: MagicMock, lake: _TestLake) -> None:
        mock_bridge = MagicMock()
        mock_bridge_cls.return_value = mock_bridge
        expected_table = pa.table({"event_id": ["e1"]})
        mock_bridge.query.return_value = expected_table

        result = lake.lineage_query("SELECT * FROM lineage")

        assert isinstance(result, pa.Table)
        mock_bridge.query.assert_called_once_with("SELECT * FROM lineage")

    @patch("arrow_lake.catalog.lineage.LineageQueryBridge")
    @patch("arrow_lake.catalog.lineage.LineageStore")
    def test_query_caches_bridge(self, mock_store_cls: MagicMock, mock_bridge_cls: MagicMock, lake: _TestLake) -> None:
        mock_bridge = MagicMock()
        mock_bridge_cls.return_value = mock_bridge
        mock_bridge.query.return_value = pa.table({"x": [1]})

        lake.lineage_query("SELECT 1")
        lake.lineage_query("SELECT 2")

        # Bridge factory should only be called once (cached)
        assert mock_bridge_cls.call_count == 1

    @patch("arrow_lake.catalog.lineage.LineageQueryBridge")
    @patch("arrow_lake.catalog.lineage.LineageStore")
    def test_query_passes_session_manager(self, mock_store_cls: MagicMock, mock_bridge_cls: MagicMock, lake: _TestLake) -> None:
        mock_bridge = MagicMock()
        mock_bridge_cls.return_value = mock_bridge
        mock_bridge.query.return_value = pa.table({"x": [1]})

        lake.lineage_query("SELECT * FROM lineage")

        # Verify that the bridge was created with session_manager
        call_kwargs = mock_bridge_cls.call_args
        assert "session_manager" in call_kwargs.kwargs
