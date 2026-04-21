"""Regression test — verify Lake facade API signatures remain stable.

M0a Day 4 — ensures M0 refactoring does not break public API contracts.
"""

from __future__ import annotations

import inspect

import pytest


# Expected public method signatures for backward compatibility.
# Format: method_name -> list of required positional parameter names (excl. self).
LAKE_METHOD_SIGNATURES: dict[str, list[str]] = {
    "ingest": ["dataset_name", "file_paths"],
    "ingest_mixed": ["dataset_name", "sources"],
    "ingest_images": ["dataset_name", "image_paths"],
    "ingest_videos": ["dataset_name", "video_paths"],
    "ingest_http": ["dataset_name", "urls"],
    "create_dataset": ["name", "data"],
    "append_dataset": ["name", "data"],
    "delete_dataset": ["name"],
    "list_datasets": [],
    "catalog": [],
    "search": ["dataset_name", "query_vector"],
    "text_search": ["dataset_name", "query"],
    "hybrid_search": ["dataset_name", "query_vector", "query_text"],
    "olap_query": ["dataset_name", "sql"],
    "sql_query": ["dataset_name", "sql"],
    "faceted_search": ["dataset_name", "query_vector"],
    "ensemble_search": ["dataset_name", "query_vector"],
    "export": ["dataset_name", "output_path"],
    "query": ["dataset_name", "sql"],
    "quality_filter": ["dataset_name"],
    "deduplicate": ["dataset_name"],
    "create_vector_index": ["dataset_name"],
    "create_fts_index": ["dataset_name"],
    "daft_query": ["dataset_name"],
    "lineage_record_event": ["dataset_name", "operation"],
    "lineage_history": ["dataset_name"],
    "lineage_query": ["sql"],
    "audit_record": ["event_type"],
    "audit_verify": ["audit_id"],
    "audit_query": [],
    "audit_export": ["dataset_name"],
    "list_flows": [],
    "get_flow_info": ["name"],
    "version": [],
}


class TestLakeFacadeAPI:
    """Verify all Lake public methods retain their expected signatures."""

    @pytest.fixture(autouse=True)
    def _import_lake(self) -> None:
        from arrow_lake import Lake

        self.Lake = Lake

    @pytest.mark.parametrize("method_name", list(LAKE_METHOD_SIGNATURES.keys()))
    def test_method_exists(self, method_name: str) -> None:
        """Every expected method must still exist on the Lake class."""
        assert hasattr(self.Lake, method_name), f"Missing method: {method_name}"

    @pytest.mark.parametrize("method_name", list(LAKE_METHOD_SIGNATURES.keys()))
    def test_first_params_match(self, method_name: str) -> None:
        """Required positional parameters must not change order or removal."""
        method = getattr(self.Lake, method_name)
        sig = inspect.signature(method)
        params = [
            name
            for name, param in sig.parameters.items()
            if name != "self" and param.default is inspect.Parameter.empty
        ]
        expected = LAKE_METHOD_SIGNATURES[method_name]
        assert params[: len(expected)] == expected, (
            f"{method_name}: expected first params {expected}, got {params[: len(expected)]}"
        )

    def test_no_removed_public_methods(self) -> None:
        """No expected public methods should be missing."""
        actual = {
            name
            for name in dir(self.Lake)
            if not name.startswith("_") and callable(getattr(self.Lake, name))
        }
        missing = set(LAKE_METHOD_SIGNATURES.keys()) - actual
        assert not missing, f"Removed methods: {missing}"
