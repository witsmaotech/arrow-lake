"""Cover missing lines in arrow_lake.query.daft_api."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pyarrow as pa
import pytest

from arrow_lake.config import StorageBackend
from arrow_lake.query.daft_api import (
    DaftQueryEngine,
    LazyDaftFrame,
    LazyGroupedFrame,
)


# ---------------------------------------------------------------------------
# LazyDaftFrame branches
# ---------------------------------------------------------------------------


class TestLazyDaftFrameMisc:
    def test_with_columns(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.with_columns({"col1": MagicMock()})
        mock_df.with_column.assert_not_called()  # uses with_columns
        mock_df.select.assert_not_called()

    def test_exclude(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.exclude("a", "b")
        mock_df.exclude.assert_called_once_with("a", "b")

    def test_drop_null(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.drop_null("col")
        mock_df.drop_null.assert_called_once_with("col")

    def test_fill_null(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.fill_null(0, "col")
        mock_df.with_column.assert_called_once()

    def test_distinct_no_args(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.distinct()
        mock_df.distinct.assert_called_once()

    def test_count_rows(self) -> None:
        mock_df = MagicMock()
        mock_df.count_rows.return_value = 42
        f = LazyDaftFrame(mock_df)
        assert f.count_rows() == 42

    def test_pivot(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.pivot(group_by="a", pivot_col="b", value_col="c", agg_fn="sum")
        mock_df.pivot.assert_called_once()

    def test_unpivot(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.unpivot(ids=["a", "b"])
        mock_df.unpivot.assert_called_once()

    def test_explode(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.explode("col")
        mock_df.explode.assert_called_once()

    def test_sample(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.sample(fraction=0.5)
        mock_df.sample.assert_called_once()

    def test_describe(self) -> None:
        mock_df = MagicMock()
        mock_df.describe.return_value = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.describe()
        mock_df.describe.assert_called_once()

    def test_schema(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.schema()
        mock_df.schema.assert_called_once()

    def test_join(self) -> None:
        mock_df = MagicMock()
        other = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.join(other, on="key")
        mock_df.join.assert_called_once()

    @pytest.mark.xfail(reason="Daft runtime type validation requires real DataFrame")
    def test_sql(self) -> None:
        mock_df = MagicMock()
        f = LazyDaftFrame(mock_df)
        f.sql("SELECT * FROM self")
        mock_df.sql.assert_called_once_with("SELECT * FROM self")

    def test_collect_truncation(self) -> None:
        mock_df = MagicMock()
        big_table = pa.table({"a": range(100)})
        mock_df.to_arrow.return_value = big_table
        f = LazyDaftFrame(mock_df)
        result = f.collect(max_rows=10)
        assert result.num_rows == 10

    def test_repr(self) -> None:
        mock_df = MagicMock()
        r = repr(LazyDaftFrame(mock_df))
        assert "LazyDaftFrame" in r


# ---------------------------------------------------------------------------
# LazyGroupedFrame
# ---------------------------------------------------------------------------


class TestLazyGroupedFrame:
    def test_repr(self) -> None:
        mock_grouped = MagicMock()
        r = repr(LazyGroupedFrame(mock_grouped))
        assert "LazyGroupedFrame" in r

    def test_agg(self) -> None:
        mock_grouped = MagicMock()
        gf = LazyGroupedFrame(mock_grouped)
        gf.agg(MagicMock())
        mock_grouped.agg.assert_called_once()

    def test_sum(self) -> None:
        mock_grouped = MagicMock()
        gf = LazyGroupedFrame(mock_grouped)
        gf.sum()
        mock_grouped.sum.assert_called_once()

    def test_mean(self) -> None:
        mock_grouped = MagicMock()
        gf = LazyGroupedFrame(mock_grouped)
        gf.mean()
        mock_grouped.mean.assert_called_once()

    def test_count(self) -> None:
        mock_grouped = MagicMock()
        gf = LazyGroupedFrame(mock_grouped)
        gf.count()
        mock_grouped.count.assert_called_once()

    def test_min(self) -> None:
        mock_grouped = MagicMock()
        gf = LazyGroupedFrame(mock_grouped)
        gf.min()
        mock_grouped.min.assert_called_once()

    def test_max(self) -> None:
        mock_grouped = MagicMock()
        gf = LazyGroupedFrame(mock_grouped)
        gf.max()
        mock_grouped.max.assert_called_once()

    def test_stddev(self) -> None:
        mock_grouped = MagicMock()
        gf = LazyGroupedFrame(mock_grouped)
        gf.stddev()
        mock_grouped.stddev.assert_called_once()

    def test_var(self) -> None:
        mock_grouped = MagicMock()
        gf = LazyGroupedFrame(mock_grouped)
        gf.var()
        mock_grouped.var.assert_called_once()

    def test_collect_truncation(self) -> None:
        mock_grouped = MagicMock()
        big_table = pa.table({"a": range(100)})
        mock_grouped.sum.return_value.to_arrow.return_value = big_table
        gf = LazyGroupedFrame(mock_grouped)
        result = gf.collect(max_rows=10)
        assert result.num_rows == 10

    def test_collect_no_truncation(self) -> None:
        mock_grouped = MagicMock()
        small_table = pa.table({"a": [1, 2, 3]})
        mock_grouped.sum.return_value.to_arrow.return_value = small_table
        gf = LazyGroupedFrame(mock_grouped)
        result = gf.collect(max_rows=100)
        assert result.num_rows == 3


# ---------------------------------------------------------------------------
# DaftQueryEngine
# ---------------------------------------------------------------------------


class TestDaftQueryEngine:
    def test_repr(self) -> None:
        eng = DaftQueryEngine(base_uri="/tmp/test")
        assert "/tmp/test" in repr(eng)

    @pytest.mark.xfail(reason="Daft set_planning_config signature introspection requires real Daft runtime")
    @patch("arrow_lake.query.daft_api.daft")
    def test_apply_planning_config(self, mock_daft: MagicMock) -> None:
        mock_sig = MagicMock()
        mock_sig.parameters = {"default_num_partitions": MagicMock()}
        mock_daft.set_planning_config.__signature__ = mock_sig
        mock_cfg = MagicMock()
        mock_cfg.default_num_partitions = 4
        mock_cfg.target_partition_max_memory_bytes = None
        DaftQueryEngine._apply_planning_config(mock_cfg)
        mock_daft.set_planning_config.assert_called_once()

    @pytest.mark.xfail(reason="Daft set_planning_config signature introspection requires real Daft runtime")
    @patch("arrow_lake.query.daft_api.daft")
    def test_apply_planning_config_no_kwargs(self, mock_daft: MagicMock) -> None:
        mock_sig = MagicMock()
        mock_sig.parameters = {}
        mock_daft.set_planning_config.__signature__ = mock_sig
        mock_cfg = MagicMock()
        DaftQueryEngine._apply_planning_config(mock_cfg)
        mock_daft.set_planning_config.assert_not_called()

    def test_build_io_config_local(self) -> None:
        cfg = MagicMock()
        cfg.backend = "local"
        result = DaftQueryEngine._build_io_config(cfg)
        assert result is None

    def test_build_io_config_s3(self) -> None:
        cfg = MagicMock()
        cfg.backend = StorageBackend.S3
        cfg.s3_region = "us-east-1"
        cfg.s3_endpoint = "https://minio:9000"
        cfg.s3_access_key = "key"
        cfg.s3_secret_key = "secret"
        mock_io_cls = MagicMock()
        mock_s3_cls = MagicMock()
        with patch.dict("sys.modules", {"daft.io": MagicMock(IOConfig=mock_io_cls, S3Config=mock_s3_cls)}):
            mock_io_cls.return_value = MagicMock()
            result = DaftQueryEngine._build_io_config(cfg)
            assert result is not None

    def test_init_with_storage_config(self) -> None:
        mock_cfg = MagicMock()
        with patch.object(DaftQueryEngine, "_build_io_config", return_value=MagicMock()):
            eng = DaftQueryEngine(base_uri="/tmp", storage_config=mock_cfg)
        assert eng._io_config is not None

    def test_init_with_daft_config(self) -> None:
        mock_dc = MagicMock()
        with patch.object(DaftQueryEngine, "_apply_planning_config"):
            eng = DaftQueryEngine(base_uri="/tmp", daft_config=mock_dc)
        assert eng._daft_config is mock_dc

    def test_read_gravitino_no_config(self) -> None:
        eng = DaftQueryEngine(base_uri="/tmp")
        with pytest.raises(RuntimeError, match="not configured"):
            eng.read_gravitino_table("tbl")

    def test_read_gravitino_disabled(self) -> None:
        eng = DaftQueryEngine(base_uri="/tmp", gravitino_config=MagicMock(enabled=False))
        with pytest.raises(RuntimeError, match="not configured"):
            eng.read_gravitino_table("tbl")

    @patch("arrow_lake.query.daft_api.daft")
    def test_read_gravitino_federated_fallback(self, mock_daft: MagicMock) -> None:
        grav_cfg = MagicMock()
        grav_cfg.enabled = True
        grav_cfg.lance_rest_uri = "http://rest:8080"
        mock_daft.read_lance.return_value = MagicMock()
        eng = DaftQueryEngine(base_uri="/tmp", gravitino_config=grav_cfg)
        with patch(
            "arrow_lake.query.daft_api.FederatedQueryEngine",
            side_effect=ImportError("nope"),
            create=True,
        ):
            result = eng.read_gravitino_table("catalog.schema.tbl")
        assert result is not None

    @patch("arrow_lake.query.daft_api.daft")
    def test_read_gravitino_full_failure(self, mock_daft: MagicMock) -> None:
        grav_cfg = MagicMock()
        grav_cfg.enabled = True
        grav_cfg.lance_rest_uri = "http://rest:8080"
        mock_daft.read_lance.side_effect = RuntimeError("fail")
        eng = DaftQueryEngine(base_uri="/tmp", gravitino_config=grav_cfg)
        with patch(
            "arrow_lake.query.daft_api.FederatedQueryEngine",
            side_effect=ImportError,
            create=True,
        ):
            with pytest.raises(RuntimeError, match="Failed to read"):
                eng.read_gravitino_table("tbl")

    @patch("arrow_lake.query.daft_api.daft")
    def test_load_local(self, mock_daft: MagicMock) -> None:
        mock_daft.read_lance.return_value = MagicMock()
        eng = DaftQueryEngine(base_uri="/tmp/lake")
        result = eng.load("myds")
        assert result is not None

    @patch("arrow_lake.query.daft_api.daft")
    def test_load_with_columns(self, mock_daft: MagicMock) -> None:
        mock_df = MagicMock()
        mock_daft.read_lance.return_value = mock_df
        eng = DaftQueryEngine(base_uri="/tmp/lake")
        result = eng.load("myds", columns=["col1"])
        mock_df.select.assert_called_once_with("col1")

    def test_load_invalid_name(self) -> None:
        eng = DaftQueryEngine(base_uri="/tmp")
        with pytest.raises(ValueError, match="Invalid dataset"):
            eng.load("bad name!")

    def test_load_invalid_column(self) -> None:
        eng = DaftQueryEngine(base_uri="/tmp")
        with pytest.raises(ValueError, match="Invalid column"):
            eng.load("myds", columns=["bad col!"])

    @patch("arrow_lake.query.daft_api.daft")
    def test_load_not_found(self, mock_daft: MagicMock) -> None:
        mock_daft.read_lance.side_effect = FileNotFoundError("nf")
        eng = DaftQueryEngine(base_uri="/tmp")
        with pytest.raises(FileNotFoundError, match="not found"):
            eng.load("myds")

    @patch("arrow_lake.query.daft_api.daft")
    def test_load_other_error(self, mock_daft: MagicMock) -> None:
        mock_daft.read_lance.side_effect = RuntimeError("err")
        eng = DaftQueryEngine(base_uri="/tmp")
        with pytest.raises(RuntimeError, match="Failed to load"):
            eng.load("myds")

    @patch("arrow_lake.query.daft_api.daft")
    def test_load_s3_path(self, mock_daft: MagicMock) -> None:
        mock_daft.read_lance.return_value = MagicMock()
        mock_sc = MagicMock()
        mock_sc.s3_uri = "s3://bucket/data"
        with patch.object(DaftQueryEngine, "_build_io_config", return_value=MagicMock()):
            eng = DaftQueryEngine(base_uri="/tmp", storage_config=mock_sc)
            result = eng.load("myds")
        assert result is not None
