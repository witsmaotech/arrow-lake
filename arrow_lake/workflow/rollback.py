"""State rollback via Lance version checkpointing (Story 6.5).

Provides checkpoint/rollback functionality for workflow steps:
- Checkpoint current Lance version before step execution
- Roll back to last-known-good version on FATAL errors
- Preserve dead-letter tables during rollback
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from arrow_lake.exceptions import ErrorCode, StorageError, WorkflowError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CheckpointInfo:
    """Immutable record of a version checkpoint."""

    dataset_name: str
    version: int
    tag: str
    timestamp: str


class StateRollback:
    """Manages Lance version checkpoints and rollback operations.

    Usage::

        rb = StateRollback(storage_manager)
        rb.checkpoint("documents", tag="pre-step-validate")
        # ... execute step that may fail ...
        rb.rollback("documents")  # restores to checkpoint
    """

    def __init__(self, storage: Any) -> None:
        self._storage = storage
        self._checkpoints: dict[str, CheckpointInfo] = {}

    def checkpoint(
        self,
        dataset_name: str,
        *,
        tag_prefix: str = "pre-step",
    ) -> CheckpointInfo:
        """Create a version checkpoint for a dataset.

        Args:
            dataset_name: Name of the Lance dataset.
            tag_prefix: Prefix for the checkpoint tag.

        Returns:
            CheckpointInfo with version details.

        Raises:
            WorkflowError: If checkpointing fails.
        """
        from datetime import UTC, datetime

        try:
            version = self._storage.get_version(dataset_name)
            timestamp = datetime.now(tz=UTC).isoformat()
            tag = f"{tag_prefix}-v{version}"
            self._storage.create_tag(dataset_name, tag, version=version)

            info = CheckpointInfo(
                dataset_name=dataset_name,
                version=version,
                tag=tag,
                timestamp=timestamp,
            )
            self._checkpoints[dataset_name] = info
            logger.info(
                "workflow_checkpoint_created",
                dataset=dataset_name,
                version=version,
                tag=tag,
            )
            return info
        except (OSError, StorageError) as exc:
            raise WorkflowError(
                error_code=ErrorCode.WORKFLOW_STATE_ROLLBACK_FAILED,
                message=f"Failed to checkpoint {dataset_name}: {exc}",
            ) from exc

    def rollback(self, dataset_name: str) -> int:
        """Roll back a dataset to its last checkpoint.

        Args:
            dataset_name: Name of the dataset to roll back.

        Returns:
            The version number after rollback.

        Raises:
            WorkflowError: If no checkpoint exists or rollback fails.
        """
        info = self._checkpoints.get(dataset_name)
        if info is None:
            raise WorkflowError(
                error_code=ErrorCode.WORKFLOW_STATE_ROLLBACK_FAILED,
                message=f"No checkpoint found for {dataset_name}",
            )

        tmp_name = f"_rollback_safety_{dataset_name}"
        # P0-2 (C3, 2026-08-21): only clean up the safety copy once the data is
        # known to be safely back in place (restore or recovery succeeded). On
        # the double-failure path the safety copy is the *only* surviving
        # replica — deleting it there destroyed the data permanently.
        data_restored = False
        try:
            # Create safety copy of the *original* dataset before touching it.
            self._storage.copy_dataset(dataset_name, tmp_name)

            # Read data at checkpoint version to restore it as current
            checkpoint_data = self._storage.read_at_tag(dataset_name, info.tag)
            logger.info(
                "rollback_safety_copy_created",
                dataset=dataset_name,
                tmp_name=tmp_name,
            )

            # Delete original dataset and recreate with checkpoint data
            self._storage.restore_dataset(dataset_name, checkpoint_data)
            data_restored = True

            new_version = self._storage.get_version(dataset_name)
            logger.info(
                "workflow_rollback_completed",
                dataset=dataset_name,
                from_version=info.version,
                to_version=new_version,
            )
            return new_version
        except (OSError, StorageError, WorkflowError):
            # Attempt recovery from the safety copy
            try:
                self._storage.restore_from(tmp_name, dataset_name)
                data_restored = True
                logger.info(
                    "rollback_recovery_from_safety_succeeded",
                    dataset=dataset_name,
                )
            except Exception:
                logger.error(
                    "rollback_recovery_failed",
                    dataset=dataset_name,
                    tmp_name=tmp_name,
                    message=(
                        f"Data may only exist at safety dataset '{tmp_name}' — "
                        f"safety copy preserved for manual recovery"
                    ),
                )
            raise
        finally:
            # Clean up the safety copy only when the data is confirmed restored;
            # otherwise it is the last surviving replica and must be kept.
            if data_restored:
                try:
                    self._storage.delete_dataset(tmp_name)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "rollback_safety_cleanup_failed",
                        tmp_name=tmp_name,
                        message="Temp safety dataset not cleaned up",
                    )

    def has_checkpoint(self, dataset_name: str) -> bool:
        """Check if a dataset has a checkpoint.

        Args:
            dataset_name: Name of the dataset.

        Returns:
            True if a checkpoint exists.
        """
        return dataset_name in self._checkpoints

    def clear_checkpoint(self, dataset_name: str) -> None:
        """Remove a checkpoint record (does not delete the tag).

        Args:
            dataset_name: Name of the dataset.
        """
        self._checkpoints.pop(dataset_name, None)
