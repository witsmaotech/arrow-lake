#!/usr/bin/env python3
"""API-38 — Personal Tokens + the /me user-state surface (v1.9.0)

Business scenario: an admin issues a **personal API token** for a user, then
that token is used to call the per-user ``/api/v1/me/*`` endpoints (saved
queries, preferences, notifications). A shared JWT or the bare api_key will
NOT work for ``/me/*`` — these endpoints are keyed to a real user id, which
the personal-token auth path sets on ``request.state.user_id``.

Capabilities (v1.9.0):
  * ``POST /api/v1/admin/users/{user_id}/tokens`` (ADMIN) — issue a personal
    token; the plaintext is returned EXACTLY ONCE.
    Model: ``admin.py:CreateTokenRequest`` (name / scopes / expires_at).
  * ``GET  /api/v1/admin/users/{user_id}/tokens`` (ADMIN) — list a user's
    tokens (no plaintext, just prefixes).
  * ``DELETE /api/v1/admin/users/{user_id}/tokens/{token_id}`` (ADMIN) — revoke.
  * ``/api/v1/me/*`` (VIEWER + **personal token**):
      - saved-queries CRUD  (SaveQueryRequest: name/query_text/query_type/dataset/is_public)
      - preferences GET/PUT  (PreferencesRequest: {preferences: {...}})
      - notifications list + mark-read

Hard constraint: ``user_state.py:_user_id`` rejects requests that carry no
real user (shared api_key / JWT without user binding) with HTTP 403.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
# ADMIN key (the dev key is an ADMIN key).
ADMIN_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")

# A real user id to issue a token for. Set ADMIN_DEMO_USER_ID to an existing
# user (from GET /admin/users). Defaults to 1 (typically the seeded admin).
USER_ID = int(os.environ.get("ADMIN_DEMO_USER_ID", "1"))


def main() -> None:
    print("=" * 64)
    print("API-38  Personal Tokens + /me user-state (v1.9.0)")
    print("=" * 64)

    admin = ArrowLakeClient(BASE_URL, ADMIN_KEY)

    # --- Step 1: issue a personal token (admin acts on behalf of a user) ---
    print(f"\n# --- Step 1: POST /admin/users/{USER_ID}/tokens (issue) ---")
    body = {
        "name": f"cookbook-me-demo-{USER_ID}",
        "scopes": [],  # empty = all scopes the user holds
        # "expires_at": "2026-12-31T23:59:59Z",  # optional ISO-8601
    }
    resp = admin._request("POST", f"/api/v1/admin/users/{USER_ID}/tokens", body)
    plaintext = resp.get("token")
    if not plaintext:
        print(f"  [FAIL] could not issue token: status={resp.get('status')} "
              f"detail={resp.get('detail')}")
        print("  (needs system_db enabled + a real user; set ADMIN_DEMO_USER_ID)")
        return
    token_id = resp.get("id")
    print(f"  -> token_id={token_id} prefix={resp.get('token_prefix')}")
    print(f"  -> plaintext returned ONCE (len={len(plaintext)})")

    # --- Step 2: list the user's tokens (no plaintext, just prefixes) ---
    print(f"\n# --- Step 2: GET /admin/users/{USER_ID}/tokens (list) ---")
    resp = admin._request("GET", f"/api/v1/admin/users/{USER_ID}/tokens")
    for t in (resp.get("tokens") or [])[:5]:
        print(f"         id={t.get('id')} name={t.get('name')} prefix={t.get('token_prefix')}")

    # --- Step 3: build a client that authenticates with the personal token ---
    print("\n# --- Step 3: call /me/* with the personal token (X-API-Key) ---")
    me = ArrowLakeClient(BASE_URL, plaintext)

    # --- Step 4: saved-queries CRUD ---
    print("\n# --- Step 4: /me/saved-queries (POST / GET / DELETE) ---")
    sq = me._request("POST", "/api/v1/me/saved-queries", {
        "name": "top vendors by revenue",
        "query_text": "SELECT vendor, SUM(revenue) AS r FROM sales GROUP BY vendor ORDER BY r DESC LIMIT 10",
        "query_type": "sql",
        "dataset": "sales",
        "is_public": False,
    })
    qid = sq.get("id")
    print(f"  -> saved query id={qid}")

    listed = me._request("GET", "/api/v1/me/saved-queries")
    print(f"  -> user now has {len(listed.get('queries') or [])} saved queries")

    if qid:
        dele = me._request("DELETE", f"/api/v1/me/saved-queries/{qid}")
        print(f"  -> delete id={qid}: {dele}")

    # --- Step 5: preferences (GET / PUT) ---
    print("\n# --- Step 5: /me/preferences (PUT then GET) ---")
    put = me._request("PUT", "/api/v1/me/preferences",
                      {"preferences": {"theme": "dark", "page_size": 50, "lang": "zh"}})
    print(f"  -> put success={put.get('success', put.get('status') == 200)}")
    got = me._request("GET", "/api/v1/me/preferences")
    print(f"  -> preferences = {got.get('preferences') or got.get('data')}")

    # --- Step 6: notifications (list + mark-read) ---
    print("\n# --- Step 6: /me/notifications ---")
    notes = me._request("GET", "/api/v1/me/notifications")
    items = notes.get("notifications") or notes.get("data") or []
    print(f"  -> {len(items)} notification(s)")
    if items:
        nid = items[0].get("id")
        rd = me._request("POST", "/api/v1/me/notifications/read",
                         {"notification_id": nid} if nid is not None else None)
        print(f"  -> mark-read id={nid}: {rd}")

    # --- Step 7: prove JWT/shared-api-key is rejected for /me/* ---
    print("\n# --- Step 7: shared api_key CANNOT call /me/* (403) ---")
    bare = ArrowLakeClient(BASE_URL, ADMIN_KEY)  # shared key, no user binding
    denied = bare._request("GET", "/api/v1/me/saved-queries")
    print(f"  -> status={denied.get('status')} detail={denied.get('detail')}")

    # --- Step 8: revoke the personal token ---
    print(f"\n# --- Step 8: revoke token id={token_id} ---")
    rev = admin._request("DELETE",
                         f"/api/v1/admin/users/{USER_ID}/tokens/{token_id}")
    print(f"  -> revoked={rev.get('revoked')}")
    # prove it no longer works
    after = me._request("GET", "/api/v1/me/saved-queries")
    print(f"  -> post-revoke /me call status={after.get('status')} (expected 401)")

    print("\n" + "=" * 64)
    print("API-38  Personal Tokens + /me user-state — DONE")
    print("=" * 64)


if __name__ == "__main__":
    main()
