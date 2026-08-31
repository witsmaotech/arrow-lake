"""M19(四维 review):quality/llm_enrich.py 此前零测试引用。

产品接线(api/routers/quality.py 的 schema AI 标注端点)却无任何 mock
单测——LLM 耦合代码裸奔。本文件以 FakeLake + FakeProvider 钉住:
* label_column:逐行标注→新列写回、空文本行 ""、行数保护
* extract_fields:JSON 抽取→多列写回、非法 JSON 行容错、fields 空拒
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pytest

from arrow_lake.quality.llm_enrich import extract_fields, label_column


@dataclass
class _Resp:
    content: str


class FakeProvider:
    """generate() 按序回放脚本;记录 prompt 供断言。"""

    model = "fake-llm"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def generate(self, messages: Any) -> _Resp:
        self.prompts.append(messages[-1].content)
        return _Resp(self._replies.pop(0) if self._replies else "")


class FakeLake:
    def __init__(self, table: pa.Table) -> None:
        self._table = table
        self.added: list[tuple[str, pa.Table, str | None]] = []

    def read_dataset(self, name: str, table: str | None = None) -> pa.Table:
        return self._table

    def add_columns_table(
        self, name: str, data: pa.Table, table: str | None = None,
    ) -> None:
        self.added.append((name, data, table))


_TABLE = pa.table({"text": ["甲烷超标", "", "阀门泄漏"]})


def test_label_column_writes_new_column() -> None:
    lake = FakeLake(_TABLE)
    prov = FakeProvider(["高", "低"])  # 空文本行不发 LLM,2 replies 够
    out = asyncio.run(label_column(
        lake, "demo", "text", "severity", "分类:{text}",
        provider=prov, table="rows"))
    assert out["succeeded"] == 2 and out["failed"] == 1
    name, added, table = lake.added[0]
    assert (name, table) == ("demo", "rows")
    assert added.column_names == ["severity"]
    assert added.column("severity").to_pylist() == ["高", "", "低"]
    # 空文本行不产生 LLM 调用
    assert len(prov.prompts) == 2
    assert all("分类:" in p for p in prov.prompts)


def test_label_column_size_guard() -> None:
    lake = FakeLake(pa.table({"text": ["x"] * 10}))
    with pytest.raises(ValueError):
        asyncio.run(label_column(
            lake, "demo", "text", "c", "{text}",
            provider=FakeProvider([]), max_rows=3))


def test_extract_fields_parses_json_and_tolerates_bad_rows() -> None:
    table = pa.table({"text": ["甲烷超标", "", "阀门泄漏", "管道破损"]})
    lake = FakeLake(table)
    prov = FakeProvider([
        '{"severity": "高", "location": "东站"}',   # 正常 JSON
        '前缀噪音 {"severity": "低", "location": "西站"} 后缀',  # _extract_json 剥壳
        'not json at all',                          # 容错 → 空字段
    ])
    out = asyncio.run(extract_fields(
        lake, "demo", "text",
        [{"name": "severity", "type": "string", "description": "严重度"},
         {"name": "location", "type": "string", "description": "位置"}],
        provider=prov))
    # 4 行:2 成功 + 1 容错空 + 1 空文本(不发 LLM)
    assert out["succeeded"] == 2 and out["failed"] == 2
    _, added, _ = lake.added[0]
    assert added.column_names == ["severity", "location"]
    assert added.column("severity").to_pylist() == ["高", "", "低", ""]
    assert added.column("location").to_pylist() == ["东站", "", "西站", ""]


def test_extract_fields_empty_fields_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        asyncio.run(extract_fields(
            FakeLake(_TABLE), "demo", "text", [], provider=FakeProvider([])))
