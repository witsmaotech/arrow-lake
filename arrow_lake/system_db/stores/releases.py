"""ReleaseStore (v1.11.4 MS5 W3.1) — sys_releases 发布注册表。

写后显式 commit(libSQL 约定);``UNIQUE(dataset, tag)`` 撞重 →
``create_release`` 返回 None(调用方 422 重复 tag)。
"""

from __future__ import annotations

from typing import Any

from arrow_lake.system_db.connection import SystemDB

_COLS = (
    "id, dataset, tag, major, minor, patch, lance_version, changelog, "
    "quality_report_id, total_score, star, admission, datasheet_yaml, "
    "status, released_by, created_at"
)


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row[0], "dataset": row[1], "tag": row[2],
        "major": row[3], "minor": row[4], "patch": row[5],
        "lance_version": row[6], "changelog": row[7],
        "quality_report_id": row[8], "total_score": row[9],
        "star": row[10], "admission": row[11], "datasheet_yaml": row[12],
        "status": row[13], "released_by": row[14], "created_at": row[15],
    }


class ReleaseStore:
    """sys_releases:发布历史 + 最新 active 查询(劣化比较基准)。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def create_release(
        self,
        *,
        dataset: str,
        tag: str,
        lance_version: int,
        changelog: str,
        quality_report_id: int | None,
        total_score: float | None,
        star: int | None,
        admission: str | None,
        datasheet_yaml: str,
        released_by: str = "system",
    ) -> dict[str, Any] | None:
        """落一条发布;``(dataset, tag)`` 已存在 → None(重复 tag)。

        先查后插(UNIQUE 索引兜底真并发;不靠约束异常——SystemDB.execute
        对任何异常做重连重试,:memory: 重连即空库,约束异常会被吞成
        "no such table")。
        """
        from arrow_lake.release.registry import parse_tag

        if self.get_release(dataset, tag) is not None:
            return None
        major, minor, patch = parse_tag(tag)
        self._db.execute(
            """INSERT INTO sys_releases
                   (dataset, tag, major, minor, patch, lance_version,
                    changelog, quality_report_id, total_score, star,
                    admission, datasheet_yaml, released_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset, tag, major, minor, patch, lance_version,
                changelog, quality_report_id, total_score, star,
                admission, datasheet_yaml, released_by,
            ),
        )
        self._db.commit()
        got = self.get_release(dataset, tag)
        assert got is not None
        return got

    def retire_release(self, dataset: str, tag: str) -> bool:
        """active → retired(软状态);已 retired/不存在 → False。

        ⚠️不用 ``SELECT changes()`` 判定——http 型 libSQL 各语句不保同一
        连接,changes() 恒 0(live 实证:UPDATE 已提交但返回 False);
        以**前后状态对照**为准。
        """
        rec = self.get_release(dataset, tag)
        if rec is None or rec["status"] != "active":
            return False
        self._db.execute(
            "UPDATE sys_releases SET status = 'retired' "
            "WHERE dataset = ? AND tag = ? AND status = 'active'",
            (dataset, tag),
        )
        self._db.commit()
        after = self.get_release(dataset, tag)
        return after is not None and after["status"] == "retired"

    # -- 读 ---------------------------------------------------------------

    def get_release(self, dataset: str, tag: str) -> dict[str, Any] | None:
        cur = self._db.execute(
            f"SELECT {_COLS} FROM sys_releases WHERE dataset = ? AND tag = ?",
            (dataset, tag),
        )
        row = cur.fetchone() if cur is not None else None
        return _row_dict(row) if row is not None else None

    def list_releases(self, dataset: str, limit: int = 50) -> list[dict[str, Any]]:
        """发布历史 newest-first(active+retired 全保留)。"""
        cur = self._db.execute(
            f"SELECT {_COLS} FROM sys_releases WHERE dataset = ? "
            "ORDER BY id DESC LIMIT ?",
            (dataset, limit),
        )
        rows = cur.fetchall() if cur is not None else []
        return [_row_dict(r) for r in rows]

    def latest_release(
        self, dataset: str, *, active_only: bool = True,
    ) -> dict[str, Any] | None:
        """最新发布(默认 active;劣化比较/规格书导出的基准)。"""
        where = "dataset = ?" + (" AND status = 'active'" if active_only else "")
        cur = self._db.execute(
            f"SELECT {_COLS} FROM sys_releases WHERE {where} "
            "ORDER BY id DESC LIMIT 1",
            (dataset,),
        )
        row = cur.fetchone() if cur is not None else None
        return _row_dict(row) if row is not None else None
