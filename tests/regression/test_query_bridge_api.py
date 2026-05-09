"""Regression test — verify query bridge APIs remain stable.

M0a Day 4 — ensures bridge class interfaces don't change during M0 refactoring.
"""

from __future__ import annotations

# Expected bridge class public methods.
BRIDGE_METHODS: dict[str, list[str]] = {
    "OlapSearchBridge": ["query", "explain"],
    "MetadataSearchBridge": ["query"],
    "FacetedSearchBridge": ["search"],
    "VectorSearchBridge": ["search", "create_index", "get_index_info"],
    "FullTextSearchBridge": ["search", "create_index"],
    "HybridSearchBridge": ["search"],
    "EnsembleSearchBridge": ["search"],
}


class TestQueryBridgeAPI:
    """Verify query bridge class methods remain stable."""

    def test_olap_bridge(self) -> None:
        from arrow_lake.query.olap import OlapSearchBridge

        self._check_bridge("OlapSearchBridge", OlapSearchBridge)

    def test_metadata_bridge(self) -> None:
        from arrow_lake.query.metadata import MetadataSearchBridge

        self._check_bridge("MetadataSearchBridge", MetadataSearchBridge)

    def test_faceted_bridge(self) -> None:
        from arrow_lake.query.faceted import FacetedSearchBridge

        self._check_bridge("FacetedSearchBridge", FacetedSearchBridge)

    def test_vector_bridge(self) -> None:
        from arrow_lake.query.vector import VectorSearchBridge

        self._check_bridge("VectorSearchBridge", VectorSearchBridge)

    def test_fts_bridge(self) -> None:
        from arrow_lake.query.fts import FullTextSearchBridge

        self._check_bridge("FullTextSearchBridge", FullTextSearchBridge)

    def test_hybrid_bridge(self) -> None:
        from arrow_lake.query.hybrid import HybridSearchBridge

        self._check_bridge("HybridSearchBridge", HybridSearchBridge)

    def test_ensemble_bridge(self) -> None:
        from arrow_lake.query.ensemble import EnsembleSearchBridge

        self._check_bridge("EnsembleSearchBridge", EnsembleSearchBridge)

    def _check_bridge(self, name: str, cls: type) -> None:
        expected = BRIDGE_METHODS[name]
        for method_name in expected:
            assert hasattr(cls, method_name), f"{name} missing method: {method_name}"
            assert callable(getattr(cls, method_name))
