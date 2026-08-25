"""W3.1 contract_check gate stage — shadow/enforce/off + wiring audit.

The stage runs AFTER the three quality stages, evaluating compiled contract
row-constraints (TRUE = violation) against the batch via in-memory DuckDB.
shadow counts/logs/dead-letters without dropping; enforce drops. No
constraints configured → stage skipped entirely (zero overhead).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from arrow_lake.contract.compiler import compile_contract
from arrow_lake.contract.schema import parse_contract
from arrow_lake.quality.gate import IngestionQualityGate

CONTRACT_YAML = """
dataset: gas_net
tables:
  segments:
    columns:
      - name: material
        enum: [PE, steel]
      - name: note
        required: true
"""

BATCH = pa.table({
    "material": ["PE", "PVC", None, "steel"],
    "note": ["a", "b", "c", None],
})


def _constraints() -> tuple:
    bundle = compile_contract(parse_contract(CONTRACT_YAML))
    return bundle.rows


class TestContractStageShadow:
    def test_shadow_counts_and_reports(self) -> None:
        gate = IngestionQualityGate(
            mode="shadow", contract_constraints=_constraints(),
        )
        gated, result = gate.check(BATCH, dataset_name="gas_net", table_name="segments")
        # gate-level: violations counted with reasons; the GATED view is the
        # enforce artifact — shadow semantics live at the caller (Ingestor
        # ignores `gated` unless mode == enforce), covered e2e below.
        assert result.contract_rejected == 2
        assert any(r.startswith("contract:") for r in result.rejection_reasons)
        assert gated.num_rows == 2

    def test_shadow_dead_letter_carries_marker(self) -> None:
        writer = MagicMock()
        gate = IngestionQualityGate(
            mode="shadow", contract_constraints=_constraints(),
            dead_letter_writer=writer,
        )
        gate.check(BATCH, dataset_name="gas_net", table_name="segments")
        assert writer.write.called
        dataset_arg, batch, source = writer.write.call_args[0]
        assert dataset_arg == "gas_net"
        assert source == "quality_gate"
        assert "_contract_violation" in batch.column_names
        assert batch.num_rows == 2

    def test_no_constraints_zero_overhead(self) -> None:
        gate = IngestionQualityGate(mode="enforce")
        passed, result = gate.check(BATCH, dataset_name="ds")
        assert passed.num_rows == 4
        assert result.contract_rejected == 0

    def test_other_table_constraints_not_applied(self) -> None:
        # constraints target table 'segments'; batch claims table 'stations'
        gate = IngestionQualityGate(
            mode="enforce", contract_constraints=_constraints(),
        )
        passed, result = gate.check(BATCH, dataset_name="gas_net", table_name="stations")
        assert passed.num_rows == 4
        assert result.contract_rejected == 0

    def test_legacy_dataset_section_applies_without_table_name(self) -> None:
        yml = """
dataset: plain_ds
ontology:
  columns:
    - name: material
      enum: [PE, steel]
"""
        bundle = compile_contract(parse_contract(yml))
        gate = IngestionQualityGate(mode="enforce", contract_constraints=bundle.rows)
        passed, result = gate.check(BATCH, dataset_name="plain_ds")
        assert passed.num_rows == 3  # PVC dropped; NULL passes domain check
        assert result.contract_rejected == 1


class TestContractStageEnforce:
    def test_enforce_drops_violating_rows(self) -> None:
        gate = IngestionQualityGate(
            mode="enforce", contract_constraints=_constraints(),
        )
        passed, result = gate.check(BATCH, dataset_name="gas_net", table_name="segments")
        # PVC (enum) and missing note (required) dropped; NULL material passes
        assert passed.num_rows == 2
        assert passed.column("material").to_pylist() == ["PE", None]
        assert result.contract_rejected == 2

    def test_metric_counter_incremented(self) -> None:
        from arrow_lake.core.metrics import contract_check_total

        before = contract_check_total.labels(dataset="m_ds", result="reject")._value.get()
        gate = IngestionQualityGate(
            mode="enforce", contract_constraints=_constraints(),
        )
        gate.check(BATCH, dataset_name="m_ds", table_name="segments")
        after = contract_check_total.labels(dataset="m_ds", result="reject")._value.get()
        assert after == before + 1


class TestMakeIngestorWiring:
    def _lake(self, tmp_path):
        from arrow_lake.config import ArrowLakeConfig, StorageBackend
        from arrow_lake import Lake

        cfg = ArrowLakeConfig()
        cfg.storage.backend = StorageBackend.LOCAL
        cfg.quality.gate_mode = "enforce"  # default is shadow; enforce to observe drops
        return Lake(base_uri=str(tmp_path / "lake"), config=cfg)

    def test_contract_store_injects_constraints(self, tmp_path) -> None:
        from arrow_lake.system_db import Migrator, SystemDB
        from arrow_lake.system_db.stores import ContractStore

        lake = self._lake(tmp_path)
        db = SystemDB(":memory:")
        Migrator(db).run()
        lake._contract_store = ContractStore(db)
        lake._contract_store.save_contract("gas_net", CONTRACT_YAML)
        try:
            ingestor = lake._make_ingestor("gas_net")
            assert ingestor._quality_gate is not None
            assert len(ingestor._quality_gate.contract_constraints) == 2
            lake.ingest("gas_net", [self._csv(tmp_path)], table="segments")
            t = lake._get_storage().read_dataset("gas_net", table="segments")
            assert t.num_rows == 2  # enforce path: PVC + missing note dropped
        finally:
            db.close()

    def test_no_store_no_constraints(self, tmp_path) -> None:
        lake = self._lake(tmp_path)
        ingestor = lake._make_ingestor("gas_net")
        assert ingestor._quality_gate is None or \
            not ingestor._quality_gate.contract_constraints
        lake.ingest("gas_net", [self._csv(tmp_path)], table="segments")
        t = lake._get_storage().read_dataset("gas_net", table="segments")
        assert t.num_rows == 4  # no contract → nothing dropped

    def test_shadow_e2e_rows_pass_through(self, tmp_path) -> None:
        from arrow_lake.config import ArrowLakeConfig, StorageBackend
        from arrow_lake import Lake
        from arrow_lake.system_db import Migrator, SystemDB
        from arrow_lake.system_db.stores import ContractStore

        cfg = ArrowLakeConfig()
        cfg.storage.backend = StorageBackend.LOCAL
        cfg.quality.gate_mode = "shadow"  # the shipped default
        lake = Lake(base_uri=str(tmp_path / "lake"), config=cfg)
        db = SystemDB(":memory:")
        Migrator(db).run()
        lake._contract_store = ContractStore(db)
        lake._contract_store.save_contract("gas_net", CONTRACT_YAML)
        try:
            lake.ingest("gas_net", [self._csv(tmp_path)], table="segments")
            t = lake._get_storage().read_dataset("gas_net", table="segments")
            assert t.num_rows == 4  # shadow: violations counted, rows land
        finally:
            db.close()

    @staticmethod
    def _csv(tmp_path) -> str:
        p = tmp_path / "b.csv"
        p.write_text("material,note\nPE,a\nPVC,b\n,c\nsteel,\n")
        return str(p)
