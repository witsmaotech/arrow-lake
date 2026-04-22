"""Tests for faceted.py dataset_name validation (P0 SQL injection fix).

Tests that dataset_name is validated against injection via _SAFE_IDENTIFIER_RE.
"""

from __future__ import annotations

import pytest
from arrow_lake.query.faceted import FacetedSearchBridge


class TestDatasetNameValidation:
    """Test that dataset_name is validated in faceted search."""

    def test_normal_dataset_name_accepted(self) -> None:
        """Valid identifiers are accepted."""
        bridge = FacetedSearchBridge(None)
        # Should not raise
        bridge.search(
            "my_dataset",
            [0.1, 0.2],
            facets=["category"],
        )

    def test_dataset_name_with_semicolon_rejected(self) -> None:
        """Dataset names with semicolons are rejected by _validate_where_clause."""
        bridge = FacetedSearchBridge(None)
        with pytest.raises(ValueError, match="Invalid"):
            bridge.search(
                "bad;name",
                [0.1, 0.2],
            )

    def test_dataset_name_with_sql_injection_rejected(self) -> None:
        """Dataset names with SQL patterns are rejected."""
        bridge = FacetedSearchBridge(None)
        with pytest.raises(ValueError, match="Invalid"):
            bridge.search(
                "users; DROP TABLE users",
                [0.1, 0.2],
            )

    def test_dataset_name_with_special_chars_rejected(self) -> None:
        """Dataset names with special characters are rejected."""
        bridge = FacetedSearchBridge(None)
        with pytest.raises(ValueError, match="Invalid"):
            bridge.search(
                "bad-name!",
                [0.1, 0.2],
            )

    def test_dataset_name_with_spaces_rejected(self) -> None:
        """Dataset names with spaces are rejected."""
        bridge = FacetedSearchBridge(None)
        with pytest.raises(ValueError, match="Invalid"):
            bridge.search(
                "bad name",
                [0.1, 0.2],
            )

    def test_dataset_name_starts_with_number_rejected(self) -> None:
        """Dataset names starting with a number are rejected."""
        bridge = FacetedSearchBridge(None)
        with pytest.raises(ValueError, match="Invalid"):
            bridge.search(
                "123dataset",
                [0.1, 0.2],
            )
