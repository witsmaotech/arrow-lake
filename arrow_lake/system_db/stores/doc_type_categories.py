"""v1.10.0 M5: system_db store for the dynamic doc_type ↔ category dictionary.

The canonical doc_type taxonomy was a code-level constant
(:data:`doc_type_router.DOC_TYPE_ALIASES` / :data:`DOC_TYPE_DESCRIPTIONS`).
M5 promotes it to a runtime-managed dictionary: admins add a category (e.g.
``security``) and it is immediately usable as a template ``category`` (Layer-2
routing is ``template.category == doc_type``) and as an ingest ``doc_type``.

The table is seeded at startup from the Python taxonomy constants
(:meth:`seed_if_empty`) so the 11 built-in domains are present without
duplicating them in SQL. Custom categories added via the admin API are
``source='custom'``; both are first-class for routing/validation.

Mirrors the existing store pattern (see stores/extraction_templates.py): thin
methods over ``SystemDB.execute(sql, params)``, returning plain dicts. Every
write commits explicitly (libSQL does not autocommit).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from arrow_lake.system_db.connection import SystemDB

logger = logging.getLogger(__name__)

# Category names are lowercase identifiers (aligned with DOC_TYPE_ALIASES keys
# and the template name regex). No path-traversal / casing surface.
_CATEGORY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# zh labels for canonical categories that have no CJK alias in DOC_TYPE_ALIASES
# (general / project). Used only at seed time so every category shows a Chinese
# label in the UI dropdowns.
_SEED_ZH_FALLBACK = {"general": "通用", "project": "项目"}

_COLS = ("name", "desc_zh", "desc_en", "aliases", "source", "created_at")


def _parse_aliases(raw: str | None) -> list[str]:
    return [a.strip() for a in (raw or "").split(",") if a.strip()]


class CategoryExistsError(ValueError):
    """Raised by :meth:`DocTypeCategoryStore.add_category` when the name is
    already present. Distinct from an invalid-name :class:`ValueError` so the
    API layer can map duplicate → 409 (conflict) vs invalid → 422 (validation).
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"category {name!r} already exists")


class DocTypeCategoryStore:
    """Runtime doc_type / template-category dictionary (libSQL system_db)."""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # --- seed ---------------------------------------------------------------

    def seed_if_empty(self) -> int:
        """Seed the 11 canonical doc_types from the Python taxonomy constants
        when the table is empty, then backfill any missing zh labels on
        already-seeded rows (so categories seeded before the zh fallback get a
        Chinese label on the next startup). Returns inserted count. Idempotent.
        """
        from arrow_lake.knowledge_graph.doc_type_router import (
            DOC_TYPE_ALIASES,
            DOC_TYPE_DESCRIPTIONS,
        )

        inserted = 0
        if self._count() == 0:
            for name in DOC_TYPE_DESCRIPTIONS:  # canonical order
                aliases = DOC_TYPE_ALIASES.get(name, ())
                zh = self._canonical_zh(name, aliases)
                # INSERT OR IGNORE: concurrency-safe under multi-worker startup
                # (two workers may both see count==0 before either commits; a
                # plain INSERT would crash the loser on PK violation).
                self._db.execute(
                    """INSERT OR IGNORE INTO doc_type_categories (name, desc_zh, desc_en, aliases, source)
                       VALUES (?, ?, ?, ?, 'seed')""",
                    (name, zh, DOC_TYPE_DESCRIPTIONS[name], ",".join(aliases)),
                )
                inserted += 1
        # backfill: ensure every canonical category has a zh label (covers rows
        # seeded before _SEED_ZH_FALLBACK existed, e.g. general/project). The
        # UPDATE is a no-op once desc_zh is populated. Idempotent + cheap.
        for name in DOC_TYPE_DESCRIPTIONS:
            zh = self._canonical_zh(name, DOC_TYPE_ALIASES.get(name, ()))
            if zh:
                self._db.execute(
                    "UPDATE doc_type_categories SET desc_zh = ? "
                    "WHERE name = ? AND (desc_zh IS NULL OR desc_zh = '')",
                    (zh, name),
                )
        self._db.commit()
        if inserted:
            logger.info("doc_type_categories_seeded count=%d", inserted)
        return inserted

    @staticmethod
    def _canonical_zh(name: str, aliases: tuple[str, ...]) -> str:
        """Short zh label for a canonical category: first CJK alias, else the
        fixed fallback (general/project). Empty if neither applies."""
        return next((a for a in aliases if _has_cjk(a)), "") or _SEED_ZH_FALLBACK.get(name, "")

    def _count(self) -> int:
        cur = self._db.execute("SELECT COUNT(*) FROM doc_type_categories")
        row = cur.fetchone() if cur is not None else None
        return int(row[0]) if row is not None else 0

    # --- read ---------------------------------------------------------------

    def list_categories(self) -> list[dict[str, Any]]:
        cur = self._db.execute(
            f"SELECT {', '.join(_COLS)} FROM doc_type_categories ORDER BY name")
        rows = cur.fetchall() if cur is not None else []
        out = [self._row(r) for r in rows]
        return out

    def known_names(self) -> set[str]:
        """All category names (seed + custom). Used by template validation to
        enforce ``category ∈ dictionary``."""
        cur = self._db.execute("SELECT name FROM doc_type_categories")
        rows = cur.fetchall() if cur is not None else []
        return {r[0] for r in rows}

    def get_category(self, name: str) -> dict[str, Any] | None:
        cur = self._db.execute(
            f"SELECT {', '.join(_COLS)} FROM doc_type_categories WHERE name = ?",
            (name,))
        row = cur.fetchone() if cur is not None else None
        return self._row(row) if row is not None else None

    # --- write --------------------------------------------------------------

    def add_category(
        self, name: str, *, desc_zh: str | None = None, desc_en: str | None = None,
        aliases: list[str] | None = None, source: str = "custom",
    ) -> None:
        """Insert a new category (``source='custom'`` by default). Raises
        :class:`ValueError` on an invalid/duplicate name.
        """
        if not isinstance(name, str) or not _CATEGORY_NAME_RE.match(name):
            raise ValueError(f"invalid category name {name!r} (must match {_CATEGORY_NAME_RE.pattern})")
        if self.get_category(name) is not None:
            raise CategoryExistsError(name)
        self._db.execute(
            """INSERT INTO doc_type_categories (name, desc_zh, desc_en, aliases, source)
               VALUES (?, ?, ?, ?, ?)""",
            (name, desc_zh, desc_en, ",".join(aliases or []), source),
        )
        self._db.commit()  # libsql 不 autocommit,显式提交才跨连接/重启持久
        logger.info("doc_type_category_added name=%s source=%s", name, source)

    def delete_category(self, name: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM doc_type_categories WHERE name = ?", (name,))
        self._db.commit()
        removed = cur is not None and cur.rowcount > 0
        if removed:
            logger.info("doc_type_category_deleted name=%s", name)
        return removed

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r) if hasattr(r, "keys") else dict(zip(_COLS, r))
        d["aliases"] = _parse_aliases(d.get("aliases"))
        return d


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in (text or ""))
