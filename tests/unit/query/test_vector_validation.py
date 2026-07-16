"""Unit tests for query-vector validation (audit P2: vector SQL string-interpolation).

The DuckDB ``lance_vector_search`` path builds SQL by string-interpolating the
query vector (``str(v)``). ``_validate_query_vector`` is a pure guard ensuring
every element is a finite float before it reaches SQL — closing the only
unvalidated interpolation in the vector search builder.
"""

from __future__ import annotations

import math

import pytest

from arrow_lake.exceptions import ErrorCode, QueryError
from arrow_lake.query.vector import _resolve_nprobes, _validate_query_vector
from arrow_lake.config import VectorSearchConfig


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_validate_query_vector_accepts_finite_floats() -> None:
    out = _validate_query_vector([0.0, 1.5, -2.25])
    assert out == [0.0, 1.5, -2.25]


def test_validate_query_vector_coerces_ints() -> None:
    out = _validate_query_vector([1, 2, 3])
    assert out == [1.0, 2.0, 3.0]


def test_validate_query_vector_single_element() -> None:
    assert _validate_query_vector([42.0]) == [42.0]


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def _is_invalid_query_err(excinfo: pytest.ExceptionInfo[BaseException]) -> None:
    assert excinfo.value.error_code == ErrorCode.VECTOR_INVALID_QUERY  # type: ignore[attr-defined]


def test_validate_query_vector_rejects_empty() -> None:
    with pytest.raises(QueryError) as excinfo:
        _validate_query_vector([])
    _is_invalid_query_err(excinfo)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_validate_query_vector_rejects_non_finite(bad: float) -> None:
    with pytest.raises(QueryError) as excinfo:
        _validate_query_vector([1.0, bad, 2.0])
    _is_invalid_query_err(excinfo)


def test_validate_query_vector_rejects_string_element() -> None:
    with pytest.raises(QueryError) as excinfo:
        _validate_query_vector([1.0, "not-a-number; DROP TABLE"])  # type: ignore[list-item]
    _is_invalid_query_err(excinfo)


def test_validate_query_vector_rejects_none_element() -> None:
    with pytest.raises(QueryError) as excinfo:
        _validate_query_vector([1.0, None])  # type: ignore[list-item]
    _is_invalid_query_err(excinfo)


def test_validate_query_vector_rejects_non_sequence() -> None:
    with pytest.raises(QueryError) as excinfo:
        _validate_query_vector(1.5)  # type: ignore[arg-type]
    _is_invalid_query_err(excinfo)


# ---------------------------------------------------------------------------
# _resolve_nprobes — clamp requested nprobes to a safe, config-bounded value
# (audit P2: IVF nprobes was fixed, ignoring max_nprobes + num_partitions).
# ---------------------------------------------------------------------------


def _cfg(nprobes: int = 20, max_nprobes: int = 256) -> VectorSearchConfig:
    return VectorSearchConfig(nprobes=nprobes, max_nprobes=max_nprobes)


def test_resolve_nprobes_default_when_not_requested() -> None:
    # requested None → cfg.nprobes, within bounds → unchanged
    assert _resolve_nprobes(None, 256, _cfg(nprobes=20)) == 20


def test_resolve_nprobes_clamps_to_num_partitions() -> None:
    # fewer partitions than configured nprobes → can't probe more than exist
    assert _resolve_nprobes(None, 10, _cfg(nprobes=20)) == 10


def test_resolve_nprobes_respects_max_nprobes_ceiling() -> None:
    # requested above max_nprobes → clamped to max_nprobes (and num_partitions)
    assert _resolve_nprobes(500, 256, _cfg(max_nprobes=128)) == 128
    # max_nprobes above num_partitions → num_partitions is the hard cap
    assert _resolve_nprobes(500, 64, _cfg(max_nprobes=256)) == 64


def test_resolve_nprobes_floors_at_one() -> None:
    assert _resolve_nprobes(0, 256, _cfg()) == 1
    assert _resolve_nprobes(None, 256, _cfg(nprobes=0)) == 1


def test_resolve_nprobes_explicit_within_bounds_passes_through() -> None:
    assert _resolve_nprobes(50, 256, _cfg()) == 50

