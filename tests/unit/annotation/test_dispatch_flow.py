"""W2.4 — annotation/dispatch.run_dispatch:采样→脱敏→预标注→LS import 全链。

纯流程函数,依赖全注入(LSClient / extractor / bind 回调),mock 即全链
e2e;真 LLM 与真 LS 只在 W5 live 验证出现。契约(设计 §1/§5/§8):

* 顺序固定:采样 → 脱敏 → HE 抽取(**脱敏文本上**,span 自洽)→ 静态
  prediction → LS import(分批);
* LS 懒绑定(W1.4 红线:创建项目时不调 LS):ls_project_id 缺 → create
  + 回写;已绑定 → get 校验,404 → 重建;
* 单行 HE 失败容错(空 prediction 仍派发——预测是建议非强制,S4);
* LS import 失败不吞(LSClientError 上抛,router 翻 502)。
"""

from __future__ import annotations

from typing import Any

import pytest
from arrow_lake.annotation.dispatch import (
    DispatchOutcome,
    LSClientError,
    run_dispatch,
    stable_row_id,
)
from arrow_lake.annotation.sampler import SampleBudget
from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)

CFG = "<View/>"
ROWS = [
    {"i": 0, "text": "调压站 A 压力异常"},
    {"i": 1, "text": "凤凰花园小区发生燃气泄漏事故"},
    {"i": 2, "text": "阀门 B 定期巡检完成"},
]


class FakeLS:
    """记录调用的 LSClient 替身;existing 控制已绑定 project 是否存活。"""

    def __init__(self, *, existing: bool = True, fail_import: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.existing = existing
        self.fail_import = fail_import
        self.created: list[str] = []
        self.imported: list[list[dict]] = []

    def create_project(self, title: str, labeling_config: str) -> dict:
        self.calls.append(("create", title))
        self.created.append(title)
        return {"id": 99, "title": title}

    def get_project(self, project_id: int) -> dict:
        self.calls.append(("get", project_id))
        if not self.existing:
            raise LSClientError(
                f"LS GET /api/projects/{project_id} → 404: gone", status=404)
        return {"id": project_id}

    def import_tasks(self, project_id: int, tasks: list[dict]) -> dict:
        if self.fail_import:
            raise LSClientError("LS POST import → 502: boom")
        self.calls.append(("import", project_id))
        self.imported.append(tasks)
        return {"task_ids": list(range(len(tasks)))}


class FakeExtractor:
    """async extract;可注入失败行号。"""

    def __init__(self, fail_rows: set[int] | None = None) -> None:
        self.seen: list[str] = []
        self.fail_rows = fail_rows or set()

    async def extract(self, text: str, **kwargs: Any) -> ExtractionResult:
        self.seen.append(text)
        if len(self.seen) - 1 in self.fail_rows:
            raise RuntimeError("LLM down")
        if "燃气泄漏" in text:
            return ExtractionResult(
                entities=(ExtractedEntity("燃气泄漏事故", "事故"),),
                relations=(ExtractedRelation("调压站", "燃气泄漏事故", "导致"),),
                raw_text=text,
            )
        return ExtractionResult(entities=(), relations=(), raw_text=text)


def _run(ls: FakeLS, extractor: FakeExtractor, **overrides: Any):
    kwargs: dict[str, Any] = dict(
        project="p1", dataset="ds", labeling_config=CFG,
        ls_project_id=None, rows=ROWS, text_column="text", total=3,
        budget=SampleBudget(),
        quality_scores={
            stable_row_id(ROWS[i]["text"], i): v
            for i, v in enumerate([0.9, 0.1, 0.5])
        },
        embeddings=None, dead_row_ids=None, committee=None,
        generalize_rules=(), entity_names=(), hmac_key=b"k",
        ls_client=ls, extractor=extractor, bind_ls_project=None,
        import_batch_size=50,
    )
    kwargs.update(overrides)
    import asyncio

    return asyncio.run(run_dispatch(**kwargs))


class TestHappyPath:
    def test_full_chain_dispatches_all_rows(self):
        ls, ex = FakeLS(), FakeExtractor()
        bound: list[tuple[str, int]] = []
        outcome = _run(ls, ex, bind_ls_project=lambda n, i: bound.append((n, i)))
        assert isinstance(outcome, DispatchOutcome)
        assert outcome.dispatched == 3
        assert outcome.strategies["uncertainty"] == 3
        assert ls.created == ["p1"]                      # 懒创建
        assert bound == [("p1", 99)]                     # 回写注册表
        assert len(ls.imported[0]) == 3

    def test_task_data_carries_row_id_strategy_masked_text(self):
        ls, ex = FakeLS(), FakeExtractor()
        _run(ls, ex)
        task = ls.imported[0][0]
        assert {"row_id", "strategy", "text"} <= set(task["data"])

    def test_prediction_from_he_on_masked_text(self):
        ls, ex = FakeLS(), FakeExtractor()
        _run(ls, ex, entity_names=["凤凰花园小区"])
        task = next(
            t for t in ls.imported[0] if "凤凰花园小区" not in t["data"]["text"]
        )
        # 实体在脱敏文本上抽取 → span 可定位
        assert task["predictions"][0]["model_version"] == "hyper-extract"
        regions = [r for r in task["predictions"][0]["result"] if r["type"] == "labels"]
        assert regions and regions[0]["from_name"] == "events"

    def test_masking_applied_to_task_text(self):
        ls, ex = FakeLS(), FakeExtractor()
        _run(ls, ex, entity_names=["凤凰花园小区"])
        texts = [t["data"]["text"] for t in ls.imported[0]]
        assert all("凤凰花园小区" not in t for t in texts)
        assert any(t.startswith("凤_") for t in texts)   # 首字假名


class TestLSBinding:
    def test_existing_binding_reused_no_create(self):
        ls, ex = FakeLS(existing=True), FakeExtractor()
        _run(ls, ex, ls_project_id=7)
        assert ls.created == []
        assert ("get", 7) in ls.calls
        assert all(c == ("import", 7) for c in ls.calls if c[0] == "import")

    def test_dead_binding_recreated(self):
        ls, ex = FakeLS(existing=False), FakeExtractor()
        bound: list[int] = []
        outcome = _run(
            ls, ex, ls_project_id=7,
            bind_ls_project=lambda n, i: bound.append(i),
        )
        assert ls.created == ["p1"]          # 404 → 重建
        assert bound == [99]
        assert outcome.ls_project_id == 99

    def test_import_failure_propagates(self):
        ls, ex = FakeLS(fail_import=True), FakeExtractor()
        with pytest.raises(LSClientError, match="502"):
            _run(ls, ex)


class TestRowTolerance:
    def test_empty_text_rows_skipped(self):
        rows = [*ROWS, {"i": 3, "text": "  "}]
        ls, ex = FakeLS(), FakeExtractor()
        outcome = _run(ls, ex, rows=rows, total=4)
        assert outcome.dispatched == 3
        assert outcome.skipped == 1

    def test_extractor_failure_yields_empty_prediction(self):
        ls, ex = FakeLS(), FakeExtractor(fail_rows={0, 1, 2})
        outcome = _run(ls, ex)
        assert outcome.dispatched == 3     # HE 挂 → 空 prediction 仍派发
        for task in ls.imported[0]:
            assert task["predictions"][0]["result"] == []

    def test_missing_text_column_skips_all(self):
        ls, ex = FakeLS(), FakeExtractor()
        outcome = _run(ls, ex, rows=[{"i": 0, "body": "no text here"}])
        assert outcome.dispatched == 0
        assert outcome.skipped == 1

    def test_import_batching(self):
        ls, ex = FakeLS(), FakeExtractor()
        outcome = _run(ls, ex, total=3, import_batch_size=2)
        assert outcome.dispatched == 3
        assert [len(b) for b in ls.imported] == [2, 1]
