"""v1.10.0 M5: DocTypeCategoryStore over an in-memory sqlite (libSQL-compatible).

Covers seed (from the code-level taxonomy), CRUD, and the validation-facing
``known_names`` used to enforce ``template.category ∈ dictionary``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from arrow_lake.system_db.stores.doc_type_categories import (
    CategoryExistsError, DocTypeCategoryStore,
)
from arrow_lake.knowledge_graph.doc_type_router import (
    DOC_TYPE_ALIASES, DOC_TYPE_DESCRIPTIONS,
)

_MIGRATION = (Path(__file__).resolve().parents[3]
              / "arrow_lake" / "system_db" / "migrations" / "V007__doc_type_categories.sql")


@pytest.fixture
def store():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(_MIGRATION.read_text(encoding="utf-8"))
    return DocTypeCategoryStore(db)


def test_seed_inserts_canonical_types(store):
    n = store.seed_if_empty()
    assert n == len(DOC_TYPE_DESCRIPTIONS)  # 11 canonical
    names = store.known_names()
    # every canonical doc_type is seeded
    assert set(DOC_TYPE_DESCRIPTIONS) <= names


def test_seed_is_idempotent(store):
    store.seed_if_empty()
    assert store.seed_if_empty() == 0
    assert len(store.known_names()) == len(DOC_TYPE_DESCRIPTIONS)


def test_seed_carries_aliases(store):
    store.seed_if_empty()
    paper = store.get_category("paper")
    assert paper is not None
    assert "论文" in paper["aliases"]
    assert paper["source"] == "seed"


def test_add_custom_category(store):
    store.seed_if_empty()
    store.add_category("security", desc_en="cybersecurity", aliases=["cyber", "sec"])
    assert "security" in store.known_names()
    c = store.get_category("security")
    assert c["source"] == "custom"
    assert c["aliases"] == ["cyber", "sec"]


def test_add_rejects_invalid_name(store):
    store.seed_if_empty()
    with pytest.raises(ValueError):
        store.add_category("Bad-Name")
    with pytest.raises(ValueError):
        store.add_category("1numstart")


def test_add_rejects_duplicate(store):
    store.seed_if_empty()
    # review: duplicate raises the DISTINCT CategoryExistsError (a ValueError
    # subclass) so the API maps it to 409, not the 422 used for invalid names.
    with pytest.raises(CategoryExistsError):
        store.add_category("paper")  # already seeded


def test_seed_concurrency_safe_with_insert_or_ignore():
    # review: seed uses INSERT OR IGNORE so a concurrent seed (multi-worker)
    # doesn't crash on PK violation. Seed twice into the same db.
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(_MIGRATION.read_text(encoding="utf-8"))
    s = DocTypeCategoryStore(db)
    s.seed_if_empty()
    s.seed_if_empty()  # second "worker" — must not raise
    assert len(s.known_names()) == len(DOC_TYPE_DESCRIPTIONS)


def test_category_create_model_rejects_comma_in_alias():
    # review: aliases serialize as comma-joined TEXT, so a comma would corrupt
    # the round-trip; the pydantic model rejects it at the boundary.
    from arrow_lake.api.routers.doc_type_categories import CategoryCreate
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CategoryCreate(name="x", aliases=["ok", "has,comma"])
    # valid aliases pass
    m = CategoryCreate(name="x", aliases=["cyber", "sec"])
    assert m.aliases == ["cyber", "sec"]


def test_delete(store):
    store.seed_if_empty()
    store.add_category("security")
    assert store.delete_category("security") is True
    assert store.delete_category("security") is False
    assert "security" not in store.known_names()


def test_known_names_is_superset_for_routing(store):
    """seed names must cover every gallery category a preset/project template
    uses (so a template with category=finance is never rejected)."""
    store.seed_if_empty()
    known = store.known_names()
    for canon in DOC_TYPE_ALIASES:
        assert canon in known
