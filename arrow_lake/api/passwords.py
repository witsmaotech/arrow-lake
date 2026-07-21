"""Password hashing — stdlib hashlib.pbkdf2 (zero external deps).

Stored format: ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``

Used by v1.9.1 username/password login against the libSQL IdentityStore.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

ALGO = "pbkdf2_sha256"
ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGO}${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


_DUMMY_HASH = hash_password("arrow-lake-dummy-verify")


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Equalize timing with the real path to avoid user enumeration via response time.
        verify_password(password, _DUMMY_HASH)
        return False
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$", 3)
    except ValueError:
        return False
    if algo != ALGO:
        return False
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
    return hmac.compare_digest(dk, expected)
