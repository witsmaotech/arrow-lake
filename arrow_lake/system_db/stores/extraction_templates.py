"""v1.10.0 P3: system_db store for user extraction-template metadata + bindings.

The YAML files on the writable volume are the single source of truth; this
store is the searchable index (list/search by doc_type) and the per-dataset
binding table. :meth:`reconcile` self-heals the index against the filesystem
(templates edited/deleted out-of-band are corrected).

Mirrors the existing store pattern (see stores/user_state.py): thin methods over
``SystemDB.execute(sql, params)``, returning plain dicts.
"""

from __future__ import annotations

import logging
from typing import Any

from arrow_lake.system_db.connection import SystemDB, SystemDBError

logger = logging.getLogger(__name__)


class ExtractionTemplateStore:
    """Metadata + bindings for user extraction templates (libSQL system_db)."""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # --- template metadata -------------------------------------------------

    def upsert_template(
        self, *, name: str, file_path: str, content_hash: str,
        doc_type: str | None = None, description: str | None = None,
        owner: str | None = None,
    ) -> None:
        self._db.execute(
            """INSERT INTO extraction_templates
                   (name, source, doc_type, file_path, description, owner, content_hash)
               VALUES (?, 'user', ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   doc_type = excluded.doc_type,
                   file_path = excluded.file_path,
                   description = excluded.description,
                   content_hash = excluded.content_hash,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (name, doc_type, file_path, description, owner, content_hash),
        )

    def list_templates(self, *, doc_type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT name, doc_type, file_path, description, owner, content_hash, updated_at FROM extraction_templates"
        params: tuple = ()
        if doc_type:
            sql += " WHERE doc_type = ?"
            params = (doc_type,)
        sql += " ORDER BY name"
        cur = self._db.execute(sql, params)
        rows = cur.fetchall() if cur is not None else []
        return [dict(r) for r in rows] if rows and hasattr(rows[0], "keys") else [dict(zip(
            ("name", "doc_type", "file_path", "description", "owner", "content_hash", "updated_at"), r
        )) for r in rows]

    def get_template(self, name: str) -> dict[str, Any] | None:
        cur = self._db.execute(
            "SELECT name, doc_type, file_path, description, owner, content_hash, updated_at "
            "FROM extraction_templates WHERE name = ?", (name,))
        row = cur.fetchone() if cur is not None else None
        if row is None:
            return None
        return dict(row) if hasattr(row, "keys") else dict(zip(
            ("name", "doc_type", "file_path", "description", "owner", "content_hash", "updated_at"), row))

    def delete_template(self, name: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM extraction_templates WHERE name = ?", (name,))
        return cur is not None and cur.rowcount > 0

    def set_default(self, doc_type: str, template_name: str) -> None:
        """Mark ``template_name`` as the default for ``doc_type`` (single holder)."""
        self._db.execute("UPDATE extraction_templates SET is_default_for = NULL WHERE is_default_for = ?",
                         (doc_type,))
        self._db.execute("UPDATE extraction_templates SET is_default_for = ? WHERE name = ?",
                         (doc_type, template_name))

    # --- per-dataset bindings ---------------------------------------------

    def set_binding(self, dataset_name: str, template_name: str, *, bound_by: str | None = None) -> None:
        self._db.execute(
            """INSERT INTO dataset_template_bindings (dataset_name, template_name, bound_by)
               VALUES (?, ?, ?)
               ON CONFLICT(dataset_name) DO UPDATE SET
                   template_name = excluded.template_name,
                   bound_by = excluded.bound_by,
                   bound_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (dataset_name, template_name, bound_by),
        )

    def get_binding(self, dataset_name: str) -> str | None:
        cur = self._db.execute(
            "SELECT template_name FROM dataset_template_bindings WHERE dataset_name = ?",
            (dataset_name,))
        row = cur.fetchone() if cur is not None else None
        return row[0] if row is not None else None

    def clear_binding(self, dataset_name: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM dataset_template_bindings WHERE dataset_name = ?", (dataset_name,))
        return cur is not None and cur.rowcount > 0

    def list_bindings(self, template_name: str) -> list[str]:
        """Datasets bound to ``template_name`` (for delete-in-use checks)."""
        cur = self._db.execute(
            "SELECT dataset_name FROM dataset_template_bindings WHERE template_name = ?",
            (template_name,))
        rows = cur.fetchall() if cur is not None else []
        return [r[0] for r in rows]

    # --- self-heal --------------------------------------------------------

    def reconcile(self, on_disk: list[tuple[str, str]]) -> dict[str, list[str]]:
        """Sync the index to the filesystem. ``on_disk`` = list of (name, path).

        Removes table rows whose file is gone; returns ``{"removed": [...],
        "missing": [...]}`` where ``missing`` are table rows without a file
        (caller may re-add by re-reading YAML). Idempotent + best-effort.
        """
        disk_names = {n for n, _ in on_disk}
        indexed = {r["name"]: r for r in self.list_templates()}
        removed: list[str] = []
        for name in set(indexed) - disk_names:
            self.delete_template(name)
            removed.append(name)
        missing = list(disk_names - set(indexed))
        return {"removed": removed, "missing": missing}
