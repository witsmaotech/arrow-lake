"""Tests for _LakeKGMixin facade methods."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from arrow_lake._lake_kg import _LakeKGMixin
from arrow_lake.config import ArrowLakeConfig, HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError


def _make_config(enabled: bool = False) -> ArrowLakeConfig:
    cfg = ArrowLakeConfig()
    cfg.hugegraph = HugeGraphConfig(enabled=enabled)
    return cfg


# ---------------------------------------------------------------------------
# Minimal Lake subclass for testing mixin in isolation
# ---------------------------------------------------------------------------


class _TestLake(_LakeKGMixin):
    """Thin wrapper to expose _LakeKGMixin without full Lake.__init__."""

    def __init__(self, config: ArrowLakeConfig) -> None:
        self._config = config
        self._components: dict[str, object] = {}

    def _get_component(self, key: str, factory) -> object:
        if key not in self._components:
            self._components[key] = factory()
        return self._components[key]


# ---------------------------------------------------------------------------
# Tests: KG not enabled
# ---------------------------------------------------------------------------


class TestKGDisabled:
    """All KG methods should raise KGError when hugegraph.enabled=False."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=False))

    def test_ensure_kg_enabled_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            lake._ensure_kg_enabled()
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_kg_build_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            await lake.kg_build("my_data")
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_kg_stats_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            await lake.kg_stats()
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_kg_query_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            await lake.kg_query("g.V().count()")
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_kg_get_neighbors_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            await lake.kg_get_neighbors("entity:1")
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_kg_delete_graph_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            await lake.kg_delete_graph()
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_kg_build_status_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            await lake.kg_build_status("task-123")
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_kg_search_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            await lake.kg_search("ds", "q")
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_kg_chat_raises(self, lake: _TestLake) -> None:
        with pytest.raises(KGError) as exc_info:
            await lake.kg_chat("ds", "q")
        assert exc_info.value.error_code == ErrorCode.KG_GRAPH_NOT_FOUND


# ---------------------------------------------------------------------------
# Tests: KG enabled -- basic flow with mocks
# ---------------------------------------------------------------------------


class TestKGEnabled:
    """KG methods should delegate to underlying components when enabled."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=True))

    def test_get_kg_client_returns_client(self, lake: _TestLake) -> None:
        client = lake._get_kg_client()
        assert client is not None
        # Second call should return same cached instance
        assert lake._get_kg_client() is client

    def test_get_kg_retriever_returns_retriever(self, lake: _TestLake) -> None:
        retriever = lake._get_kg_retriever()
        assert retriever is not None

    @pytest.mark.asyncio()
    async def test_kg_stats_delegates(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.get_stats = AsyncMock(return_value={
            "total_vertices": 42,
            "total_edges": 100,
        })
        lake._components["kg_client"] = mock_client

        result = await lake.kg_stats()
        assert result["total_vertices"] == 42
        assert result["total_edges"] == 100
        mock_client.get_stats.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_kg_query_delegates(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.gremlin = AsyncMock(return_value=[{"id": "v1"}])
        lake._components["kg_client"] = mock_client

        result = await lake.kg_query("g.V().count()")
        assert result == [{"id": "v1"}]
        mock_client.gremlin.assert_awaited_once_with("g.V().count()")

    @pytest.mark.asyncio()
    async def test_kg_get_neighbors_delegates(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.traverser_kneighbor = AsyncMock(return_value=[
            {"id": "v2", "label": "entity"},
        ])
        lake._components["kg_client"] = mock_client

        result = await lake.kg_get_neighbors("entity:1", depth=2)
        assert len(result) == 1
        mock_client.traverser_kneighbor.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_kg_delete_graph_delegates(self, lake: _TestLake) -> None:
        mock_client = AsyncMock()
        mock_client.clear = AsyncMock()
        lake._components["kg_client"] = mock_client

        await lake.kg_delete_graph()
        mock_client.clear.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_kg_get_neighbors_clamps_depth(self, lake: _TestLake) -> None:
        """Depth should be clamped to max_traversal_depth from config."""
        mock_client = AsyncMock()
        mock_client.traverser_kneighbor = AsyncMock(return_value=[])
        lake._components["kg_client"] = mock_client

        max_depth = lake._config.hugegraph.max_traversal_depth
        await lake.kg_get_neighbors("e1", depth=max_depth + 5)

        call_kwargs = mock_client.traverser_kneighbor.call_args
        assert call_kwargs.kwargs.get("depth") == max_depth

    # ------------------------------------------------------------------
    # [#2] KA semantic search / RAG chat (hyper-extract)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_kg_search_delegates_and_serializes(self, lake: _TestLake) -> None:
        """kg_search delegates to extractor.search_ka and serializes nodes/edges."""
        node = SimpleNamespace(type="concept", name="聚合根", definition="一致性边界")
        edge = SimpleNamespace(type="contains", source="聚合根", target="实体")
        ext = MagicMock()
        ext.search_ka = MagicMock(return_value=([node], [edge]))
        lake._components["kg_extractor"] = ext

        result = await lake.kg_search("jd_ddd", "聚合根是什么", top_k=5)
        ext.search_ka.assert_called_once_with("jd_ddd", "聚合根是什么", 5)
        assert result["node_count"] == 1 and result["edge_count"] == 1
        assert result["nodes"][0]["name"] == "聚合根"
        assert result["edges"][0]["type"] == "contains"

    @pytest.mark.asyncio()
    async def test_kg_search_clamps_top_k(self, lake: _TestLake) -> None:
        """top_k is clamped to [1, 50]."""
        ext = MagicMock()
        ext.search_ka = MagicMock(return_value=([], []))
        lake._components["kg_extractor"] = ext

        await lake.kg_search("ds", "q", top_k=9999)
        assert ext.search_ka.call_args.args[2] == 50  # clamped to max
        await lake.kg_search("ds", "q", top_k=0)
        assert ext.search_ka.call_args.args[2] == 1  # clamped to min

    @pytest.mark.asyncio()
    async def test_kg_chat_delegates(self, lake: _TestLake) -> None:
        """kg_chat delegates to extractor.chat_ka and extracts answer + retrieved."""
        retrieved = [SimpleNamespace(name="聚合根")]
        resp = SimpleNamespace(
            content="聚合根是数据修改的一致性边界。",
            additional_kwargs={"retrieved_items": retrieved},
        )
        ext = MagicMock()
        ext.chat_ka = MagicMock(return_value=resp)
        lake._components["kg_extractor"] = ext

        result = await lake.kg_chat("jd_ddd", "什么是聚合根", top_k=5)
        ext.chat_ka.assert_called_once_with("jd_ddd", "什么是聚合根", 5)
        assert "聚合根" in result["answer"]
        assert result["retrieval_count"] == 1
        assert result["retrieved_items"][0]["name"] == "聚合根"

    @pytest.mark.asyncio()
    async def test_kg_search_raises_when_legacy_extractor(self, lake: _TestLake) -> None:
        """Legacy EntityExtractor (no search_ka) → KGError, not AttributeError."""
        ext = MagicMock(spec=[])  # no attributes at all → hasattr(search_ka)=False
        lake._components["kg_extractor"] = ext

        with pytest.raises(KGError) as exc_info:
            await lake.kg_search("ds", "q")
        assert exc_info.value.error_code == ErrorCode.KG_QUERY_FAILED

    @pytest.mark.asyncio()
    async def test_kg_chat_raises_when_legacy_extractor(self, lake: _TestLake) -> None:
        ext = MagicMock(spec=[])
        lake._components["kg_extractor"] = ext

        with pytest.raises(KGError) as exc_info:
            await lake.kg_chat("ds", "q")
        assert exc_info.value.error_code == ErrorCode.KG_QUERY_FAILED
