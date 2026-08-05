"""Atomic JSON write helper for KG sidecar / checkpoint files (v1.10.2 P0.2).

Prevents torn writes when a build is killed mid-``write_text`` (container OOM /
restart): the file is written to a temp in the **same directory**, then
``os.replace`` swaps it in atomically (POSIX guarantee on the same filesystem).
A crash before ``os.replace`` leaves the old file intact and a stale ``.tmp``
(which we clean up on the next attempt); a crash after leaves the new file
whole. Either way the reader never sees a half-written file.

Used by: ``fed_chunks.json`` (he_extractor), ``map_reduce.json`` (builder).
``metadata.json`` is written by hyper-extract's ``ka.dump`` (3rd-party) and is
not routed through here.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_json(path: str | Path, obj: object, *, indent: int | None = None) -> None:
    """Atomically serialize ``obj`` to ``path`` as UTF-8 JSON.

    Writes ``<{name>.<unique>.tmp`` in the same dir, then ``os.replace``.
    Best-effort cleanup of the temp on any failure (the call still re-raises).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=indent)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
