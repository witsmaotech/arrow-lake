"""Lightweight SQL migration runner (no alembic / no ORM dependency).

Sequential ``V<NNN>__<desc>.sql`` files under
``arrow_lake/system_db/migrations``; each applied once and recorded in the
``schema_version`` table. Migration files are idempotent
(``CREATE ... IF NOT EXISTS``) so a fresh DB and a re-run both succeed.

This deliberately avoids SQLAlchemy/alembic to match the project's no-ORM
status — control-plane schema is small and slowly evolving.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from arrow_lake.system_db.connection import SystemDB

logger = structlog.get_logger(__name__)

_MIGRATION_RE = re.compile(r"^V(\d+)__.*\.sql$")

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    filename   TEXT NOT NULL
);
"""


class Migrator:
    """Apply pending SQL migrations to a :class:`SystemDB`.

    Args:
        db: connected SystemDB.
        migrations_dir: directory of ``V<NNN>__*.sql`` files; defaults to
            the package ``migrations/`` folder.
    """

    def __init__(
        self, db: SystemDB, migrations_dir: Path | str | None = None
    ) -> None:
        self._db = db
        if migrations_dir in (None, ""):
            migrations_dir = Path(__file__).resolve().parent / "migrations"
        self._dir = Path(migrations_dir)

    def list_files(self) -> list[tuple[int, Path]]:
        """Return ``(version, path)`` pairs in ascending version order."""
        out: list[tuple[int, Path]] = []
        if not self._dir.is_dir():
            return out
        for path in self._dir.glob("V*.sql"):
            m = _MIGRATION_RE.match(path.name)
            if m:
                out.append((int(m.group(1)), path))
        return sorted(out, key=lambda t: t[0])

    def applied_versions(self) -> set[int]:
        """Return the set of migration versions already applied."""
        self._db.executescript(_SCHEMA_VERSION_DDL)
        self._db.commit()
        cur = self._db.execute("SELECT version FROM schema_version")
        rows = cur.fetchall() if cur is not None else []
        return {int(r[0]) for r in rows}

    def run(self) -> list[int]:
        """Apply every pending migration. Returns the versions applied now."""
        applied_now: list[int] = []
        versions = self.applied_versions()
        for version, path in self.list_files():
            if version in versions:
                continue
            sql = path.read_text(encoding="utf-8")
            logger.info("system_db_migrate", version=version, file=path.name)
            with self._db.with_write() as db:
                db.executescript(sql)
                db.execute(
                    "INSERT INTO schema_version (version, filename) VALUES (?, ?)",
                    (version, path.name),
                )
            applied_now.append(version)
        if applied_now:
            logger.info("system_db_migrate_done", applied=applied_now)
        else:
            logger.info("system_db_migrate_uptodate", applied=[])
        return applied_now
