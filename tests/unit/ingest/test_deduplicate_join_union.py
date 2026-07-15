"""Unit tests for deduplicate transform + ingest_join/union — Sprint 10."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import daft
import pytest
from arrow_lake.ingest.transforms import apply_transforms, build_transforms


class TestDeduplicateTransform:
    def test_missing_columns_raises(self) -> None:
        with pytest.raises(ValueError, match="deduplicate requires"):
            build_transforms([{"op": "deduplicate"}])

    def test_distinct_on_columns(self) -> None:
        df = daft.from_pydict({
            "id": [1, 2, 2, 3, 3, 3],
            "val": ["a", "b", "c", "d", "e", "f"],
        })
        transforms = build_transforms([{"op": "deduplicate", "columns": ["id"]}])
        result = apply_transforms(df, transforms)
        count_val = result.count().to_arrow().column(0)[0].as_py()
        assert count_val == 3

    def test_deduplicate_with_order(self) -> None:
        df = daft.from_pydict({
            "key": ["a", "a", "b"],
            "ts": [1, 2, 3],
            "val": ["old", "new", "only"],
        })
        transforms = build_transforms([
            {"op": "deduplicate", "columns": ["key"], "order_by": "ts", "desc": True},
        ])
        result = apply_transforms(df, transforms)
        count_val = result.count().to_arrow().column(0)[0].as_py()
        assert count_val == 2
        # "a" keeps ts=2 (desc), "b" keeps ts=3


class TestIngestJoin:
    @patch("daft.from_arrow")
    def test_join_basic(self, mock_from: MagicMock) -> None:
        mock_left_df = MagicMock()
        mock_right_df = MagicMock()
        mock_joined = MagicMock()
        mock_count = MagicMock()
        mock_arrow = MagicMock()
        mock_arrow.column.return_value = [MagicMock(as_py=MagicMock(return_value=50))]
        mock_count.to_arrow.return_value = mock_arrow
        mock_joined.count.return_value = mock_count
        mock_left_df.join.return_value = mock_joined

        mock_from.side_effect = [mock_left_df, mock_right_df]

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        mock_manager.read_dataset.side_effect = [
            MagicMock(num_rows=100),  # left table
            MagicMock(num_rows=80),   # right table
        ]

        from arrow_lake.ingest.ingestor import Ingestor
        ingestor = Ingestor(mock_manager)
        report = ingestor.ingest_join("orders", right_dataset="products", left_on="product_id")

        assert report.total_rows == 50
        mock_manager.write_lance_from_dataframe.assert_called_once()


class TestIngestUnion:
    @patch("daft.from_arrow")
    def test_union_two_datasets(self, mock_from: MagicMock) -> None:
        mock_df1 = MagicMock()
        mock_df2 = MagicMock()
        mock_union = MagicMock()
        mock_count = MagicMock()
        mock_arrow = MagicMock()
        mock_arrow.column.return_value = [MagicMock(as_py=MagicMock(return_value=200))]
        mock_count.to_arrow.return_value = mock_arrow
        mock_union.count.return_value = mock_count
        mock_df1.union_all.return_value = mock_union

        mock_from.side_effect = [mock_df1, mock_df2]

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        mock_manager.read_dataset.side_effect = [
            MagicMock(num_rows=100),
            MagicMock(num_rows=100),
        ]

        from arrow_lake.ingest.ingestor import Ingestor
        ingestor = Ingestor(mock_manager)
        report = ingestor.ingest_union("combined", source_datasets=["sales_q1", "sales_q2"])

        assert report.total_rows == 200
        assert report.total_files == 2
