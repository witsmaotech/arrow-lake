"""W2.1 — 相关性回路编排(v1.11.4 MS5 F5.2)。

直接测 ``quality/relevance.py`` 三件套(mock LS 全链 / score 由
compute_relevance 单测覆盖,此处验证编排):
* ``classify_relevance``:标签解析;脏输出 → None(宁缺勿错);
* ``dispatch_relevance``:懒绑定(404 重建/在用复用)、choices 预标注、
  脱敏、row_id 内容 hash、空文本跳过;
* ``llm_only_relevance``:LLM 判定直接落 ADL(annotator=llm:model),
  ADL adl_id 幂等(重跑零新行)。
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.annotation.dispatch import LSClientError
from arrow_lake.quality.dimensions import compute_relevance
from arrow_lake.quality.relevance import (
    RELEVANCE_LS_CONFIG,
    classify_relevance,
    dispatch_relevance,
    llm_only_relevance,
    relevance_project_name,
)
from arrow_lake.rag.provider import LLMResponse

ROWS = [
    {"text": "阀门泄漏应急处置流程"},
    {"text": "管道压力异常升高"},
    {"text": ""},  # 空文本 → 跳过
    {"text": "厨房燃气灶打不着火"},
]


class FakeProvider:
    model = "test-model"

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    async def generate(self, messages) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=self._content, model=self.model, provider="fake")


class FakeLS:
    def __init__(self, existing: int | None = None) -> None:
        self.existing = existing
        self.created: list[tuple[str, str]] = []
        self.imported: list[dict] = []

    def get_project(self, pid: int) -> dict:
        if self.existing == pid:
            return {"id": pid}
        raise LSClientError(f"no project {pid}", status=404)

    def create_project(self, title: str, cfg: str) -> dict:
        self.created.append((title, cfg))
        return {"id": 99}

    def import_tasks(self, pid: int, tasks: list[dict]) -> dict:
        self.imported.extend(tasks)
        return {"task_ids": list(range(len(tasks)))}


class RecLake:
    """llm_only 路径的记录 lake(无 ADL 表 → 全新写)。"""

    def __init__(self) -> None:
        self.written: list[tuple[str, pa.Table]] = []

    def read_dataset(self, name: str):
        raise KeyError(name)

    def _get_storage(self) -> RecLake:
        return self

    def dataset_exists(self, name: str) -> bool:
        return False

    def create_dataset(self, name: str, table: pa.Table) -> None:
        self.written.append((name, table))

    def append_dataset(self, name: str, table: pa.Table) -> None:
        self.written.append((name, table))


# --- classify ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_parses_label_with_noise() -> None:
    assert await classify_relevance(FakeProvider("答案:高相关。"), "t") == "高相关"
    assert await classify_relevance(FakeProvider("间接相关"), "t") == "间接相关"


@pytest.mark.asyncio
async def test_classify_garbage_returns_none() -> None:
    assert await classify_relevance(FakeProvider("我觉得还行吧"), "t") is None


# --- dispatch(LS 全链) --------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_full_chain_creates_and_preannotates() -> None:
    ls = FakeLS()
    provider = FakeProvider("高相关")
    bound: list[int] = []
    out = await dispatch_relevance(
        ls_client=ls, project_title="alerts__relevance", rows=ROWS,
        text_column="text", provider=provider, ls_project_id=None,
        bind_ls_project=bound.append,
    )
    assert ls.created == [("alerts__relevance", RELEVANCE_LS_CONFIG)]
    assert bound == [99]
    assert out["dispatched"] == 3 and out["skipped"] == 1
    assert out["preannotated"] == 3
    # 任务结构:choices 预标注 + row_id 内容 hash 前缀 + strategy
    task = ls.imported[0]
    assert task["data"]["strategy"] == "relevance"
    assert task["data"]["row_id"].startswith("h")
    pred = task["predictions"][0]["result"][0]
    assert pred["type"] == "choices" and pred["value"]["choices"] == ["高相关"]


@pytest.mark.asyncio
async def test_dispatch_reuses_bound_project() -> None:
    ls = FakeLS(existing=7)
    provider = FakeProvider("不相关")
    out = await dispatch_relevance(
        ls_client=ls, project_title="x", rows=ROWS[:1], text_column="text",
        provider=provider, ls_project_id=7,
    )
    assert ls.created == [] and out["ls_project_id"] == 7


@pytest.mark.asyncio
async def test_dispatch_without_provider_empty_predictions() -> None:
    ls = FakeLS()
    out = await dispatch_relevance(
        ls_client=ls, project_title="x", rows=ROWS[:1], text_column="text",
        provider=None, ls_project_id=None,
    )
    assert out["preannotated"] == 0
    assert ls.imported[0]["predictions"][0]["result"] == []


# --- llm_only(直评写 ADL) ----------------------------------------------------

@pytest.mark.asyncio
async def test_llm_only_writes_adl_and_is_idempotent() -> None:
    lake = RecLake()
    provider = FakeProvider("高相关")
    out = await llm_only_relevance(
        lake=lake, dataset="alerts", rows=ROWS[:2], text_column="text",
        provider=provider,
    )
    assert out["assessed"] == 2 and out["adl_rows_written"] == 2
    assert out["annotator"] == "llm:test-model"
    name, table = lake.written[0]
    assert name == "alerts_adl"
    # 写出的行就是 relevance 行 → compute_relevance 可聚合
    res = compute_relevance(table)
    assert res.score == 100.0 and res.source == "llm"
    # 幂等:同判定重跑 → adl_id 去重零新行
    lake2 = RecLake()
    # 模拟已有表:把首轮写入的表作为现存状态喂回
    class ExistingLake(RecLake):
        def __init__(self, existing: pa.Table):
            super().__init__()
            self._existing = existing

        def read_dataset(self, name: str):
            if name == "alerts_adl":
                return self._existing
            raise KeyError(name)

        def dataset_exists(self, name: str) -> bool:
            return name == "alerts_adl"

        def append_dataset(self, name: str, table: pa.Table) -> None:
            self.written.append((name, table))

    lake2 = ExistingLake(table)
    out2 = await llm_only_relevance(
        lake=lake2, dataset="alerts", rows=ROWS[:2], text_column="text",
        provider=FakeProvider("高相关"),
    )
    assert out2["adl_rows_written"] == 0  # 同内容判定 → 幂等


@pytest.mark.asyncio
async def test_llm_only_failed_rows_skipped() -> None:
    lake = RecLake()
    out = await llm_only_relevance(
        lake=lake, dataset="alerts", rows=ROWS[:1], text_column="text",
        provider=FakeProvider("不合法输出"),
    )
    assert out["assessed"] == 0 and out["failed"] == 1
    assert lake.written == []


def test_project_name_convention() -> None:
    assert relevance_project_name("alerts") == "alerts__relevance"
