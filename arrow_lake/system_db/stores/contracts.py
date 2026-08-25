"""ContractStore (DR13/DR14, v1.11.0.1 W2.1) — dataset contract version chain.

Mirrors OntologyVersionStore: same source_hash → no new version; content
change → next version + structured diff (computed from parsed contract
features, not raw text). Writes commit explicitly (libSQL pitfall).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from arrow_lake.system_db.connection import SystemDB


def _row_to_version(row: Any, *, with_yaml: bool) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": row[0],
        "scope": row[1],
        "version": row[2],
        "source_hash": row[3],
        "diff": json.loads(row[4]) if row[4] else None,
        "created_at": row[5],
    }
    if with_yaml:
        rec["contract_yaml"] = row[6]
    return rec


class ContractStore:
    """dataset_contracts version chain: skip-on-same-hash, diff-on-change."""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def save_contract(self, scope: str, contract_yaml: str) -> dict[str, Any]:
        """Insert the next version unless the latest has the same hash.

        Returns ``{"id", "version", "created", "diff"}`` — ``created`` is
        False when an identical-hash version already exists (skipped).
        """
        source_hash = hashlib.sha1(contract_yaml.encode("utf-8")).hexdigest()
        latest = self._latest(scope)
        if latest is not None and latest["source_hash"] == source_hash:
            return {
                "id": latest["id"], "version": latest["version"],
                "created": False, "diff": latest["diff"],
            }

        diff: dict[str, Any] | None = None
        diff_json: str | None = None
        if latest is not None:
            from arrow_lake.contract.schema import (
                contract_features,
                diff_features,
                parse_contract,
            )

            diff = diff_features(
                contract_features(parse_contract(latest["contract_yaml"])),
                contract_features(parse_contract(contract_yaml)),
            )
            diff_json = json.dumps(diff, ensure_ascii=False)

        version = (latest["version"] + 1) if latest is not None else 1
        self._db.execute(
            """INSERT INTO dataset_contracts
                   (scope, version, contract_yaml, source_hash, diff_json)
               VALUES (?, ?, ?, ?, ?)""",
            (scope, version, contract_yaml, source_hash, diff_json),
        )
        self._db.commit()
        rows = self._db.execute(
            "SELECT id FROM dataset_contracts WHERE scope = ? AND version = ?",
            (scope, version),
        ).fetchall()
        return {"id": int(rows[0][0]), "version": version, "created": True, "diff": diff}

    # -- 读 ---------------------------------------------------------------

    def list_versions(self, scope: str, limit: int = 50) -> list[dict[str, Any]]:
        """Version chain newest-first; omits the YAML payload (list view)."""
        cur = self._db.execute(
            "SELECT id, scope, version, source_hash, diff_json, created_at "
            "FROM dataset_contracts WHERE scope = ? "
            "ORDER BY version DESC LIMIT ?",
            (scope, limit),
        )
        rows = cur.fetchall() if cur is not None else []
        return [_row_to_version(r, with_yaml=False) for r in rows]

    def list_scopes(self) -> list[dict[str, Any]]:
        """One row per scope carrying its latest version (list view)."""
        cur = self._db.execute(
            "SELECT scope, version, source_hash, created_at FROM dataset_contracts "
            "WHERE version = (SELECT MAX(version) FROM dataset_contracts c2 "
            "                 WHERE c2.scope = dataset_contracts.scope) "
            "ORDER BY scope"
        )
        rows = cur.fetchall() if cur is not None else []
        return [
            {"scope": r[0], "version": r[1], "source_hash": r[2], "created_at": r[3]}
            for r in rows
        ]

    def get_version(
        self, scope: str, *, version: int | None = None,
    ) -> dict[str, Any] | None:
        """Latest version (None) or a specific one, including the YAML."""
        if version is None:
            cur = self._db.execute(
                "SELECT id, scope, version, source_hash, diff_json, created_at, contract_yaml "
                "FROM dataset_contracts WHERE scope = ? ORDER BY version DESC LIMIT 1",
                (scope,),
            )
        else:
            cur = self._db.execute(
                "SELECT id, scope, version, source_hash, diff_json, created_at, contract_yaml "
                "FROM dataset_contracts WHERE scope = ? AND version = ?",
                (scope, version),
            )
        row = cur.fetchone() if cur is not None else None
        return _row_to_version(row, with_yaml=True) if row is not None else None

    def _latest(self, scope: str) -> dict[str, Any] | None:
        return self.get_version(scope)
