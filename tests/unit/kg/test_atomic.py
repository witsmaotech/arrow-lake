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


def _raise_oserror(*_a: object, **_k: object) -> None:
    raise OSError("simulated replace failure")


def test_atomic_write_failure_keeps_old_and_cleans_tmp(
    tmp_path: Path, monkeypatch
) -> None:
    """On os.replace failure: old file intact, no stale tmp left (review M3)."""
    import pytest

    p = tmp_path / "a.json"
    p.write_text(json.dumps({"old": 1}), "utf-8")
    monkeypatch.setattr(
        "arrow_lake.knowledge_graph._atomic.os.replace", _raise_oserror
    )
    with pytest.raises(OSError):
        atomic_write_json(p, {"new": 2})
    # old file intact (reader never sees a half-written file)
    assert json.loads(p.read_text("utf-8")) == {"old": 1}
    # the failed attempt's tmp was cleaned by the except branch
    assert not list(tmp_path.glob(".a.json.*.tmp"))


def test_atomic_write_sweeps_preexisting_stale_tmp(tmp_path: Path) -> None:
    """A stale tmp from a prior crash is swept on the next write (review M1)."""
    p = tmp_path / "a.json"
    stale = tmp_path / ".a.json.deadbeef.tmp"
    stale.write_text("garbage", "utf-8")
    atomic_write_json(p, {"x": 1})
    assert not stale.exists()  # swept
    assert json.loads(p.read_text("utf-8")) == {"x": 1}
