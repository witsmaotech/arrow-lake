"""System database (libSQL/Turso) — unified control-plane persistence.

Public surface:

* :class:`SystemDB`   — singleton libSQL connection (retry + health probe).
* :class:`Migrator`   — lightweight sequential SQL migration runner.
* :class:`SystemDBError`, :class:`SystemDBConfig`.

Stores live under :mod:`arrow_lake.system_db.stores`.
"""

from __future__ import annotations

from arrow_lake.config.system_db import SystemDBConfig
from arrow_lake.system_db.connection import SystemDB, SystemDBError
from arrow_lake.system_db.migrator import Migrator

__all__ = [
    "Migrator",
    "SystemDB",
    "SystemDBConfig",
    "SystemDBError",
]
