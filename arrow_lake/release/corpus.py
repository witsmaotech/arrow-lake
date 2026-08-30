"""F5.6 — 训练语料四形态导出(v1.11.4 MS5 W4.1,设计 §8 / S7)。

**全部经 masking 出域**(``apply_annotation_masking``,与 MS4 脱敏
前置同源 L2/L3);落点 ``{base_dir}/{release-tag}/{form}.jsonl``(D4,
持久卷,不进 git)。四形态一次交付(S7):

* **① SFT 指令对**:源表文本(脱敏)× ADL 最新**人工**标注(五段
  结构化 target);system = 本体定义 + active 规则(调用方组装);
* **② 预训练三元组**:KG 快照(HG REST vertices/edges)→ ``{subject,
  predicate, object, context: 双端定义}``;chunk 顶点与不可解析端点跳过;
* **③ RLHF 偏好对**:``{prompt, chosen: 专家, rejected: 模型}``——
  decisions(MS3)是无状态即时求值、无持久数据面 → 导出侧由调用方供
  对;空对 → 空文件+提示(设计风险表,不阻塞其余形态);
* **④ 回归黄金集**:ADL **approved 且人工**行 → ``{input, expected,
  row_id}``;``-m golden`` 离线回归入口见 ``tests/benchmark/golden/``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa

__all__ = [
    "build_golden_records",
    "build_pretrain_records",
    "build_rlhf_records",
    "build_sft_records",
    "write_corpus",
]


def _mask(
    text: str,
    generalize_rules: tuple[tuple[str, str], ...] = (),
    entity_names: tuple[str, ...] = (),
    hmac_key: bytes | None = None,
) -> str:
    """L2 泛化 + L3 假名(与 dispatch 同源;无规则无实体=透传,口径见
    annotation.masking——脱敏是配置驱动,不是内置 PII 魔法检测)。"""
    from arrow_lake.annotation.masking import apply_annotation_masking

    return apply_annotation_masking(
        text, generalize_rules=generalize_rules,
        entity_names=entity_names, hmac_key=hmac_key)


def _is_llm(annotator_id: Any) -> bool:
    return str(annotator_id or "").startswith("llm:")


def _five_part(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "objects": row.get("objects") or [],
        "events": row.get("events") or [],
        "rules_applied": row.get("rules_applied") or [],
        "scenario": row.get("scenario") or "",
        "relations": row.get("relations") or [],
    }


def _latest_human_by_row(
    adl: pa.Table,
    *, approved_only: bool = False,
) -> dict[str, dict[str, Any]]:
    """ADL → 每行取最新**人工**标注(llm: 前缀弃;重标注取最大版本)。"""
    latest: dict[str, dict[str, Any]] = {}
    for row in (adl.to_pylist() if adl is not None else []):
        if _is_llm(row.get("annotator_id")):
            continue
        if approved_only and row.get("review_status") != "approved":
            continue
        row_id = row["source_row_id"]
        cur = latest.get(row_id)
        if cur is None or int(row.get("adl_version") or 0) >= int(
                cur.get("adl_version") or 0):
            latest[row_id] = row
    return latest


# --------------------------------------------------------------------------- #
# ① SFT
# --------------------------------------------------------------------------- #

def build_sft_records(
    *, rows: list[dict[str, Any]], adl: pa.Table | None,
    system_prompt: str, text_column: str = "text",
    generalize_rules: tuple[tuple[str, str], ...] = (),
    entity_names: tuple[str, ...] = (),
    hmac_key: bytes | None = None,
) -> list[dict[str, Any]]:
    """源行 × 最新人工五段标注 → SFT 指令对(instruction 脱敏)。"""
    by_row = _latest_human_by_row(adl) if adl is not None else {}
    out: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get(text_column) or "").strip()
        if not text:
            continue
        target = by_row.get(str(row.get("row_id") or ""))
        if target is None:
            continue  # 无人工标注的行不入 SFT(宁缺勿错)
        out.append({
            "system": system_prompt,
            "instruction": _mask(text, generalize_rules, entity_names, hmac_key),
            "output": _five_part(target),
        })
    return out


# --------------------------------------------------------------------------- #
# ② pretrain
# --------------------------------------------------------------------------- #

def build_pretrain_records(
    *, vertices: list[dict[str, Any]], edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """KG 快照 → 三元组+定义上下文(chunk 顶点/缺端点跳过)。"""
    def _name(v: dict[str, Any]) -> tuple[str, str] | None:
        props = v.get("properties") or {}
        name = str(props.get("name") or v.get("name") or "").strip()
        if not name or str(v.get("label") or "") == "chunk":
            return None
        return name, str(props.get("definition") or "")

    info: dict[str, tuple[str, str]] = {}
    for v in vertices:
        parsed = _name(v)
        if parsed is not None:
            info[str(v.get("id"))] = parsed

    out: list[dict[str, Any]] = []
    for e in edges:
        src = info.get(str(e.get("outV")))
        dst = info.get(str(e.get("inV")))
        if src is None or dst is None:
            continue
        out.append({
            "subject": src[0],
            "predicate": str(e.get("label") or ""),
            "object": dst[0],
            "context": {
                "subject_definition": src[1],
                "object_definition": dst[1],
            },
        })
    return out


# --------------------------------------------------------------------------- #
# ③ RLHF
# --------------------------------------------------------------------------- #

def build_rlhf_records(
    *, pairs: list[dict[str, Any]],
    generalize_rules: tuple[tuple[str, str], ...] = (),
    entity_names: tuple[str, ...] = (),
    hmac_key: bytes | None = None,
) -> list[dict[str, Any]]:
    """偏好对(prompt 脱敏;chosen=专家 / rejected=模型,上游成对)。"""
    out: list[dict[str, Any]] = []
    for p in pairs:
        prompt = str(p.get("prompt") or "").strip()
        if not prompt or not p.get("chosen") or not p.get("rejected"):
            continue
        out.append({
            "prompt": _mask(prompt, generalize_rules, entity_names, hmac_key),
            "chosen": p["chosen"],
            "rejected": p["rejected"],
        })
    return out


# --------------------------------------------------------------------------- #
# ④ golden
# --------------------------------------------------------------------------- #

def build_golden_records(
    *, rows: list[dict[str, Any]], adl: pa.Table | None,
    text_column: str = "text",
    generalize_rules: tuple[tuple[str, str], ...] = (),
    entity_names: tuple[str, ...] = (),
    hmac_key: bytes | None = None,
) -> list[dict[str, Any]]:
    """ADL approved 且人工的行 → 黄金集(input 脱敏;LLM 行不采)。"""
    by_row = _latest_human_by_row(adl, approved_only=True) if adl is not None else {}
    out: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get(text_column) or "").strip()
        row_id = str(row.get("row_id") or "")
        target = by_row.get(row_id)
        if not text or target is None:
            continue
        out.append({
            "input": _mask(text, generalize_rules, entity_names, hmac_key),
            "expected": _five_part(target),
            "row_id": row_id,
            "source": "adl-approved",
        })
    return out


# --------------------------------------------------------------------------- #
# 写盘(D4)
# --------------------------------------------------------------------------- #

def write_corpus(
    base_dir: Path, *, tag: str, form: str, records: list[dict[str, Any]],
) -> Path:
    """``{base_dir}/{tag}/{form}.jsonl``;空形态也落空文件(形态可感知)。"""
    out_dir = Path(base_dir) / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{form}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path
