"""W3.1 (v1.11.1 F2.2) — 单位注册表:维度 → 仿射变换 {factor, offset}。

from/to 必须同维度(校验拒);注册表外单位走对齐配置的显式 factor(alignment
侧)。%LEL 是相对浓度,与体积浓度 % 不可仿射换算——独立维度钉死语义边界。
"""

from __future__ import annotations

import pytest

from arrow_lake.semantic.units import conversion, convert, dimension_of


class TestConvert:
    def test_kpa_to_mpa(self) -> None:
        assert convert(2.0, frm="kPa", to="MPa") == pytest.approx(0.002)

    def test_celsius_to_kelvin_offset(self) -> None:
        assert convert(25.0, frm="°C", to="K") == pytest.approx(298.15)

    def test_kelvin_to_celsius_reverse(self) -> None:
        assert convert(273.15, frm="K", to="°C") == pytest.approx(0.0)

    def test_ppm_to_percent(self) -> None:
        assert convert(250.0, frm="ppm", to="%") == pytest.approx(0.025)

    def test_ls_to_m3h(self) -> None:
        assert convert(1.0, frm="L/s", to="m³/h") == pytest.approx(3.6)

    def test_aliases(self) -> None:
        assert convert(25.0, frm="℃", to="K") == pytest.approx(298.15)
        assert convert(1.0, frm="m3/h", to="L/s") == pytest.approx(1 / 3.6)

    def test_identity(self) -> None:
        assert conversion("kPa", "kPa") == (1.0, 0.0)


class TestValidation:
    def test_cross_dimension_rejected(self) -> None:
        with pytest.raises(ValueError, match="dimension"):
            conversion("kPa", "m")

    def test_unknown_unit_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown unit"):
            conversion("barg", "kPa")

    def test_lel_not_convertible_to_percent(self) -> None:
        with pytest.raises(ValueError, match="dimension"):
            conversion("%LEL", "%")

    def test_dimension_of(self) -> None:
        assert dimension_of("kPa") == "pressure"
        assert dimension_of("%LEL") != dimension_of("%")
        assert dimension_of("furlong") is None
