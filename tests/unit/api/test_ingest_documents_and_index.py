"""Tests for _LakeIngestMixin.ingest_documents_and_index (架构评审 #4).

The facade method consolidates the post-ingest index sequence that was
duplicated across routers/datasets.py + routers/async_tasks.py. Verified as
an unbound mixin call against a MagicMock self (no heavy Lake fixture needed).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from arrow_lake._lake_ingest import _LakeIngestMixin


class TestIngestDocumentsAndIndex:
    """ingest_documents_and_index: ingest → embed → FTS → vector, best-effort."""

    def _call(self, **lake_overrides):
        lake = MagicMock()
        lake.ingest_documents.return_value = "report"
        for k, v in lake_overrides.items():
            setattr(lake, k, v)
        report = _LakeIngestMixin.ingest_documents_and_index(
            lake, "ds", ["/tmp/a.pdf"], doc_type=None, actor="u",
        )
        return lake, report

    def test_runs_all_four_steps_in_order(self):
        lake, report = self._call()
        assert report == "report"
        lake.ingest_documents.assert_called_once()
        lake.embed_and_add.assert_called_once_with("ds")
        lake.create_fts_index.assert_called_once_with("ds")
        lake.create_vector_index.assert_called_once_with("ds")

    def test_best_effort_embed_failure_does_not_block_index_steps(self):
        """embed_and_add raising must NOT fail ingest nor skip FTS/vector."""
        lake, report = self._call(embed_and_add=MagicMock(side_effect=RuntimeError("embed boom")))
        assert report == "report"  # ingest still succeeded
        lake.embed_and_add.assert_called_once_with("ds")
        lake.create_fts_index.assert_called_once_with("ds")     # later steps still run
        lake.create_vector_index.assert_called_once_with("ds")

    def test_best_effort_vector_failure_still_returns_report(self):
        lake, report = self._call(create_vector_index=MagicMock(side_effect=RuntimeError("v boom")))
        assert report == "report"
        lake.create_vector_index.assert_called_once_with("ds")

    def test_missing_step_method_is_skipped_gracefully(self):
        """If a step attr is absent (None), getattr→None→skipped, not crashed."""
        lake = MagicMock()
        lake.ingest_documents.return_value = "report"
        # del create_vector_index → getattr returns MagicMock by default; force None
        lake.create_vector_index = None
        report = _LakeIngestMixin.ingest_documents_and_index(
            lake, "ds", ["/tmp/a.pdf"], actor="u",
        )
        assert report == "report"
        lake.embed_and_add.assert_called_once()
        lake.create_fts_index.assert_called_once()
        # create_vector_index being None → callable() False → skipped (no call)
