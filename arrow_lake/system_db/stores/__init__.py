"""Control-plane stores over :class:`arrow_lake.system_db.SystemDB`.

Each store wraps a narrow slice of the relational schema and is the sole
write/read owner of its tables. Upper-layer domain objects
(``PermissionChecker`` / auth / ...) depend on the store *Protocol*, not on
libsql details — so the store can be swapped or mocked without touching
callers.

P0 ships :class:`RbacStore` and :class:`IdentityStore`. P1+ add catalog /
task-history / lineage-index / rag-session / governance / user-state stores.
"""

from __future__ import annotations

from arrow_lake.system_db.stores.base import TTLCache, FailMode
from arrow_lake.system_db.stores.catalog import CatalogStore
from arrow_lake.system_db.stores.governance import GovernanceStore
from arrow_lake.system_db.stores.identity import IdentityStore
from arrow_lake.system_db.stores.ingest_dlq import IngestDLQStore
from arrow_lake.system_db.stores.lineage_index import LineageIndexStore
from arrow_lake.system_db.stores.ontology import OntologyRulesStore, OntologyVersionStore
from arrow_lake.system_db.stores.rag_session import RagSessionStore
from arrow_lake.system_db.stores.rbac import RbacStore
from arrow_lake.system_db.stores.task_history import TaskHistoryStore
from arrow_lake.system_db.stores.user_state import UserStateStore

__all__ = [
    "CatalogStore",
    "FailMode",
    "GovernanceStore",
    "IdentityStore",
    "IngestDLQStore",
    "LineageIndexStore",
    "OntologyRulesStore",
    "OntologyVersionStore",
    "RagSessionStore",
    "RbacStore",
    "TaskHistoryStore",
    "TTLCache",
    "UserStateStore",
]
