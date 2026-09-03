"""Tests for quality/gate.py — IngestionQualityGate check pipeline.

v1.10.7 WP5 (review H9/H10): the gate is now WIRED into ingest, so these
tests run the REAL pipeline (schema validation / content filters / scoring)
instead of patching stage methods. Each historical bug is pinned:

- bug ① partial schema rejection silently re-admitted the rejected rows;
- bug ② score threshold crashed on _DummyReport (no .total) — dead code
  until wiring, so nobody noticed;
- bug ③ datasets without an ``id`` column never dead-lettered rejections
  (rows silently dropped in enforce mode).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

if TYPE_CHECKING:
    from arrow_lake.quality.base import QualityFilterRegistry

from arrow_lake.quality.gate import GateResult, IngestionQualityGate, _dicts_to_table


def _table() -> pa.Table:
    return pa.table({"id": [1, 2, 3], "text": ["a", "b", "c"]})


class TestGateResult:
    def test_creation(self) -> None:
        r = GateResult(
            total=10, passed=8, rejected=2,
            schema_rejected=1, filter_rejected=1, score_rejected=0,
            pass_rate=0.8, rejection_reasons=("schema:1",), duration_seconds=0.01,
        )
        assert r.passed == 8
        assert r.pass_rate == 0.8

    def test_frozen(self) -> None:
        r = GateResult(total=10, passed=10, rejected=0,
                       schema_rejected=0, filter_rejected=0, score_rejected=0,
                       pass_rate=1.0, rejection_reasons=(), duration_seconds=0.0)
        with pytest.raises(AttributeError):
            r.total = 99  # type: ignore[misc]


def _registry() -> "QualityFilterRegistry":
    from arrow_lake.quality.base import QualityFilterRegistry
    from arrow_lake.quality.builtin import TextLengthFilter

    reg = QualityFilterRegistry()
    reg.register(TextLengthFilter(min_chars=1))
    return reg


class TestRealPipeline:
    def test_no_checks_passes_all(self) -> None:
        gate = IngestionQualityGate()
        passed, result = gate.check(_table(), dataset_name="ds")
        assert result.total == 3
        assert result.passed == 3
        assert result.rejected == 0
        assert passed.num_rows == 3

    def test_partial_rejection_drops_bad_rows(self) -> None:
        """bug ①: 1 of 3 rows fails a stage → exactly the 2 good rows pass.

        (Pinned via the real text_length filter — per-row type variance
        can't exist inside one arrow column, so the filter stage is the
        faithful partial-rejection path.) The old gate handed back the
        FULL table whenever any row survived a stage.
        """
        dirty = pa.table({"id": [1, 2, 3], "text_content": ["ok", "", "also ok"]})
        gate = IngestionQualityGate(
            active_filters="text_length", filter_registry=_registry(),
        )
        passed, result = gate.check(dirty, dataset_name="ds")

        assert result.filter_rejected == 1
        assert "filter:1" in result.rejection_reasons
        assert passed.num_rows == 2
        assert passed.column("id").to_pylist() == [1, 3]

    def test_real_filter_rejects_short_text(self) -> None:
        """Real registry path: text_length filter drops empty-text rows."""
        dirty = pa.table({"id": [1, 2, 3], "text_content": ["ok", "", "also ok"]})
        gate = IngestionQualityGate(
            active_filters="text_length", filter_registry=_registry(),
        )
        passed, result = gate.check(dirty, dataset_name="ds")

        assert result.filter_rejected == 1
        assert passed.column("id").to_pylist() == [1, 3]

    def test_schema_rejection_all_rows(self) -> None:
        """Schema stage: string ids vs int64 target reject every row."""
        schema = pa.schema([("id", pa.int64()), ("text", pa.string())])
        dirty = pa.table({"id": ["1", "2"], "text": ["a", "b"]})
        gate = IngestionQualityGate(schema_mode="strict", target_schema=schema)
        passed, result = gate.check(dirty, dataset_name="ds")

        assert result.schema_rejected == 2
        assert passed.num_rows == 0

    def test_score_threshold_runs_without_crash(self) -> None:
        """bug ②: _DummyReport had no .total → AttributeError. Real path must
        complete and (survivors all score 1.0) reject nothing at 0.5."""
        gate = IngestionQualityGate(min_quality_score=0.5)
        passed, result = gate.check(_table(), dataset_name="ds")
        assert result.score_rejected == 0
        assert passed.num_rows == 3

    def test_all_rows_rejected_returns_empty_table(self) -> None:
        """Review finding (2026-08-24): an empty pa.Table is FALSY — the old
        ``report.passed_table or table`` fallback re-admitted the ENTIRE batch
        when a filter rejected every row (silently: rejected also read 0)."""
        from arrow_lake.quality.base import QualityFilterRegistry
        from arrow_lake.quality.builtin import TextLengthFilter

        reg = QualityFilterRegistry()
        reg.register(TextLengthFilter(min_chars=100))
        dirty = pa.table({"id": [1, 2, 3], "text_content": ["a", "b", "c"]})
        gate = IngestionQualityGate(active_filters="text_length", filter_registry=reg)

        passed, result = gate.check(dirty, dataset_name="ds")

        assert result.filter_rejected == 3
        assert result.passed == 0
        assert passed.num_rows == 0

    def test_empty_table(self) -> None:
        gate = IngestionQualityGate()
        empty = pa.table({"id": pa.array([], type=pa.int64())})
        _, result = gate.check(empty, dataset_name="")
        assert result.total == 0

    def test_empty_schema_rejection_builds_typed_empty_table(self) -> None:
        """All rows rejected → empty result table must carry the target schema
        (typed columns, not null-typed) so downstream Lance writes don't choke."""
        schema = pa.schema([("id", pa.int64()), ("text", pa.string())])
        dirty = pa.table({"id": ["x", "y"], "text": ["a", "b"]})
        gate = IngestionQualityGate(schema_mode="strict", target_schema=schema)
        passed, result = gate.check(dirty, dataset_name="ds")
        assert result.schema_rejected == 2
        assert passed.num_rows == 0
        assert passed.schema.field("id").type == pa.int64()


class TestDeadLetter:
    def test_dead_letter_without_id_column(self) -> None:
        """bug ③: no ``id`` column → old gate returned early and the rejected
        rows vanished. Now every stage contributes its exact rejected rows."""
        writer = MagicMock()
        dirty = pa.table({"text_content": ["ok", "", "also ok"], "kind": ["a", "b", "c"]})
        gate = IngestionQualityGate(
            active_filters="text_length", filter_registry=_registry(),
            dead_letter_writer=writer,
        )
        passed, result = gate.check(dirty, dataset_name="ds")

        assert result.filter_rejected == 1
        writer.write.assert_called_once()
        args = writer.write.call_args[0]
        rejected_table = args[1]
        assert rejected_table.num_rows == 1
        assert rejected_table.column("text_content").to_pylist() == [""]

    def test_dead_letter_schema_rejected_rows_exact(self) -> None:
        writer = MagicMock()
        schema = pa.schema([("id", pa.int64()), ("text", pa.string())])
        dirty = pa.table({"id": ["1", "bad", "3"], "text": ["a", "b", "c"]})
        gate = IngestionQualityGate(
            schema_mode="strict", target_schema=schema, dead_letter_writer=writer,
        )
        passed, result = gate.check(dirty, dataset_name="ds")

        assert result.schema_rejected == 3
        writer.write.assert_called_once()
        rejected_table = writer.write.call_args[0][1]
        assert rejected_table.num_rows == 3
        # original values survive (tolerant conversion keeps "bad" as-is)
        assert "bad" in rejected_table.column("id").to_pylist()

    def test_no_dead_letter_when_all_pass(self) -> None:
        writer = MagicMock()
        gate = IngestionQualityGate(dead_letter_writer=writer)
        gate.check(_table(), dataset_name="ds")
        writer.write.assert_not_called()

    def test_real_dead_letter_writer_end_to_end(self) -> None:
        """Regression (found in container verification): the wiring must
        pass a REAL DeadLetterWriter — the gate calls write(ds, table,
        filter_name) with 3 args, so a bare StorageWriter-protocol adapter
        (2-arg write) fails silently under the except-and-warn. This pins
        the actual production stack: gate → DeadLetterWriter → storage."""
        from arrow_lake.quality.dead_letter import DeadLetterWriter

        class _FakeStorage:
            """Stands in for _DeadLetterStorageAdapter: StorageWriter protocol."""

            def __init__(self) -> None:
                self.written: dict[str, pa.Table] = {}

            def write(self, table_name: str, table: pa.Table) -> int:
                self.written[table_name] = table
                return table.num_rows

        storage = _FakeStorage()
        dirty = pa.table({"id": [1, 2, 3], "text_content": ["ok", "", "also ok"]})
        gate = IngestionQualityGate(
            active_filters="text_length", filter_registry=_registry(),
            dead_letter_writer=DeadLetterWriter(storage=storage),
        )
        passed, result = gate.check(dirty, dataset_name="ds")

        assert result.filter_rejected == 1
        # B-3: 死信表落 internal 命名空间(_ 前缀,非 admin 隐藏 + ADMIN-only 守卫)
        assert "_ds_dead_letter" in storage.written
        dl = storage.written["_ds_dead_letter"]
        assert dl.num_rows == 1
        assert "_rejection_reason" in dl.column_names
        assert "_filter_name" in dl.column_names
        assert dl.column("text_content").to_pylist() == [""]


class TestShadowVsEnforce:
    def _ingestor_with(self, gate) -> tuple:
        from arrow_lake.ingest.ingestor import Ingestor

        manager = MagicMock()
        manager.dataset_exists.return_value = False
        ing = Ingestor(manager, quality_gate=gate)
        return ing, manager

    def _filter_gate(self, mode: str) -> IngestionQualityGate:
        return IngestionQualityGate(
            mode=mode, active_filters="text_length", filter_registry=_registry(),
        )

    def test_shadow_keeps_all_rows(self) -> None:
        """Shadow mode: rows still written, rejections only counted/logged."""
        dirty = pa.table({"id": [1, 2, 3], "text_content": ["ok", "", "also ok"]})
        ing, manager = self._ingestor_with(self._filter_gate("shadow"))

        ing._write_table("ds", dirty, [], "test.csv")
        written = manager.create_dataset.call_args[0][1]
        assert written.num_rows == 3  # shadow: nothing dropped

    def test_enforce_drops_bad_rows(self) -> None:
        dirty = pa.table({"id": [1, 2, 3], "text_content": ["ok", "", "also ok"]})
        ing, manager = self._ingestor_with(self._filter_gate("enforce"))

        ing._write_table("ds", dirty, [], "test.csv")
        written = manager.create_dataset.call_args[0][1]
        assert written.num_rows == 2  # enforce: rejected rows never written


class TestGateFactory:
    def test_build_from_config_shadow_default(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        cfg = SimpleNamespace(gate_mode="shadow", schema_validation="strict",
                              active_filters="", filter_mode="all",
                              min_quality_score=0.0, enabled=True)
        gate = build_quality_gate(cfg)
        assert gate is not None
        assert gate.mode == "shadow"

    # ── v1.11.0.3 W3: per-dataset gate-mode override (contract enforce pilot) ──

    def _cfg(self, **overrides) -> SimpleNamespace:
        base = dict(gate_mode="shadow", schema_validation="lenient",
                    active_filters="", filter_mode="all",
                    min_quality_score=0.0, enabled=True,
                    gate_mode_overrides={})
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_override_enforce_for_named_dataset(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        cfg = self._cfg(gate_mode_overrides={"demo_gas": "enforce"})
        assert build_quality_gate(cfg, dataset_name="demo_gas").mode == "enforce"

    def test_other_datasets_keep_global_mode(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        cfg = self._cfg(gate_mode_overrides={"demo_gas": "enforce"})
        assert build_quality_gate(cfg, dataset_name="other_ds").mode == "shadow"

    def test_override_off_disables_gate_for_dataset(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        cfg = self._cfg(gate_mode_overrides={"noisy_ds": "off"})
        assert build_quality_gate(cfg, dataset_name="noisy_ds") is None
        assert build_quality_gate(cfg, dataset_name="kept_ds") is not None

    def test_override_enforce_when_global_off(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        # pilot semantics: global stays off, the listed dataset still enforces
        cfg = self._cfg(gate_mode="off", gate_mode_overrides={"demo_gas": "enforce"})
        assert build_quality_gate(cfg, dataset_name="demo_gas").mode == "enforce"

    def test_invalid_override_value_ignored(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        cfg = self._cfg(gate_mode_overrides={"demo_gas": "bogus"})
        assert build_quality_gate(cfg, dataset_name="demo_gas").mode == "shadow"

    def test_config_without_overrides_field_unchanged(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        # pre-1.11.0.3 configs (SimpleNamespace mocks in older tests) lack the
        # field entirely — getattr default must keep them working
        cfg = SimpleNamespace(gate_mode="shadow", schema_validation="lenient",
                              active_filters="", filter_mode="all",
                              min_quality_score=0.0, enabled=True)
        assert build_quality_gate(cfg, dataset_name="any").mode == "shadow"

    def test_build_returns_none_when_off(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        cfg = SimpleNamespace(gate_mode="off", schema_validation="lenient",
                              active_filters="", filter_mode="all",
                              min_quality_score=0.0, enabled=True)
        assert build_quality_gate(cfg) is None

    def test_build_returns_none_when_quality_disabled(self) -> None:
        from arrow_lake.quality.gate import build_quality_gate

        cfg = SimpleNamespace(gate_mode="shadow", schema_validation="lenient",
                              active_filters="", filter_mode="all",
                              min_quality_score=0.0, enabled=False)
        assert build_quality_gate(cfg) is None


class TestDictsToTable:
    def test_empty_rows(self) -> None:
        schema = pa.schema([("id", pa.int64())])
        result = _dicts_to_table([], schema)
        assert result.num_rows == 0
        assert "id" in result.column_names
        assert result.schema.field("id").type == pa.int64()

    def test_with_data(self) -> None:
        schema = pa.schema([("id", pa.int64()), ("name", pa.string())])
        result = _dicts_to_table([{"id": 1, "name": "a"}], schema)
        assert result.num_rows == 1
