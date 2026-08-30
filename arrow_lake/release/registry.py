"""F5.4 — 发布注册:语义化 tag 纯函数 + ``sys_releases``(V021)。

发布 = **Lance 版本锁定 + 注册**(设计 §6):``lance_version`` 为发布
时刻数据集的 Lance 版本号,``tag`` 语义化(MAJOR= schema 破坏 /
MINOR=数据增量(默认)/ PATCH=质量修订,人工指定);retire=软状态
(历史保留,latest active 之外不再作为劣化比较基准)。
"""

from __future__ import annotations

from typing import Literal

__all__ = ["Bump", "format_tag", "next_tag", "parse_tag"]

Bump = Literal["major", "minor", "patch"]


def parse_tag(tag: str) -> tuple[int, int, int]:
    """``vMAJOR.MINOR.PATCH`` → 三元组;MAJOR ≥ 1(0.y.z 非发布语义)。"""
    parts = tag.split(".")
    if len(parts) != 3 or not parts[0].startswith("v"):
        raise ValueError(f"release tag must be vMAJOR.MINOR.PATCH, got {tag!r}")
    try:
        major, minor, patch = (int(p) for p in (parts[0][1:], parts[1], parts[2]))
    except ValueError as exc:
        raise ValueError(f"release tag must be vMAJOR.MINOR.PATCH, got {tag!r}") from exc
    if major < 1 or minor < 0 or patch < 0:
        raise ValueError(f"release tag must have major >= 1, got {tag!r}")
    return major, minor, patch


def format_tag(version: tuple[int, int, int]) -> str:
    return f"v{version[0]}.{version[1]}.{version[2]}"


def next_tag(
    latest: tuple[int, int, int] | None, bump: Bump = "minor",
) -> tuple[int, int, int]:
    """下一 tag:首个发布恒 v1.0.0;否则按 bump 位进位(低位清零)。"""
    if latest is None:
        return 1, 0, 0
    major, minor, patch = latest
    if bump == "major":
        return major + 1, 0, 0
    if bump == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1
