"""Tests for per-dataset lance scan mode selection (v1.9.8).

Structured datasets (no vector column) use the fast native lance scan path
(LIMIT/OFFSET pushdown); vector datasets stay on pyarrow_fallback to avoid
the IVF_PQ Rust panic.
"""
import pyarrow as pa

from arrow_lake.query.olap import OlapSearchBridge


def test_has_vector_column_false_for_structured():
    """Structured table (no fixed_size_list) → False → eligible for fast native scan."""
    tbl = pa.table({"id": pa.array([1, 2], pa.int64()), "name": pa.array(["a", "b"], pa.string())})
    assert OlapSearchBridge._has_vector_column(tbl) is False


def test_has_vector_column_true_with_embedding():
    """Table with a fixed_size_list<float> (vector) column → True → stay pyarrow_fallback."""
    vec_type = pa.list_(pa.float32(), 4)  # fixed_size_list<float32, 4>
    tbl = pa.table({"id": pa.array([1]), "embedding": pa.array([[1.0, 2, 3, 4]], vec_type)})
    assert OlapSearchBridge._has_vector_column(tbl) is True


def test_has_vector_column_safe_on_unknown_schema():
    """Source without .schema → True (conservative: stay on pyarrow_fallback)."""
    assert OlapSearchBridge._has_vector_column(object()) is True
