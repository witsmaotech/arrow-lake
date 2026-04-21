"""Admin mixin — catalog, dataset management, workflows, and versioning."""

from __future__ import annotations

from typing import Any


class _LakeAdminMixin:
    """Provides catalog listing, dataset management, workflow introspection, and versioning."""

    def catalog(self) -> Any:
        """List all datasets with metadata (Story 7.1).

        Returns:
            CatalogResult with dataset entries (name, version, row count).
        """
        from arrow_lake._models import CatalogEntry, CatalogResult

        storage = self._get_storage()
        names = storage.list_datasets()
        entries: list[CatalogEntry] = []
        for name in names:
            try:
                ds = storage.open_dataset(name)
                num_rows = ds.count_rows()
            except Exception:
                num_rows = 0
            try:
                version = storage.get_version(name)
            except Exception:
                version = 0
            entries.append(CatalogEntry(name=name, version=version, num_rows=num_rows))
        return CatalogResult(datasets=entries, total=len(entries))

    def list_datasets(self) -> list[str]:
        """List all dataset names.

        Returns:
            Sorted list of dataset name strings.
        """
        return self._get_storage().list_datasets()

    def delete_dataset(self, name: str) -> None:
        """Delete a dataset and all its data.

        Args:
            name: Dataset name to delete.
        """
        self._get_storage().delete_dataset(name)

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
