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
