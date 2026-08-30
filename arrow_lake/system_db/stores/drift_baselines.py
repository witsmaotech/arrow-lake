"""DriftBaselineStore (v1.11.4 MS5 W2.2) — 漂移基线快照链。

append-only 历史(最新一条为生效基线);发布时自动快照(W3 接线)、
手动可重置。写后显式 commit(libSQL 约定)。
"""

from __future__ import annotations

import json
from typing import Any

from arrow_lake.system_db.connection import SystemDB

_COLS = "id, dataset, columns_json, source, created_at"


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row[0],
        "dataset": row[1],
        "columns": json.loads(row[2]) if row[2] else {},
        "source": row[3],
        "created_at": row[4],
    }


class DriftBaselineStore:
    """sys_drift_baselines:append-only 基线链 + 最新基线查询。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    def set_baseline(
        self, dataset: str, columns: dict[str, Any], *, source: str = "manual",
    ) -> dict[str, Any]:
        """落一条基线快照(不查重;最新一条生效)。"""
        self._db.execute(
            """INSERT INTO sys_drift_baselines (dataset, columns_json, source)
               VALUES (?, ?, ?)""",
            (dataset, json.dumps(columns, ensure_ascii=False), source),
        )
        self._db.commit()
        return self.get_baseline(dataset) or {}

    def get_baseline(self, dataset: str) -> dict[str, Any] | None:
        """最新一条基线(检测对比对象)。"""
        cur = self._db.execute(
            f"SELECT {_COLS} FROM sys_drift_baselines WHERE dataset = ? "
            "ORDER BY id DESC LIMIT 1",
            (dataset,),
        )
        row = cur.fetchone() if cur is not None else None
        return _row_dict(row) if row is not None else None

    def list_history(self, dataset: str, limit: int = 20) -> list[dict[str, Any]]:
        """基线历史 newest-first。"""
        cur = self._db.execute(
            f"SELECT {_COLS} FROM sys_drift_baselines WHERE dataset = ? "
            "ORDER BY id DESC LIMIT ?",
            (dataset, limit),
        )
        rows = cur.fetchall() if cur is not None else []
        return [_row_dict(r) for r in rows]
