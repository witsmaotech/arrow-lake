"""Tests for atomic_write_json (v1.10.2 P0.2)."""

from __future__ import annotations

import json
from pathlib import Path

from arrow_lake.knowledge_graph._atomic import atomic_write_json


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    atomic_write_json(p, {"x": 1})
    assert json.loads(p.read_text("utf-8")) == {"x": 1}


def test_atomic_write_overwrites(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    p.write_text("OLD", "utf-8")
    atomic_write_json(p, {"y": 2})
    assert json.loads(p.read_text("utf-8")) == {"y": 2}


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "deep" / "a.json"
    atomic_write_json(p, [1, 2, 3])
    assert json.loads(p.read_text("utf-8")) == [1, 2, 3]


def test_atomic_write_no_torn_tmp_left(tmp_path: Path) -> None:
    """On success no stale .tmp lingers in the directory."""
    p = tmp_path / "a.json"
    atomic_write_json(p, {"x": 1})
    leftovers = [
        f for f in tmp_path.iterdir() if f.name.startswith(".a.json") and f.suffix == ".tmp"
    ]
    assert leftovers == []


def test_atomic_write_preserves_utf8(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    atomic_write_json(p, {"name": "应急指挥中心"})
    assert json.loads(p.read_text("utf-8"))["name"] == "应急指挥中心"


def test_atomic_write_indent_readable(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    atomic_write_json(p, {"a": 1}, indent=2)
    assert "\n" in p.read_text("utf-8")  # pretty-printed
