"""v1.11.0 MS1 (F1.2): ontology_rules 注册表 store(libSQL system_db)。

规则在此**登记不执行**(条件表达式到 MS3 决策层才落地);状态机
draft → active → retired(→ draft 复活),非法迁移拒绝。

沿用 store 约定(stores/extraction_templates.py):薄方法 over
``SystemDB.execute``;**写后必须显式 commit**(libSQL 不 autocommit,
CLAUDE.md 速查坑);读侧行转 dict 带 keys 防御。
"""

from __future__ import annotations

import logging
from typing import Any

from arrow_lake.system_db.connection import SystemDB

logger = logging.getLogger(__name__)

# 合则状态迁移;其余一律 ValueError(draft → retired 跳级、任意 → 未知态等)
_LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active"},
    "active": {"retired"},
    "retired": {"draft"},
}

_RULE_COLUMNS = (
    "id", "rule_id", "scope", "condition_expr", "conclusion",
    "source_ref", "status", "created_at", "updated_at",
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(zip(_RULE_COLUMNS, row))


class OntologyRulesStore:
    """ontology_rules 表的 CRUD + 状态机。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def upsert_rule(
        self,
        rule_id: str,
        *,
        scope: str,
        condition_expr: str,
        conclusion: str,
        source_ref: str,
    ) -> None:
        """新建或更新规则(更新不改 status — 状态只走 transition)。"""
        self._db.execute(
            """INSERT INTO ontology_rules
                   (rule_id, scope, condition_expr, conclusion, source_ref)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(rule_id) DO UPDATE SET
                   scope = excluded.scope,
                   condition_expr = excluded.condition_expr,
                   conclusion = excluded.conclusion,
                   source_ref = excluded.source_ref,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (rule_id, scope, condition_expr, conclusion, source_ref),
        )
        self._db.commit()

    def transition(self, rule_id: str, to_status: str) -> bool:
        """按状态机迁移;规则不存在返回 False,非法迁移 ValueError。"""
        rule = self.get_rule(rule_id)
        if rule is None:
            return False
        current = rule["status"]
        if to_status not in _LEGAL_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"illegal transition {current!r} → {to_status!r} "
                f"(legal: draft→active→retired→draft)"
            )
        self._db.execute(
            """UPDATE ontology_rules
                  SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                WHERE rule_id = ?""",
            (to_status, rule_id),
        )
        self._db.commit()
        return True

    def delete_rule(self, rule_id: str) -> bool:
        deleted = self._db.execute(
            "DELETE FROM ontology_rules WHERE rule_id = ?", (rule_id,)
        )
        self._db.commit()
        return bool(deleted.rowcount) if hasattr(deleted, "rowcount") else True

    # -- 读 ---------------------------------------------------------------

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        rows = self._db.execute(
            "SELECT * FROM ontology_rules WHERE rule_id = ?", (rule_id,)
        ).fetchall()
        return _row_to_dict(rows[0]) if rows else None

    def list_rules(
        self, *, scope: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ontology_rules WHERE 1=1"
        params: list[Any] = []
        if scope is not None:
            sql += " AND scope = ?"
            params.append(scope)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY rule_id"
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(r) for r in rows]
