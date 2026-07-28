"""Tests for column-level lineage hooks (auto_record_materialize/clean).

Backlog P0-5: derived-operation hooks now carry column_lineage mappings.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_auto_record_clean_records_column_lineage(monkeypatch) -> None:
    from arrow_lake.catalog import lineage_hooks

    store = MagicMock()
    monkeypatch.setattr(lineage_hooks, "_get_store", lambda storage: store)

    lineage_hooks.auto_record_clean("storage", "ds", columns=["phone", "email"])

    store.record_event.assert_called_once()
    cl = store.record_event.call_args.kwargs.get("column_lineage")
    assert cl is not None and len(cl) == 2
    assert {c.source_column for c in cl} == {"phone", "email"}
    # clean rewrites in-place: source == target == same column
    assert all(c.source_dataset == "ds" and c.target_column == c.source_column for c in cl)


def test_auto_record_materialize_records_column_lineage(monkeypatch) -> None:
    from arrow_lake.catalog import lineage_hooks

    store = MagicMock()
    monkeypatch.setattr(lineage_hooks, "_get_store", lambda storage: store)

    lineage_hooks.auto_record_materialize(
        "storage", "derived_ds",
        source_datasets=["src1"], columns=["a", "b"],
        sql="SELECT a, b FROM src1",
    )

    store.record_event.assert_called_once()
    cl = store.record_event.call_args.kwargs.get("column_lineage")
    assert cl is not None and len(cl) == 2
    assert all(c.source_dataset == "src1" and c.transform_expr == "sql-project" for c in cl)


def test_existing_hooks_still_work(monkeypatch) -> None:
    """Regression: existing ingest/query hooks unaffected by the new hooks."""
    from arrow_lake.catalog import lineage_hooks

    store = MagicMock()
    monkeypatch.setattr(lineage_hooks, "_get_store", lambda storage: store)

    lineage_hooks.auto_record_ingest("storage", "ds", source_files=["/data/x.csv"])
    store.record_event.assert_called_once()
    # ingest has no column lineage → None
    assert store.record_event.call_args.kwargs.get("column_lineage") is None
