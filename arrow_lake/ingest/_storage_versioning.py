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

    # ------------------------------------------------------------------
    # Branch operations (v1.8.0 #1 — Git-style data branching)
    # ------------------------------------------------------------------
    # Lance 7.0.0 branch model:
    #   - create_branch(name, reference=None)  → None/HEAD = branch from latest
    #   - branches.list() / branches.delete(name)
    #   - checkout_version((branch, None))     → read view at branch HEAD
    #     (lance.dataset(version=str) treats strings as TAGS, so branches must
    #      be resolved via checkout_version with the (branch, version) tuple)

    def create_branch(self, name: str, branch: str, version: int | None = None) -> None:
        """Create a named branch at a dataset version (defaults to HEAD).

        Lance branches are Git-style mutable refs: a branch created at HEAD
        tracks subsequent writes independently of main. Branching from HEAD
        (``version=None``) is the supported path; branching from a historical
        version delegates to Lance and may be unsupported upstream.

        Args:
            name: Dataset name.
            branch: Branch name.
            version: Version to branch from (defaults to latest/HEAD).

        Raises:
            StorageError: If dataset does not exist, name/branch invalid,
                or branch already exists.
        """
        self._validate_name(name)
        self._validate_identifier(branch, "branch")
        # Pre-check existence: Lance 7.0.0 raises a generic clone error on
        # duplicate branch names instead of a clean "already exists", so we
        # detect duplicates explicitly via list_branches.
        if branch in self.list_branches(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Branch '{branch}' already exists for dataset '{name}'",
            )
        import lance

        ds = lance.dataset(self.dataset_uri(name), storage_options=self._storage_options)
        # version=None → let Lance default to HEAD (proven path); explicit
        # historical versions delegate to Lance and may be unsupported upstream.
        reference = None if version is None else version
        try:
            ds.create_branch(branch, reference=reference)
        except (ValueError, OSError, RuntimeError) as exc:
            msg = str(exc).lower()
            if "already" in msg or "exists" in msg:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Branch '{branch}' already exists for dataset '{name}'",
                ) from exc
            raise

    def list_branches(self, name: str) -> list[str]:
        """List all branch names for a dataset.

        Args:
            name: Dataset name.

        Returns:
            List of branch names.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        self._validate_name(name)
        import lance

        ds = lance.dataset(self.dataset_uri(name), storage_options=self._storage_options)
        try:
            return list(ds.branches.list())
        except (ValueError, OSError, RuntimeError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to list branches for dataset '{name}'",
            ) from exc

    def delete_branch(self, name: str, branch: str) -> None:
        """Delete a named branch from a dataset.

        Args:
            name: Dataset name.
            branch: Branch name.

        Raises:
            StorageError: If dataset does not exist, name/branch invalid,
                or branch not found.
        """
        self._validate_name(name)
        self._validate_identifier(branch, "branch")
        import lance

        ds = lance.dataset(self.dataset_uri(name), storage_options=self._storage_options)
        try:
            ds.branches.delete(branch)
        except (ValueError, OSError, RuntimeError) as exc:
            msg = str(exc).lower()
            if "not found" in msg or "does not exist" in msg:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Branch '{branch}' not found for dataset '{name}'",
                ) from exc
            raise

    def read_at_branch(self, name: str, branch: str) -> pa.Table:
        """Read dataset data at a branch HEAD.

        Lance resolves branches via ``checkout_version((branch, None))``;
        string refs passed to ``lance.dataset(version=...)`` are interpreted
        as tags, so branches require the tuple checkout path.

        Args:
            name: Dataset name.
            branch: Branch name.

        Returns:
            Arrow Table with the branch HEAD data.

        Raises:
            StorageError: If dataset or branch does not exist, or name/branch invalid.
        """
        self._validate_name(name)
        self._validate_identifier(branch, "branch")

        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        import lance

        ds = lance.dataset(self.dataset_uri(name), storage_options=self._storage_options)
        try:
            branch_ds = ds.checkout_version((branch, None))
        except (ValueError, OSError, RuntimeError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Branch '{branch}' not found for dataset '{name}'",
            ) from exc

        return branch_ds.to_table()
