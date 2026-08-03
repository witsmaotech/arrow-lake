-- V007__doc_type_categories.sql — v1.10.0 M5: dynamic doc_type ↔ template-category dictionary
--
-- The canonical doc_type taxonomy (DOC_TYPE_ALIASES / DOC_TYPE_DESCRIPTIONS in
-- doc_type_router.py) was a code-level constant: adding a new domain (e.g.
-- "security") required a code change. M5 promotes it to a runtime-managed
-- dictionary so an admin can add categories that are immediately usable as a
-- template `category` (Layer-2 routing == template.category == doc_type) and as
-- an ingest doc_type. The table is seeded at startup from the Python taxonomy
-- constants (seed_if_empty), so this migration is schema-only (DRY — the 11
-- canonical types already live in code, not duplicated here). Idempotent.
-- See docs/v1.10.0-extraction-template-management-plan.md §M5.

CREATE TABLE IF NOT EXISTS doc_type_categories (
    name        TEXT PRIMARY KEY,             -- canonical doc_type / category key (lowercase identifier)
    desc_zh     TEXT,                         -- short zh label (optional)
    desc_en     TEXT,                         -- short en description (optional)
    aliases     TEXT,                         -- comma-separated aliases (display + future normalization)
    source      TEXT NOT NULL DEFAULT 'seed', -- 'seed' (built-in) | 'custom' (admin-added at runtime)
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
