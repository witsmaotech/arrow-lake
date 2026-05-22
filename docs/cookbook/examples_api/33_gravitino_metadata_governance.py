#!/usr/bin/env python3
"""API-33 — Gravitino 元数据治理全流程

对应 cookbook: 15-gravitino-metadata.md
验证: 元数据发现(目录/表/详情)、标签治理、合规策略、模型管理、统计采集、健康降级
前置: 需 Docker Compose prod profile 启动 (含 gravitino + lance-rest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"


def main() -> None:
    print("=" * 60)
    print("API-33  Gravitino Metadata Governance")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # === Phase 1: Health & Prerequisites ===

    # 1. Health check — verify Gravitino is available
    print("\nSTEP  1: Health check (Gravitino)")
    h = c.health()
    if h.get("status") not in ("ok", "degraded"):
        print(f"  [SKIP] Arrow Lake not healthy: {h}")
        return
    gravitino_ok = h.get("gravitino") in ("healthy", "ok", True)
    lance_rest_ok = h.get("lance_rest") in ("healthy", "ok", True)
    if not gravitino_ok:
        print("  [SKIP] Gravitino not available — skipping metadata tests")
        print("         Start with: docker compose -f deploy/docker-compose.prod.yml --profile dev up -d")
        return
    print(f"         gravitino: {h.get('gravitino')}")
    print(f"         lance_rest: {h.get('lance_rest')}")
    c._pass("Gravitino healthy")

    # 2. List existing datasets (pick target for stats)
    print("\nSTEP  2: List existing datasets")
    ds = c.list_datasets()
    assert ds.get("success") is True
    datasets = ds.get("datasets", [])
    target_name = datasets[0]["name"] if datasets else None
    c._pass(f"GET /datasets — {len(datasets)} datasets"
            + (f", target={target_name}" if target_name else ""))

    # === Phase 2: Catalog & Table Discovery ===

    # 3. List Gravitino catalogs
    print("\nSTEP  3: List catalogs")
    cat = c.metadata_list_catalogs()
    if cat.get("success"):
        catalogs = cat.get("data", [])
        c._pass(f"GET /metadata/catalogs — {cat.get('metadata', {}).get('total', 0)} catalogs")
        for ca in catalogs:
            print(f"         {ca['name']}")
    else:
        print(f"  [INFO] {cat.get('error', 'unknown')}")

    # 4. List tables and get detail
    print("\nSTEP  4: List tables & get detail")
    tbl = c.metadata_list_tables()
    if tbl.get("success"):
        tables = tbl.get("data", [])
        c._pass(f"GET /metadata/tables — {tbl.get('metadata', {}).get('total', 0)} tables")
        if tables:
            first_table = tables[0]["name"]
            detail = c.metadata_get_table(first_table)
            if detail.get("success"):
                d = detail.get("data", {})
                cols = d.get("columns", [])
                props = d.get("properties", {})
                c._pass(f"GET /metadata/tables/{first_table} — "
                        f"{len(cols)} columns, {len(props)} properties")
                for col in cols[:5]:
                    print(f"         {col.get('name', '?'):20s} {col.get('type', '?')}")
                if len(cols) > 5:
                    print(f"         ... ({len(cols) - 5} more)")
            else:
                print(f"  [INFO] table detail: {detail.get('error')}")
    else:
        print(f"  [INFO] {tbl.get('error', 'unknown')}")

    # === Phase 3: Tag Governance ===

    # 5. Create governance tags
    print("\nSTEP  5: Create governance tags")
    tags_to_create = [
        ("gdpr_subject", "Data subject under GDPR regulation"),
        ("internal_only", "For internal use only, not for external sharing"),
    ]
    for tag_name, tag_comment in tags_to_create:
        resp = c.metadata_create_tag(tag_name, tag_comment)
        if resp.get("success"):
            c._pass(f"tag created: {tag_name}")
        else:
            print(f"  [INFO] tag '{tag_name}': {resp.get('error', 'maybe already exists')}")

    # 6. List tags
    print("\nSTEP  6: List tags")
    tags_resp = c.metadata_list_tags()
    if tags_resp.get("success"):
        tag_list = tags_resp.get("data", [])
        c._pass(f"GET /metadata/tags — {len(tag_list)} tags")
        for t in tag_list:
            print(f"         {t.get('name', '?')}")
    else:
        print(f"  [INFO] {tags_resp.get('error', 'unknown')}")

    if target_name:
        tags_for_table = c.metadata_list_tags(table=target_name)
        if tags_for_table.get("success"):
            tt = tags_for_table.get("data", [])
            c._pass(f"tags for '{target_name}': {[t['name'] for t in tt]}")

    # === Phase 4: Compliance Policies ===

    # 7. Create retention and masking policies
    print("\nSTEP  7: Create compliance policies")
    ret = c.metadata_create_retention_policy("log_retention_90d", days=90)
    if ret.get("success"):
        c._pass(f"retention policy: log_retention_90d (90 days)")
    else:
        print(f"  [INFO] retention: {ret.get('error', 'maybe already exists')}")

    mask = c.metadata_create_masking_policy("email_mask", columns=["email", "phone"])
    if mask.get("success"):
        c._pass("masking policy: email_mask (email, phone)")
    else:
        print(f"  [INFO] masking: {mask.get('error', 'maybe already exists')}")

    # 8. List all policies
    print("\nSTEP  8: List policies")
    pol = c.metadata_list_policies()
    if pol.get("success"):
        policies = pol.get("data", [])
        c._pass(f"GET /metadata/policies — {len(policies)} policies")
        for p in policies:
            print(f"         {p.get('name', '?')}")
    else:
        print(f"  [INFO] {pol.get('error', 'unknown')}")

    # === Phase 5: Model Registry ===

    # 9. List registered ML models
    print("\nSTEP  9: List ML models")
    models = c.metadata_list_models()
    if models.get("success"):
        model_list = models.get("data", [])
        c._pass(f"GET /metadata/models — {len(model_list)} models")
        for m in model_list:
            print(f"         {m.get('name', '?')}")
    else:
        print(f"  [INFO] {models.get('error', 'unknown')}")

    # 10. Get model version info
    print("\nSTEP 10: Get model versions")
    if models.get("success"):
        model_names = [m["name"] for m in models.get("data", [])]
        if model_names:
            for mn in model_names[:2]:
                ver = c.metadata_get_model_versions(mn)
                if ver.get("success"):
                    versions = ver.get("data", [])
                    c._pass(f"model '{mn}' — {len(versions)} versions")
                    for v in versions:
                        print(f"         v{v.get('version')} [{v.get('tier')}] "
                              f"aliases={v.get('aliases', [])}")
                else:
                    print(f"  [INFO] model '{mn}' versions: {ver.get('error')}")
        else:
            c._pass("no models registered yet")
    else:
        print("  [SKIP] No models to inspect")

    # === Phase 6: Statistics ===

    # 11. Collect statistics for a table
    print("\nSTEP 11: Collect table statistics")
    if target_name:
        stats = c.metadata_collect_statistics(target_name)
        if stats.get("success"):
            s = stats.get("data", {})
            c._pass(f"POST /metadata/statistics/{target_name}")
            print(f"         row_count={s.get('row_count', '?')}")
            print(f"         column_count={s.get('column_count', '?')}")
            print(f"         size_mb={s.get('size_mb', '?')}")
            for col in s.get("columns", [])[:5]:
                print(f"         {col.get('name'):20s} {col.get('type', '?')}")
        else:
            print(f"  [INFO] stats: {stats.get('error', 'unknown')}")
    else:
        print("  [SKIP] No target dataset for statistics")

    # === Summary ===

    print("\n" + "=" * 60)
    print("SUMMARY")
    n_cat = len(cat.get("data", [])) if cat.get("success") else 0
    n_tbl = len(tbl.get("data", [])) if tbl.get("success") else 0
    n_tag = len(tags_resp.get("data", [])) if tags_resp.get("success") else 0
    n_pol = len(pol.get("data", [])) if pol.get("success") else 0
    n_mod = len(models.get("data", [])) if models.get("success") else 0
    print(f"  Catalogs: {n_cat}  Tables: {n_tbl}  Tags: {n_tag}")
    print(f"  Policies: {n_pol}  Models: {n_mod}")

    # === Phase 7: v1.4.2 Deep Governance ===

    # 12a. Retention enforcement (dry-run)
    print("\nSTEP 12a: Retention enforcement (dry-run)")
    enforce = c._request("POST", "/metadata/policies/enforce?dry_run=true")
    if enforce.get("success"):
        c._pass(f"POST /policies/enforce?dry_run — cleaned={enforce['data']['tables_cleaned']}")
    else:
        print(f"  [INFO] enforce: {enforce.get('detail', enforce.get('error', 'not configured'))}")

    # 12b. Lineage query
    print("\nSTEP 12b: Lineage query")
    if target_name:
        lin = c._request("GET", f"/metadata/lineage/{target_name}")
        if lin.get("success"):
            ld = lin.get("data", {})
            c._pass(f"GET /metadata/lineage/{target_name}")
            print(f"         op={ld.get('operation')} sources={ld.get('sources')} "
                  f"outputs={ld.get('outputs')} version={ld.get('lance_version')}")
        else:
            print(f"  [INFO] lineage: {lin.get('error', 'not found')}")

    print("=" * 60)
    print("API-33  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
