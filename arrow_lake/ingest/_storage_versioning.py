"""StorageVersioningMixin -- version management and tag operations."""

from __future__ import annotations

from typing import cast

import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, StorageError


class StorageVersioningMixin:
    """Versioning and tag operations for Lance datasets."""

    def get_version(self, name: str) -> int:
        """Get the current version number of a dataset.

        Args:
            name: Dataset name.

        Returns:
            Current version number.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        self._validate_name(name)
        table = self._open_lance(self._get_dataset_path(name))
        return cast(int, table.version)

    def list_versions(self, name: str) -> list[dict[str, object]]:
        """List all versions of a dataset with metadata.

        Args:
            name: Dataset name.

        Returns:
            List of version metadata dicts, each containing
            'version', 'timestamp', and 'metadata'.
        """
        self._validate_name(name)
        table = self._open_lance(self._get_dataset_path(name))
        raw_versions = table.list_versions()
        return [
            {
                "version": v["version"],
                "timestamp": v["timestamp"],
                "metadata": v["metadata"],
            }
            for v in raw_versions
        ]

    def create_tag(self, name: str, tag: str, version: int | None = None) -> None:
        """Create a named tag for a dataset version.

        Args:
            name: Dataset name.
            tag: Tag name.
            version: Version to tag (defaults to latest).

        Raises:
            StorageError: If dataset does not exist, name/tag invalid, or tag already exists.
        """
        self._validate_name(name)
        self._validate_identifier(tag, "tag")
        table = self._open_lance(self._get_dataset_path(name))
        if version is None:
            version = table.version
        try:
            table.tags.create(tag, version=version)
        except (ValueError, OSError, RuntimeError) as exc:
            msg = str(exc).lower()
            if "already" in msg or "exists" in msg:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Tag '{tag}' already exists for dataset '{name}'",
                ) from exc
            raise

    def list_tags(self, name: str) -> dict[str, int]:
        """List all tags for a dataset.

        Args:
            name: Dataset name.

        Returns:
            Dict mapping tag names to version numbers.

        Raises:
            StorageError: If a tag cannot be resolved or name is invalid.
        """
        self._validate_name(name)
        import lance

        lance_uri = self.dataset_uri(name)
        table = self._open_lance(self._get_dataset_path(name))
        tag_names = list(table.tags.list())
        result: dict[str, int] = {}
        for tag_name in tag_names:
            try:
                ds = lance.dataset(
                    lance_uri, version=tag_name, storage_options=self._storage_options
                )
                result[tag_name] = ds.version
            except (ValueError, OSError, RuntimeError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Failed to resolve tag '{tag_name}' for dataset '{name}'",
                ) from exc
        return result

    def delete_tag(self, name: str, tag: str) -> None:
        """Delete a named tag from a dataset.

        Args:
            name: Dataset name.
            tag: Tag name.

        Raises:
            StorageError: If dataset does not exist, name/tag invalid, or tag not found.
        """
        self._validate_name(name)
        self._validate_identifier(tag, "tag")
        table = self._open_lance(self._get_dataset_path(name))
        try:
            table.tags.delete(tag)
        except (ValueError, OSError, RuntimeError) as exc:
            msg = str(exc).lower()
            if "not found" in msg or "does not exist" in msg:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Tag '{tag}' not found for dataset '{name}'",
                ) from exc
            raise

    def read_at_tag(self, name: str, tag: str) -> pa.Table:
        """Read dataset data at a specific tag.

        Args:
            name: Dataset name.
            tag: Tag name.

        Returns:
            Arrow Table with the tagged version's data.

        Raises:
            StorageError: If dataset or tag does not exist, or name/tag invalid.
        """
        self._validate_name(name)
        self._validate_identifier(tag, "tag")
        import lance

        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        lance_uri = self.dataset_uri(name)

        try:
            ds = lance.dataset(
                lance_uri, version=tag, storage_options=self._storage_options
            )
        except (ValueError, OSError, RuntimeError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Tag '{tag}' not found for dataset '{name}'",
            ) from exc

        return ds.to_table()
