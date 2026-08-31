"""DecisionsHistoryStore(发版前清偿 D 项,2026-08-31)。

MS3 研判无状态 → 本表给 RLHF 配对(F5.6③)与飞轮低置信自动检测
(F5.8)提供数据面。opt-in 写入(decisions/assess?record_history=true);
读侧:按对象最新、低置信清单。写后显式 commit(libSQL 约定)。
"""

from __future__ import annotations

import json
from typing import Any

from arrow_lake.system_db.connection import SystemDB

_COLS = ("id, dataset, object_type, object_id, lifecycle_state, "
         "matched_rules, rule_ids_json, conclusions_json, confidence, "
         "actor, created_at")


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row[0], "dataset": row[1], "object_type": row[2],
        "object_id": row[3], "lifecycle_state": row[4],
        "matched_rules": row[5],
        "rule_ids": json.loads(row[6]) if row[6] else [],
        "conclusions": json.loads(row[7]) if row[7] else [],
        "confidence": row[8], "actor": row[9], "created_at": row[10],
    }


class DecisionsHistoryStore:
    """decisions_history:append-only 研判历史 + 低置信查询。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def record(
        self, *, dataset: str, object_type: str, object_id: str,
        lifecycle_state: str | None, matched_rules: int,
        rule_ids: list[str], conclusions: list[dict[str, Any]],
        confidence: float, actor: str = "",
    ) -> dict[str, Any]:
        self._db.execute(
            """INSERT INTO decisions_history
                   (dataset, object_type, object_id, lifecycle_state,
                    matched_rules, rule_ids_json, conclusions_json,
                    confidence, actor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset, object_type, object_id, lifecycle_state,
                matched_rules, json.dumps(rule_ids, ensure_ascii=False),
                json.dumps(conclusions, ensure_ascii=False),
                confidence, actor,
            ),
        )
        self._db.commit()
        cur = self._db.execute(
            f"SELECT {_COLS} FROM decisions_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone() if cur is not None else None
        return _row_dict(row) if row is not None else None

    # -- 读 ---------------------------------------------------------------

    def latest_for_object(
        self, dataset: str, object_id: str,
    ) -> dict[str, Any] | None:
        cur = self._db.execute(
            f"SELECT {_COLS} FROM decisions_history "
            "WHERE dataset = ? AND object_id = ? ORDER BY id DESC LIMIT 1",
            (dataset, object_id),
        )
        row = cur.fetchone() if cur is not None else None
        return _row_dict(row) if row is not None else None

    def low_confidence(
        self, dataset: str, *, threshold: float, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """置信度低于阈值的最新研判(飞轮 F5.8 自动回流数据面)。"""
        cur = self._db.execute(
            f"SELECT {_COLS} FROM decisions_history "
            "WHERE dataset = ? AND confidence < ? "
            "ORDER BY confidence ASC LIMIT ?",
            (dataset, threshold, limit),
        )
        rows = cur.fetchall() if cur is not None else []
        return [_row_dict(r) for r in rows]

    def list_history(
        self, dataset: str, *, limit: int = 50,
    ) -> list[dict[str, Any]]:
        cur = self._db.execute(
            f"SELECT {_COLS} FROM decisions_history WHERE dataset = ? "
            "ORDER BY id DESC LIMIT ?",
            (dataset, limit),
        )
        rows = cur.fetchall() if cur is not None else []
        return [_row_dict(r) for r in rows]
