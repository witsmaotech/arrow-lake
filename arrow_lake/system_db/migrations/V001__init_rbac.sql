-- V001__init_rbac.sql — P0: RBAC + identity + personal tokens (v1.9.0)
--
-- Faithful relational projection of arrow_lake/api/rbac.py's four in-memory
-- dicts (_dataset_acls / _row_col_acls / _schema_acls / _deny_list) plus a
-- real users table + self-managed personal API tokens.
--
-- Idempotent: every statement uses IF NOT EXISTS / INSERT OR IGNORE, so a
-- fresh DB and a re-run both succeed.

-- ── identity ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE,
    password_hash TEXT,                 -- argon2; null for token-only / bootstrap users (no SSO in v1.9)
    role          TEXT NOT NULL DEFAULT 'viewer',   -- admin | editor | viewer
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── self-managed API tokens (replace the single shared api_key) ───────
CREATE TABLE IF NOT EXISTS personal_tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,                 -- user-chosen label ("ci-pipeline")
    token_hash    TEXT UNIQUE NOT NULL,          -- sha256(token), never plaintext
    token_prefix  TEXT NOT NULL,                 -- first 8 chars for UI recognition
    scopes        TEXT,                          -- JSON permission scope
    expires_at    TEXT,
    last_used_at  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_personal_tokens_user  ON personal_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_personal_tokens_hash  ON personal_tokens(token_hash);

-- ── role → permission matrix (seeded at startup from _ROLE_PERMISSIONS) ──
CREATE TABLE IF NOT EXISTS role_permissions (
    role       TEXT NOT NULL,
    permission TEXT NOT NULL,
    PRIMARY KEY (role, permission)
);

-- ── dataset → role → action grants  (replaces _dataset_acls) ──────────
CREATE TABLE IF NOT EXISTS dataset_acl_grants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_name TEXT NOT NULL,
    role         TEXT NOT NULL,
    action       TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (dataset_name, role, action)
);
CREATE INDEX IF NOT EXISTS idx_dataset_acl_grants_ds ON dataset_acl_grants(dataset_name);

-- ── dataset row/column-level ACL  (replaces _row_col_acls DatasetACL) ──
CREATE TABLE IF NOT EXISTS dataset_row_col_acls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_name    TEXT NOT NULL,
    role            TEXT NOT NULL,
    visible_columns TEXT,        -- JSON array; empty/null = all columns
    row_filter      TEXT,        -- simple SQL WHERE expression; empty = all rows
    denied_actions  TEXT,        -- JSON array of denied actions (overrides grants)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (dataset_name, role)
);
CREATE INDEX IF NOT EXISTS idx_row_col_acls_ds ON dataset_row_col_acls(dataset_name);

-- ── schema-level ACL  (replaces _schema_acls SchemaACL) ───────────────
CREATE TABLE IF NOT EXISTS schema_acls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name     TEXT NOT NULL,
    role            TEXT NOT NULL,
    allowed_actions TEXT,        -- JSON array
    denied_actions  TEXT,        -- JSON array (overrides allowed)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (schema_name, role)
);

-- ── dataset → denied actions  (replaces _deny_list) ───────────────────
CREATE TABLE IF NOT EXISTS acl_denies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_name TEXT NOT NULL,
    action       TEXT NOT NULL,
    reason       TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (dataset_name, action)
);
