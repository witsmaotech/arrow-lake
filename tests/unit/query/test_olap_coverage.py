"""Coverage for OLAP internals — _apply_limit, _validate_sql, _get_profiling_info, explain."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config import OlapConfig
from arrow_lake.exceptions import QueryError
from arrow_lake.query.olap import OlapSearchBridge


@pytest.fixture
def bridge() -> OlapSearchBridge:
    storage = MagicMock()
    storage.read_dataset.return_value = __import__("pyarrow").table({"id": [1, 2, 3]})
    return OlapSearchBridge(storage=storage, config=OlapConfig())


# ── _apply_limit ──


class TestApplyLimit:
    def test_appends_limit(self) -> None:
        sql = OlapSearchBridge._apply_limit("SELECT * FROM t", 100)
        assert sql == "SELECT * FROM t LIMIT 100"

    def test_min_with_existing_limit(self) -> None:
        sql = OlapSearchBridge._apply_limit("SELECT * FROM t LIMIT 500", 100)
        assert "LIMIT 100" in sql

    def test_keeps_smaller_existing(self) -> None:
        sql = OlapSearchBridge._apply_limit("SELECT * FROM t LIMIT 50", 100)
        assert "LIMIT 50" in sql

    def test_preserves_offset(self) -> None:
        sql = OlapSearchBridge._apply_limit("SELECT * FROM t LIMIT 50 OFFSET 10", 100)
        assert "OFFSET 10" in sql

    def test_strips_trailing_semicolons(self) -> None:
        sql = OlapSearchBridge._apply_limit("SELECT * FROM t;", 10)
        assert sql == "SELECT * FROM t LIMIT 10"


# ── _validate_sql ──


class TestValidateSql:
    def test_empty_sql(self, bridge: OlapSearchBridge) -> None:
        with pytest.raises(QueryError, match="empty"):
            bridge._validate_sql("")

    def test_whitespace_only(self, bridge: OlapSearchBridge) -> None:
        with pytest.raises(QueryError, match="empty"):
            bridge._validate_sql("   ")

    def test_non_select(self, bridge: OlapSearchBridge) -> None:
        with pytest.raises(QueryError, match="SELECT"):
            bridge._validate_sql("INSERT INTO t VALUES (1)")

    def test_dangerous_keyword(self, bridge: OlapSearchBridge) -> None:
        with pytest.raises(QueryError, match="not allowed"):
            bridge._validate_sql("SELECT * FROM t; DROP TABLE t")

    def test_semicolons_blocked(self, bridge: OlapSearchBridge) -> None:
        with pytest.raises(QueryError, match="Semicolons"):
            bridge._validate_sql("SELECT * FROM t;")

    def test_valid_select_passes(self, bridge: OlapSearchBridge) -> None:
        bridge._validate_sql("SELECT id, name FROM users LIMIT 10")  # Should not raise

    def test_join_blocked_when_disabled(self, bridge: OlapSearchBridge) -> None:
        cfg = OlapConfig(enable_join=False)
        b = OlapSearchBridge(storage=MagicMock(), config=cfg)
        with pytest.raises(QueryError, match="JOIN"):
            b._validate_sql("SELECT * FROM t1 INNER JOIN t2 ON t1.id = t2.id")


# ── explain ──


class TestExplain:
    def test_explain(self, bridge: OlapSearchBridge) -> None:
        result = bridge.explain("ds1", "SELECT * FROM ds1")
        assert isinstance(result, str)
        assert len(result) > 0


# ── _validate_dataset_name ──


class TestValidateDatasetName:
    def test_valid_name(self) -> None:
        from arrow_lake.query.olap import _validate_dataset_name
        _validate_dataset_name("my_dataset")  # Should not raise

    def test_invalid_name(self) -> None:
        from arrow_lake.query.olap import _validate_dataset_name
        with pytest.raises(ValueError):
            _validate_dataset_name("../../evil")
