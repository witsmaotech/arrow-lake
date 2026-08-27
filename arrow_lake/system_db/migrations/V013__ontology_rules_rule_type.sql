-- v1.11.1 MS2 (W1.4 / DR15 D-2): ontology_rules rule classification + version.
-- rule_type: 建模侧 M3 five-way classification (validation/computation/
-- derivation/transformation/risk_control) — enum enforced at the pydantic/
-- store layer (SQLite ALTER cannot add CHECK). version: independent of the
-- draft→active→retired state machine. Bare ALTER is safe: the Migrator runs
-- each version exactly once (V008 precedent).
ALTER TABLE ontology_rules ADD COLUMN rule_type TEXT NOT NULL DEFAULT 'validation';
ALTER TABLE ontology_rules ADD COLUMN version TEXT NOT NULL DEFAULT '1';
