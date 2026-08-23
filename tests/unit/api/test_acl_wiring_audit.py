"""v1.10.7 WP1a wiring audit: every targeted read endpoint must carry the
dataset deny/ACL guard (Depends or manual call). Source-scan guard so a new
endpoint or refactor cannot silently drop coverage (review H1 regression)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# file -> endpoint function names that MUST reference the guard within their
# signature block (Depends(authorize_dataset_read) or manual authorize_dataset).
WIRING = {
    "arrow_lake/api/routers/query.py": [
        "olap_query", "metadata_query", "graph_query", "daft_query",
    ],
    "arrow_lake/api/routers/search.py": [
        "vector_search", "full_text_search", "hybrid_search",
        "faceted_search", "ensemble_search",
    ],
    "arrow_lake/api/routers/export.py": [
        "export_dataset", "get_export_status", "download_export", "export_to",
    ],
    "arrow_lake/api/routers/embedding.py": [
        "create_vector_index", "create_fts_index", "create_scalar_index",
        "create_facet_indexes", "list_indices", "drop_index",
    ],
    "arrow_lake/api/routers/datasets.py": [
        "get_dataset", "get_dataset_schema",
    ],
}

# endpoints enforcing SOURCE-level SQL ACL (review C2) — must call the helper.
SQL_ENFORCED = {
    "arrow_lake/api/routers/query.py": ["olap_query", "metadata_query", "daft_query"],
}

# manual-guard endpoints (body param dataset_name)
MANUAL = {
    "arrow_lake/api/routers/rag.py": ["rag_query", "rag_query_stream", "rag_extract"],
    # kg build-info takes dataset as a query param → inline _enforce_read_acl
    "arrow_lake/api/routers/knowledge_graph.py": ["kg_build_info"],
}


def _signature_block(source: str, fn: str) -> str:
    i = source.index(f"async def {fn}(")
    j = source.index(") ->", i)
    return source[i:j]


def _body_block(source: str, fn: str, span: int = 1600) -> str:
    i = source.index(f"async def {fn}(")
    return source[i : i + span]


def test_read_endpoints_carry_deny_guard() -> None:
    missing: list[str] = []
    for rel, fns in WIRING.items():
        src = (ROOT / rel).read_text()
        for fn in fns:
            if "Depends(authorize_dataset_read)" not in _signature_block(src, fn):
                missing.append(f"{rel}::{fn}")
    assert not missing, f"endpoints missing deny guard: {missing}"


def test_sql_endpoints_enforce_source_acl() -> None:
    missing: list[str] = []
    for rel, fns in SQL_ENFORCED.items():
        src = (ROOT / rel).read_text()
        for fn in fns:
            body = _body_block(src, fn)
            # daft delegates to _apply_pipeline with checker/role (helper
            # applies the enforcement); others call the helper inline.
            if "_acl_enforced_sql(" not in body and "checker=checker" not in body:
                missing.append(f"{rel}::{fn}")
    assert not missing, f"SQL endpoints missing source-level ACL: {missing}"


def test_rag_endpoints_manual_guard_and_fail_closed() -> None:
    src = (ROOT / "arrow_lake/api/routers/rag.py").read_text()
    for fn in MANUAL["arrow_lake/api/routers/rag.py"]:
        body = _body_block(src, fn)
        assert "authorize_dataset(request, req.dataset_name)" in body, fn
        assert "row/column ACL" in body and "403" in body, f"{fn} not fail-closed"


def test_kg_build_info_manual_guard() -> None:
    src = (ROOT / "arrow_lake/api/routers/knowledge_graph.py").read_text()
    body = _body_block(src, "kg_build_info")
    assert "_enforce_read_acl(checker, _user, dataset)" in body, "kg_build_info missing deny guard"
