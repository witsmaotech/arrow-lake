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

        result = await lake.kg_chat("jd_ddd", "什么是聚合根", top_k=5, engine="chat_ka")
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

    @pytest.mark.asyncio()
    async def test_kg_rebuild_index_delegates(self, lake: _TestLake) -> None:
        """[#7] kg_rebuild_index delegates to extractor.rebuild_ka_index."""
        ext = MagicMock()
        ext.search_ka = MagicMock(return_value=([], []))  # marks as he extractor
        ext.rebuild_ka_index = MagicMock(return_value={
            "dataset": "ds", "index_rebuilt": True, "node_count": 7, "edge_count": 9,
            "ka_dir": "/tmp/ka/ds/ka",
        })
        lake._components["kg_extractor"] = ext

        result = await lake.kg_rebuild_index("ds")
        ext.rebuild_ka_index.assert_called_once_with("ds")
        assert result["index_rebuilt"] is True
        assert result["node_count"] == 7

    @pytest.mark.asyncio()
    async def test_kg_export_obsidian_delegates(self, lake: _TestLake) -> None:
        """[#5] kg_export_obsidian delegates to extractor.export_ka_obsidian."""
        ext = MagicMock()
        ext.search_ka = MagicMock(return_value=([], []))  # marks as he extractor
        ext.export_ka_obsidian = MagicMock(return_value={
            "dataset": "ds", "vault_path": "/tmp/ka/ds/obsidian",
            "node_count": 5, "edge_count": 8, "vault_name": "Knowledge Vault",
        })
        lake._components["kg_extractor"] = ext

        result = await lake.kg_export_obsidian("ds", overwrite=True)
        # default out_dir is server-derived (he_ka_base_dir/ds/obsidian)
        args, kwargs = ext.export_ka_obsidian.call_args
        assert args[0] == "ds"
        assert "obsidian" in args[1]
        assert kwargs["overwrite"] is True
        assert result["node_count"] == 5


# ---------------------------------------------------------------------------
# Tests: GraphRAG neighbor-context helpers and kg_chat graph augmentation
# ---------------------------------------------------------------------------


class TestGraphRagHelpers:
    """Pure-function helpers for GraphRAG neighbor-context expansion."""

    def test_build_neighbor_context_out_and_in_edges(self) -> None:
        from arrow_lake._lake_kg import _build_neighbor_context

        verts = [
            {"id": "V1", "label": "系统", "properties": {"name": "ArrowLake"}},
            {"id": "V2", "label": "组件", "properties": {"name": "LanceDB"}},
            {"id": "V3", "label": "模型", "properties": {"name": "qwen"}},
        ]
        edges = [
            {"outV": "V1", "inV": "V2", "label": "contains"},  # outgoing
            {"outV": "V3", "inV": "V1", "label": "serves"},  # incoming
            {"outV": "V2", "inV": "V3", "label": "uses"},  # unrelated to anchor
        ]
        ctx = _build_neighbor_context(["ArrowLake", "缺失实体"], verts, edges)
        assert len(ctx) == 1
        assert ctx[0]["entity"] == "ArrowLake"
        rels = ctx[0]["relations"]
        assert any("contains" in r and "LanceDB" in r for r in rels)
        assert any("serves" in r and "qwen" in r for r in rels)
        assert not any("uses" in r for r in rels)

    def test_build_neighbor_context_caps_per_anchor(self) -> None:
        from arrow_lake._lake_kg import _build_neighbor_context

        verts = [{"id": f"V{i}", "label": "x", "properties": {"name": f"n{i}"}} for i in range(1, 9)]
        verts.insert(0, {"id": "V0", "label": "x", "properties": {"name": "anchor"}})
        edges = [{"outV": "V0", "inV": f"V{i}", "label": "r"} for i in range(1, 9)]
        ctx = _build_neighbor_context(["anchor"], verts, edges, max_per_anchor=3)
        assert len(ctx[0]["relations"]) == 3

    def test_ka_node_name_ka_and_hugegraph(self) -> None:
        from arrow_lake._lake_kg import _ka_node_name

        assert _ka_node_name({"name": "KA节点"}) == "KA节点"
        assert _ka_node_name({"id": "V2", "label": "组件", "properties": {"name": "LanceDB"}}) == "LanceDB"
        assert _ka_node_name({"label": "只label"}) == "只label"

    def test_augment_question_injects_and_truncates(self) -> None:
        from arrow_lake._lake_kg import _augment_question_with_graph

        out = _augment_question_with_graph("为什么?", [{"entity": "X", "relations": ["—[r]→ Y"]}])
        assert "知识图谱邻居上下文" in out and "【问题】" in out and "X" in out
        big = [{"entity": "E", "relations": ["—" + "z" * 50 + "→"]} for _ in range(5)]
        out2 = _augment_question_with_graph("q", big, max_chars=20)
        assert "已省略" in out2


class TestKgChatGraphContext:
    """kg_chat / _graph_neighbor_context with mocked extractor + HugeGraph client."""

    @pytest.fixture()
    def lake(self) -> _TestLake:
        return _TestLake(_make_config(enabled=True))

    @pytest.fixture(autouse=True)
    def _clear_snapshot_cache(self):
        from arrow_lake._lake_kg import _KG_SNAPSHOT_CACHE

        _KG_SNAPSHOT_CACHE.clear()
        yield
        _KG_SNAPSHOT_CACHE.clear()

    @staticmethod
    def _extractor(anchor_names: list[str]) -> MagicMock:
        ext = MagicMock()
        ext.search_ka = MagicMock(return_value=([{"name": n} for n in anchor_names], []))
        ext.chat_ka = MagicMock(
            return_value=SimpleNamespace(content="答案", additional_kwargs={})
        )
        return ext

    @pytest.mark.asyncio()
    async def test_snapshot_path_covers_anchors(self, lake: _TestLake) -> None:
        ext = self._extractor(["ArrowLake"])
        client = AsyncMock()
        client.get_graph_snapshot = AsyncMock(
            return_value=(
                [{"id": "V1", "label": "系统", "properties": {"name": "ArrowLake"}},
                 {"id": "V2", "label": "组件", "properties": {"name": "LanceDB"}}],
                [{"outV": "V1", "inV": "V2", "label": "contains"}],
            )
        )
        lake._components["kg_client"] = client
        ctx = await lake._graph_neighbor_context(ext, "ds", "q", 5)
        assert ctx and ctx[0]["entity"] == "ArrowLake"
        assert any("contains" in r for r in ctx[0]["relations"])
        client.find_vertices_by_property.assert_not_called()  # snapshot covered it

    @pytest.mark.asyncio()
    async def test_lookup_fallback_for_missed_anchors(self, lake: _TestLake) -> None:
        ext = self._extractor(["FarEntity"])
        client = AsyncMock()
        client.get_graph_snapshot = AsyncMock(
            return_value=([{"id": "V9", "label": "x", "properties": {"name": "other"}}], [])
        )
        client.find_vertices_by_property = AsyncMock(
            return_value=[{"id": "V100", "label": "概念", "properties": {"name": "FarEntity"}}]
        )
        client.traverser_kneighbor = AsyncMock(
            return_value=[{"id": "VN", "label": "组件", "properties": {"name": "Neighbor"}}]
        )
        lake._components["kg_client"] = client
        ctx = await lake._graph_neighbor_context(ext, "ds", "q", 5)
        assert ctx and ctx[0]["entity"] == "FarEntity"
        assert any("Neighbor" in r for r in ctx[0]["relations"])
        client.find_vertices_by_property.assert_awaited()

    @pytest.mark.asyncio()
    async def test_search_failure_returns_empty(self, lake: _TestLake) -> None:
        ext = MagicMock()
        ext.search_ka = MagicMock(side_effect=RuntimeError("boom"))
        ctx = await lake._graph_neighbor_context(ext, "ds", "q", 5)
        assert ctx == []

    @pytest.mark.asyncio()
    async def test_kg_chat_chat_ka_engine_returns_neighbor_context(self, lake: _TestLake) -> None:
        ext = self._extractor(["X"])
        lake._get_he_extractor_or_raise = lambda: ext  # type: ignore[assignment]
        client = AsyncMock()
        client.get_graph_snapshot = AsyncMock(
            return_value=(
                [{"id": "V1", "label": "x", "properties": {"name": "X"}},
                 {"id": "V2", "label": "y", "properties": {"name": "Y"}}],
                [{"outV": "V1", "inV": "V2", "label": "r"}],
            )
        )
        lake._components["kg_client"] = client
        result = await lake.kg_chat("ds", "q", engine="chat_ka")
        assert result["answer"] == "答案"
        assert result["neighbor_context"] and result["neighbor_context"][0]["entity"] == "X"
        sent_question = ext.chat_ka.call_args.args[1]
        assert "知识图谱邻居上下文" in sent_question  # augmented prompt carries graph block

    @pytest.mark.asyncio()
    async def test_kg_chat_chat_ka_graph_context_false_skips(self, lake: _TestLake) -> None:
        ext = MagicMock()
        ext.chat_ka = MagicMock(return_value=SimpleNamespace(content="A", additional_kwargs={}))
        lake._get_he_extractor_or_raise = lambda: ext  # type: ignore[assignment]
        result = await lake.kg_chat("ds", "q", graph_context=False, engine="chat_ka")
        assert result["neighbor_context"] == []
        ext.search_ka.assert_not_called()
        assert ext.chat_ka.call_args.args[1] == "q"  # question passed through unchanged

    @pytest.mark.asyncio()
    async def test_kg_chat_graphrag_engine_direct_llm(self, lake: _TestLake) -> None:
        ext = self._extractor(["X"])
        lake._get_he_extractor_or_raise = lambda: ext  # type: ignore[assignment]
        client = AsyncMock()
        client.get_graph_snapshot = AsyncMock(return_value=([], []))  # no neighbor ctx
        lake._components["kg_client"] = client
        provider = AsyncMock()
        provider.generate = AsyncMock(return_value=SimpleNamespace(content="graphrag答"))
        lake._components["kg_qa_provider"] = provider
        result = await lake.kg_chat("ds", "q", engine="graphrag")
        assert result["answer"] == "graphrag答"
        provider.generate.assert_awaited_once()
        ext.chat_ka.assert_not_called()  # graphrag path bypasses chat_ka
        msgs = provider.generate.call_args.args[0]
        assert msgs[0].role == "system"  # structured messages, isolation (#1 fix)
        assert any(m.role == "user" and "当前问题" in m.content for m in msgs)

    @pytest.mark.asyncio()
    async def test_kg_chat_graphrag_multi_turn_history(self, lake: _TestLake) -> None:
        ext = self._extractor(["X"])
        lake._get_he_extractor_or_raise = lambda: ext  # type: ignore[assignment]
        client = AsyncMock()
        client.get_graph_snapshot = AsyncMock(return_value=([], []))
        lake._components["kg_client"] = client
        provider = AsyncMock()
        provider.generate = AsyncMock(return_value=SimpleNamespace(content="ans"))
        lake._components["kg_qa_provider"] = provider
        await lake.kg_chat("ds", "q2", engine="graphrag", history=[{"q": "q1", "a": "a1"}])
        msgs = provider.generate.call_args.args[0]
        roles = [m.role for m in msgs]
        # system, context(user), history user, history assistant, current question(user)
        assert roles[:1] == ["system"]
        assert "user" in roles and "assistant" in roles  # history turns present

    @pytest.mark.asyncio()
    async def test_kg_chat_stream_yields_meta_then_deltas(self, lake: _TestLake) -> None:
        ext = self._extractor(["X"])
        lake._get_he_extractor_or_raise = lambda: ext  # type: ignore[assignment]
        client = AsyncMock()
        client.get_graph_snapshot = AsyncMock(return_value=([], []))
        lake._components["kg_client"] = client

        async def _gen(msgs):
            for t in ("Hel", "lo"):
                yield t

        provider = SimpleNamespace(generate_stream=_gen)
        lake._components["kg_qa_provider"] = provider
        out = [x async for x in lake.kg_chat_stream("ds", "q")]
        assert out[0][0] == "meta"
        assert out[0][1]["retrieval_count"] == 0 or isinstance(out[0][1]["retrieval_count"], int)
        deltas = [p for k, p in out if k == "delta"]
        assert "".join(deltas) == "Hello"
