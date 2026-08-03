"""v1.10.0 M4: TemplateQualityRunStore over an in-memory sqlite (libSQL-compatible)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from arrow_lake.system_db.stores.template_quality_runs import TemplateQualityRunStore

_MIGRATION = (Path(__file__).resolve().parents[3]
              / "arrow_lake" / "system_db" / "migrations" / "V006__template_quality_runs.sql")


@pytest.fixture
def store():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(_MIGRATION.read_text(encoding="utf-8"))
    return TemplateQualityRunStore(db)


def test_save_list_get_delete(store):
    rid = store.save_run(template_name="project_concept_graph", document="文档正文…",
                         scenario_hint="智慧城市", temp_dataset="_quality_abc",
                         entity_count=12, relation_count=9, graph_snapshot='{"nodes":[]}')
    assert rid  # lastrowid
    assert rid  # lastrowid
    rows = store.list_runs("project_concept_graph")
    assert len(rows) == 1
    assert rows[0]["entity_count"] == 12
    assert rows[0]["template_name"] == "project_concept_graph"
    # list is a summary (no document / graph_snapshot)
    assert "document" not in rows[0]

    detail = store.get_run(rid)
    assert detail is not None
    assert detail["document"] == "文档正文…"
    assert detail["graph_snapshot"] == '{"nodes":[]}'

    assert store.delete_run(rid) is True
    assert store.get_run(rid) is None
    assert store.delete_run(rid) is False  # already gone


def test_list_orders_newest_first_and_filters_by_template(store):
    store.save_run(template_name="t_a", document="d1")
    store.save_run(template_name="t_a", document="d2")
    store.save_run(template_name="t_b", document="d3")
    a = store.list_runs("t_a")
    assert len(a) == 2
    assert a[0]["id"] > a[1]["id"]  # DESC
    assert len(store.list_runs("t_b")) == 1
    assert len(store.list_runs("t_c")) == 0


def test_optional_fields_default(store):
    rid = store.save_run(template_name="t", document="d")
    d = store.get_run(rid)
    assert d["entity_count"] == 0
    assert d["relation_count"] == 0
    assert d["scenario_hint"] is None
    assert d["graph_snapshot"] is None
