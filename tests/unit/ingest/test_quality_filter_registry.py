"""Tests for arrow_lake.quality.base — Story 4.8 QualityFilter Protocol + Registry."""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.quality.base import (
    FilterResult,
    QualityFilter,
    QualityFilterRegistry,
)


class _AlwaysPassFilter:
    """Test filter that passes all rows."""

    def __init__(self) -> None:
        self.name = "always_pass"
        self.call_count = 0

    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        self.call_count += 1
        return table, table.slice(0, 0)


class _AlwaysRejectFilter:
    """Test filter that rejects all rows."""

    def __init__(self) -> None:
        self.name = "always_reject"
        self.call_count = 0

    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        self.call_count += 1
        return table.slice(0, 0), table


class _HalfRejectFilter:
    """Test filter that rejects the first half of rows."""

    def __init__(self) -> None:
        self.name = "half_reject"
        self.call_count = 0

    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        self.call_count += 1
        mid = table.num_rows // 2
        return table.slice(mid), table.slice(0, mid)


def _make_table(n: int = 10) -> pa.Table:
    return pa.table({"text": [f"row-{i}" for i in range(n)]})


class TestQualityFilterProtocol:
    """Test QualityFilter Protocol runtime checking."""

    def test_valid_filter_is_instance(self) -> None:
        f = _AlwaysPassFilter()
        assert isinstance(f, QualityFilter)

    def test_filter_has_name(self) -> None:
        f = _AlwaysPassFilter()
        assert f.name == "always_pass"

    def test_filter_callable(self) -> None:
        f = _AlwaysPassFilter()
        table = _make_table(5)
        passed, rejected = f.filter(table)
        assert passed.num_rows == 5
        assert rejected.num_rows == 0

    def test_non_conforming_object_not_instance(self) -> None:
        class Bad:
            pass

        assert not isinstance(Bad(), QualityFilter)


class TestFilterResult:
    """Test FilterResult frozen dataclass."""

    def test_creation(self) -> None:
        r = FilterResult(filter_name="test", passed_count=8, rejected_count=2)
        assert r.filter_name == "test"
        assert r.passed_count == 8
        assert r.rejected_count == 2

    def test_frozen(self) -> None:
        r = FilterResult(filter_name="test", passed_count=8, rejected_count=2)
        with pytest.raises(AttributeError):
            r.passed_count = 5


class TestQualityFilterRegistry:
    """Test QualityFilterRegistry core functionality."""

    def test_register_and_list(self) -> None:
        registry = QualityFilterRegistry()
        f = _AlwaysPassFilter()
        registry.register(f)
        assert "always_pass" in registry.list_filters()

    def test_register_duplicate_raises(self) -> None:
        registry = QualityFilterRegistry()
        f1 = _AlwaysPassFilter()
        f2 = _AlwaysPassFilter()
        registry.register(f1)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(f2)

    def test_unregister(self) -> None:
        registry = QualityFilterRegistry()
        f = _AlwaysPassFilter()
        registry.register(f)
        registry.unregister("always_pass")
        assert "always_pass" not in registry.list_filters()

    def test_unregister_missing_raises(self) -> None:
        registry = QualityFilterRegistry()
        with pytest.raises(KeyError, match="not registered"):
            registry.unregister("nonexistent")

    def test_get(self) -> None:
        registry = QualityFilterRegistry()
        f = _AlwaysPassFilter()
        registry.register(f)
        assert registry.get("always_pass") is f

    def test_get_missing_raises(self) -> None:
        registry = QualityFilterRegistry()
        with pytest.raises(KeyError, match="not registered"):
            registry.get("nonexistent")

    def test_clear(self) -> None:
        registry = QualityFilterRegistry()
        registry.register(_AlwaysPassFilter())
        registry.register(_AlwaysRejectFilter())
        registry.clear()
        assert registry.list_filters() == []

    def test_get_active_filters_empty_string(self) -> None:
        registry = QualityFilterRegistry()
        registry.register(_AlwaysPassFilter())
        active = registry.get_active_filters("")
        assert active == []

    def test_get_active_filters_by_name(self) -> None:
        registry = QualityFilterRegistry()
        f_pass = _AlwaysPassFilter()
        f_reject = _AlwaysRejectFilter()
        registry.register(f_pass)
        registry.register(f_reject)
        active = registry.get_active_filters("always_pass,always_reject")
        assert len(active) == 2
        assert active[0] is f_pass
        assert active[1] is f_reject

    def test_get_active_filters_unknown_name_skipped(self) -> None:
        registry = QualityFilterRegistry()
        f = _AlwaysPassFilter()
        registry.register(f)
        active = registry.get_active_filters("always_pass,nonexistent")
        assert len(active) == 1
        assert active[0] is f

    def test_get_active_filters_unknown_name_raises(self) -> None:
        registry = QualityFilterRegistry()
        with pytest.raises(KeyError, match="nonexistent"):
            registry.get_active_filters("nonexistent", skip_unknown=False)


class TestQualityFilterRegistryApplyAll:
    """Test apply_all with AND/OR filter modes."""

    def test_apply_all_and_mode(self) -> None:
        """AND mode: cumulative reject — each filter sees only the passing rows."""
        registry = QualityFilterRegistry()
        f1 = _HalfRejectFilter()  # rejects first 5 of 10
        registry.register(f1)

        table = _make_table(10)
        report = registry.apply_all(table, active_filters="half_reject")

        assert report.total == 10
        assert report.passed == 5
        assert report.rejected == 5

    def test_apply_all_and_mode_empty_table(self) -> None:
        registry = QualityFilterRegistry()
        registry.register(_AlwaysPassFilter())

        table = _make_table(0)
        report = registry.apply_all(table, active_filters="always_pass")

        assert report.total == 0
        assert report.passed == 0
        assert report.rejected == 0

    def test_apply_all_or_mode(self) -> None:
        """OR mode: cumulative pass — each filter sees only the failing rows."""
        registry = QualityFilterRegistry()
        f_pass = _AlwaysPassFilter()
        f_reject = _AlwaysRejectFilter()
        registry.register(f_pass)
        registry.register(f_reject)

        table = _make_table(10)
        report = registry.apply_all(table, active_filters="always_pass,always_reject", mode="any")

        assert report.total == 10
        assert report.passed == 10  # always_pass passes all in OR mode
        assert report.rejected == 0

    def test_apply_all_single_filter(self) -> None:
        registry = QualityFilterRegistry()
        registry.register(_HalfRejectFilter())

        table = _make_table(10)
        report = registry.apply_all(table, active_filters="half_reject")

        assert report.total == 10
        assert report.passed == 5
        assert report.rejected == 5
        assert len(report.filter_results) == 1

    def test_apply_all_no_active_filters(self) -> None:
        registry = QualityFilterRegistry()

        table = _make_table(10)
        report = registry.apply_all(table, active_filters="")

        assert report.total == 10
        assert report.passed == 10
        assert report.rejected == 0


class TestApplyAndShortCircuit:
    """Cover L192: break when current.num_rows == 0 in _apply_and."""

    def test_and_mode_short_circuits_after_total_reject(self) -> None:
        """First filter rejects all rows -> break at L192, second filter not called."""
        registry = QualityFilterRegistry()
        f_reject = _AlwaysRejectFilter()
        f_pass = _AlwaysPassFilter()
        registry.register(f_reject)
        registry.register(f_pass)

        table = _make_table(5)
        report = registry.apply_all(table, active_filters="always_reject,always_pass")

        # First filter rejects all, break fires, second filter never called
        assert report.passed == 0
        assert report.rejected == 5
        assert f_reject.call_count == 1
        assert f_pass.call_count == 0


class TestApplyOrEmptyPass:
    """Cover L246: all_passed = table.slice(0, 0) when no filter passes any rows."""

    def test_or_mode_no_filter_passes(self) -> None:
        """All filters reject -> accumulated_passed_chunks empty -> L246 reached."""
        registry = QualityFilterRegistry()
        f1 = _AlwaysRejectFilter()
        # Second reject filter with a different name
        class _RejectFilter2:
            name = "reject2"
            def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
                return table.slice(0, 0), table
        registry.register(f1)
        registry.register(_RejectFilter2())

        table = _make_table(4)
        report = registry.apply_all(table, active_filters="always_reject,reject2", mode="any")

        assert report.passed == 0
        assert report.rejected == 4
