"""Tests for _LakeRAGMixin facade methods — rag_query, rag_query_stream, rag_extract, session management."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake._lake_rag import _LakeRAGMixin
from arrow_lake.config import ArrowLakeConfig, HugeGraphConfig, LLMConfig, RAGConfig


def _make_config(*, kg_enabled: bool = False) -> ArrowLakeConfig:
    cfg = ArrowLakeConfig()
    cfg.rag = RAGConfig()
    cfg.llm = LLMConfig()
    cfg.hugegraph = HugeGraphConfig(enabled=kg_enabled)
    return cfg


class _TestLake(_LakeRAGMixin):
    """Thin wrapper to expose _LakeRAGMixin without full Lake.__init__."""

    def __init__(self, config: ArrowLakeConfig | None = None) -> None:
        self._config = config or _make_config()
        self._components: dict[str, object] = {}

    def _get_component(self, key: str, factory) -> object:
        if key not in self._components:
            self._components[key] = factory()
        return self._components[key]

    def text_search(self, dataset_name: str, query: str, *, top_k: int = 5) -> MagicMock:
        """Mock text_search used by _rag_retriever."""
        mock_result = MagicMock()
        mock_result.table = pa.table({
            "text": ["result"],
            "row_id": ["r1"],
            "_score": [0.9],
        })
        return mock_result


@pytest.fixture()
def lake() -> _TestLake:
    return _TestLake()


def _mock_rag_pipeline() -> MagicMock:
    """Create a fully mocked RAGPipeline."""
    pipeline = MagicMock()
    pipeline.query = AsyncMock(return_value=MagicMock(
        answer="test answer",
        citations=(),
        retrieval_count=1,
        context_tokens=10,
        llm_usage={"total_tokens": 15},
        latency_ms=50.0,
    ))
    pipeline.query_stream = AsyncMock()
    pipeline.extract_entities = AsyncMock(return_value=MagicMock(
        answer="entities",
        citations=(),
        retrieval_count=1,
        context_tokens=5,
        llm_usage={"total_tokens": 10},
        latency_ms=30.0,
    ))
    pipeline.batch_query = AsyncMock(return_value=[])
    pipeline._session_store = MagicMock()
    pipeline._session_store.get_history.return_value = [{"turn_id": 1}]
    pipeline._session_store.save_feedback = MagicMock()
    pipeline._session_store.get_feedback.return_value = [{"rating": "positive"}]
    pipeline._session_store.cleanup_expired.return_value = 3
    return pipeline


# ---------------------------------------------------------------------------
# _get_rag_pipeline — patch source modules for local imports
# ---------------------------------------------------------------------------


class TestGetRAGPipeline:
    """Test _get_rag_pipeline factory with KG disabled and enabled."""

    @patch("arrow_lake.rag.provider.create_llm_provider")
    def test_creates_base_pipeline_when_kg_disabled(self, mock_provider_fn: MagicMock, lake: _TestLake) -> None:
        mock_provider = MagicMock()
        mock_provider_fn.return_value = mock_provider

        with patch("arrow_lake._lake_rag.RAGPipeline") as mock_pipeline_cls:
            mock_pipeline_cls.return_value = MagicMock()

            lake._get_rag_pipeline()

            mock_pipeline_cls.assert_called_once()
            call_kwargs = mock_pipeline_cls.call_args.kwargs
            assert call_kwargs.get("llm_provider") is mock_provider

    @patch("arrow_lake.rag.provider.create_llm_provider")
    def test_creates_base_pipeline_on_graph_rag_failure(self, mock_provider_fn: MagicMock) -> None:
        cfg = _make_config(kg_enabled=True)
        lake_kg = _TestLake(config=cfg)

        mock_provider = MagicMock()
        mock_provider_fn.return_value = mock_provider

        with patch("arrow_lake._lake_rag.RAGPipeline") as mock_pipeline_cls:
            mock_pipeline_cls.return_value = MagicMock()

            with patch.object(lake_kg, "_create_graph_rag_pipeline", side_effect=RuntimeError("KG unavailable")):
                lake_kg._get_rag_pipeline()

            mock_pipeline_cls.assert_called()

    @patch("arrow_lake.rag.provider.create_llm_provider")
    def test_caches_pipeline(self, mock_provider_fn: MagicMock, lake: _TestLake) -> None:
        mock_provider_fn.return_value = MagicMock()

        with patch("arrow_lake._lake_rag.RAGPipeline") as mock_pipeline_cls:
            mock_pipeline_cls.return_value = MagicMock()

            p1 = lake._get_rag_pipeline()
            p2 = lake._get_rag_pipeline()
            assert p1 is p2


# ---------------------------------------------------------------------------
# rag_query
# ---------------------------------------------------------------------------


class TestRagQuery:
    """Test rag_query delegates to pipeline."""

    @pytest.mark.asyncio()
    async def test_rag_query_returns_response(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False):
            result = await lake.rag_query("What is AI?", "test_ds")

        mock_pipeline.query.assert_awaited_once()
        assert result.answer == "test answer"

    @pytest.mark.asyncio()
    async def test_rag_query_with_all_params(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False):
            await lake.rag_query(
                "What is X?",
                "test_ds",
                top_k=10,
                strategy="vector",
                template_name="custom_qa",
                session_id="sess-1",
            )

        call_kwargs = mock_pipeline.query.call_args.kwargs
        assert call_kwargs["top_k"] == 10
        assert call_kwargs["strategy"] == "vector"
        assert call_kwargs["template_name"] == "custom_qa"
        assert call_kwargs["session_id"] == "sess-1"

    @pytest.mark.asyncio()
    async def test_rag_query_records_metrics(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True), \
             patch("arrow_lake.core.metrics.query_total") as mock_total, \
             patch("arrow_lake.core.metrics.query_latency_seconds") as mock_latency:

            await lake.rag_query("Q?", "ds")

            mock_total.labels.assert_called_with(query_type="rag_query")
            mock_latency.labels.assert_called_with(query_type="rag_query")


# ---------------------------------------------------------------------------
# rag_query_stream
# ---------------------------------------------------------------------------


class TestRagQueryStream:
    """Test rag_query_stream yields chunks."""

    @pytest.mark.asyncio()
    async def test_rag_query_stream_yields_chunks(self, lake: _TestLake) -> None:
        mock_pipeline = MagicMock()

        async def _fake_stream(**kwargs):
            yield "chunk1"
            yield "chunk2"

        mock_pipeline.query_stream = _fake_stream
        lake._components["rag_pipeline"] = mock_pipeline

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False):
            chunks = []
            async for chunk in lake.rag_query_stream("Q?", "ds"):
                chunks.append(chunk)

        assert chunks == ["chunk1", "chunk2"]

    @pytest.mark.asyncio()
    async def test_rag_query_stream_records_metrics(self, lake: _TestLake) -> None:
        mock_pipeline = MagicMock()

        async def _fake_stream(**kwargs):
            yield "x"

        mock_pipeline.query_stream = _fake_stream
        lake._components["rag_pipeline"] = mock_pipeline

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True), \
             patch("arrow_lake.core.metrics.query_total") as mock_total:

            chunks = []
            async for chunk in lake.rag_query_stream("Q?", "ds"):
                chunks.append(chunk)

            mock_total.labels.assert_called_with(query_type="rag_query_stream")


# ---------------------------------------------------------------------------
# rag_extract
# ---------------------------------------------------------------------------


class TestRagExtract:
    """Test rag_extract delegates to pipeline.extract_entities."""

    @pytest.mark.asyncio()
    async def test_rag_extract_returns_response(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        result = await lake.rag_extract("ds")

        mock_pipeline.extract_entities.assert_awaited_once()
        assert result.answer == "entities"

    @pytest.mark.asyncio()
    async def test_rag_extract_with_params(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        await lake.rag_extract(
            "ds",
            text_column="body",
            top_k=5,
            template_name="custom_extract",
        )

        call_kwargs = mock_pipeline.extract_entities.call_args.kwargs
        assert call_kwargs["text_column"] == "body"
        assert call_kwargs["top_k"] == 5
        assert call_kwargs["template_name"] == "custom_extract"


# ---------------------------------------------------------------------------
# rag_batch_query
# ---------------------------------------------------------------------------


class TestRagBatchQuery:
    """Test rag_batch_query delegates to pipeline.batch_query."""

    @pytest.mark.asyncio()
    async def test_batch_query_delegates(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        await lake.rag_batch_query(
            ["Q1?", "Q2?"],
            "ds",
            top_k=3,
            strategy="hybrid",
            concurrency=3,
        )

        mock_pipeline.batch_query.assert_awaited_once_with(
            questions=["Q1?", "Q2?"],
            dataset_name="ds",
            top_k=3,
            strategy="hybrid",
            concurrency=3,
        )


# ---------------------------------------------------------------------------
# rag_get_history / rag_feedback / rag_get_feedback / rag_cleanup
# ---------------------------------------------------------------------------


class TestRagSessionManagement:
    """Test session history, feedback, and cleanup methods."""

    def test_rag_get_history_returns_history(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        result = lake.rag_get_history("sess-1")
        mock_pipeline._session_store.get_history.assert_called_once_with("sess-1")
        assert result == [{"turn_id": 1}]

    def test_rag_get_history_returns_empty_when_no_store(self, lake: _TestLake) -> None:
        mock_pipeline = MagicMock()
        mock_pipeline._session_store = None
        lake._components["rag_pipeline"] = mock_pipeline

        result = lake.rag_get_history("sess-1")
        assert result == []

    def test_rag_feedback_saves(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        lake.rag_feedback("sess-1", 1, "positive", comment="great")
        mock_pipeline._session_store.save_feedback.assert_called_once()

    def test_rag_feedback_with_flagged_citations(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        lake.rag_feedback("sess-1", 1, "negative", flagged_citations=[0, 2], comment="bad refs")
        call_kwargs = mock_pipeline._session_store.save_feedback.call_args
        assert call_kwargs.kwargs.get("flagged_citation_indices") == (0, 2)

    def test_rag_feedback_noop_when_no_store(self, lake: _TestLake) -> None:
        mock_pipeline = MagicMock()
        mock_pipeline._session_store = None
        lake._components["rag_pipeline"] = mock_pipeline

        # Should not raise
        lake.rag_feedback("sess-1", 1, "neutral")

    def test_rag_get_feedback_returns_feedback(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        result = lake.rag_get_feedback("sess-1")
        assert result == [{"rating": "positive"}]

    def test_rag_get_feedback_empty_when_no_store(self, lake: _TestLake) -> None:
        mock_pipeline = MagicMock()
        mock_pipeline._session_store = None
        lake._components["rag_pipeline"] = mock_pipeline

        result = lake.rag_get_feedback("sess-1")
        assert result == []

    def test_rag_cleanup_expired_sessions(self, lake: _TestLake) -> None:
        mock_pipeline = _mock_rag_pipeline()
        lake._components["rag_pipeline"] = mock_pipeline

        result = lake.rag_cleanup_expired_sessions()
        assert result == 3
        mock_pipeline._session_store.cleanup_expired.assert_called_once()

    def test_rag_cleanup_returns_zero_when_no_store(self, lake: _TestLake) -> None:
        mock_pipeline = MagicMock()
        mock_pipeline._session_store = None
        lake._components["rag_pipeline"] = mock_pipeline

        result = lake.rag_cleanup_expired_sessions()
        assert result == 0


# ---------------------------------------------------------------------------
# _create_graph_rag_pipeline
# ---------------------------------------------------------------------------


class TestCreateGraphRAGPipeline:
    """Test _create_graph_rag_pipeline creates GraphRAGPipeline."""

    def test_creates_graph_rag_pipeline(self) -> None:
        cfg = _make_config(kg_enabled=True)
        lake = _TestLake(config=cfg)

        with patch("arrow_lake.knowledge_graph.client.HugeGraphClient"), \
             patch("arrow_lake.knowledge_graph.extractor.EntityExtractor"), \
             patch("arrow_lake.knowledge_graph.retriever.KGRetriever"), \
             patch("arrow_lake.rag.graph_rag.GraphRAGPipeline") as mock_pipeline_cls:

            mock_pipeline_cls.return_value = MagicMock()
            lake._create_graph_rag_pipeline(MagicMock())

            mock_pipeline_cls.assert_called_once()

    def test_propagates_client_errors(self) -> None:
        cfg = _make_config(kg_enabled=True)
        lake = _TestLake(config=cfg)

        with patch("arrow_lake.knowledge_graph.client.HugeGraphClient", side_effect=ConnectionError("no KG")):
            with pytest.raises(ConnectionError):
                lake._create_graph_rag_pipeline(MagicMock())
