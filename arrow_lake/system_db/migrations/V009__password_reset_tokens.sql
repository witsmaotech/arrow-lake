-- v1.10.5 M1: one-time password reset tokens (admin-issued, no email channel).
-- The admin endpoint generates a plaintext token and returns it exactly once;
-- only its sha256 lands here. Consuming marks used_at and rotates the password.
-- Idempotent.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,       -- sha256(plaintext); plaintext never stored
    expires_at  TEXT NOT NULL,              -- ISO8601 UTC (30min default at issuance)
    used_at     TEXT,                       -- NULL = still redeemable (single use)
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user
    ON password_reset_tokens(user_id);
