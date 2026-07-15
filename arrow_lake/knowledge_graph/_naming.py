"""Per-dataset HugeGraph graph-name derivation.

Maps a Lance ``dataset_name`` (the lake path) to a stable, HugeGraph-safe
graph name so each dataset's knowledge graph lives in its own isolated graph
(``kg_{sanitized}``). Idempotent: the same dataset always maps to the same
graph name.

HugeGraph 1.7 graph names: ``[a-zA-Z0-9_]+``, practical length cap ~48 chars.
"""

from __future__ import annotations

import re

# Safe margin under HugeGraph's graph-name length limit.
_MAX = 48
# Keep only [a-z0-9_] after lowercasing; everything else collapses to '_'.
_RE = re.compile(r"[^a-z0-9_]")
_PREFIX = "kg_"
_PLACEHOLDER = "default"


def graph_name_for(dataset_name: str) -> str:
    """Map a Lance dataset name to a stable, HugeGraph-safe graph name.

    Rule: lowercase → non ``[a-z0-9_]`` to ``_`` → strip leading/trailing
    ``_`` → truncate to fit the cap → prefix ``kg_``. Empty / all-symbol
    input maps to ``kg_default``.

    Args:
        dataset_name: Lance dataset name (the lake path).

    Returns:
        Graph name like ``kg_my_docs``. Idempotent.
    """
    sanitized = _RE.sub("_", dataset_name.lower()).strip("_")
    cap = _MAX - len(_PREFIX)
    sanitized = sanitized[:cap] or _PLACEHOLDER
    return f"{_PREFIX}{sanitized}"


def artifact_key_for(dataset_name: str) -> str:
    """Single-source key shared by ALL per-dataset artifacts (graph + KA dump).

    Returns the graph-name STEM (without the ``kg_`` prefix), so:
    ``graph_name_for(ds) == "kg_" + artifact_key_for(ds)`` and the KA dump dir
    ``<base>/<artifact_key_for(ds)>/ka`` derive from ONE function — they can
    never diverge for a given dataset (the case-variant / long-name collision
    risk is now consistent: search and graph always agree).

    For already-canonical names (lowercase ``[a-z0-9_]``, ≤45 chars) this EQUALS
    the raw dataset name, so existing KA dumps (e.g. ``jd_ddd``) are unchanged.
    """
    stem = graph_name_for(dataset_name)
    return stem[len(_PREFIX):] if stem.startswith(_PREFIX) else stem
