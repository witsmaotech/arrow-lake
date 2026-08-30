"""W4.1 — release/corpus.py 语料四形态(v1.11.4 MS5 F5.6,S7)。

红线:**全部经 masking 出域**(与 MS4 脱敏前置同源 L2/L3);落点
``{base_dir}/{release-tag}/{form}.jsonl``(D4,持久卷不进 git)。四形态
一次交付;decisions 数据面空(MS3 无状态)→ ③RLHF 空导出+提示(设计
风险表口径,不阻塞其余形态)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.annotation.adl import ADL_SCHEMA
from arrow_lake.release.corpus import (
    build_golden_records,
    build_pretrain_records,
    build_rlhf_records,
    build_sft_records,
    write_corpus,
)

PHONE = "电话13812345678发生泄漏"
RULES = ((r"1[3-9]\d{9}", "[手机号]"),)  # L2 泛化规则(脱敏=配置驱动)


def _adl_row(row: str, annotator: str, *, version=1, status="approved",
             scenario="s1", objects=None, rules=("r1",), ground_llm=False):
    return {
        "adl_id": f"{row}-{annotator}-v{version}", "source_dataset": "alerts",
        "source_row_id": row, "objects": objects or [],
        "events": [], "rules_applied": list(rules), "scenario": scenario,
        "relations": [], "annotator_id": annotator,
        "annotated_at": "2026-08-30T00:00:00+00:00",
        "review_status": status, "reviewer_id": "", "batch_id": "b",
        "adl_version": version,
    }


def _adl(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=ADL_SCHEMA)


def _source(n: int = 2) -> list[dict]:
    return [{"text": f"阀门泄漏 {PHONE} 案例{i}", "row_id": f"h{i}"}
            for i in range(n)]


# --- ① SFT -------------------------------------------------------------------

def test_sft_structure_and_masking() -> None:
    span = [{"label": "阀门", "start": 0, "end": 2}]
    adl = _adl([_adl_row("h0", "ann1", objects=span, scenario="泄漏处置")])
    out = build_sft_records(
        rows=_source(1), adl=adl, system_prompt="本体:阀门;规则:r1",
        text_column="text", generalize_rules=RULES,
    )
    assert len(out) == 1
    rec = out[0]
    assert rec["system"].startswith("本体")
    # 脱敏:手机号不得原样出域
    assert "13812345678" not in rec["instruction"]
    assert "阀门泄漏" in rec["instruction"]  # 非敏感内容保留
    assert rec["output"]["objects"][0]["label"] == "阀门"
    assert rec["output"]["scenario"] == "泄漏处置"
    assert rec["output"]["rules_applied"] == ["r1"]


def test_sft_latest_human_annotation_wins() -> None:
    adl = _adl([
        _adl_row("h0", "ann1", version=1, scenario="旧判定"),
        _adl_row("h0", "ann1", version=2, scenario="新判定"),   # 重标注
        _adl_row("h0", "llm:qwen", version=3, scenario="机器判定"),  # LLM 不采
    ])
    out = build_sft_records(rows=_source(1), adl=adl, system_prompt="s",
                            text_column="text")
    assert out[0]["output"]["scenario"] == "新判定"


def test_sft_empty_sources() -> None:
    assert build_sft_records(rows=[], adl=_adl([]), system_prompt="s") == []
    # ADL 空 → 无可配对标注
    assert build_sft_records(rows=_source(1), adl=_adl([]),
                             system_prompt="s", text_column="text") == []


# --- ② pretrain --------------------------------------------------------------

def _vertex(vid: str, name: str, definition: str = "") -> dict:
    return {"id": vid, "label": "entity", "properties": {
        "name": name, "definition": definition}}


def test_pretrain_triples_with_definition_context() -> None:
    vertices = [
        _vertex("3:a", "应急指挥中心", "全市燃气应急中枢"),
        _vertex("3:b", "阀门"),
    ]
    edges = [{"id": "e1", "label": "处置", "outV": "3:a", "inV": "3:b"}]
    out = build_pretrain_records(vertices=vertices, edges=edges)
    assert len(out) == 1
    rec = out[0]
    assert rec == {
        "subject": "应急指挥中心", "predicate": "处置", "object": "阀门",
        "context": {"subject_definition": "全市燃气应急中枢",
                    "object_definition": ""},
    }


def test_pretrain_skips_unresolvable_edges_and_chunk_vertices() -> None:
    vertices = [_vertex("3:a", "A")]
    edges = [
        {"id": "e1", "label": "r", "outV": "3:a", "inV": "9:ghost"},  # 端点缺
        {"id": "e2", "label": "r", "outV": "3:a", "inV": "2:chunk1"},  # chunk 端点
    ]
    assert build_pretrain_records(vertices=vertices, edges=edges) == []


def test_pretrain_empty() -> None:
    assert build_pretrain_records(vertices=[], edges=[]) == []


# --- ③ RLHF ------------------------------------------------------------------

def test_rlhf_pairs_masked_prompt() -> None:
    pairs = [{
        "prompt": f"研判对象:{PHONE}",
        "chosen": {"scenario": "专家判定"},
        "rejected": {"scenario": "模型误判"},
    }]
    out = build_rlhf_records(pairs=pairs, generalize_rules=RULES)
    assert "13812345678" not in out[0]["prompt"]
    assert out[0]["chosen"]["scenario"] == "专家判定"
    assert out[0]["rejected"]["scenario"] == "模型误判"


def test_rlhf_empty_pairs() -> None:
    assert build_rlhf_records(pairs=[]) == []


# --- ④ golden ----------------------------------------------------------------

def test_golden_approved_only_and_masking() -> None:
    adl = _adl([
        _adl_row("h0", "ann1", status="approved", scenario="通过判定"),
        _adl_row("h1", "ann1", status="arbitration"),   # 非-approved 不入
        _adl_row("h2", "ann1", status="pending"),
    ])
    rows = [
        {"text": f"案例0 {PHONE}", "row_id": "h0"},
        {"text": "案例1", "row_id": "h1"},
        {"text": "案例2", "row_id": "h2"},
    ]
    out = build_golden_records(
        rows=rows, adl=adl, text_column="text", generalize_rules=RULES)
    assert len(out) == 1
    assert "13812345678" not in out[0]["input"]
    assert "[手机号]" in out[0]["input"]
    assert out[0]["row_id"] == "h0"
    assert out[0]["expected"]["scenario"] == "通过判定"
    assert out[0]["source"] == "adl-approved"


def test_golden_llm_rows_excluded() -> None:
    adl = _adl([_adl_row("h0", "llm:qwen", status="approved")])
    out = build_golden_records(
        rows=[{"text": "t", "row_id": "h0"}], adl=adl, text_column="text")
    assert out == []  # 黄金集=人工真值,LLM 行不采


# --- 写盘 --------------------------------------------------------------------

def test_write_corpus_jsonl(tmp_path: Path) -> None:
    recs = [{"a": 1}, {"a": 2}]
    path = write_corpus(tmp_path, tag="v1.0.0", form="sft", records=recs)
    assert path == tmp_path / "v1.0.0" / "sft.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(x)["a"] for x in lines] == [1, 2]
    # 空形态也落空文件(消费方可感知形态存在但无数据)
    p2 = write_corpus(tmp_path, tag="v1.0.0", form="rlhf", records=[])
    assert p2.exists() and p2.read_text(encoding="utf-8") == ""
