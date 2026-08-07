"""[#11] Per-dataset KA dump versioning — archive / list / rollback / prune.

The active KA dump lives at ``<base>/<dataset>/ka/`` (``data.json`` /
``metadata.json`` / ``index/``) and is overwritten on every ``kg_build``. This
module keeps history so a rebuild that regresses quality (or fails) can be
rolled back: before each build the current dump is archived under
``<base>/<dataset>/ka/versions/v{ts}/`` with a ``manifest.json`` index.

Layout::

    <base>/<dataset>/ka/                 # active dump (read by search/chat/rebuild)
      data.json
      metadata.json
      index/
      versions/
        manifest.json                    # [{version, created_at, node_count, edge_count}, ...]
        v20260714t201500/                # archived dump (same files as active)
          data.json
          metadata.json
          index/

The active dump path is unchanged, so the read path (``_ka_dir_for`` /
``load_ka_for_query``) needs no modification — versioning is a pure add-on.

Single-writer (``kg_build`` serializes per dataset), so the manifest is a plain
JSON file with no locking.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_VERSIONS_SUBDIR = "versions"
_MANIFEST = "manifest.json"
# hyper-extract dump files / dirs that make up a version snapshot.
_DUMP_MEMBERS = ("data.json", "metadata.json", "index")


def _ka_dir(base_dir: Path, dataset_name: str) -> Path:
    # [#naming] artifact_key_for = the graph-name stem — KA dir and HugeGraph
    # graph (kg_{stem}) share one source. Canonical names (jd_ddd) unchanged.
    from arrow_lake.knowledge_graph._naming import artifact_key_for
    return Path(base_dir) / artifact_key_for(dataset_name) / "ka"


def _versions_dir(ka_dir: Path) -> Path:
    return ka_dir / _VERSIONS_SUBDIR


def _manifest_path(ka_dir: Path) -> Path:
    return _versions_dir(ka_dir) / _MANIFEST


def _read_manifest(ka_dir: Path) -> list[dict]:
    mp = _manifest_path(ka_dir)
    if not mp.is_file():
        return []
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_manifest(ka_dir: Path, entries: list[dict]) -> None:
    vd = _versions_dir(ka_dir)
    vd.mkdir(parents=True, exist_ok=True)
    _manifest_path(ka_dir).write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _count_nodes_edges(data_json: Path) -> tuple[int, int]:
    try:
        d = json.loads(data_json.read_text(encoding="utf-8"))
        nodes = d.get("nodes", []) if isinstance(d, dict) else []
        edges = d.get("edges", []) if isinstance(d, dict) else []
        return len(nodes), len(edges)
    except (OSError, json.JSONDecodeError):
        return 0, 0


def _copy_dump(src_dir: Path, dst_dir: Path) -> None:
    """Copy the active dump members (data.json/metadata.json) into dst_dir.

    The ``index/`` FAISS dir is deliberately excluded — it is fully rebuildable
    from ``data.json`` (``_ensure_ka_index`` rebuilds on load when the on-disk
    index is missing), so archiving it on every version only wastes disk (the
    bulk of a dump; audit P2). Both archive and rollback use this helper, so a
    rolled-back active dump rebuilds its index on the next search/chat.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    for member in _DUMP_MEMBERS:
        if member == "index":
            continue  # rebuildable from data.json; not archived to save disk
        s = src_dir / member
        d = dst_dir / member
        if not s.exists():
            continue
        if s.is_dir():
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)


def has_active_dump(base_dir: Path, dataset_name: str) -> bool:
    """True iff an active KA dump (data.json) exists for ``dataset_name``.

    Only the dataset-granularity path writes ``data.json``; the map_reduce path
    (default for large datasets since v1.9.12) writes ``map_reduce.json`` +
    ``fed_chunks.json`` instead. For "has this ever been built?" use
    :func:`has_prior_build`, which recognizes both paths' markers.
    """
    return (_ka_dir(base_dir, dataset_name) / "data.json").is_file()


# Build markers that indicate a prior kg_build ran for this dataset, regardless
# of extraction granularity. ``data.json`` = dataset path; ``map_reduce.json`` /
# ``fed_chunks.json`` = map_reduce path. ``fed_chunks.json`` is the canonical
# shared marker (written by both paths — see KGBuilder._execute_build).
_BUILD_MARKERS = ("data.json", "map_reduce.json", "fed_chunks.json")


def has_prior_build(base_dir: Path, dataset_name: str) -> bool:
    """True iff any KA build marker exists for ``dataset_name`` (either path).

    Drives the kg.html 首次/增量 pre-build label. Unlike :func:`has_active_dump`
    this recognizes the map_reduce checkpoint, so a dataset built via the default
    large-file path (no ``data.json``) is correctly reported as rebuildable.
    """
    ka_dir = _ka_dir(base_dir, dataset_name)
    return any((ka_dir / marker).is_file() for marker in _BUILD_MARKERS)


def archive_current(base_dir: Path, dataset_name: str) -> dict | None:
    """Archive the current active dump as a new version (called before overwrite).

    Returns the new manifest entry, or ``None`` if there is no active dump to
    archive (first build). No-op if archiving is disabled (``base_dir`` is None).
    """
    if base_dir is None:
        return None
    ka_dir = _ka_dir(base_dir, dataset_name)
    if not (ka_dir / "data.json").is_file():
        return None  # nothing to archive (first build)

    ts = datetime.now().strftime("v%Y%m%dt%H%M%S")
    version = ts
    # avoid clobber in the rare same-second double-build
    v_dir = _versions_dir(ka_dir) / version
    i = 1
    while v_dir.exists():
        v_dir = _versions_dir(ka_dir) / f"{version}_{i}"
        i += 1

    _copy_dump(ka_dir, v_dir)
    nodes, edges = _count_nodes_edges(v_dir / "data.json")
    entry = {
        "version": v_dir.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "node_count": nodes,
        "edge_count": edges,
    }
    entries = _read_manifest(ka_dir)
    entries.append(entry)
    _write_manifest(ka_dir, entries)
    logger.info("KA version archived: %s/%s (%d nodes, %d edges)",
                dataset_name, v_dir.name, nodes, edges)
    return entry


def list_versions(base_dir: Path, dataset_name: str) -> list[dict]:
    """Return archived versions (newest first). Empty if none."""
    ka_dir = _ka_dir(base_dir, dataset_name)
    entries = _read_manifest(ka_dir)
    return list(reversed(entries))  # newest first


def rollback(base_dir: Path, dataset_name: str, version: str) -> dict:
    """Restore ``version`` as the active dump (the current active is archived first).

    Raises ``FileNotFoundError`` if ``version`` is not in the manifest.
    """
    ka_dir = _ka_dir(base_dir, dataset_name)
    entries = _read_manifest(ka_dir)
    match = next((e for e in entries if e.get("version") == version), None)
    if match is None:
        raise FileNotFoundError(
            f"KA version {version!r} not found for dataset {dataset_name!r}"
        )
    v_dir = _versions_dir(ka_dir) / version
    if not v_dir.is_dir():
        raise FileNotFoundError(
            f"KA version {version!r} directory missing for dataset {dataset_name!r}"
        )
    # Archive the current active first so rollback is itself reversible.
    archive_current(base_dir, dataset_name)
    # Restore the named version → active.
    _copy_dump(v_dir, ka_dir)
    logger.info("KA rollback: %s → %s", dataset_name, version)
    nodes, edges = _count_nodes_edges(ka_dir / "data.json")
    return {"dataset": dataset_name, "restored_version": version,
            "node_count": nodes, "edge_count": edges}


def prune(base_dir: Path, dataset_name: str, keep: int = 5) -> dict:
    """Keep only the newest ``keep`` archived versions; delete the rest.

    Returns a summary of how many were kept / removed. ``keep`` < 0 keeps all.
    """
    if keep < 0:
        return {"dataset": dataset_name, "kept": -1, "removed": 0}
    ka_dir = _ka_dir(base_dir, dataset_name)
    entries = _read_manifest(ka_dir)
    # newest first
    ordered = list(reversed(entries))
    keep_list = ordered[:keep]
    drop_list = ordered[keep:]
    removed = 0
    for e in drop_list:
        v_dir = _versions_dir(ka_dir) / e.get("version", "")
        if v_dir.is_dir():
            shutil.rmtree(v_dir, ignore_errors=True)
            removed += 1
    # manifest back to original order (oldest first), keeping only survivors
    survivors = {e.get("version") for e in keep_list}
    new_entries = [e for e in entries if e.get("version") in survivors]
    _write_manifest(ka_dir, new_entries)
    logger.info("KA prune %s: kept %d, removed %d", dataset_name, len(keep_list), removed)
    return {"dataset": dataset_name, "kept": len(keep_list), "removed": removed}
