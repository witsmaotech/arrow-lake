"""Admin mixin — catalog, dataset management, workflows, versioning, backup, health."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from arrow_lake.exceptions import StorageError

logger = logging.getLogger(__name__)

# Short-TTL cache for catalog() — /datasets opens every dataset on each call
# (count_rows + list_versions + list_indices). This avoids re-doing that work
# within a few seconds. Stale window ≤ _CATALOG_CACHE_TTL; bumped through on
# next call. Invalidation is time-based (ingest/delete settle within TTL).
_CATALOG_CACHE_TTL: float = 5.0
_CATALOG_CACHE: dict[str, Any] = {"ts": 0.0, "result": None}

# W4.2: columns stamped by the documents/images/videos ingest pipelines —
# their presence marks a dataset for the console's text (non-structured) line.
_DOCUMENT_HINT_COLUMNS = frozenset({"chunk_text", "page_number", "chunk_index"})

if TYPE_CHECKING:
    from arrow_lake._models import CatalogResult, HealthInfo
    from arrow_lake.ops.backup import BackupInfo


class _LakeAdminMixin:
    """Provides catalog listing, dataset management, workflow introspection, versioning, backup, and health."""

    def catalog(self) -> CatalogResult:
        """List all datasets with metadata (Story 7.1).

        Returns:
            CatalogResult with dataset entries (name, version, row count).
        """
        from arrow_lake.core.metrics import catalog_queries_total, get_metrics_enabled

        if get_metrics_enabled():
            catalog_queries_total.inc()
        from arrow_lake._models import CatalogEntry, CatalogResult

        cached = _CATALOG_CACHE["result"]
        if cached is not None and (time.monotonic() - _CATALOG_CACHE["ts"]) < _CATALOG_CACHE_TTL:
            return cached

        storage = self._get_storage()
        names = storage.list_datasets()
        entries: list[CatalogEntry] = []
        for name in names:
            ds = None
            num_rows = 0
            kind = "structured"
            try:
                ds = storage.open_dataset(name)
                num_rows = ds.count_rows()
            except (StorageError, OSError):
                ds = None
            try:
                version = storage.get_version(name)
            except (StorageError, OSError):
                version = 0
            num_columns = 0
            vector_dim: int | None = None
            has_vector = False
            has_fts = False
            size_bytes: int | None = None
            created_at: str | None = None
            updated_at: str | None = None
            if ds is not None:
                # The dataset is already opened for count_rows above; pull the
                # cheap extended metadata from the same handle (schema, indices,
                # size) so the catalog table can show columns / vector dim /
                # index status / size without extra opens.
                try:
                    import pyarrow as pa

                    schema = ds.schema
                    num_columns = len(schema)
                    for field in schema:
                        if pa.types.is_fixed_size_list(field.type):
                            vector_dim = int(field.type.list_size)
                            break
                    # W4.2: document heuristic — the documents/images/videos
                    # ingest pipelines stamp these columns; a table carrying
                    # any of them is presented on the console's text line.
                    if any(
                        f.name in _DOCUMENT_HINT_COLUMNS for f in schema
                    ):
                        kind = "document"
                except Exception:  # noqa: BLE001
                    pass
                try:
                    for idx in ds.list_indices() or []:
                        if isinstance(idx, dict):
                            t = str(idx.get("type") or idx.get("index_type") or "")
                            cols = idx.get("columns") or []
                        else:
                            t = str(getattr(idx, "type", None) or getattr(idx, "index_type", None) or "")
                            cols = getattr(idx, "columns", None) or []
                        tu = t.upper()
                        cols_s = " ".join(str(c).lower() for c in cols)
                        if "FTS" in tu or "BM25" in tu or "INVERT" in tu or "fts" in cols_s:
                            has_fts = True
                        elif "VECTOR" in tu or "IVF" in tu or "HNSW" in tu:
                            has_vector = True
                except Exception:  # noqa: BLE001
                    pass
                # LanceTable.list_versions() → [{"version","timestamp"(datetime),
                # "metadata": {"total_files_size"(str), ...}}]. One call gives us
                # initial-ingest time (v1), last-write time (latest version), and
                # the on-disk size (latest version's total_files_size). The
                # LanceTable wrapper lacks .versions()/.size_in_bytes(), so this
                # is the right entry point.
                try:
                    vs = ds.list_versions() or []
                    if vs:
                        # Single pass for v1 (created) + latest (updated): avoid
                        # sorting the whole version list just to read both ends.
                        first = last = vs[0]
                        for v in vs[1:]:
                            vv = v.get("version", 0)
                            if vv < first.get("version", 0):
                                first = v
                            if vv > last.get("version", 0):
                                last = v
                        c, u = first.get("timestamp"), last.get("timestamp")
                        if c is not None:
                            created_at = c.isoformat() if hasattr(c, "isoformat") else str(c)
                        if u is not None:
                            updated_at = u.isoformat() if hasattr(u, "isoformat") else str(u)
                        meta = last.get("metadata") or {}
                        sz = meta.get("total_files_size")
                        if sz is not None:
                            size_bytes = int(sz)
                except Exception:  # noqa: BLE001
                    pass
            entries.append(CatalogEntry(
                name=name, version=version, num_rows=num_rows,
                num_columns=num_columns, vector_dim=vector_dim,
                has_vector_index=has_vector, has_fts_index=has_fts, size_bytes=size_bytes,
                created_at=created_at, updated_at=updated_at,
                kind=kind,
            ))
        # W4.2: container datasets (DR14) — a directory of tables, invisible
        # to the bare-table listing above. Sum per-table row counts cheaply
        # via per-table opens; per-table schema/index details stay on the
        # detail/schema endpoints (?table=), not on the catalog row.
        # P1-6 (review 2026-08-26): ONE enumeration returns {name: tables};
        # the per-container list_container_tables re-query is gone.
        try:
            containers = storage.list_containers_with_tables()
        except Exception:  # noqa: BLE001 — listing must not fail the catalog
            containers = {}
        for cname, tnames in containers.items():
            num_rows = 0
            for tname in tnames:
                try:
                    num_rows += int(
                        storage.open_dataset(cname, table=tname).count_rows())
                except (StorageError, OSError):
                    pass
            entries.append(CatalogEntry(
                name=cname, version=0, num_rows=num_rows, num_columns=0,
                kind="container",
            ))
        entries.sort(key=lambda e: e.name)
        result = CatalogResult(datasets=entries, total=len(entries))
        _CATALOG_CACHE["ts"] = time.monotonic()
        _CATALOG_CACHE["result"] = result
        return result

    def list_indices(self, dataset_name: str) -> list[dict[str, Any]]:
        """List indexes on a dataset as [{name, type, columns}].

        Normalizes Lance's IndexMetadata (dict or object form) for the API.
        """
        ds = self._get_storage().open_dataset(dataset_name)
        out: list[dict[str, Any]] = []
        for idx in ds.list_indices() or []:
            if isinstance(idx, dict):
                name = idx.get("name")
                t = idx.get("type") or idx.get("index_type")
                cols = idx.get("columns") or []
            else:
                name = getattr(idx, "name", None)
                t = getattr(idx, "type", None) or getattr(idx, "index_type", None)
                cols = getattr(idx, "columns", None) or []
            out.append({"name": name, "type": str(t or ""), "columns": list(cols)})
        return out

    def drop_index(self, dataset_name: str, index_name: str) -> None:
        """Drop an index by name (LanceTable.drop_index)."""
        ds = self._get_storage().open_dataset(dataset_name)
        ds.drop_index(index_name)

    def list_datasets(self) -> list[str]:
        """List all dataset names.

        Returns:
            Sorted list of dataset name strings.
        """
        return self._get_storage().list_datasets()

    def open_dataset(self, name: str, *, table: str | None = None) -> Any:
        """Open a dataset and return the underlying Lance dataset object.

        Args:
            name: Dataset name.
            table: Optional table name within a container dataset (DR14).

        Returns:
            The opened Lance dataset.

        Raises:
            StorageError: If the dataset does not exist.
        """
        return self._get_storage().open_dataset(name, table=table)

    def update_field_comments(self, name: str, comments: dict[str, str]) -> None:
        """In-place update of column ``comment`` metadata (no data rewrite).

        Thin facade over ``StorageManager.update_field_comments`` for the
        ``/schema/annotate`` endpoint and DB comment capture.
        """
        self._get_storage().update_field_comments(name, comments)

    def delete_dataset(
        self, name: str, *, actor: str = "system", cascade: bool = True,
        table: str | None = None,
    ) -> None:
        """Delete a dataset and all its data.

        Args:
            name: Dataset name to delete.
            actor: Who triggered the deletion (for the audit record).
            cascade: When True (default) also reclaim the derived assets built
                on top of the dataset — its KG graph (``kg_{name}``), KA dump
                directory, Gravitino/catalog metadata, RBAC grants/denies, and
                extraction-template bindings. Set False to drop only the Lance
                table (e.g. to preserve a KA dump for re-ingesting the same
                name). Every cascade step is best-effort and never blocks the
                core Lance-table deletion.
            table: Optional — drop ONLY this table inside a container dataset
                (siblings and container-derived assets are untouched, so
                cascade does not apply). P2-1 (review 2026-08-26 §三): the
                container_registry row is reclaimed here too — the registry
                used to be write-only, so a dropped table stayed declared
                forever (permanent control-plane drift).
        """
        pre_version = self._safe_version(name)
        with self._trace_span("delete_dataset", dataset=name):
            self._get_storage().delete_dataset(name, table=table)
        if table is not None:
            # Table-scoped delete: only the container_registry row needs
            # reclaiming (best-effort — registry is a mirror, D3).
            try:
                catalog_store = getattr(self, "_catalog_store", None)
                if catalog_store is not None:
                    catalog_store.drop_container_table(name, table)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "cascade container table unregister failed for %s/%s",
                    name, table, exc_info=True,
                )
            try:
                self.audit_record(
                    "dataset.table_deleted", dataset_name=name, actor=actor,
                    lance_version=pre_version,
                    payload={"actor": actor, "table": table},
                )
            except Exception:  # noqa: BLE001
                logger.warning("delete audit failed for %s", name, exc_info=True)
            return
        # Cascade: reclaim derived assets (best-effort, per-subsystem isolation).
        # KG graph drop is gated by cascade too so cascade=False truly preserves
        # everything built on top (KG + KA + metadata) for same-name reuse.
        cascade_summary: dict[str, str] = {}
        if cascade:
            self._drop_dataset_kg_graph_best_effort(name)
            cascade_summary = self._cascade_delete_derived_assets(name)
        from arrow_lake.core.metrics import catalog_tables_total, get_metrics_enabled

        if get_metrics_enabled():
            val = catalog_tables_total._value.get()
            if val is not None and val > 0:
                catalog_tables_total.dec()
        # v1.9.4: durable audit for destructive ops (compliance red line).
        # Covers ALL callers (REST + CLI + internal); best-effort, never blocks.
        try:
            self.audit_record(
                "dataset.deleted", dataset_name=name, actor=actor,
                lance_version=pre_version,
                payload={"actor": actor, "cascade": cascade, **cascade_summary},
            )
        except Exception:  # noqa: BLE001
            logger.warning("delete audit failed for %s", name, exc_info=True)

    def _cascade_delete_derived_assets(self, name: str) -> dict[str, str]:
        """Best-effort cleanup of a dataset's derived assets after its Lance
        table is dropped.

        Each subsystem is isolated in its own try/except — a failure in one
        (e.g. Gravitino down) never blocks the others or the overall deletion.
        Returns a ``{step: "ok"|"skipped"|"failed"}`` summary for the audit
        record (observability: which cleanups ran vs were unavailable).
        """
        import shutil
        from pathlib import Path

        summary: dict[str, str] = {}

        # 1) KA dump directory: {he_ka_base_dir}/{artifact_key}/ holds ka/ and
        # obsidian/. NOTE the path divergence — the Obsidian export (_lake_kg)
        # keys on the RAW dataset name while ka/ keys on artifact_key_for(name);
        # for non-canonical names (mixed case / hyphens / >45 chars) they split,
        # so rmtree BOTH stems (usually identical; cheap and safe).
        try:
            hg_cfg = getattr(self._config, "hugegraph", None)
            ka_base = getattr(hg_cfg, "he_ka_base_dir", None)
            if ka_base:
                from arrow_lake.knowledge_graph._naming import artifact_key_for

                for stem in {artifact_key_for(name), name}:
                    shutil.rmtree(Path(ka_base) / stem, ignore_errors=True)
                summary["ka_dir"] = "ok"
            else:
                summary["ka_dir"] = "skipped"
        except Exception:  # noqa: BLE001
            summary["ka_dir"] = "failed"
            logger.warning("cascade KA dir purge failed for %s", name, exc_info=True)

        # 2) libSQL catalog registry row (v1.9.0 control plane) + container
        # registry row (DR14 W1.2 — plain datasets are usually not registered
        # as containers; unregister is a harmless no-op when absent).
        try:
            catalog_store = getattr(self, "_catalog_store", None)
            if catalog_store is not None:
                catalog_store.delete_table(name)
                try:
                    catalog_store.unregister_container(name)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "cascade container unregister failed for %s", name, exc_info=True
                    )
                summary["catalog"] = "ok"
            else:
                summary["catalog"] = "skipped"
        except Exception:  # noqa: BLE001
            summary["catalog"] = "failed"
            logger.warning("cascade catalog cleanup failed for %s", name, exc_info=True)

        # 3) Gravitino table + fileset deregistration (_get_gravitino_bridge is
        # not None-safe; guard on config.gravitino.enabled before calling).
        try:
            if getattr(self._config.gravitino, "enabled", False):
                self._get_gravitino_bridge().deregister_dataset(name)
                summary["gravitino"] = "ok"
            else:
                summary["gravitino"] = "skipped"
        except Exception:  # noqa: BLE001
            summary["gravitino"] = "failed"
            logger.warning(
                "cascade Gravitino deregister failed for %s", name, exc_info=True
            )

        # 4) RBAC grants + row/col ACL + denies.
        try:
            rbac_store = getattr(self, "_rbac_store", None)
            if rbac_store is not None:
                rbac_store.purge_dataset(name)
                summary["rbac"] = "ok"
            else:
                summary["rbac"] = "skipped"
        except Exception:  # noqa: BLE001
            summary["rbac"] = "failed"
            logger.warning("cascade RBAC purge failed for %s", name, exc_info=True)

        # 5) Extraction-template bindings.
        try:
            tpl_store = getattr(self, "_extraction_template_store", None)
            if tpl_store is not None:
                tpl_store.clear_binding(name)
                summary["template_bindings"] = "ok"
            else:
                summary["template_bindings"] = "skipped"
        except Exception:  # noqa: BLE001
            summary["template_bindings"] = "failed"
            logger.warning(
                "cascade template-binding clear failed for %s", name, exc_info=True
            )

        return summary

    def _drop_dataset_kg_graph_best_effort(self, name: str) -> None:
        """v1.8.6: best-effort drop of the ``kg_{name}`` graph on dataset delete.

        Bridges sync (CLI) and async (API) call contexts. A missing graph or
        KG-disabled is a no-op; never raises — dataset deletion must not fail
        because the KG graph drop failed.
        """
        try:
            client = self._get_kg_client()  # None when KG disabled (no raise)
        except Exception:
            return
        if client is None:
            return
        try:
            import asyncio
            from arrow_lake.knowledge_graph._naming import graph_name_for
            coro = client.drop_graph(graph_name_for(name))
            try:
                asyncio.get_running_loop()  # raises RuntimeError if no loop
                from arrow_lake.api.tasks import spawn_background
                spawn_background(coro)  # async ctx: fire-and-forget (strong ref)
            except RuntimeError:
                asyncio.run(coro)  # sync ctx: run to completion
        except Exception as exc:
            # HugeGraph drop_graph 对不存在的图返回非 2xx → KGError。这正是
            # desired state(图已不在):按 docstring "missing graph is a no-op" 静默,
            # 不刷 traceback 噪音;仅对真失败(网络/权限/5xx)告警。
            msg = str(exc).lower()
            ctx = getattr(exc, "context", None) or {}
            status = ctx.get("status_code") if isinstance(ctx, dict) else None
            if status == 404 or any(
                k in msg for k in ("not exist", "not found", "does not exist", "no such")
            ):
                logger.debug("KG graph already absent for dataset %s (no-op)", name)
                return
            logger.warning(
                "best-effort KG graph drop failed for dataset %s", name, exc_info=True
            )

    def restore_dataset(
        self, name: str, data: Any, *, actor: str = "system", table: str | None = None,
    ) -> None:
        """Replace a dataset entirely with new data (delete + recreate).

        Used for schema changes, column additions, and full dataset reloads.

        Args:
            name: Dataset name.
            data: Arrow table with the new dataset content.
            table: Optional table within a container dataset (DR14) — the
                restore targets ``{name}/{table}.lance`` keeping container
                layout intact.

        Raises:
            StorageError: If dataset does not exist or write fails.
        """
        self._get_storage().restore_dataset(name, data, table=table)
        # v1.9.4: audit restore (companion to delete audit); best-effort.
        try:
            self.audit_record(
                "dataset.restored", dataset_name=name, actor=actor,
                lance_version=self._safe_version(name), payload={"actor": actor},
            )
        except Exception:  # noqa: BLE001
            logger.warning("restore audit failed for %s", name, exc_info=True)

    def get_dataset_version(self, name: str) -> int:
        """Get the current version number of a dataset.

        Args:
            name: Dataset name.

        Returns:
            Version number (0-based).
        """
        return self._get_storage().get_version(name)

    def list_dataset_versions(self, name: str) -> list[dict[str, Any]]:
        """List all versions of a dataset.

        Args:
            name: Dataset name.

        Returns:
            List of version metadata dicts.
        """
        return self._get_storage().list_versions(name)

    # ------------------------------------------------------------------
    # Tag operations
    # ------------------------------------------------------------------

    def create_tag(self, dataset_name: str, tag: str, version: int | None = None) -> None:
        """Create a named tag for a dataset version.

        Tags provide human-readable aliases for specific dataset versions,
        enabling reproducible queries and data snapshot references.

        Args:
            dataset_name: Dataset to tag.
            tag: Tag name (e.g. "clean_baseline", "v1").
            version: Version to tag. If None, tags the latest version.
        """
        self._get_storage().create_tag(dataset_name, tag, version)

    def read_at_tag(self, dataset_name: str, tag: str) -> Any:
        """Read dataset data at a specific tag.

        Args:
            dataset_name: Dataset name.
            tag: Tag name to read at.

        Returns:
            Arrow Table with the data at the tagged version.
        """
        return self._get_storage().read_at_tag(dataset_name, tag)

    def list_tags(self, dataset_name: str) -> dict[str, int]:
        """List all tags for a dataset.

        Args:
            dataset_name: Dataset name.

        Returns:
            Dict mapping tag name to version number.
        """
        return self._get_storage().list_tags(dataset_name)

    def delete_tag(self, dataset_name: str, tag: str) -> None:
        """Delete a named tag from a dataset.

        Args:
            dataset_name: Dataset name.
            tag: Tag name to delete.
        """
        self._get_storage().delete_tag(dataset_name, tag)

    # ------------------------------------------------------------------
    # Branch operations (v1.8.0 #1 — Git-style data branching)
    # ------------------------------------------------------------------

    def create_branch(
        self, dataset_name: str, branch: str, version: int | None = None
    ) -> None:
        """Create a named branch at a dataset version (defaults to HEAD).

        Args:
            dataset_name: Dataset to branch.
            branch: Branch name (e.g. "experiment", "staging").
            version: Version to branch from (None = latest/HEAD).
        """
        self._get_storage().create_branch(dataset_name, branch, version)

    def list_branches(self, dataset_name: str) -> list[str]:
        """List all branch names for a dataset.

        Args:
            dataset_name: Dataset name.

        Returns:
            List of branch names.
        """
        return self._get_storage().list_branches(dataset_name)

    def delete_branch(self, dataset_name: str, branch: str) -> None:
        """Delete a named branch from a dataset.

        Args:
            dataset_name: Dataset name.
            branch: Branch name to delete.
        """
        self._get_storage().delete_branch(dataset_name, branch)

    def read_at_branch(self, dataset_name: str, branch: str) -> Any:
        """Read dataset data at a branch HEAD.

        Args:
            dataset_name: Dataset name.
            branch: Branch name to read at.

        Returns:
            Arrow Table with the branch HEAD data.
        """
        return self._get_storage().read_at_branch(dataset_name, branch)

    def add_blob_column(
        self, dataset_name: str, column_name: str, blobs: list[bytes]
    ) -> None:
        """Append raw binary blobs as a Lance column (v1.8.0 #2 — blob 存储).

        Stores media originals (image / audio / video bytes) in-place in Lance
        as a native binary column, alongside embeddings — avoiding a separate
        object store + path reference (多模态一致性 + 省一次 IO). Delegates to
        ``add_columns_table`` with a ``pa.binary()`` column.

        Args:
            dataset_name: Target dataset (must already exist).
            column_name: Name for the blob column.
            blobs: Raw bytes per row — length MUST equal the dataset row count.
        """
        import pyarrow as pa

        table = pa.table({column_name: pa.array(blobs, type=pa.binary())})
        self._get_storage().add_columns_table(dataset_name, table)

    # ------------------------------------------------------------------
    # Gravitino unified catalog (v1.8.0 #19 — three engines via Gravitino)
    # ------------------------------------------------------------------

    def _get_gravitino_bridge(self) -> Any:
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

        return self._get_component(
            "gravitino", lambda: GravitinoBridge(self._config.gravitino)
        )

    def gravitino_register_dataset(
        self, dataset_name: str, *, location: str = ""
    ) -> None:
        """Register a dataset in the Gravitino catalog (v1.8.0 #19).

        Registers as a Gravitino Table (with schema) + Fileset so DuckDB, Daft,
        and lancedb can all resolve the dataset via Gravitino — unified catalog.
        """
        schema = None
        try:
            schema = self._get_storage().open_dataset(dataset_name).schema
        except Exception:
            schema = None
        self._get_gravitino_bridge().register_dataset(
            dataset_name, schema=schema, location=location
        )

    def gravitino_deregister_dataset(self, dataset_name: str) -> None:
        """Remove a dataset from the Gravitino catalog (v1.8.0 #19)."""
        self._get_gravitino_bridge().deregister_dataset(dataset_name)

    def gravitino_sync_inbound(self) -> list[Any]:
        """Pull external metadata changes from Gravitino (v1.8.0 #19)."""
        return self._get_gravitino_bridge().sync_inbound()

    def gravitino_table_statistics(self, dataset_name: str) -> Any:
        """Get table statistics from Gravitino (v1.8.0 #19)."""
        return self._get_gravitino_bridge().get_table_statistics(dataset_name)

    def gravitino_health(self) -> tuple[str, bool]:
        """Gravitino catalog health check (v1.8.0 #19)."""
        return self._get_gravitino_bridge().health()

    def add_column(
        self, name: str, column_name: str, sql_expr: str, *, table: str | None = None,
    ) -> None:
        """Add a computed column to an existing dataset.

        Args:
            name: Dataset name.
            column_name: Name of the new column.
            sql_expr: SQL expression for the column value (e.g. "price * 0.9").
            table: Optional table within a container dataset (DR14).
        """
        self._get_storage().add_column(name, column_name, sql_expr, table=table)

    def add_columns_table(self, name: str, columns: Any, *, table: str | None = None) -> None:
        """Add pre-computed columns to a dataset without full rewrite.

        Avoids the cost of ``restore_dataset`` (drop + recreate) by using
        Lance's native in-place column addition.

        Args:
            name: Dataset name.
            columns: Arrow Table with new columns (must be row-aligned).
            table: Optional table within a container dataset (DR14).
        """
        self._get_storage().add_columns_table(name, columns, table=table)

    def alter_column(
        self, name: str, column_name: str, new_type: Any, *, table: str | None = None,
    ) -> None:
        """Change the data type of an existing column.

        Args:
            name: Dataset name.
            column_name: Column to modify.
            new_type: Target PyArrow data type (e.g. pa.float32()).
            table: Optional table within a container dataset (DR14).
        """
        self._get_storage().alter_column(name, column_name, new_type, table=table)

    def drop_column(self, name: str, column_name: str, *, table: str | None = None) -> None:
        """Remove a column from a dataset.

        Args:
            name: Dataset name.
            column_name: Column to remove.
            table: Optional table within a container dataset (DR14).
        """
        self._get_storage().drop_column(name, column_name, table=table)

    def compact_dataset(self, name: str) -> Any:
        """Compact a dataset by merging small fragments.

        Args:
            name: Dataset name.

        Returns:
            CompactionStats with before/after file counts and sizes.
        """
        with self._trace_span("compact_dataset", dataset=name):
            return self._get_storage().compact(name)

    def read_dataset(
        self, name: str, *, columns: list[str] | None = None,
        table: str | None = None, version: int | None = None,
    ) -> Any:
        """Read a dataset as an Arrow table.

        Args:
            name: Dataset name.
            columns: Optional column names to read (None = all).
            table: Optional table within a container dataset (DR14).
            version: Lance as-of version(四维 review M9:发布链三读同源
                ——锁版本后按 version 读,drift 基线/datasheet 与锁定的
                lance_version 一致;None = latest)。
        """
        return self._get_storage().read_dataset(
            name, columns=columns, table=table, version=version)

    def scan_dataset(self, name: str, **kwargs: Any) -> Any:
        """Create a Lance dataset scanner for lazy row-by-row reading.

        Args:
            name: Dataset name.
            **kwargs: Scanner options (columns, filter, etc.).
        """
        return self._get_storage().scan_dataset(name, **kwargs)

    def rename_dataset(self, name: str, new_name: str) -> None:
        """Rename a dataset (copy + delete original).

        Args:
            name: Current dataset name.
            new_name: Target dataset name.

        Raises:
            StorageError: If source not found, target already exists, or names invalid.
        """
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("rename_dataset"):
            self._get_storage().rename_dataset(name, new_name)

    def copy_dataset(self, name: str, new_name: str) -> None:
        """Copy a dataset to a new name.

        Args:
            name: Source dataset name.
            new_name: Target dataset name.

        Raises:
            StorageError: If source not found, target already exists, or names invalid.
        """
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("copy_dataset"):
            self._get_storage().copy_dataset(name, new_name)

    def merge_datasets(self, source_names: list[str], target_name: str) -> None:
        """Merge multiple source datasets into a single target.

        All sources must have identical schemas.

        Args:
            source_names: List of source dataset names.
            target_name: Name for the new merged dataset.

        Raises:
            StorageError: If any source not found, target exists, or schema mismatch.
        """
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("merge_datasets"):
            self._get_storage().merge_datasets(source_names, target_name)

    def backup_create(
        self,
        dataset_names: list[str] | None = None,
        *,
        blob_prefixes: list[str] | None = None,
        backup_id: str | None = None,
    ) -> BackupInfo:
        """Create a backup of Lance datasets and/or blob prefixes.

        Args:
            dataset_names: Datasets to back up (None = none).
            blob_prefixes: Blob prefixes to back up (None = none).
            backup_id: Custom backup ID (None = auto-generate).

        Returns:
            BackupInfo with backup metadata.
        """
        from arrow_lake.ops.backup import BackupManager
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        mgr = BackupManager(
            storage_config=sc,
            lance_base_uri=self._base_uri,
            blob_store=blob_store,
        )
        with self._trace_span("backup_create", datasets=len(dataset_names or [])):
            return mgr.create_backup(
                dataset_names=dataset_names,
                blob_prefixes=blob_prefixes,
                backup_id=backup_id,
            )

    def backup_restore(
        self,
        backup_id: str,
        *,
        dataset_names: list[str] | None = None,
        blob_prefixes: list[str] | None = None,
        overwrite: bool = False,
    ) -> BackupInfo:
        """Restore a backup.

        Args:
            backup_id: Backup ID to restore.
            dataset_names: Datasets to restore (None = all from backup).
            blob_prefixes: Blob prefixes to restore (None = all from backup).
            overwrite: Whether to overwrite existing datasets.

        Returns:
            BackupInfo with restored backup metadata.
        """
        from arrow_lake.ops.backup import BackupManager
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        mgr = BackupManager(
            storage_config=sc,
            lance_base_uri=self._base_uri,
            blob_store=blob_store,
        )
        with self._trace_span("backup_restore", backup_id=backup_id):
            return mgr.restore_backup(
                backup_id,
                dataset_names=dataset_names,
                blob_prefixes=blob_prefixes,
                overwrite=overwrite,
        )

    def backup_list(self) -> list[BackupInfo]:
        """List all available backups.

        Returns:
            List of BackupInfo sorted by creation time (newest first).
        """
        from arrow_lake.ops.backup import BackupManager
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        mgr = BackupManager(
            storage_config=sc,
            lance_base_uri=self._base_uri,
            blob_store=blob_store,
        )
        return mgr.list_backups()

    def backup_delete(self, backup_id: str) -> None:
        """Delete a backup and all its data.

        Args:
            backup_id: Backup ID to delete.
        """
        from arrow_lake.ops.backup import BackupManager
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        mgr = BackupManager(
            storage_config=sc,
            lance_base_uri=self._base_uri,
            blob_store=blob_store,
        )
        mgr.delete_backup(backup_id)

    def health(self) -> HealthInfo:
        """Return health status of the Arrow Lake SDK instance.

        Checks storage accessibility, DuckDB session pool stats, and uptime.

        Returns:
            HealthInfo with comprehensive health status.
        """
        from arrow_lake._models import HealthInfo
        from arrow_lake._version import __version__

        sc = self._config.storage
        base_uri = sc.base_uri
        storage_status = "not_found"
        storage_ok = False

        if base_uri.startswith("s3://"):
            try:
                import urllib.request

                endpoint = sc.s3_endpoint
                if endpoint:
                    health_url = endpoint.rstrip("/") + "/minio/health/live"
                    if not health_url.startswith(("http://", "https://")):
                        storage_status = "invalid_endpoint_scheme"
                    else:
                        urllib.request.urlopen(health_url, timeout=3)
                        storage_status = "accessible"
                        storage_ok = True
                else:
                    storage_status = "no_endpoint_configured"
            except (ImportError, OSError):
                storage_status = "endpoint_unreachable"
        elif os.path.isdir(base_uri):
            storage_status = "accessible"
            storage_ok = True

        status = "ok" if storage_ok else "degraded"
        uptime = time.monotonic() - self._start_time

        session_pool_info: dict[str, Any] | None = None
        sm = self._components.get("session_manager")
        if sm is not None:
            stats = sm.get_stats()
            session_pool_info = {
                "pool_size": stats.pool_size,
                "active_sessions": stats.active_sessions,
                "queued_requests": stats.queued_requests,
                "total_queries": stats.total_queries,
                "total_errors": stats.total_errors,
                "total_timeouts": stats.total_timeouts,
                "avg_wait_seconds": stats.avg_wait_seconds,
                "slow_query_count": stats.slow_query_count,
            }

        return HealthInfo(
            status=status,
            version=__version__,
            storage_status=storage_status,
            storage_ok=storage_ok,
            uptime_seconds=round(uptime, 2),
            session_pool=session_pool_info,
        )

    def list_flows(self) -> list[str]:
        """List all registered Metaflow workflow names (Epic 6).

        Returns:
            Sorted list of registered flow names.
        """
        import flows

        flows._register_flows()
        return flows.FlowRegistry.list_flows()

    def get_flow_info(self, name: str) -> dict[str, Any]:
        """Get metadata for a registered Metaflow workflow (Epic 6).

        Args:
            name: Registered flow name.

        Returns:
            Dict with flow class name, module, and docstring.

        Raises:
            WorkflowError: If flow is not registered.
        """
        import flows

        from arrow_lake.exceptions import ErrorCode, WorkflowError

        flows._register_flows()
        try:
            flow_cls = flows.FlowRegistry.get(name)
        except KeyError:
            raise WorkflowError(
                error_code=ErrorCode.WORKFLOW_FLOW_NOT_FOUND,
                message=f"Flow '{name}' is not registered",
            ) from None
        return {
            "name": name,
            "class": flow_cls.__name__,
            "module": flow_cls.__module__,
            "doc": flow_cls.__doc__,
        }

    def version(self) -> str:
        """Return the current platform version."""
        from arrow_lake._version import __version__

        return __version__

    # ------------------------------------------------------------------
    # Blob Lifecycle
    # ------------------------------------------------------------------

    def _get_lifecycle_manager(self):
        """Lazy-init BlobLifecycleManager from config."""
        return self._get_component(
            "lifecycle_manager",
            lambda: self._create_lifecycle_manager(),
        )

    def _create_lifecycle_manager(self):
        from arrow_lake.storage.blob_store import BlobStoreManager
        from arrow_lake.storage.lifecycle import BlobLifecycleManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        return BlobLifecycleManager(
            config=self._config.lifecycle,
            s3_client=blob_store._s3,
        )

    def lifecycle_apply(self, prefix: str = "") -> dict[str, Any]:
        """Apply lifecycle rules to the S3 bucket/prefix."""
        mgr = self._get_lifecycle_manager()
        return mgr.apply_lifecycle_rules(self._config.storage.s3_bucket, prefix=prefix)

    def lifecycle_status(self, prefix: str = "") -> list[dict[str, str]]:
        """Get storage tier for objects under prefix."""
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        blobs = blob_store.list_blobs(prefix=prefix)
        mgr = self._get_lifecycle_manager()
        results = []
        for blob in blobs:
            tier = mgr.get_object_tier(sc.s3_bucket, blob["key"])
            results.append({"key": blob["key"], "tier": tier, "size": str(blob.get("size", 0))})
        return results

    def lifecycle_restore(self, key: str, days: int = 7) -> dict[str, Any]:
        """Restore a Glacier-tiered object for temporary access."""
        mgr = self._get_lifecycle_manager()
        return mgr.restore_object(self._config.storage.s3_bucket, key, days=days)

    def lifecycle_estimate(
        self, total_size_gb: int, target_tier: str = "STANDARD_IA",
    ) -> dict[str, Any]:
        """Estimate monthly cost savings from tier transition."""
        mgr = self._get_lifecycle_manager()
        return mgr.estimate_cost_savings(total_size_gb, "STANDARD", target_tier)

    def lifecycle_rules(self, prefix: str = "") -> dict[str, Any]:
        """Preview lifecycle rules without applying."""
        mgr = self._get_lifecycle_manager()
        rules = mgr._build_lifecycle_rules(prefix)
        return {
            "enabled": self._config.lifecycle.enabled,
            "prefix": prefix or "(root)",
            "standard_to_ia_days": self._config.lifecycle.standard_to_ia_days,
            "ia_to_glacier_days": self._config.lifecycle.ia_to_glacier_days,
            "glacier_expiration_days": self._config.lifecycle.glacier_expiration_days,
            "excluded_prefixes": self._config.lifecycle.excluded_prefixes,
            "rules": rules,
        }
