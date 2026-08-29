"""AnnotationProjectStore(v1.11.3 MS4 W1.4,F4.2)。

标注项目注册表:AL 侧项目(名称/源数据集/绑定模板/LS label_config)的
SoT。LS 是 transient——``ls_project_id`` 只是懒重绑提示,LS 容器重建后
由 dispatch 重新创建/重绑,注册表本身不受影响。

写后显式 commit(libSQL 不 autocommit,速查坑);行按位置索引取值
(沿 ActionCatalogStore 模式,libSQL 行非 sqlite3.Row)。
"""

from __future__ import annotations

import hashlib
from typing import Any

from arrow_lake.system_db.connection import SystemDB

_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

_COLS = (
    "id", "name", "dataset", "template_name", "labeling_config",
    "config_source", "config_hash", "ls_project_id", "status",
    "created_at", "updated_at", "recover_watermark",
)


def _row_dict(r: Any) -> dict[str, Any]:
    return dict(zip(_COLS, r, strict=False))


class AnnotationProjectStore:
    """annotation_projects 表的薄封装(CRUD + LS 重绑)。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    def create_project(
        self,
        *,
        name: str,
        dataset: str,
        template_name: str,
        labeling_config: str,
        config_source: str = "generated",
    ) -> dict[str, Any] | None:
        """创建项目;重名 → None(路由层 422,不静默覆盖)。"""
        config_hash = hashlib.sha1(labeling_config.encode("utf-8")).hexdigest()
        cur = self._db.execute(
            """INSERT OR IGNORE INTO annotation_projects
                   (name, dataset, template_name, labeling_config,
                    config_source, config_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, dataset, template_name, labeling_config, config_source, config_hash),
        )
        self._db.commit()
        if cur is None or getattr(cur, "rowcount", 1) == 0:
            return None
        return self.get_project(name)

    def get_project(self, name: str) -> dict[str, Any] | None:
        row = self._db.execute(
            f"SELECT {', '.join(_COLS)} FROM annotation_projects WHERE name = ?", (name,)
        ).fetchone()
        return _row_dict(row) if row is not None else None

    def list_projects(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            f"SELECT {', '.join(_COLS)} FROM annotation_projects ORDER BY name"
        ).fetchall()
        return [_row_dict(r) for r in rows]

    def delete_project(self, name: str) -> bool:
        cur = self._db.execute("DELETE FROM annotation_projects WHERE name = ?", (name,))
        self._db.commit()
        return bool(getattr(cur, "rowcount", 0))

    def set_ls_project_id(self, name: str, ls_project_id: int) -> bool:
        """dispatch 重绑(W2);LS transient 重建后调用。"""
        cur = self._db.execute(
            f"UPDATE annotation_projects SET ls_project_id = ?, updated_at = {_NOW} "
            "WHERE name = ?",
            (ls_project_id, name),
        )
        self._db.commit()
        return bool(getattr(cur, "rowcount", 0))

    def set_status(self, name: str, status: str) -> bool:
        """active ↔ closed(console 关闭项目,W4)。"""
        cur = self._db.execute(
            f"UPDATE annotation_projects SET status = ?, updated_at = {_NOW} "
            "WHERE name = ?",
            (status, name),
        )
        self._db.commit()
        return bool(getattr(cur, "rowcount", 0))

    # --- 回收 watermark(W3.4,轮询对账增量游标) ---------------------------

    def set_watermark(self, name: str, watermark: int) -> bool:
        cur = self._db.execute(
            f"UPDATE annotation_projects SET recover_watermark = ?, updated_at = {_NOW} "
            "WHERE name = ?",
            (int(watermark), name),
        )
        self._db.commit()
        return bool(getattr(cur, "rowcount", 0))
