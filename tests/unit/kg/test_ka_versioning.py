"""Tests for [#11] KA dump versioning (archive / list / rollback / prune)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from arrow_lake.knowledge_graph import ka_versioning as kv


def _make_active(base: Path, ds: str, nodes: int) -> None:
    """Write a fake active dump with the given node count."""
    ka = base / ds / "ka"
    ka.mkdir(parents=True, exist_ok=True)
    (ka / "data.json").write_text(json.dumps({"nodes": [{"name": f"n{i}"} for i in range(nodes)], "edges": []}))
    (ka / "metadata.json").write_text("{}")
    (ka / "index").mkdir(exist_ok=True)
    (ka / "index" / "node_index.faiss").write_text("fake")


def test_archive_first_build_no_op(base: Path) -> None:
    """No active dump → archive returns None (first build)."""
    assert kv.archive_current(base, "ds") is None
    assert kv.list_versions(base, "ds") == []


def test_archive_then_list(base: Path) -> None:
    _make_active(base, "ds", nodes=3)
    entry = kv.archive_current(base, "ds")
    assert entry is not None and entry["node_count"] == 3
    versions = kv.list_versions(base, "ds")
    assert len(versions) == 1
    assert versions[0]["node_count"] == 3


def test_rollback_restores_and_is_reversible(base: Path) -> None:
    # v1: 3 nodes active
    _make_active(base, "ds", nodes=3)
    kv.archive_current(base, "ds")
    # v2: overwrite active with 10 nodes + archive
    _make_active(base, "ds", nodes=10)
    kv.archive_current(base, "ds")
    versions = kv.list_versions(base, "ds")
    assert len(versions) == 2
    # rollback to the oldest (3-node) version
    oldest = versions[-1]["version"]
    res = kv.rollback(base, "ds", oldest)
    assert res["restored_version"] == oldest
    active = json.loads((base / "ds" / "ka" / "data.json").read_text())
    assert len(active["nodes"]) == 3
    # current (10-node) was archived before restore → still recoverable
    assert len(kv.list_versions(base, "ds")) >= 3


def test_rollback_unknown_version_raises(base: Path) -> None:
    _make_active(base, "ds", nodes=1)
    kv.archive_current(base, "ds")
    with pytest.raises(FileNotFoundError):
        kv.rollback(base, "ds", "nonexistent")


def test_archive_excludes_rebuildable_index(base: Path) -> None:
    """audit P2: archived versions skip the rebuildable FAISS ``index/`` dir.

    The index is rebuilt on load by ``_ensure_ka_index`` when missing, so it need
    not be archived — copying it on every version wastes disk (the bulk of a dump).
    data.json + metadata.json are still archived.
    """
    _make_active(base, "ds", nodes=3)
    kv.archive_current(base, "ds")
    versions_dir = base / "ds" / "ka" / "versions"
    v_dirs = [p for p in versions_dir.iterdir() if p.is_dir()]
    assert len(v_dirs) == 1
    v_dir = v_dirs[0]
    assert (v_dir / "data.json").is_file()       # data preserved
    assert (v_dir / "metadata.json").is_file()   # metadata preserved
    assert not (v_dir / "index").exists()        # index NOT archived (rebuildable)


def test_prune_keeps_newest(base: Path) -> None:
    for n in (1, 2, 3, 4):
        _make_active(base, "ds", nodes=n)
        kv.archive_current(base, "ds")
        time.sleep(0.01)  # distinct version timestamps
    assert len(kv.list_versions(base, "ds")) == 4
    res = kv.prune(base, "ds", keep=2)
    assert res["removed"] == 2 and res["kept"] == 2
    remaining = kv.list_versions(base, "ds")
    assert len(remaining) == 2
    # newest 2 survived (node_count 4 and 3)
    counts = sorted(e["node_count"] for e in remaining)
    assert counts == [3, 4]


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    return tmp_path / "ka_base"
