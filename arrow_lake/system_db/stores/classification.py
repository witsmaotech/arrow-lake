"""DatasetClassificationStore(v1.11.5 W2 #4)——数据集 PII 分级。

登记不校验(分级是治理事实,内容核验后续投放);四档封闭集
public/internal/confidential/restricted。消费面:corpus 导出分级-脱敏绑定
校验(release.py,W2 #5)。写后显式 commit(libSQL 不 autocommit,速查坑);
分级变更的审计在 router 层(audit dataset.classification_changed)。
"""

from __future__ import annotations

from typing import Any

from arrow_lake.system_db.connection import SystemDB

_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

# 四档封闭集(敏感性递增);None(无行)= 未分级
TIERS = ("public", "internal", "confidential", "restricted")


class DatasetClassificationStore:
    """dataset_classification 单表。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    def set(
        self,
        dataset: str,
        tier: str,
        *,
        actor: str = "",
        note: str | None = None,
    ) -> dict[str, Any]:
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
        self._db.execute(
            f"INSERT INTO dataset_classification (dataset, tier, actor, note, created_at, updated_at) "
            f"VALUES (?, ?, ?, ?, {_NOW}, {_NOW}) "
            "ON CONFLICT(dataset) DO UPDATE SET "
            "tier=excluded.tier, actor=excluded.actor, note=excluded.note, "
            f"updated_at={_NOW}",
            (dataset, tier, actor, note),
        )
        self._db.commit()
        rec = self.get(dataset)
        assert rec is not None  # INSERT 即建行,防御分支
        return rec

    def get(self, dataset: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT dataset, tier, actor, note, created_at, updated_at "
            "FROM dataset_classification WHERE dataset=?",
            (dataset,),
        ).fetchone()
        if row is None:
            return None
        return {
            "dataset": row[0],
            "tier": row[1],
            "actor": row[2],
            "note": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }

    def delete(self, dataset: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM dataset_classification WHERE dataset=?", (dataset,)
        )
        self._db.commit()
        return bool(cur.rowcount) if hasattr(cur, "rowcount") else True

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT dataset, tier, actor, note, created_at, updated_at "
            "FROM dataset_classification ORDER BY dataset"
        ).fetchall()
        return [
            {
                "dataset": r[0], "tier": r[1], "actor": r[2],
                "note": r[3], "created_at": r[4], "updated_at": r[5],
            }
            for r in rows
        ]
