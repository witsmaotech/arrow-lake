"""v1.11.0 MS1 (F1.2/F1.4): ontology 存储层(libSQL system_db)。

* :class:`OntologyRulesStore` — ontology_rules 注册表。规则在此**登记不
  执行**(条件表达式到 MS3 决策层才落地);状态机 draft → active →
  retired(→ draft 复活),非法迁移拒绝。
* :class:`OntologyVersionStore` — ontology_versions 快照链(模板 → SHACL
  Turtle + 结构化 diff)。同 ``source_hash`` 跳过;内容变 → 下一版本。

沿用 store 约定(stores/extraction_templates.py):薄方法 over
``SystemDB.execute``;**写后必须显式 commit**(libSQL 不 autocommit,
CLAUDE.md 速查坑);读侧行转 dict 带 keys 防御。
"""

from __future__ import annotations

import json
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

# v1.11.1 W1.4 (DR15 D-2): 建模侧 M3 五分类 code(console 中文标签:验证/计算/
# 推导/转换/风控)。SQLite CHECK 加不了,枚举在此与 API pydantic 双层把关。
RULE_TYPES = (
    "validation", "computation", "derivation", "transformation", "risk_control",
)

_RULE_COLUMNS = (
    "id", "rule_id", "scope", "condition_expr", "conclusion",
    "source_ref", "status", "created_at", "updated_at",
    # ALTER ADD COLUMN lands at the END (V013), not in declaration order.
    "rule_type", "version",
)

_VERSION_COLUMNS = (
    "id", "scope", "template_name", "version", "shapes_turtle",
    "source_hash", "diff_json", "created_at",
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(zip(_RULE_COLUMNS, row))


def _row_to_version(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(zip(_VERSION_COLUMNS, row))


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
        rule_type: str | None = None,
        version: str | None = None,
    ) -> None:
        """新建或更新规则(更新不改 status — 状态只走 transition)。

        ``rule_type``/``version`` 省略时:插入回落默认(validation/'1'),
        更新保留现值(不静默重置分类)。
        """
        if rule_type is not None and rule_type not in RULE_TYPES:
            raise ValueError(
                f"rule_type must be one of {RULE_TYPES}, got {rule_type!r}"
            )
        current = self.get_rule(rule_id)
        rt = rule_type or (current["rule_type"] if current else "validation")
        ver = version or (current["version"] if current else "1")
        self._db.execute(
            """INSERT INTO ontology_rules
                   (rule_id, scope, condition_expr, conclusion, source_ref,
                    rule_type, version)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rule_id) DO UPDATE SET
                   scope = excluded.scope,
                   condition_expr = excluded.condition_expr,
                   conclusion = excluded.conclusion,
                   source_ref = excluded.source_ref,
                   rule_type = excluded.rule_type,
                   version = excluded.version,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (rule_id, scope, condition_expr, conclusion, source_ref, rt, ver),
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
        self, *, scope: str | None = None, status: str | None = None,
        rule_type: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ontology_rules WHERE 1=1"
        params: list[Any] = []
        if scope is not None:
            sql += " AND scope = ?"
            params.append(scope)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if rule_type is not None:
            sql += " AND rule_type = ?"
            params.append(rule_type)
        sql += " ORDER BY rule_id"
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(r) for r in rows]


class OntologyVersionStore:
    """ontology_versions 快照链:同 hash 跳过,内容变 → 新版本 + 结构化 diff。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def snapshot(
        self,
        *,
        scope: str,
        template_name: str,
        shapes_turtle: str,
        source_hash: str,
    ) -> dict[str, Any]:
        """Insert the next version unless the latest has the same hash.

        Returns:
            ``{"id", "version", "created", "diff"}`` — ``created`` is False
            when an identical-hash snapshot already exists (skipped).
        """
        latest = self._latest(scope, template_name)
        if latest is not None and latest["source_hash"] == source_hash:
            return {
                "id": latest["id"],
                "version": latest["version"],
                "created": False,
                "diff": self._parse_diff(latest["diff_json"]),
            }

        diff_json: str | None = None
        diff: dict[str, Any] | None = None
        if latest is not None:
            from arrow_lake.ontology.versioning import diff_features, extract_features

            diff = diff_features(
                extract_features(latest["shapes_turtle"]),
                extract_features(shapes_turtle),
            )
            diff_json = json.dumps(diff, ensure_ascii=False)

        version = (latest["version"] + 1) if latest is not None else 1
        self._db.execute(
            """INSERT INTO ontology_versions
                   (scope, template_name, version, shapes_turtle, source_hash, diff_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scope, template_name, version, shapes_turtle, source_hash, diff_json),
        )
        self._db.commit()
        rows = self._db.execute(
            "SELECT id FROM ontology_versions "
            "WHERE scope = ? AND template_name = ? AND version = ?",
            (scope, template_name, version),
        ).fetchall()
        return {
            "id": int(rows[0][0]),
            "version": version,
            "created": True,
            "diff": diff,
        }

    # -- 读 ---------------------------------------------------------------

    def list_versions(
        self,
        *,
        scope: str | None = None,
        template_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Version chain newest-first; omits the Turtle payload (list view)."""
        sql = (
            "SELECT id, scope, template_name, version, source_hash, "
            "diff_json, created_at FROM ontology_versions WHERE 1=1"
        )
        params: list[Any] = []
        if scope is not None:
            sql += " AND scope = ?"
            params.append(scope)
        if template_name is not None:
            sql += " AND template_name = ?"
            params.append(template_name)
        sql += " ORDER BY version DESC LIMIT ?"
        params.append(int(limit))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(zip(
                ("id", "scope", "template_name", "version",
                 "source_hash", "diff_json", "created_at"),
                tuple(r),
            ))
            d["diff"] = self._parse_diff(d.pop("diff_json"))
            out.append(d)
        return out

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        """Full row including ``shapes_turtle``; None when unknown id."""
        rows = self._db.execute(
            "SELECT * FROM ontology_versions WHERE id = ?", (int(version_id),)
        ).fetchall()
        if not rows:
            return None
        d = _row_to_version(rows[0])
        d["diff"] = self._parse_diff(d.pop("diff_json", None))
        return d

    # -- 内部 --------------------------------------------------------------

    def _latest(self, scope: str, template_name: str) -> dict[str, Any] | None:
        rows = self._db.execute(
            "SELECT * FROM ontology_versions "
            "WHERE scope = ? AND template_name = ? ORDER BY version DESC LIMIT 1",
            (scope, template_name),
        ).fetchall()
        return _row_to_version(rows[0]) if rows else None

    @staticmethod
    def _parse_diff(raw: str | None) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None
