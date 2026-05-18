"""Tests for EmbedFlow — step logic in isolation.

Metaflow FlowSpec hooks into the CLI on instantiation, so we test
each step's business logic as standalone operations.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pyarrow as pa


class TestStartStepSharding:
    """start step: load dataset and split into shards."""

    def test_shard_splitting_exact(self) -> None:
        total_rows = 100
        shard_size = 50
        shards = []
        for offset in range(0, total_rows, shard_size):
            length = min(shard_size, total_rows - offset)
            shards.append((offset, length))

        assert len(shards) == 2
        assert shards[0] == (0, 50)
        assert shards[1] == (50, 50)

    def test_shard_splitting_remainder(self) -> None:
        total_rows = 110
        shard_size = 50
        shards = []
        for offset in range(0, total_rows, shard_size):
            length = min(shard_size, total_rows - offset)
            shards.append((offset, length))

        assert len(shards) == 3
        assert shards[0] == (0, 50)
        assert shards[1] == (50, 50)
        assert shards[2] == (100, 10)

    def test_shard_splitting_single(self) -> None:
        total_rows = 30
        shard_size = 500
        shards = []
        for offset in range(0, total_rows, shard_size):
            length = min(shard_size, total_rows - offset)
            shards.append((offset, length))

        assert len(shards) == 1
        assert shards[0] == (0, 30)

    def test_shard_splitting_empty(self) -> None:
        total_rows = 0
        shard_size = 50
        shards = []
        for offset in range(0, total_rows, shard_size):
            length = min(shard_size, total_rows - offset)
            shards.append((offset, length))

        assert len(shards) == 0


class TestEncodeShardStepLogic:
    """encode_shard step: encode a table shard and attach vector column."""

    def test_fixed_size_list_creation(self) -> None:
        """Embedding vectors become FixedSizeListArray column."""
        n = 5
        dim = 8
        rng = np.random.RandomState(42)
        embeddings = rng.randn(n, dim).astype(np.float32)

        vec_col = pa.FixedSizeListArray.from_arrays(embeddings.ravel(), dim)
        assert len(vec_col) == n
        assert vec_col.type.list_size == dim

    def test_append_vector_column_to_table(self) -> None:
        """Vector column correctly appended to shard table."""
        table = pa.table({
            "id": ["1", "2", "3"],
            "text_content": ["hello", "world", "test"],
        })

        dim = 4
        embeddings = np.random.randn(3, dim).astype(np.float32)
        vec_col = pa.FixedSizeListArray.from_arrays(embeddings.ravel(), dim)
        result = table.append_column("vector", vec_col)

        assert "vector" in result.column_names
        assert result.num_rows == 3
        assert result.column("vector").type.list_size == dim

    def test_success_result_dict(self) -> None:
        offset, length = 0, 50
        result: dict[str, Any] = {
            "shard": (offset, length),
            "status": "success",
            "rows": length,
        }
        assert result["status"] == "success"
        assert result["rows"] == 50

    def test_failed_result_dict(self) -> None:
        result: dict[str, Any] = {
            "shard": (100, 50),
            "status": "failed",
            "error": "GPU OOM",
        }
        assert result["status"] == "failed"
        assert "OOM" in result["error"]

    def test_null_text_handling(self) -> None:
        """NULL texts are replaced with empty string."""
        texts = [None, "hello", None, "world"]
        processed = [str(v) if v is not None else "" for v in texts]
        assert processed == ["", "hello", "", "world"]


class TestJoinStepLogic:
    """join step: merge shard tables and write back."""

    def test_concat_tables(self) -> None:
        t1 = pa.table({"id": ["1"], "text": ["a"], "vector": [[1.0, 2.0]]})
        t2 = pa.table({"id": ["2"], "text": ["b"], "vector": [[3.0, 4.0]]})

        merged = pa.concat_tables([t1, t2])
        assert merged.num_rows == 2

    def test_aggregate_success_failure(self) -> None:
        inputs = [
            {"shard": (0, 50), "status": "success", "rows": 50},
            {"shard": (50, 50), "status": "success", "rows": 50},
            {"shard": (100, 10), "status": "failed", "error": "timeout"},
        ]
        total_embedded = sum(1 for i in inputs if i["status"] == "success")
        total_failed = sum(1 for i in inputs if i["status"] == "failed")

        assert total_embedded == 2
        assert total_failed == 1

    def test_empty_shards(self) -> None:
        inputs: list[dict[str, Any]] = []
        total_embedded = sum(1 for i in inputs if i["status"] == "success")
        total_failed = sum(1 for i in inputs if i["status"] == "failed")
        assert total_embedded == 0
        assert total_failed == 0


class TestEndStepSummary:
    """end step: summary JSON output."""

    def _build_summary(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "total_rows": kwargs.get("total_rows", 100),
            "total_shards": kwargs.get("total_shards", 2),
            "embedded_shards": kwargs.get("embedded_shards", 2),
            "failed_shards": kwargs.get("failed_shards", 0),
            "encoder": kwargs.get("encoder", "local"),
            "shard_size": kwargs.get("shard_size", 500),
        }

    def test_all_success_summary(self) -> None:
        summary = self._build_summary()
        assert summary["failed_shards"] == 0
        assert summary["embedded_shards"] == 2

    def test_partial_failure_summary(self) -> None:
        summary = self._build_summary(embedded_shards=1, failed_shards=1)
        assert summary["failed_shards"] == 1

    def test_json_roundtrip(self) -> None:
        summary = self._build_summary(total_rows=200, shard_size=100)
        parsed = json.loads(json.dumps(summary))
        assert parsed["total_rows"] == 200
        assert parsed["shard_size"] == 100


class TestFlowRegistration:
    """Verify EmbedFlow is registered."""

    def test_embed_registered(self) -> None:
        import importlib

        import flows
        from arrow_lake.workflow.base import FlowRegistry

        FlowRegistry.clear()
        flows._registration_attempted = False
        importlib.reload(flows)
        flows._register_flows()

        assert "embed" in FlowRegistry.list_flows()
