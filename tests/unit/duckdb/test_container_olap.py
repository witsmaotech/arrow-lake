"""W3.2 OLAP two-part addressing — `FROM ds.table` + D6 bare-container 422.

Container tables register under a DuckDB schema named after the dataset
(probe-verified: schema + qualified view; dotted conn.register fails), so
`FROM gas_net.segments` resolves natively and plain single-table datasets
keep their main-schema view untouched. Scan-mode/breaker keys flow through
as the two-part name (per-table overrides work day one).
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend
from arrow_lake.exceptions import QueryError


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    config = ArrowLakeConfig()
    config.storage.backend = StorageBackend.LOCAL
    lake = Lake(base_uri=str(tmp_path / "lake"), config=config)
    lake.create_dataset(
        "gas_net", pa.table({"seg_id": ["G1", "G2"], "pressure": [100.0, 200.0]}),
        table="segments",
    )
    lake.create_dataset(
        "gas_net", pa.table({"sta_id": ["S1"], "name": ["east"]}),
        table="stations",
    )
    lake.create_dataset("plain_ds", pa.table({"a": [1, 2, 3]}))
    return lake


class TestTwoPartQuery:
    def test_from_ds_table(self, lake: Lake) -> None:
        result = lake.olap_query("gas_net.segments", "SELECT count(*) AS n FROM gas_net.segments")
        assert result.row_count == 1
        assert result.table.column("n").to_pylist() == [2]

    def test_two_part_with_filter(self, lake: Lake) -> None:
        result = lake.olap_query(
            "gas_net.segments",
            "SELECT seg_id FROM gas_net.segments WHERE pressure > 150",
        )
        assert result.table.column("seg_id").to_pylist() == ["G2"]

    def test_plain_dataset_bare_name_unchanged(self, lake: Lake) -> None:
        result = lake.olap_query("plain_ds", "SELECT count(*) AS n FROM plain_ds")
        assert result.table.column("n").to_pylist() == [3]

    def test_other_table_of_same_container(self, lake: Lake) -> None:
        result = lake.olap_query("gas_net.stations", "SELECT name FROM gas_net.stations")
        assert result.table.column("name").to_pylist() == ["east"]


class TestD6BareContainerName:
    def test_bare_container_name_rejected(self, lake: Lake) -> None:
        with pytest.raises(QueryError) as ei:
            lake.olap_query("gas_net", "SELECT * FROM gas_net")
        assert "segments" in str(ei.value) and "stations" in str(ei.value)

    def test_bare_plain_name_still_works(self, lake: Lake) -> None:
        result = lake.olap_query("plain_ds", "SELECT count(*) AS n FROM plain_ds")
        assert result.table.column("n").to_pylist() == [3]


class TestExplainTwoPart:
    def test_explain_two_part(self, lake: Lake) -> None:
        # explain() is the second _register_dataset consumer (metadata path);
        # a successful EXPLAIN proves the schema-qualified registration
        # resolved (the plan may reference the internal view name)
        from arrow_lake.query.olap import OlapSearchBridge

        bridge = OlapSearchBridge(lake._get_storage(), lake._config.olap)
        plan = bridge.explain("gas_net.segments", "SELECT count(*) FROM gas_net.segments")
        assert plan and plan.strip()
