"""Tests for Story 6.5 — State Rollback."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.exceptions import WorkflowError
from arrow_lake.workflow.rollback import CheckpointInfo, StateRollback


class TestCheckpointInfo:
    """Test CheckpointInfo frozen dataclass."""

    def test_fields(self) -> None:
        info = CheckpointInfo(
            dataset_name="documents",
            version=3,
            tag="pre-step-v3",
            timestamp="2026-04-14T12:00:00Z",
        )
        assert info.dataset_name == "documents"
        assert info.version == 3
        assert "pre-step" in info.tag

    def test_frozen(self) -> None:
        info = CheckpointInfo(dataset_name="d", version=1, tag="t", timestamp="ts")
        with pytest.raises(AttributeError):
            info.version = 99


class TestStateRollback:
    """Test StateRollback checkpoint and rollback."""

    def test_checkpoint_creates_tag(self) -> None:
        storage = MagicMock()
        storage.get_version.return_value = 5
        rb = StateRollback(storage)
        info = rb.checkpoint("documents")
        assert info.version == 5
        assert "pre-step" in info.tag
        storage.create_tag.assert_called_once()
        assert storage.create_tag.call_args[0][0] == "documents"  # dataset_name
        assert storage.create_tag.call_args[0][1].startswith("pre-step-")  # tag

    def test_checkpoint_stores_info(self) -> None:
        storage = MagicMock()
        storage.get_version.return_value = 2
        rb = StateRollback(storage)
        rb.checkpoint("docs")
        assert rb.has_checkpoint("docs")
        assert rb._checkpoints["docs"].version == 2

    def test_checkpoint_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.get_version.side_effect = RuntimeError("db error")
        rb = StateRollback(storage)
        with pytest.raises(WorkflowError, match="WORKFLOW_STATE_ROLLBACK_FAILED"):
            rb.checkpoint("bad")

    def test_has_checkpoint_false(self) -> None:
        rb = StateRollback(MagicMock())
        assert not rb.has_checkpoint("nonexistent")

    def test_rollback_raises_when_no_checkpoint(self) -> None:
        rb = StateRollback(MagicMock())
        with pytest.raises(WorkflowError, match="No checkpoint"):
            rb.rollback("nonexistent")

    def test_clear_checkpoint(self) -> None:
        storage = MagicMock()
        storage.get_version.return_value = 1
        rb = StateRollback(storage)
        rb.checkpoint("tbl")
        assert rb.has_checkpoint("tbl")
        rb.clear_checkpoint("tbl")
        assert not rb.has_checkpoint("tbl")

    def test_rollback_raises_on_failure(self) -> None:
        info = CheckpointInfo(
            dataset_name="docs",
            version=3,
            tag="pre-v3",
            timestamp="ts",
        )
        storage = MagicMock()
        storage.get_version.return_value = 5
        storage.read_at_tag.side_effect = RuntimeError("read failed")

        rb = StateRollback(storage)
        rb._checkpoints["docs"] = info

        with pytest.raises(WorkflowError, match="Rollback failed"):
            rb.rollback("docs")

    def test_rollback_safety_copy_failure(self) -> None:
        """Rollback raises if temp safety copy creation fails."""
        import pyarrow as pa

        info = CheckpointInfo(
            dataset_name="docs",
            version=3,
            tag="pre-v3",
            timestamp="ts",
        )
        storage = MagicMock()
        checkpoint_data = pa.table({"a": [1, 2, 3]})
        storage.read_at_tag.return_value = checkpoint_data
        storage.create_dataset.side_effect = RuntimeError("disk full")

        rb = StateRollback(storage)
        rb._checkpoints["docs"] = info

        with pytest.raises(WorkflowError, match="safety copy"):
            rb.rollback("docs")

    def test_rollback_success(self) -> None:
        """Rollback succeeds: reads tag, creates temp, deletes original, recreates."""
        import pyarrow as pa

        info = CheckpointInfo(
            dataset_name="docs",
            version=3,
            tag="pre-v3",
            timestamp="ts",
        )
        checkpoint_data = pa.table({"a": [1, 2, 3]})
        storage = MagicMock()
        storage.read_at_tag.return_value = checkpoint_data
        storage.get_version.return_value = 5

        rb = StateRollback(storage)
        rb._checkpoints["docs"] = info

        result = rb.rollback("docs")

        assert result == 5
        storage.create_dataset.assert_called()
        storage.delete_dataset.assert_called()
        storage.restore_dataset.assert_called_once_with("docs", checkpoint_data)

    def test_multiple_checkpoints(self) -> None:
        storage = MagicMock()
        storage.get_version.side_effect = [3, 7]
        rb = StateRollback(storage)
        rb.checkpoint("users")
        rb.checkpoint("documents")
        assert rb.has_checkpoint("users")
        assert rb.has_checkpoint("documents")
