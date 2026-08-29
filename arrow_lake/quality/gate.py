"""Ingestion quality gate — pre-write quality checks for incoming data.

v1.10.7 WP5 (review H9/H10): the gate is wired into every ingest path via
``build_quality_gate`` + ``Ingestor(quality_gate=...)``. ``mode`` selects the
policy: ``shadow`` (default — count/log only, rows unchanged) or ``enforce``
(drop/reject). The three historical bugs are fixed here:

1. partial schema rejection re-admitted the rejected rows;
2. the score threshold crashed on a report stub with no ``.total``;
3. datasets without an ``id`` column never dead-lettered rejections — every
   stage now contributes its exact rejected rows, no id-diffing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GateResult:
    """Result of an ingestion quality gate check."""

    total: int
    passed: int
    rejected: int
    schema_rejected: int
    filter_rejected: int
    score_rejected: int
    pass_rate: float
    rejection_reasons: tuple[str, ...]
    duration_seconds: float
    contract_rejected: int = 0


class IngestionQualityGate:
    """Quality gate that runs checks BEFORE data is written to Lance.

    Three-stage pipeline:
    1. Schema validation (reuses SchemaValidationGate)
    2. Content filtering (reuses QualityFilterRegistry)
    3. Quality scoring threshold (reuses compute_quality_scores)

    Rejected rows are routed to dead-letter storage with their exact row
    content (each stage knows precisely which rows it dropped).
    """

    def __init__(
        self,
        *,
        mode: str = "enforce",
        schema_mode: str = "lenient",
        active_filters: str = "",
        filter_mode: str = "all",
        min_quality_score: float = 0.0,
        none_score_policy: str = "pass",
        dead_letter_writer: Any | None = None,
        target_schema: pa.Schema | None = None,
        filter_registry: Any | None = None,
        contract_constraints: tuple = (),
        schema_max_rows: int = 100_000,
    ) -> None:
        self._mode = mode
        # M-16: schema 段 to_pydict 物化截断帽(超出部分跳过该段采样)
        self._schema_max_rows = max(1, int(schema_max_rows))
        self._schema_mode = schema_mode
        self._active_filters = active_filters
        self._filter_mode = filter_mode
        self._min_quality_score = min_quality_score
        self._none_score_policy = none_score_policy
        self._dead_letter_writer = dead_letter_writer
        self._target_schema = target_schema
        # A registry pre-loaded with the configured builtin filters (see
        # build_quality_gate). Bare QualityFilterRegistry() registers nothing,
        # so active_filters alone would be a silent no-op.
        self._filter_registry = filter_registry
        # v1.11.0.1 W3.1: compiled contract row-constraints (TRUE = violation),
        # already filtered per write target at check time via ``table_name``.
        self._contract_constraints = tuple(contract_constraints)

    @property
    def mode(self) -> str:
        """``shadow`` = count/log only; ``enforce`` = drop rejected rows."""
        return self._mode

    @property
    def contract_constraints(self) -> tuple:
        return self._contract_constraints

    def check(
        self, table: pa.Table, *, dataset_name: str = "", table_name: str | None = None,
    ) -> tuple[pa.Table, GateResult]:
        """Run all quality checks on table. Returns (passed_table, result).

        ``table_name`` scopes contract constraints to the write target
        (container table name, or the dataset-name section for legacy
        single-table contracts — the default when omitted).
        """
        from arrow_lake.core.metrics import (
            contract_check_total,
            quality_check_total,
            quality_reject_total,
        )

        start = time.monotonic()
        total_rows = table.num_rows
        schema_rejected = 0
        filter_rejected = 0
        score_rejected = 0
        contract_rejected = 0
        reasons: list[str] = []
        current = table
        dead_letter_batches: list[pa.Table] = []

        # ── Stage 1: Schema validation ──
        if self._target_schema is not None:
            current, n_schema_rej, rejected_rows = self._validate_schema(current)
            schema_rejected = n_schema_rej
            if schema_rejected > 0:
                reasons.append(f"schema:{schema_rejected}")
                if rejected_rows is not None and rejected_rows.num_rows > 0:
                    dead_letter_batches.append(rejected_rows)
        elif current.num_rows == 0 and total_rows == 0:
            reasons.append("schema:empty_table")

        # ── Stage 2: Content filtering ──
        if self._active_filters:
            current, n_filter_rej, filter_rejected_rows = self._apply_filters(current, dataset_name)
            filter_rejected = n_filter_rej
            if filter_rejected > 0:
                reasons.append(f"filter:{filter_rejected}")
                if filter_rejected_rows is not None and filter_rejected_rows.num_rows > 0:
                    dead_letter_batches.append(filter_rejected_rows)

        # ── Stage 3: Quality scoring ──
        if self._min_quality_score > 0.0 and current.num_rows > 0:
            current, n_score_rej, score_rejected_rows = self._apply_score_threshold(current)
            score_rejected = n_score_rej
            if score_rejected > 0:
                reasons.append(f"score:{score_rejected}")
                if score_rejected_rows is not None and score_rejected_rows.num_rows > 0:
                    dead_letter_batches.append(score_rejected_rows)

        # ── Stage 4: Contract constraints (v1.11.0.1 W3.1) ──
        if self._contract_constraints:
            current, n_contract_rej, contract_rows = self._apply_contract(
                current, dataset_name, table_name,
            )
            contract_rejected = n_contract_rej
            if contract_rejected > 0:
                reasons.append(f"contract:{contract_rejected}")
                contract_check_total.labels(
                    dataset=dataset_name or "_unknown", result="reject",
                ).inc()
                if contract_rows is not None and contract_rows.num_rows > 0:
                    dead_letter_batches.append(contract_rows)
            else:
                contract_check_total.labels(
                    dataset=dataset_name or "_unknown", result="pass",
                ).inc()

        # ── Metrics ──
        passed = current.num_rows
        # Stage-counter sum, not row arithmetic: in enforce they are
        # identical (each stage drops its rows from `current`), but in
        # shadow stage 4 keeps `current` intact (P1-7 — the passed view is
        # the caller's enforce artifact) while its rejections must still
        # count for reporting, metrics, and dead-lettering.
        rejected = (
            schema_rejected + filter_rejected + score_rejected + contract_rejected
        )
        elapsed = time.monotonic() - start

        if dataset_name:
            quality_check_total.labels(dataset=dataset_name).inc()
        for reason in reasons:
            quality_reject_total.labels(
                dataset=dataset_name or "_unknown", reason=reason
            ).inc(rejected)

        if self._mode == "shadow" and rejected > 0:
            logger.info(
                "quality_gate.shadow_rejections",
                dataset=dataset_name,
                rejected=rejected,
                reasons=list(reasons),
                note="shadow mode: rows pass through unchanged",
            )

        # ── Dead letter: exact rejected rows from every stage ──
        if rejected > 0 and self._dead_letter_writer is not None:
            self._route_to_dead_letter(dead_letter_batches, dataset_name)

        result = GateResult(
            total=total_rows,
            passed=passed,
            rejected=rejected,
            schema_rejected=schema_rejected,
            filter_rejected=filter_rejected,
            score_rejected=score_rejected,
            pass_rate=round(passed / max(total_rows, 1), 4),
            rejection_reasons=tuple(reasons),
            duration_seconds=round(elapsed, 4),
            contract_rejected=contract_rejected,
        )
        return current, result

    # ── Stage implementations ──

    def _apply_contract(
        self, table: pa.Table, dataset_name: str, table_name: str | None,
    ) -> tuple[pa.Table, int, pa.Table | None]:
        """Stage 4: evaluate compiled contract constraints via in-memory DuckDB.

        Constraints are scoped to the write target (``table_name`` or the
        dataset-name section for legacy single-table contracts). Returns
        (passed_table, rejected_count, rejected_rows) where rejected rows
        carry a ``_contract_violation`` marker column (kind list per row).

        P0-1 (review 2026-08-26): constraints referencing a column absent
        from the batch schema are skipped INDIVIDUALLY (baseline-script
        semantics) — one typo'd column must not silently disable the other
        constraints. Evaluation failure: shadow counts and passes through
        (observational mode), enforce is fail-closed (rejects the batch
        with an ``eval_error`` marker) per project discipline B-2.

        P1-7 (review 2026-08-26): shadow mode materializes ONLY the rejected
        rows (the passed table is the caller's enforce artifact — the
        Ingestor ignores ``gated`` unless mode == enforce, so the second
        full-batch fetch was pure 2× memory waste); enforce does ONE full
        scan with a nullable marker column and splits via Arrow filters.
        """
        from arrow_lake.core.metrics import contract_check_total

        section = table_name or dataset_name
        applicable = []
        skipped = []
        for c in self._contract_constraints:
            if c.table != section:
                continue
            if c.column not in table.column_names:
                skipped.append(c)
            else:
                applicable.append(c)
        for c in skipped:
            logger.warning(
                "contract_gate_column_missing",
                dataset=dataset_name, table=table_name,
                column=c.column, kind=c.kind,
                note="constraint skipped: column absent from batch",
            )
            contract_check_total.labels(
                dataset=dataset_name or "_unknown", result="skip",
            ).inc()
        if not applicable or table.num_rows == 0:
            return table, 0, None
        # P1-9 (review 2026-08-26): the _source_table literal is escaped like
        # a SQL string (defense in depth — names are pattern-validated
        # upstream, but this stage must stay injectable-proof on its own).
        source_table_lit = str(table_name or dataset_name).replace("'", "''")
        try:
            import duckdb

            con = duckdb.connect(":memory:")
            try:
                con.register("_batch", table)
                violated_expr = " OR ".join(
                    f"COALESCE(({c.sql}), FALSE)" for c in applicable
                )
                markers = ", ".join(
                    f"CASE WHEN COALESCE(({c.sql}), FALSE) THEN '{c.kind}' END"
                    for c in applicable
                )
                marker_sel = (
                    f"SELECT *, NULLIF(concat_ws(';', {markers}), '') "
                    f"AS _contract_violation, '{source_table_lit}' "
                    f"AS _source_table FROM _batch"
                )
                if self._mode != "enforce":
                    # Shadow: only the rejected rows are needed (count +
                    # dead letter); the passed table is the original batch.
                    rejected = con.execute(
                        f"{marker_sel} WHERE {violated_expr}"
                    ).fetch_arrow_table()
                    return table, rejected.num_rows, self._with_contract_reason(
                        rejected if rejected.num_rows else None
                    )
                # Enforce: one full scan with the marker, split in Arrow.
                marked = con.execute(marker_sel).fetch_arrow_table()
            finally:
                con.close()
        except Exception:
            logger.error(
                "contract_gate_eval_failed", dataset=dataset_name,
                table=table_name, mode=self._mode, exc_info=True,
            )
            contract_check_total.labels(
                dataset=dataset_name or "_unknown", result="eval_error",
            ).inc()
            if self._mode != "enforce":
                return table, 0, None
            # Fail-closed (enforce): reject the whole batch with an explicit
            # marker so it dead-letters with a reason instead of passing.
            marked = table.append_column(
                "_contract_violation",
                pa.array(["eval_error"] * table.num_rows),
            ).append_column(
                "_source_table", pa.array([str(table_name or dataset_name)] * table.num_rows),
            )
            return table.slice(0, 0), table.num_rows, self._with_contract_reason(marked)
        mask = [
            bool(v) for v in marked.column("_contract_violation").to_pylist()
        ]
        rejected = marked.filter(mask)
        passed = marked.filter([not m for m in mask]).drop_columns(
            ["_contract_violation", "_source_table"],
        )
        return passed, rejected.num_rows, self._with_contract_reason(
            rejected if rejected.num_rows else None
        )

    @staticmethod
    def _with_contract_reason(t: pa.Table | None) -> pa.Table | None:
        """P2-6 (review 2026-08-26 §三): derive the dead-letter
        ``_rejection_reason`` from the per-row violation kinds — contract
        rows used to hit the generic "Rejected by quality_gate" fallback,
        hiding WHY a row was rejected (the kinds lived only in the
        ``_contract_violation`` marker column)."""
        if t is None or t.num_rows == 0 or "_rejection_reason" in t.column_names:
            return t
        kinds = t.column("_contract_violation").to_pylist()
        return t.append_column(
            "_rejection_reason",
            pa.array(
                [f"contract:{k or 'unknown'}" for k in kinds], type=pa.string(),
            ),
        )

    def _validate_schema(
        self, table: pa.Table
    ) -> tuple[pa.Table, int, pa.Table | None]:
        """Run schema validation.

        Returns (valid_table, rejected_count, rejected_rows_table). Bug ① fix:
        a PARTIAL rejection returns only the valid rows — the old code handed
        back the full table whenever at least one row survived.
        """
        from arrow_lake.quality.schema_validation import SchemaValidationGate

        gate = SchemaValidationGate(mode=self._schema_mode)
        # M-16: to_pydict 全量物化线性成本——截断帽保护(超出部分跳过,
        # 采样代表性;enforce 大批须向量化,见 config 注释)
        max_rows = self._schema_max_rows
        if table.num_rows > max_rows:
            from arrow_lake.metrics import quality_gate_truncated_total

            logger.warning(
                "quality_gate_schema_truncated",
                total=table.num_rows, sampled=max_rows,
            )
            try:
                quality_gate_truncated_total.labels(stage="schema").inc()
            except Exception:
                pass
            table = table.slice(0, max_rows)
        rows = table.to_pydict()
        row_list = [
            {col: rows[col][i] for col in rows}
            for i in range(table.num_rows)
        ]
        valid, rejected_rows = gate.validate(row_list, self._target_schema)
        n_rejected = table.num_rows - len(valid)
        if n_rejected == 0:
            return table, 0, None
        rejected_table = _dicts_to_table(rejected_rows, self._target_schema)
        return _dicts_to_table(valid, self._target_schema), n_rejected, rejected_table

    def _apply_filters(
        self, table: pa.Table, dataset_name: str
    ) -> tuple[pa.Table, int, pa.Table | None]:
        """Run quality filters; the report carries the exact rejected rows."""
        from arrow_lake.quality.base import QualityFilterRegistry

        registry = self._filter_registry or QualityFilterRegistry()
        report = registry.apply_all(table, self._active_filters, mode=self._filter_mode)
        if report.rejected == 0:
            return table, 0, None
        # NOT `report.passed_table or table` — an empty pa.Table is FALSY, so
        # a filter that rejects EVERY row would fall back to the original
        # full table and silently re-admit the whole batch (verified in
        # review: enforce + all-rejected wrote all rows with rejected=0).
        passed_table = report.passed_table if report.passed_table is not None else table
        return passed_table, report.rejected, report.rejected_table

    def _apply_score_threshold(
        self, table: pa.Table
    ) -> tuple[pa.Table, int, pa.Table | None]:
        """Filter rows below the quality-score threshold.

        Bug ② fix: a real ``QualityReport`` (the stub had no ``.total`` and
        crashed compute_quality_scores). Rows whose score is None follow
        ``none_score_policy``: ``pass`` (default, counted) or ``reject``.
        """
        from arrow_lake.quality.models import QualityReport
        from arrow_lake.quality.scoring import compute_quality_scores

        report = QualityReport(total=table.num_rows, rejected=0)
        scored = compute_quality_scores(table, report)
        scores = scored.column("quality_score")
        none_passed = 0
        mask: list[bool] = []
        for v in scores:
            val = v.as_py()
            if val is None:
                keep = self._none_score_policy == "pass"
                none_passed += 1 if keep else 0
                mask.append(keep)
            else:
                mask.append(val >= self._min_quality_score)
        filtered = scored.filter(mask)
        if "quality_score" not in table.column_names:
            filtered = filtered.drop_columns(["quality_score"])
        n_rejected = table.num_rows - filtered.num_rows
        if n_rejected == 0:
            if none_passed:
                logger.info(
                    "quality_gate.none_score_passed",
                    dataset_hint=none_passed,
                    policy=self._none_score_policy,
                )
            return table, 0, None
        rejected_rows = scored.filter([not m for m in mask])
        if "quality_score" not in table.column_names:
            rejected_rows = rejected_rows.drop_columns(["quality_score"])
        return filtered, n_rejected, rejected_rows

    def _route_to_dead_letter(
        self, rejected_batches: list[pa.Table], dataset_name: str
    ) -> None:
        """Write each stage's rejected rows to dead letter storage.

        Bug ③ fix: the old implementation diffed by an ``id`` column and
        silently dropped rejections for id-less datasets. Stages now hand
        over their exact rejected rows — no id required.
        """
        for batch in rejected_batches:
            if batch.num_rows == 0:
                continue
            try:
                self._dead_letter_writer.write(dataset_name, batch, "quality_gate")
            except Exception:
                logger.warning(
                    "quality_gate.dead_letter_failed", dataset=dataset_name, exc_info=True
                )


def build_quality_gate(
    config: Any,
    *,
    target_schema: pa.Schema | None = None,
    dead_letter_writer: Any | None = None,
    contract_constraints: tuple = (),
    dataset_name: str | None = None,
) -> IngestionQualityGate | None:
    """Construct the gate from a QualityConfig (v1.10.7 WP5 wiring).

    Returns None when the gate is off (``gate_mode="off"`` or quality
    disabled) so callers keep their no-gate fast path. Mirrors
    ``Lake.quality_filter``'s builtin registration so ``active_filters``
    actually resolves (a bare registry knows no filter names).

    v1.11.0.3 W3: ``dataset_name`` resolves ``gate_mode_overrides`` — a
    listed dataset's mode replaces the global one (pilot semantics: one
    dataset can enforce while the global mode stays shadow). Invalid
    override values are ignored with a warning; ``quality.enabled=False``
    stays the master switch.
    """
    mode = str(getattr(config, "gate_mode", "shadow"))
    overrides = getattr(config, "gate_mode_overrides", None) or {}
    if dataset_name and dataset_name in overrides:
        resolved = str(overrides[dataset_name])
        if resolved in ("off", "shadow", "enforce"):
            if resolved != mode:
                logger.info(
                    "quality_gate_mode_resolved", dataset=dataset_name,
                    mode=resolved, source="override", global_mode=mode,
                )
            mode = resolved
        else:
            logger.warning(
                "quality_gate_mode_override_invalid", dataset=dataset_name,
                value=resolved, note="expected off|shadow|enforce; keeping global",
            )
    if mode == "off" or not getattr(config, "enabled", True):
        return None

    from arrow_lake.quality.base import QualityFilterRegistry
    from arrow_lake.quality.builtin import ImageResolutionFilter, TextLengthFilter

    registry = QualityFilterRegistry()
    active = str(getattr(config, "active_filters", "") or "")
    if "text_length" in active:
        registry.register(TextLengthFilter(
            min_chars=int(getattr(config, "text_min_chars", 1)),
            max_chars=getattr(config, "text_max_chars", None),
        ))
    if "image_resolution" in active:
        registry.register(ImageResolutionFilter(
            min_width=int(getattr(config, "image_min_width", 64)),
            min_height=int(getattr(config, "image_min_height", 64)),
        ))

    return IngestionQualityGate(
        schema_max_rows=getattr(config, 'schema_validation_max_rows', 100_000),
        mode=mode,
        schema_mode=str(getattr(config, "schema_validation", "lenient")),
        active_filters=active,
        filter_mode=str(getattr(config, "filter_mode", "all")),
        min_quality_score=float(getattr(config, "min_quality_score", 0.0) or 0.0),
        dead_letter_writer=dead_letter_writer,
        target_schema=target_schema,
        filter_registry=registry,
        contract_constraints=contract_constraints,
    )


def _dicts_to_table(rows: list[dict], schema: pa.Schema) -> pa.Table:
    """Convert a list of dicts to a PyArrow Table matching the given schema.

    Rejected rows may hold values that don't fit the target type (that's why
    they were rejected) — per-column typed conversion falls back to inferred
    types so the dead-letter batch keeps the original value instead of
    crashing the gate.
    """
    if not rows:
        # Explicit types so an all-rejected batch still yields a typed empty
        # table (null-typed columns break downstream Lance writes).
        return pa.table(
            {f.name: pa.array([], type=f.type) for f in schema}, schema=schema
        )
    cols: dict[str, list] = {f.name: [] for f in schema}
    extra: dict[str, list] = {}
    schema_names = set(schema.names)
    for row in rows:
        for field in schema:
            cols[field.name].append(row.get(field.name))
        for key in row:
            if key not in schema_names:
                extra.setdefault(key, []).append(row[key])
    arrays: dict[str, pa.Array] = {}
    for field in schema:
        try:
            arrays[field.name] = pa.array(cols[field.name], type=field.type)
        except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
            arrays[field.name] = pa.array(cols[field.name])
    try:
        table = pa.table(arrays, schema=pa.schema(
            [pa.field(n, arrays[n].type) for n in schema.names]
        ))
    except (pa.ArrowInvalid, pa.ArrowTypeError):
        table = pa.table(arrays)
    # Preserve metadata columns like _rejection_reason for dead-lettering.
    for key, values in extra.items():
        table = table.append_column(key, pa.array(values))
    return table
