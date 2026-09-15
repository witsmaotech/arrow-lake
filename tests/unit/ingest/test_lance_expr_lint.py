"""lint_lance_expr 方言提示 + resolve_lance_type 维度解析(LOW①②,v1.11.6.6)。"""

from __future__ import annotations

import pytest

from arrow_lake.ingest.schema import lint_lance_expr, resolve_lance_type


def test_lint_strips_string_literals() -> None:
    """LOW①:单引号字面量先剥——内容含 case/trim/双引号形态的字符串不再误拦。"""
    assert lint_lance_expr("regexp_replace(s, 'case', 'x', 'g')") == []
    assert lint_lance_expr("concat(a, '\"quoted\"')") == []


def test_lint_still_flags_real_traps() -> None:
    assert any("TRIM" in i for i in lint_lance_expr("trim(name)"))
    assert any("CASE" in i for i in lint_lance_expr("CASE WHEN a THEN 1 ELSE 0 END"))
    assert any("Double quotes" in i for i in lint_lance_expr('"name"'))
    # 标识符含 trap 词根(trim_label)不是调用,不拦
    assert lint_lance_expr("trim_label") == []


def test_vector_dim_rejects_superscript_digits() -> None:
    """LOW②:isdigit 过上标 '²'(int() 裸炸);isdecimal 干净 ValueError。"""
    with pytest.raises(ValueError):
        resolve_lance_type("vector:²")
    assert resolve_lance_type("vector:768")[1] is False
