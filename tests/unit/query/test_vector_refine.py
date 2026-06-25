"""Tests for VectorSearchBridge refine_factor in SDK fallback path (v1.7.1 #5).

Verifies `_search_via_lancedb` applies `.refine_factor(config.refine_factor)`,
mirroring the DuckDB native path (vector.py:415).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa

from arrow_lake.config import VectorSearchConfig
from arrow_lake.query.vector import VectorSearchBridge


def _chain_builder() -> MagicMock:
    """A MagicMock query builder whose chainable methods return self."""
    builder = MagicMock()
    for method in ("where", "limit", "nprobes", "distance_type", "refine_factor"):
        getattr(builder, method).return_value = builder
    return builder


class TestSdkPathRefineFactor:
    """The SDK fallback search path must apply refine_factor from config."""

    def test_sdk_path_applies_refine_factor_without_where(self) -> None:
        cfg = VectorSearchConfig()
        bridge = VectorSearchBridge(MagicMock(), config=cfg)

        table = MagicMock()
        builder = _chain_builder()
        table.search.return_value = builder
        builder.to_arrow.return_value = pa.table({"_distance": [0.0], "x": [1]})

        bridge._search_via_lancedb(
            table, [0.1] * 384, 10, "cosine", "text_embedding", None, None
        )

        builder.refine_factor.assert_called_once_with(cfg.refine_factor)

    def test_sdk_path_applies_refine_factor_with_where(self) -> None:
        cfg = VectorSearchConfig()
        bridge = VectorSearchBridge(MagicMock(), config=cfg)

        table = MagicMock()
        builder = _chain_builder()
        table.search.return_value = builder
        builder.to_arrow.return_value = pa.table({"_distance": [0.0], "x": [1]})

        bridge._search_via_lancedb(
            table, [0.1] * 384, 10, "cosine", "text_embedding", "modality = 'text'", None
        )

        builder.refine_factor.assert_called_once_with(cfg.refine_factor)
