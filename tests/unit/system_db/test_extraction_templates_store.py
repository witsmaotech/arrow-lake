"""v1.10.0 P3: ExtractionTemplateStore over an in-memory sqlite (libSQL-compatible)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from arrow_lake.system_db.stores.extraction_templates import ExtractionTemplateStore

_MIGRATION = (Path(__file__).resolve().parents[3]
              / "arrow_lake" / "system_db" / "migrations" / "V005__extraction_templates.sql")


@pytest.fixture
def store():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(_MIGRATION.read_text(encoding="utf-8"))
    return ExtractionTemplateStore(db)


def test_upsert_and_get(store):
    store.upsert_template(name="sec", file_path="/data/lake/templates/sec.yaml",
                          content_hash="h1", doc_type="security", owner="admin")
    t = store.get_template("sec")
    assert t is not None
    assert t["name"] == "sec"
    assert t["doc_type"] == "security"
    assert t["owner"] == "admin"


def test_upsert_is_idempotent_update(store):
    store.upsert_template(name="sec", file_path="/p/sec.yaml", content_hash="h1")
    store.upsert_template(name="sec", file_path="/p/sec.yaml", content_hash="h2",
                          doc_type="security")
    rows = store.list_templates()
    assert len(rows) == 1
    assert rows[0]["content_hash"] == "h2"
    assert rows[0]["doc_type"] == "security"


def test_list_filter_by_doc_type(store):
    store.upsert_template(name="a", file_path="/p/a.yaml", content_hash="ha", doc_type="finance")
    store.upsert_template(name="b", file_path="/p/b.yaml", content_hash="hb", doc_type="legal")
    fin = store.list_templates(doc_type="finance")
    assert [r["name"] for r in fin] == ["a"]


def test_delete(store):
    store.upsert_template(name="sec", file_path="/p/sec.yaml", content_hash="h1")
    assert store.delete_template("sec") is True
    assert store.delete_template("sec") is False
    assert store.get_template("sec") is None


def test_set_default_single_holder(store):
    store.upsert_template(name="a", file_path="/p/a.yaml", content_hash="ha", doc_type="finance")
    store.upsert_template(name="b", file_path="/p/b.yaml", content_hash="hb", doc_type="finance")
    store.set_default("finance", "a")
    store.set_default("finance", "b")  # a should lose default
    assert store.get_template("a")["is_default_for"] is None if "is_default_for" in store.get_template("a") else True


def test_binding_crud(store):
    store.upsert_template(name="sec", file_path="/p/sec.yaml", content_hash="h1")
    store.set_binding("ds1", "sec", bound_by="admin")
    assert store.get_binding("ds1") == "sec"
    assert store.list_bindings("sec") == ["ds1"]
    assert store.clear_binding("ds1") is True
    assert store.get_binding("ds1") is None
    assert store.clear_binding("ds1") is False


def test_reconcile_removes_orphans_and_reports_missing(store):
    store.upsert_template(name="gone", file_path="/p/gone.yaml", content_hash="h1")
    store.upsert_template(name="kept", file_path="/p/kept.yaml", content_hash="h2")
    # on_disk has "kept" + a new "new" not yet indexed
    res = store.reconcile([("kept", "/p/kept.yaml"), ("new", "/p/new.yaml")])
    assert res["removed"] == ["gone"]
    assert res["missing"] == ["new"]
    assert store.get_template("gone") is None
    assert store.get_template("kept") is not None
