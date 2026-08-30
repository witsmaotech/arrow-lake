"""QualityReportStore (v1.11.4 MS5 W1.4) — 五维评估报告历史链。

append-only(评估历史不可变;发布层读 ``latest_report`` 做准入/劣化
比较)。写后显式 commit(libSQL 不 autocommit 的项目约定)。
"""

from __future__ import annotations

import json
from typing import Any

from arrow_lake.system_db.connection import SystemDB

_COLS = (
    "id, dataset, total_score, star, admission, verdict, "
    "dimensions_json, vetoes_json, degraded_json, spec_json, "
    "assessed_at, assessed_by"
)


def _row_dict(row: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": row[0],
        "dataset": row[1],
        "total_score": row[2],
        "star": row[3],
        "admission": row[4],
        "verdict": row[5],
        "dimensions": json.loads(row[6]) if row[6] else {},
        "vetoes": json.loads(row[7]) if row[7] else [],
        "degraded": json.loads(row[8]) if row[8] else [],
        "spec": json.loads(row[9]) if row[9] else {},
        "assessed_at": row[10],
        "assessed_by": row[11],
    }
    return rec


class QualityReportStore:
    """sys_quality_reports:append-only 报告链 + 最新报告查询。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def create_report(
        self,
        dataset: str,
        *,
        total_score: float | None,
        star: int,
        admission: str,
        verdict: str,
        dimensions: dict[str, Any],
        vetoes: list[dict[str, Any]],
        degraded: list[str],
        spec: dict[str, Any],
        assessed_by: str = "system",
    ) -> dict[str, Any]:
        """落一条评估报告(不查重——评估历史全量保留)。"""
        self._db.execute(
            """INSERT INTO sys_quality_reports
                   (dataset, total_score, star, admission, verdict,
                    dimensions_json, vetoes_json, degraded_json, spec_json,
                    assessed_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset, total_score, star, admission, verdict,
                json.dumps(dimensions, ensure_ascii=False),
                json.dumps(vetoes, ensure_ascii=False),
                json.dumps(degraded, ensure_ascii=False),
                json.dumps(spec, ensure_ascii=False),
                assessed_by,
            ),
        )
        self._db.commit()
        return self.latest_report(dataset) or {}

    # -- 读 ---------------------------------------------------------------

    def list_reports(self, dataset: str, limit: int = 50) -> list[dict[str, Any]]:
        """评估历史 newest-first。"""
        cur = self._db.execute(
            f"SELECT {_COLS} FROM sys_quality_reports WHERE dataset = ? "
            "ORDER BY id DESC LIMIT ?",
            (dataset, limit),
        )
        rows = cur.fetchall() if cur is not None else []
        return [_row_dict(r) for r in rows]

    def latest_report(self, dataset: str) -> dict[str, Any] | None:
        """最新一条报告(发布准入/劣化比较的数据源)。"""
        cur = self._db.execute(
            f"SELECT {_COLS} FROM sys_quality_reports WHERE dataset = ? "
            "ORDER BY id DESC LIMIT 1",
            (dataset,),
        )
        row = cur.fetchone() if cur is not None else None
        return _row_dict(row) if row is not None else None
