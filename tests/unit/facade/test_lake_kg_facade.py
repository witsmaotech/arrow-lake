"""Tests for _LakeKGMixin facade methods — traversers, import/export, Vermeer algorithms, build_status."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arrow_lake._lake_kg import _LakeKGMixin
from arrow_lake.config import ArrowLakeConfig, HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError


def _make_config(*, enabled: bool = True) -> ArrowLakeConfig:
    cfg = ArrowLakeConfig()
    cfg.hugegraph = HugeGraphConfig(enabled=enabled)
    return cfg


class _TestLake(_LakeKGMixin):
    """Thin wrapper to expose _LakeKGMixin without full Lake.__init__."""

    def __init__(self, config: ArrowLakeConfig | None = None) -> None:
        self._config = config or _make_config()
        self._components: dict[str, object] = {}

    def _get_component(self, key: str, factory) -> object:
        if key not in self._components:
            self._components[key] = factory()
        return self._components[key]

    def _get_storage(self) -> object:
        return MagicMock()


# ---------------------------------------------------------------------------
# Component accessors when disabled
# ---------------------------------------------------------------------------


class TestKGComponentAccessorsDisabled:
    """Component accessors return None when KG disabled."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=False))

    def test_get_kg_client_returns_none(self, lake: _TestLake) -> None:
        assert lake._get_kg_client() is None

    def test_get_kg_extractor_returns_none(self, lake: _TestLake) -> None:
        assert lake._get_kg_extractor() is None

    def test_get_kg_builder_returns_none(self, lake: _TestLake) -> None:
        assert lake._get_kg_builder() is None

    def test_get_kg_retriever_returns_none(self, lake: _TestLake) -> None:
        assert lake._get_kg_retriever() is None

    def test_get_vermeer_client_returns_none(self, lake: _TestLake) -> None:
        assert lake._get_vermeer_client() is None


# ---------------------------------------------------------------------------
# _require_kg_client / _require_kg_builder / _require_vermeer_client context managers
# ---------------------------------------------------------------------------


class TestRequireContextManagers:
    """Test _require_* context managers raise KGError when not available."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=False))

    def test_require_kg_client_raises_when_disabled(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info, lake._require_kg_client():
            pass
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    def test_require_kg_builder_raises_when_disabled(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info, lake._require_kg_builder():
            pass
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    def test_require_vermeer_client_raises_when_disabled(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info, lake._require_vermeer_client():
            pass
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    def test_require_kg_client_yields_client_when_enabled(self) -> None:
        lake = _TestLake(_make_config(enabled=True))
        mock_client = MagicMock()
        lake._components["kg_client"] = mock_client

        with lake._require_kg_client() as client:
            assert client is mock_client

    def test_require_kg_builder_yields_builder_when_enabled(self) -> None:
        lake = _TestLake(_make_config(enabled=True))
        mock_builder = MagicMock()
        lake._components["kg_builder"] = mock_builder
        # Also need kg_client and kg_extractor for builder creation
        lake._components["kg_client"] = MagicMock()
        lake._components["kg_extractor"] = MagicMock()

        with lake._require_kg_builder() as builder:
            assert builder is mock_builder


# ---------------------------------------------------------------------------
# Traverser methods
# ---------------------------------------------------------------------------


class TestKGTraverserMethods:
    """Test all traverser methods delegate to HugeGraphClient."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=True))

    @pytest.mark.asyncio()
    async def test_kg_all_shortest_paths(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_all_shortest_paths = AsyncMock(return_value=[{"path": "v1->v2"}])
        lake._components["kg_client"] = mock_client

        result = await lake.kg_all_shortest_paths("v1", "v2", max_depth=5)
        assert result == [{"path": "v1->v2"}]
        mock_client.traverser_all_shortest_paths.assert_awaited_once_with(
            "v1", "v2", direction="OUT", max_depth=5, graph_name=None,
        )

    @pytest.mark.asyncio()
    async def test_kg_weighted_shortest_path(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_weighted_shortest_path = AsyncMock(return_value={"path": "v1->v2"})
        lake._components["kg_client"] = mock_client

        result = await lake.kg_weighted_shortest_path("v1", "v2")
        assert result == {"path": "v1->v2"}

    @pytest.mark.asyncio()
    async def test_kg_single_source_shortest_path(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_single_source_shortest_path = AsyncMock(return_value={"paths": []})
        lake._components["kg_client"] = mock_client

        result = await lake.kg_single_source_shortest_path("v1")
        assert result == {"paths": []}

    @pytest.mark.asyncio()
    async def test_kg_multi_node_shortest_path(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_multi_node_shortest_path = AsyncMock(return_value=[])
        lake._components["kg_client"] = mock_client

        result = await lake.kg_multi_node_shortest_path(["v1"], ["v2"])
        assert result == []

    @pytest.mark.asyncio()
    async def test_kg_rays(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_rays = AsyncMock(return_value=[{"ray": "v1->v2->v3"}])
        lake._components["kg_client"] = mock_client

        result = await lake.kg_rays("v1", max_depth=3)
        assert result == [{"ray": "v1->v2->v3"}]

    @pytest.mark.asyncio()
    async def test_kg_rings(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_rings = AsyncMock(return_value=[{"ring": "v1->v2->v1"}])
        lake._components["kg_client"] = mock_client

        result = await lake.kg_rings("v1", direction="IN", max_depth=4)
        assert result == [{"ring": "v1->v2->v1"}]
        call_kwargs = mock_client.traverser_rings.call_args.kwargs
        assert call_kwargs["direction"] == "IN"
        assert call_kwargs["max_depth"] == 4

    @pytest.mark.asyncio()
    async def test_kg_crosspoints(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_crosspoints = AsyncMock(return_value=[{"id": "v3"}])
        lake._components["kg_client"] = mock_client

        result = await lake.kg_crosspoints("v1", "v2")
        assert result == [{"id": "v3"}]

    @pytest.mark.asyncio()
    async def test_kg_customized_paths(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_customized_paths = AsyncMock(return_value=[{"path": "custom"}])
        lake._components["kg_client"] = mock_client

        steps = [{"direction": "OUT", "labels": ["knows"]}]
        await lake.kg_customized_paths("v1", steps, with_vertex=False)
        mock_client.traverser_customized_paths.assert_awaited_once_with(
            "v1", steps, with_vertex=False, with_edge=True, graph_name=None,
        )


# ---------------------------------------------------------------------------
# Graph Import / Export
# ---------------------------------------------------------------------------


class TestKGImportExport:
    """Test kg_export_graph and kg_import_graph."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=True))

    @pytest.mark.asyncio()
    async def test_kg_export_graph(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.export_graph = AsyncMock(return_value={"vertices": [], "edges": []})
        lake._components["kg_client"] = mock_client

        result = await lake.kg_export_graph()
        assert result == {"vertices": [], "edges": []}
        mock_client.export_graph.assert_awaited_once_with(with_properties=True)

    @pytest.mark.asyncio()
    async def test_kg_export_graph_without_properties(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.export_graph = AsyncMock(return_value={"vertices": [], "edges": []})
        lake._components["kg_client"] = mock_client

        await lake.kg_export_graph(with_properties=False)
        mock_client.export_graph.assert_awaited_once_with(with_properties=False)

    @pytest.mark.asyncio()
    async def test_kg_import_graph(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.import_graph = AsyncMock(return_value={"vertices_added": 5, "edges_added": 3})
        lake._components["kg_client"] = mock_client

        data = {"vertices": [{"id": "v1"}], "edges": [{"src": "v1", "dst": "v2"}]}
        result = await lake.kg_import_graph(data)
        assert result == {"vertices_added": 5, "edges_added": 3}
        mock_client.import_graph.assert_awaited_once_with(data)


# ---------------------------------------------------------------------------
# Vermeer OLAP Algorithms
# ---------------------------------------------------------------------------


class TestKGVermeerAlgorithms:
    """Test all Vermeer OLAP algorithm methods."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=True))

    @pytest.mark.asyncio()
    async def test_kg_pagerank(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.pagerank = AsyncMock(return_value={"ranks": []})
        lake._components["vermeer_client"] = mock_client

        result = await lake.kg_pagerank(iterations=10, damping_factor=0.9)
        assert result == {"ranks": []}
        mock_client.pagerank.assert_awaited_once_with(iterations=10, damping_factor=0.9)

    @pytest.mark.asyncio()
    async def test_kg_louvain(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.louvain = AsyncMock(return_value={"communities": []})
        lake._components["vermeer_client"] = mock_client

        result = await lake.kg_louvain(resolution=1.5)
        assert result == {"communities": []}
        mock_client.louvain.assert_awaited_once_with(resolution=1.5)

    @pytest.mark.asyncio()
    async def test_kg_label_propagation(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.label_propagation = AsyncMock(return_value={"labels": []})
        lake._components["vermeer_client"] = mock_client

        await lake.kg_label_propagation(max_iter=20)
        mock_client.label_propagation.assert_awaited_once_with(max_iter=20)

    @pytest.mark.asyncio()
    async def test_kg_wcc(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.wcc = AsyncMock(return_value={"components": []})
        lake._components["vermeer_client"] = mock_client

        result = await lake.kg_wcc()
        assert result == {"components": []}
        mock_client.wcc.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_kg_triangle_count(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.triangle_count = AsyncMock(return_value={"count": 42})
        lake._components["vermeer_client"] = mock_client

        result = await lake.kg_triangle_count()
        assert result == {"count": 42}

    @pytest.mark.asyncio()
    async def test_kg_degree_centrality(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.degree_centrality = AsyncMock(return_value={"centrality": []})
        lake._components["vermeer_client"] = mock_client

        result = await lake.kg_degree_centrality()
        assert result == {"centrality": []}

    @pytest.mark.asyncio()
    async def test_kg_closeness_centrality(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.closeness_centrality = AsyncMock(return_value={"centrality": []})
        lake._components["vermeer_client"] = mock_client

        result = await lake.kg_closeness_centrality()
        assert result == {"centrality": []}

    @pytest.mark.asyncio()
    async def test_kg_k_core(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.k_core = AsyncMock(return_value={"core": []})
        lake._components["vermeer_client"] = mock_client

        await lake.kg_k_core(k=5)
        mock_client.k_core.assert_awaited_once_with(k=5)

    @pytest.mark.asyncio()
    async def test_kg_betweenness_centrality(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.betweenness_centrality = AsyncMock(return_value={"centrality": []})
        lake._components["vermeer_client"] = mock_client

        result = await lake.kg_betweenness_centrality()
        assert result == {"centrality": []}


# ---------------------------------------------------------------------------
# kg_build_status
# ---------------------------------------------------------------------------


class TestKGBuildStatus:
    """Test kg_build_status with task found/not found."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=True))

    @pytest.mark.asyncio()
    async def test_build_status_returns_dict(self, lake: _TestLake) -> None:
        mock_builder = MagicMock()
        mock_task = MagicMock()
        mock_task.task_id = "task-1"
        mock_task.status = MagicMock(value="completed")
        mock_task.dataset_name = "ds1"
        mock_task.total_chunks = 100
        mock_task.processed_chunks = 100
        mock_task.entity_count = 50
        mock_task.relation_count = 30
        mock_task.started_at = datetime(2026, 1, 1, tzinfo=UTC)
        mock_task.completed_at = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
        mock_task.error = None
        mock_builder.get_task_status.return_value = mock_task
        lake._components["kg_builder"] = mock_builder
        # Also need kg_client and kg_extractor for builder creation
        lake._components["kg_client"] = MagicMock()
        lake._components["kg_extractor"] = MagicMock()

        result = await lake.kg_build_status("task-1")
        assert result is not None
        assert result["task_id"] == "task-1"
        assert result["status"] == "completed"
        assert result["dataset_name"] == "ds1"
        assert result["total_chunks"] == 100
        assert result["processed_chunks"] == 100
        assert result["entity_count"] == 50
        assert result["relation_count"] == 30
        assert result["started_at"] == "2026-01-01T00:00:00+00:00"
        assert result["completed_at"] == "2026-01-01T01:00:00+00:00"
        assert result["error"] is None

    @pytest.mark.asyncio()
    async def test_build_status_returns_none_when_task_not_found(self, lake: _TestLake) -> None:
        mock_builder = MagicMock()
        mock_builder.get_task_status.return_value = None
        lake._components["kg_builder"] = mock_builder
        lake._components["kg_client"] = MagicMock()
        lake._components["kg_extractor"] = MagicMock()

        result = await lake.kg_build_status("missing-task")
        assert result is None

    @pytest.mark.asyncio()
    async def test_build_status_returns_none_when_no_builder(self, lake: _TestLake) -> None:
        # Remove kg_builder from components so _get_kg_builder returns None
        # This happens when enabled=True but builder creation fails
        lake._components["kg_builder"] = None

        result = await lake.kg_build_status("task-x")
        assert result is None

    @pytest.mark.asyncio()
    async def test_build_status_handles_none_dates(self, lake: _TestLake) -> None:
        mock_builder = MagicMock()
        mock_task = MagicMock()
        mock_task.task_id = "task-2"
        mock_task.status = MagicMock(value="running")
        mock_task.dataset_name = "ds2"
        mock_task.total_chunks = 50
        mock_task.processed_chunks = 10
        mock_task.entity_count = 5
        mock_task.relation_count = 2
        mock_task.started_at = None
        mock_task.completed_at = None
        mock_task.error = None
        mock_builder.get_task_status.return_value = mock_task
        lake._components["kg_builder"] = mock_builder
        lake._components["kg_client"] = MagicMock()
        lake._components["kg_extractor"] = MagicMock()

        result = await lake.kg_build_status("task-2")
        assert result["started_at"] is None
        assert result["completed_at"] is None


# ---------------------------------------------------------------------------
# kg_build
# ---------------------------------------------------------------------------


class TestKGBuild:
    """Test kg_build with column remapping logic."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=True))

    @pytest.mark.asyncio()
    async def test_kg_build_adds_missing_columns(self, lake: _TestLake) -> None:
        import pyarrow as pa

        mock_builder = AsyncMock()
        mock_builder.build = AsyncMock(return_value="task-build-1")
        lake._components["kg_builder"] = mock_builder
        lake._components["kg_client"] = MagicMock()
        lake._components["kg_extractor"] = MagicMock()

        # Table with no id, content, document_name, or chunk_index columns
        mock_table = pa.table({"text_content": ["hello", "world"]})
        mock_dataset = MagicMock()
        mock_dataset.search.return_value.to_arrow.return_value = mock_table

        mock_storage = MagicMock()
        mock_storage.open_dataset.return_value = mock_dataset
        lake._get_storage = lambda: mock_storage  # type: ignore

        result = await lake.kg_build("my_ds")
        assert result == "task-build-1"

        # Verify build was called with a table that has the added columns
        build_call = mock_builder.build.call_args
        built_table = build_call[0][1]
        assert "id" in built_table.column_names
        assert "content" in built_table.column_names
        assert "document_name" in built_table.column_names
        assert "chunk_index" in built_table.column_names

    @pytest.mark.asyncio()
    async def test_kg_build_preserves_existing_columns(self, lake: _TestLake) -> None:
        import pyarrow as pa

        mock_builder = AsyncMock()
        mock_builder.build = AsyncMock(return_value="task-build-2")
        lake._components["kg_builder"] = mock_builder
        lake._components["kg_client"] = MagicMock()
        lake._components["kg_extractor"] = MagicMock()

        # Table already has all required columns
        mock_table = pa.table({
            "id": ["1", "2"],
            "content": ["hello", "world"],
            "document_name": ["doc1", "doc1"],
            "chunk_index": [0, 1],
        })
        mock_dataset = MagicMock()
        mock_dataset.search.return_value.to_arrow.return_value = mock_table

        mock_storage = MagicMock()
        mock_storage.open_dataset.return_value = mock_dataset
        lake._get_storage = lambda: mock_storage  # type: ignore

        result = await lake.kg_build("my_ds")
        assert result == "task-build-2"

    @pytest.mark.asyncio()
    async def test_kg_build_uses_first_column_when_no_content(self, lake: _TestLake) -> None:
        import pyarrow as pa

        mock_builder = AsyncMock()
        mock_builder.build = AsyncMock(return_value="task-build-3")
        lake._components["kg_builder"] = mock_builder
        lake._components["kg_client"] = MagicMock()
        lake._components["kg_extractor"] = MagicMock()

        # Table with no id, no content, no text_content — uses first column as content
        mock_table = pa.table({"body": ["text1", "text2"], "other": ["a", "b"]})
        mock_dataset = MagicMock()
        mock_dataset.search.return_value.to_arrow.return_value = mock_table

        mock_storage = MagicMock()
        mock_storage.open_dataset.return_value = mock_dataset
        lake._get_storage = lambda: mock_storage  # type: ignore

        await lake.kg_build("my_ds")

        build_call = mock_builder.build.call_args
        built_table = build_call[0][1]
        assert "content" in built_table.column_names
        assert "other" in built_table.column_names


# ---------------------------------------------------------------------------
# _create_* factory methods
# ---------------------------------------------------------------------------


class TestKGFactoryMethods:
    """Test component factory methods."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=True))

    def test_create_kg_extractor(self, lake: _TestLake) -> None:
        # v1.8.7: default extractor_backend is now "he", so _create_kg_extractor
        # builds HyperExtractExtractor (not legacy EntityExtractor). Mock the he
        # path's two construction sites and assert the extractor is instantiated.
        with patch("arrow_lake.knowledge_graph.he_extractor.HyperExtractExtractor") as mock_he, \
             patch("arrow_lake.knowledge_graph.doc_type_router.DocTypeClassifier") as mock_cls:
            mock_cls.from_llm_config.return_value = MagicMock()
            mock_he.return_value = MagicMock()

            lake._create_kg_extractor()
            mock_he.assert_called_once()

    def test_create_kg_builder_raises_when_no_client(self, lake: _TestLake) -> None:
        # Override _get_kg_client to return None
        lake._components["kg_client"] = None
        lake._components["kg_extractor"] = MagicMock()

        with pytest.raises(KGError, match="Cannot create KGBuilder"):
            lake._create_kg_builder()

    def test_create_kg_builder_raises_when_no_extractor(self, lake: _TestLake) -> None:
        lake._components["kg_client"] = MagicMock()
        lake._components["kg_extractor"] = None

        with pytest.raises(KGError, match="Cannot create KGBuilder"):
            lake._create_kg_builder()

    def test_create_vermeer_client(self, lake: _TestLake) -> None:
        with patch("arrow_lake.knowledge_graph.vermeer_client.VermeerClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            lake._create_vermeer_client()
            mock_cls.assert_called_once_with(lake._config.hugegraph)


# ---------------------------------------------------------------------------
# Traverser methods when disabled
# ---------------------------------------------------------------------------


class TestKGTraverserMethodsDisabled:
    """All traverser methods raise KGError when disabled."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=False))

    @pytest.mark.asyncio()
    async def test_all_shortest_paths_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError):
            await lake.kg_all_shortest_paths("v1", "v2")

    @pytest.mark.asyncio()
    async def test_rays_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError):
            await lake.kg_rays("v1")

    @pytest.mark.asyncio()
    async def test_export_graph_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError):
            await lake.kg_export_graph()

    @pytest.mark.asyncio()
    async def test_import_graph_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError):
            await lake.kg_import_graph({"vertices": [], "edges": []})

    @pytest.mark.asyncio()
    async def test_pagerank_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError):
            await lake.kg_pagerank()

    @pytest.mark.asyncio()
    async def test_louvain_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError):
            await lake.kg_louvain()
