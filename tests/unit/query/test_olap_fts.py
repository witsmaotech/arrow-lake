"""Tests for OlapSearchBridge.fts_search — DuckDB native FTS (v1.8.0 #12)."""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest
from arrow_lake.config import OlapConfig


class _FakeStorage:
    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def read_dataset(self, name: str) -> pa.Table:
        return self._table


def _bridge(table: pa.Table) -> Any:
    from arrow_lake.query.olap import OlapSearchBridge

    return OlapSearchBridge(
        _FakeStorage(table), config=OlapConfig(lance_scan_mode="pyarrow_fallback")
    )


class TestFtsSearch:
    """DuckDB native FTS (BM25) as an alternative to lance_fts."""

    @pytest.fixture
    def docs(self) -> pa.Table:
        return pa.table(
            {
                "id": ["d1", "d2", "d3"],
                "text_content": [
                    "the machine learning book",
                    "a recipe for pasta",
                    "deep learning advances",
                ],
            }
        )

    def test_fts_matches_relevant_docs(self, docs: pa.Table) -> None:
        res = _bridge(docs).fts_search("docs", "learning", top_k=3)
        ids = {r["id"] for r in res.table.to_pylist()}
        assert ids == {"d1", "d3"}  # "learning" in d1 + d3, not d2 (pasta)
        assert "score" in res.table.column_names

    def test_fts_no_match_returns_empty(self, docs: pa.Table) -> None:
        res = _bridge(docs).fts_search("docs", "nonexistentterm", top_k=3)
        assert res.row_count == 0

    def test_fts_requires_id_column(self) -> None:
        from arrow_lake.exceptions import QueryError

        table = pa.table({"text_content": ["no id here"]})
        with pytest.raises(QueryError, match="id"):
            _bridge(table).fts_search("docs", "query")

    def test_fts_top_k_limit(self, docs: pa.Table) -> None:
        res = _bridge(docs).fts_search("docs", "learning", top_k=1)
        assert res.row_count == 1
