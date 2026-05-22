"""Column-level data masking engine — applies masking functions to PyArrow Tables at query time."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MaskRule:
    """A single masking rule bound to a column."""

    column: str
    function: str  # redact | hash | partial | nullify


class MaskingEngine:
    """Query-time synchronous masking: transforms PyArrow columns based on Gravitino policies.

    Usage::

        engine = MaskingEngine(config)
        masked_table = engine.apply_masking(table, dataset="users", role="viewer")
    """

    _MASK_FUNCTIONS = {
        "redact": "****",
        "partial": None,
        "hash": None,
        "nullify": None,
    }

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        # Cache: {dataset: (rules, timestamp)}
        self._cache: dict[str, tuple[list[MaskRule], float]] = {}
        self._ttl = config.masking_policy_cache_ttl_seconds

    # ── public API ──

    def apply_masking(
        self,
        table: pa.Table,
        dataset: str,
        role: str,
    ) -> pa.Table:
        """Apply masking rules to a PyArrow Table. Admin role skips masking."""
        if role == "admin":
            return table

        rules = self._get_rules(dataset)
        if not rules:
            return table

        columns = table.column_names
        for rule in rules:
            if rule.column not in columns:
                continue
            col_idx = columns.index(rule.column)
            col = table.column(col_idx)
            masked = self._mask_column(col, rule.function)
            table = table.set_column(col_idx, rule.column, masked)

        return table

    def refresh_cache(self, dataset: str) -> list[MaskRule]:
        """Force-refresh masking rules for a dataset from Gravitino."""
        rules = self._fetch_rules_from_gravitino(dataset)
        with self._lock:
            self._cache[dataset] = (rules, time.time())
        return rules

    # ── internal ──

    def _get_rules(self, dataset: str) -> list[MaskRule]:
        """Get cached rules or fetch from Gravitino if expired."""
        with self._lock:
            cached = self._cache.get(dataset)
            if cached is not None:
                rules, ts = cached
                if time.time() - ts < self._ttl:
                    return rules

        return self.refresh_cache(dataset)

    def _fetch_rules_from_gravitino(self, dataset: str) -> list[MaskRule]:
        """Read masking policies for a table from Gravitino REST API."""
        rules: list[MaskRule] = []
        try:
            import json
            from urllib.request import Request, urlopen

            url = f"{self._config.uri}/api/metalakes/{self._config.metalake}/policies"
            req = Request(url)
            req.add_header("Accept", "application/vnd.gravitino.v1+json")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            for ident in data.get("identifiers", []):
                name = ident.get("name", "")
                if "mask" not in name:
                    continue
                try:
                    detail_url = (
                        f"{self._config.uri}/api/metalakes/{self._config.metalake}"
                        f"/policies/{name}"
                    )
                    detail_req = Request(detail_url)
                    detail_req.add_header("Accept", "application/vnd.gravitino.v1+json")
                    with urlopen(detail_req, timeout=10) as detail_resp:
                        detail = json.loads(detail_resp.read().decode())

                    policy = detail.get("policy", {})
                    props = policy.get("properties", {})
                    applied_tables = props.get("applied_tables", "")
                    if applied_tables:
                        tables_list = json.loads(applied_tables)
                        if not isinstance(tables_list, list) or dataset not in tables_list:
                            continue
                        columns = json.loads(props.get("masking.columns", props.get("columns", "[]")))
                        if not isinstance(columns, list):
                            continue
                        func = props.get("masking.function", "redact")
                        for col in columns:
                            if isinstance(col, str):
                                rules.append(MaskRule(column=col, function=func))
                except Exception:
                    logger.debug("masking_engine.policy_detail_failed", name=name)
        except Exception:
            logger.debug("masking_engine.fetch_failed", exc_info=True)
        return rules

    @staticmethod
    def _mask_column(column: pa.ChunkedArray, function: str) -> pa.ChunkedArray:
        """Apply a masking function to a PyArrow chunked array."""
        if function == "redact":
            return pc.replace_substring_regex(column, pattern=".", replacement="*")

        if function == "nullify":
            # Replace all non-null values with null
            arr = column.combine_chunks()
            null_arr = pa.nulls(len(arr), type=arr.type)
            return pa.chunked_array([null_arr])

        if function == "hash":
            # SHA-256 hash of string values
            def _hash_val(val: str | None) -> str | None:
                if val is None:
                    return None
                return hashlib.sha256(val.encode()).hexdigest()[:16]

            masked = [_hash_val(v.as_py()) for v in column]
            return pa.chunked_array([pa.array(masked, type=pa.string())])

        if function == "partial":
            # Keep first 2 and last 2 chars, mask the rest
            def _partial_mask(val: str | None) -> str | None:
                if val is None:
                    return None
                if len(val) <= 4:
                    return "****"
                return val[:2] + "*" * (len(val) - 4) + val[-2:]

            masked = [_partial_mask(v.as_py()) for v in column]
            return pa.chunked_array([pa.array(masked, type=pa.string())])

        # Unknown function — no-op
        return column
