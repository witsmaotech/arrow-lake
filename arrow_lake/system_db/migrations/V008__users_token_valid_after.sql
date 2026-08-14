-- v1.10.5 M0: per-user token invalidation cutoff.
-- Tokens whose iat predates users.token_valid_after are rejected, so
-- deactivating a user or changing their password/role takes effect on the
-- next request instead of waiting out the access-token TTL.
-- NULL = no cutoff (default; pre-v1.10.5 behaviour).
ALTER TABLE users ADD COLUMN token_valid_after REAL;
