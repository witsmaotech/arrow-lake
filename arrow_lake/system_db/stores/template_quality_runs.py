"""v1.10.0 M4: system_db store for extraction-template quality-validation runs.

Persists each quality-validation run (generated doc, entity/relation counts,
graph snapshot for vis replay, RAG Q&A pairs) so admins can review a template's
validation history. The temp dataset + kg graph are cleaned up after a run, so
the snapshot stored here is the durable record.

Mirrors the existing store pattern (see stores/extraction_templates.py): thin
methods over ``SystemDB.execute(sql, params)``, returning plain dicts.
"""

from __future__ import annotations

import logging
from typing import Any

from arrow_lake.system_db.connection import SystemDB

logger = logging.getLogger(__name__)


class TemplateQualityRunStore:
    """History of extraction-template quality-validation runs (libSQL system_db)."""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    def save_run(
        self, *, template_name: str, document: str,
        scenario_hint: str | None = None, temp_dataset: str | None = None,
        entity_count: int = 0, relation_count: int = 0,
        graph_snapshot: str | None = None, rag_qa: str | None = None,
        note: str | None = None, created_by: str | None = None,
    ) -> int:
        """Insert a run; return its id."""
        cur = self._db.execute(
            """INSERT INTO template_quality_runs
                   (template_name, scenario_hint, document, temp_dataset,
                    entity_count, relation_count, graph_snapshot, rag_qa, note, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (template_name, scenario_hint, document, temp_dataset,
             entity_count, relation_count, graph_snapshot, rag_qa, note, created_by),
        )
        self._db.commit()  # libsql 不 autocommit,显式提交才跨连接/重启持久
        return cur.lastrowid if cur is not None else 0

    def list_runs(self, template_name: str) -> list[dict[str, Any]]:
        """Summary list for a template (excludes the large document/graph/qa)."""
        cols = ("id", "template_name", "scenario_hint", "temp_dataset",
                "entity_count", "relation_count", "note", "created_at", "created_by")
        cur = self._db.execute(
            f"""SELECT {", ".join(cols)} FROM template_quality_runs
               WHERE template_name = ? ORDER BY id DESC""",
            (template_name,),
        )
        rows = cur.fetchall() if cur is not None else []
        return [dict(r) for r in rows] if rows and hasattr(rows[0], "keys") else [
            dict(zip(cols, r)) for r in rows]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        """Full run detail (including document + graph snapshot + rag qa)."""
        cols = ("id", "template_name", "scenario_hint", "document", "temp_dataset",
                "entity_count", "relation_count", "graph_snapshot", "rag_qa",
                "note", "created_at", "created_by")
        cur = self._db.execute(
            f"""SELECT {", ".join(cols)} FROM template_quality_runs WHERE id = ?""",
            (run_id,))
        row = cur.fetchone() if cur is not None else None
        if row is None:
            return None
        return dict(row) if hasattr(row, "keys") else dict(zip(cols, row))

    def delete_run(self, run_id: int) -> bool:
        cur = self._db.execute(
            "DELETE FROM template_quality_runs WHERE id = ?", (run_id,))
        self._db.commit()
        return cur is not None and cur.rowcount > 0
