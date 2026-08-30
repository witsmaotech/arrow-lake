"""黄金集回归入口(v1.11.4 MS5 W4.2,D5)。

``-m golden`` 显式运行,**不进 CI 热路径**(红线⑤)——未显式选 golden
 marker 时自动 skip,机械保证。
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "golden: offline golden-set regression (MS5 F5.6④; run explicitly "
        "via `-m golden`, never in the CI hot path)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """未显式 ``-m golden`` → 黄金集用例全部 skip(红线⑤的机械保证)。"""
    expr = config.getoption("markexpr") or ""
    if "golden" in expr:
        return
    skip = pytest.mark.skip(
        reason="golden set: run explicitly with -m golden (not in CI hot path)")
    for item in items:
        if "golden" in item.keywords:
            item.add_marker(skip)
