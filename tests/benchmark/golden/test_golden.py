"""黄金集结构回归:目录内 {ds}.jsonl 全量扫描(离线,零外部依赖)。

断言口径:每条 = {input(脱敏), expected(五段), row_id, source};五段
键齐备且 input 非空。语义级回归(抽取质量对比)由各域自加用例。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent
_FILES = sorted(GOLDEN_DIR.glob("*.jsonl"))


@pytest.mark.golden
@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.stem)
def test_golden_structure(path: Path) -> None:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert lines, f"golden set {path.name} must not be empty"
    for ln in lines:
        rec = json.loads(ln)
        assert rec.get("input"), rec
        expected = rec.get("expected")
        assert isinstance(expected, dict), rec
        assert set(expected) == {
            "objects", "events", "rules_applied", "scenario", "relations"}, rec
        assert rec.get("row_id") and rec.get("source") == "adl-approved"
        # M20(四维 review):标注质量下限——试点集曾含退化数据(空 label
        # 对象/泛化标签),结构校验全过挡不住;黄金集是回归基准,退化
        # target 会把坏基准钉进未来每次对比。
        for obj in expected["objects"]:
            assert str(obj.get("label") or "").strip(), rec
            assert 0 <= int(obj.get("start", -1)) <= int(obj.get("end", -2)) \
                <= len(rec["input"]), rec


@pytest.mark.golden
@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.stem)
def test_golden_not_degenerate(path: Path) -> None:
    """退化集哨兵:全部对象都命中泛化标签(系统/属性/角色/实体/组件)
    = 标注根本没有领域语义,该批标注不合格,不得作为黄金基准。

    KNOWN_DEGENERATE:试点集 demo_annotation_alerts 的 taxonomy 本身
    便是泛型分类(模板生成的 labeling config,四维 review M20 实证)——
    登记放行;哨兵对**新增集**生效,防止下一批退化标注再被祝圣。
    """
    _GENERIC = {"系统", "属性", "角色", "实体", "组件", "数据", "组织", "过程"}
    _KNOWN_DEGENERATE = {"demo_annotation_alerts"}
    if path.stem in _KNOWN_DEGENERATE:
        pytest.skip(f"{path.stem}: known degenerate pilot taxonomy (M20 登记)")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    labels = [o.get("label") for ln in lines
              for o in json.loads(ln)["expected"]["objects"]]
    assert labels, f"{path.name}: no objects at all"
    specific = [l for l in labels if l not in _GENERIC]
    assert specific, (
        f"{path.name}: every label is generic {_GENERIC} — degenerate "
        f"annotation batch, refuse to bless as golden baseline")
