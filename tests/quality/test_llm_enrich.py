"""Tests for the LLM enrichment service (data-prep · WS2).

label_column / extract_fields with an injected fake provider + fake lake,
so no real LLM is called. Covers batching, partial failure, max_rows cap,
JSON-fence parsing, and add_columns_table persistence.
"""

from __future__ import annotations

import asyncio

import pyarrow as pa
import pytest

from arrow_lake.quality.llm_enrich import extract_fields, label_column
from arrow_lake.rag.provider import LLMResponse


class FakeProvider:
    """Calls a responder(prompt) -> content string; counts generate() calls."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self.calls = 0

    async def generate(self, messages):  # noqa: D401
        self.calls += 1
        content = self._responder(messages[-1].content)
        return LLMResponse(content=content, model="fake", provider="fake")


class FlakyProvider(FakeProvider):
    """Raises on the Nth call (1-indexed)."""

    def __init__(self, fail_on: int) -> None:
        super().__init__(lambda _p: "正向")
        self._fail_on = fail_on

    async def generate(self, messages):
        self.calls += 1
        if self.calls == self._fail_on:
            raise RuntimeError("boom")
        return LLMResponse(content="正向", model="fake", provider="fake")


class FakeLake:
    def __init__(self, table: pa.Table) -> None:
        self._table = table
        self.added: tuple[str, pa.Table] | None = None

    def read_dataset(self, name: str, *, table: str | None = None) -> pa.Table:
        # production passes table= for container datasets (DR14); accept it.
        return self._table

    def add_columns_table(self, name: str, columns: pa.Table, *, table: str | None = None) -> None:
        self.added = (name, columns)


def _table() -> pa.Table:
    return pa.table(
        {
            "id": [1, 2, 3],
            "text_content": ["好产品，强烈推荐！", "太差了，直接退货。", "一般般，凑合用。"],
        }
    )


def test_label_column_persists_new_column() -> None:
    lake = FakeLake(_table())

    def sentiment(prompt: str) -> str:
        if "推荐" in prompt or "好" in prompt:
            return "正向"
        if "差" in prompt or "退货" in prompt:
            return "负向"
        return "中性"

    prov = FakeProvider(sentiment)
    report = asyncio.run(
        label_column(
            lake, "ds", "text_content", "sentiment", "判断情感：{text}",
            provider=prov, max_rows=100,
        )
    )
    assert report["operation"] == "llm_label"
    assert report["input_rows"] == 3
    assert report["succeeded"] == 3
    assert report["failed"] == 0
    assert report["new_columns"] == ["sentiment"]

    name, cols = lake.added  # type: ignore[misc]
    assert name == "ds"
    assert cols.column_names == ["sentiment"]
    assert cols.num_rows == 3
    labels = cols.column("sentiment").to_pylist()
    assert labels == ["正向", "负向", "中性"]


def test_label_column_missing_column_raises() -> None:
    lake = FakeLake(_table())
    with pytest.raises(ValueError):
        asyncio.run(
            label_column(lake, "ds", "nope", "x", "{text}", provider=FakeProvider(lambda _p: "x"))
        )


def test_label_column_max_rows_exceeds_raises() -> None:
    lake = FakeLake(_table())  # 3 rows
    with pytest.raises(ValueError):
        asyncio.run(
            label_column(
                lake, "ds", "text_content", "s", "{text}",
                provider=FakeProvider(lambda _p: "x"), max_rows=2,
            )
        )


def test_label_column_partial_failure() -> None:
    """One row fails → counted as failed, others still persisted (concurrency=1 for determinism)."""
    lake = FakeLake(_table())
    report = asyncio.run(
        label_column(
            lake, "ds", "text_content", "s", "{text}",
            provider=FlakyProvider(fail_on=2), max_rows=100, concurrency=1,
        )
    )
    assert report["failed"] == 1
    assert report["succeeded"] == 2
    assert lake.added is not None
    assert lake.added[1].num_rows == 3  # full-length column, failed row = ""


def test_extract_fields_persists_multiple_columns() -> None:
    lake = FakeLake(_table())

    def responder(prompt: str) -> str:
        if "好产品" in prompt:
            return '{"日期":"2024-01-01","金额":"100"}'
        if "太差" in prompt:
            return '```json\n{"日期":"2024-02-02","金额":"0"}\n```'
        return "无法判断"  # non-JSON → falls back to empties

    prov = FakeProvider(responder)
    report = asyncio.run(
        extract_fields(
            lake, "ds", "text_content",
            [{"name": "日期", "type": "string"}, {"name": "金额", "type": "number"}],
            provider=prov, max_rows=100,
        )
    )
    assert report["operation"] == "extract"
    assert report["new_columns"] == ["日期", "金额"]

    _name, cols = lake.added  # type: ignore[misc]
    assert cols.column_names == ["日期", "金额"]
    assert cols.num_rows == 3
    dates = cols.column("日期").to_pylist()
    assert dates[0] == "2024-01-01"
    assert dates[1] == "2024-02-02"
    assert dates[2] == ""  # non-JSON response → empty


def test_extract_fields_empty_fields_raises() -> None:
    lake = FakeLake(_table())
    with pytest.raises(ValueError):
        asyncio.run(
            extract_fields(lake, "ds", "text_content", [], provider=FakeProvider(lambda _p: "{}"))
        )
