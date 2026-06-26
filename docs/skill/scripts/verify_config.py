#!/usr/bin/env python3
"""Arrow Lake skill — config & environment verification.

Loads ArrowLakeConfig (defaults + .env + env + optional YAML) and reports
whether the deployment is wired correctly. NEVER prints secret values —
only presence / non-placeholder / length checks.

Run:
    python docs/skill/scripts/verify_config.py
    python docs/skill/scripts/verify_config.py configs/prod.yaml

Exit codes: 0 = ok, 1 = one or more checks failed.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

EXIT_OK = 0
EXIT_FAIL = 1

LEVELS = ("OK", "WARN", "FAIL")


@dataclass
class Check:
    name: str
    level: str  # OK / WARN / FAIL
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, level: str, detail: str = "") -> None:
        self.checks.append(Check(name, level, detail))

    @property
    def failed(self) -> bool:
        return any(c.level == "FAIL" for c in self.checks)


def _looks_placeholder(val: str) -> bool:
    if not val:
        return True
    v = val.strip()
    return (not v) or v.startswith("<") or v.lower() in {"change_me", "changeme", "your-key", "secret"}


def _safe_len(val: object) -> int:
    try:
        return len(str(val))  # noqa: S101
    except Exception:  # noqa: BLE001
        return 0


def verify(yaml_path: str | None) -> Report:
    rep = Report()
    try:
        from arrow_lake.config import ArrowLakeConfig
    except Exception as exc:  # noqa: BLE001
        rep.add("import", "FAIL", f"cannot import arrow_lake: {exc}")
        return rep

    # load config
    try:
        if yaml_path:
            cfg = ArrowLakeConfig.from_yaml(yaml_path)
            rep.add("load_yaml", "OK", yaml_path)
        else:
            cfg = ArrowLakeConfig()
            rep.add("load_defaults", "OK", "defaults + .env + env")
    except Exception as exc:  # noqa: BLE001
        rep.add("load_config", "FAIL", str(exc))
        return rep

    # storage
    storage = getattr(cfg, "storage", None)
    backend = getattr(storage, "backend", None)
    rep.add("storage.backend", "OK" if backend else "WARN", str(backend))
    if backend and str(backend).lower() in ("s3", "minio"):
        ak = getattr(storage, "s3_access_key", None) or os.environ.get("ARROW_LAKE__STORAGE__S3_ACCESS_KEY")
        ep = getattr(storage, "s3_endpoint", None)
        rep.add(
            "storage.s3_creds",
            "OK" if ak and not _looks_placeholder(ak) else "FAIL",
            f"endpoint={'set' if ep else 'unset'}, key={'set' if ak else 'missing'}",
        )

    base_uri = getattr(cfg, "_base_uri", None) or getattr(storage, "base_uri", None) or "./data"
    rep.add("storage.base_uri", "OK", str(base_uri))

    # auth
    auth = getattr(cfg, "auth", None)
    enabled = bool(getattr(auth, "enabled", False))
    rep.add("auth.enabled", "OK", str(enabled))
    if enabled:
        keys = getattr(auth, "api_keys", None) or {}
        jwt_secret = getattr(auth, "jwt_secret", None) or getattr(auth, "jwt", None)
        non_placeholder = [k for k, v in (keys.items() if isinstance(keys, dict) else []) if not _looks_placeholder(v)]
        rep.add(
            "auth.api_keys",
            "OK" if non_placeholder else "FAIL",
            f"{len(non_placeholder)} valid key(s) of {len(keys) if isinstance(keys, dict) else 0}",
        )
        if jwt_secret and _looks_placeholder(str(jwt_secret)):
            rep.add("auth.jwt_secret", "WARN", "placeholder — rotate before prod")

    # api docs (recommend off in prod)
    api = getattr(cfg, "api", None)
    docs_enabled = getattr(api, "docs_enabled", True)
    rep.add("api.docs_enabled", "WARN" if docs_enabled else "OK", "turn OFF in prod" if docs_enabled else "off")

    # redis (optional, used for JWT blacklist + task store)
    redis_cfg = getattr(cfg, "redis", None)
    redis_url = getattr(redis_cfg, "url", None)
    rep.add("redis.url", "OK" if redis_url else "WARN", "set" if redis_url else "unset (task store disabled)")
    if redis_url:
        try:
            import redis  # type: ignore

            r = redis.from_url(str(redis_url), socket_connect_timeout=2)
            r.ping()
            rep.add("redis.ping", "OK", "reachable")
        except ImportError:
            rep.add("redis.ping", "WARN", "redis-py not installed; skipping live ping")
        except Exception as exc:  # noqa: BLE001
            rep.add("redis.ping", "WARN", f"unreachable: {exc!s}")

    # llm (optional)
    llm = getattr(cfg, "llm", None)
    provider = getattr(llm, "provider", None)
    rep.add("llm.provider", "OK" if provider else "WARN", str(provider) if provider else "unset")

    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="Arrow Lake config verification (no secrets printed).")
    ap.add_argument("yaml", nargs="?", default=None, help="optional YAML config path")
    args = ap.parse_args()

    print("Arrow Lake config verification")
    rep = verify(args.yaml)
    width = max((len(c.name) for c in rep.checks), default=10)
    for c in rep.checks:
        print(f"  [{c.level:<4}] {c.name:<{width}}  {c.detail}")
    print("RESULT:", "FAIL" if rep.failed else "OK")
    return EXIT_FAIL if rep.failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
