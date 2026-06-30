#!/usr/bin/env python3
"""v1.8.6 one-shot migration: rebuild KG into per-dataset ``kg_{ds}`` graphs.

Lance is the source of truth. For each Lance dataset this runs ``kg_build`` so
its entities/relations land in the isolated ``kg_{dataset}`` graph instead of
the legacy shared ``hugegraph`` graph. Idempotent — re-running rebuilds each
dataset's graph in place (drop-on-delete reclaims old graphs).

The legacy ``hugegraph`` graph is left untouched (it remains the default graph
for arbitrary gremlin ``kg_query``).

Usage:
    python scripts/migrate-kg-per-dataset.py
    python scripts/migrate-kg-per-dataset.py --dataset my_docs
    python scripts/migrate-kg-per-dataset.py --base-uri s3://my-bucket
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from arrow_lake import Lake


async def _wait_for_completion(lake: Lake, task_id: str) -> dict | None:
    """Poll kg_build_status until the task reaches a terminal state."""
    while True:
        status = await lake.kg_build_status(task_id)
        if status is None:
            return None
        if status.get("status") in ("COMPLETED", "FAILED"):
            return status
        await asyncio.sleep(2)


async def migrate(lake: Lake, only: str | None) -> int:
    """Build a per-dataset KG graph for every Lance dataset. Returns failure count."""
    catalog = lake.catalog()
    datasets = sorted(d.name for d in catalog.datasets)
    if only:
        datasets = [d for d in datasets if d == only]
    if not datasets:
        print("No datasets found — nothing to migrate.")
        return 0

    print(f"Migrating {len(datasets)} dataset(s) into per-dataset KG graphs...")
    failures = 0
    for i, ds in enumerate(datasets, 1):
        try:
            task_id = await lake.kg_build(ds)
            final = await _wait_for_completion(lake, task_id)
            if final and final.get("status") == "COMPLETED":
                ents = final.get("entity_count", 0)
                rels = final.get("relation_count", 0)
                print(f"  [{i}/{len(datasets)}] {ds}: OK ({ents} entities, {rels} relations)")
            else:
                err = (final or {}).get("error", "did not complete")
                print(f"  [{i}/{len(datasets)}] {ds}: FAIL ({err})")
                failures += 1
        except Exception as exc:  # noqa: BLE001 — per-dataset isolation
            print(f"  [{i}/{len(datasets)}] {ds}: FAIL ({exc})")
            failures += 1

    print(f"Done. {len(datasets) - failures} ok, {failures} failed.")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-uri",
        default=os.environ.get("ARROW_LAKE__STORAGE__BASE_URI", "arrow-lake"),
        help="Lake base URI / bucket (default: arrow-lake)",
    )
    parser.add_argument("--dataset", default=None, help="Migrate only this dataset name")
    args = parser.parse_args()

    lake = Lake(base_uri=args.base_uri)
    failures = asyncio.run(migrate(lake, args.dataset))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
