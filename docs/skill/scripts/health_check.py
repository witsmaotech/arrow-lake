#!/usr/bin/env python3
"""Arrow Lake skill — end-to-end facade smoke test.

Runs against a tiny SYNTHETIC dataset on a local base_uri (default ./data).
Verifies the create → read → OLAP → health path. Zero external model deps.

Run (project venv recommended, per project convention .venv/bin/python3):
    python docs/skill/scripts/health_check.py ./data

Exit codes: 0 = pass, 1 = fail.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

EXIT_OK = 0
EXIT_FAIL = 1


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Result:
    steps: list[Step] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append(Step(name, ok, detail))

    @property
    def passed(self) -> bool:
        return all(s.ok for s in self.steps)


def run(base_uri: str, dataset: str = "skill_smoke") -> Result:
    result = Result()
    try:
        import pyarrow as pa
        from arrow_lake import Lake
    except Exception as exc:  # noqa: BLE001
        result.add("import", False, f"cannot import arrow_lake/pyarrow: {exc}")
        return result

    table = pa.table(
        {
            "id": ["1", "2", "3"],
            "text": ["machine learning basics", "deep learning overview", "data analytics intro"],
            "category": ["ml", "ml", "data"],
        }
    )

    lake = Lake(base_uri)
    try:
        # create
        try:
            lake.create_dataset(dataset, table)
            result.add("create_dataset", True, f"{table.num_rows} rows")
        except Exception as exc:  # noqa: BLE001
            result.add("create_dataset", False, str(exc))
            return result

        # list
        names = lake.list_datasets()
        result.add("list_datasets", dataset in names, f"{len(names)} datasets")

        # read
        try:
            rt = lake.read_dataset(dataset)
            result.add("read_dataset", rt.num_rows == table.num_rows, f"{rt.num_rows} rows")
        except Exception as exc:  # noqa: BLE001
            result.add("read_dataset", False, str(exc))

        # OLAP (DuckDB) — note: NO params= arg
        try:
            res = lake.olap_query(
                dataset,
                "SELECT category, COUNT(*) AS c FROM {ds} GROUP BY category".format(ds=dataset),
            )
            rows = res.table.to_pylist()
            result.add("olap_query", len(rows) > 0, f"{len(rows)} groups")
        except Exception as exc:  # noqa: BLE001
            result.add("olap_query", False, str(exc))

        # version
        try:
            ver = lake.version()
            result.add("version", bool(ver), str(ver))
        except Exception as exc:  # noqa: BLE001
            result.add("version", False, str(exc))

        # health
        try:
            h = lake.health()
            # HealthInfo; be tolerant of attribute shape
            status = getattr(h, "status", None) or str(h)
            result.add("health", True, str(status))
        except Exception as exc:  # noqa: BLE001
            result.add("health", False, str(exc))
    finally:
        # cleanup
        try:
            lake.delete_dataset(dataset)
        except Exception:  # noqa: BLE001
            pass
        try:
            lake.shutdown()
        except Exception:  # noqa: BLE001
            pass

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Arrow Lake facade smoke test (synthetic data).")
    ap.add_argument("base_uri", nargs="?", default="./data", help="Lance base URI (default ./data)")
    ap.add_argument("--dataset", default="skill_smoke", help="temp dataset name")
    args = ap.parse_args()

    print(f"Arrow Lake smoke test — base_uri={args.base_uri}")
    result = run(args.base_uri, args.dataset)
    width = max(len(s.name) for s in result.steps) if result.steps else 10
    for s in result.steps:
        mark = "OK  " if s.ok else "FAIL"
        print(f"  [{mark}] {s.name:<{width}}  {s.detail}")
    print("RESULT:", "PASS" if result.passed else "FAIL")
    return EXIT_OK if result.passed else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
