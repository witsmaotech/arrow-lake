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
