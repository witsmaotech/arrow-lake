"""单位注册表(v1.11.1 W3.1,F2.2)——维度 → 仿射变换。

每单位以 ``{factor, offset}``` 表达到维度规范单位的仿射变换
(``canonical = value * factor + offset``):压力/浓度/流量/长度是纯因子,
温度是带偏移的仿射(°C↔K)。**from/to 必须同维度**(跨维度拒——单位换算
不是语义对齐能猜的事);注册表外单位走对齐配置的显式 ``factor``
(alignment 侧,S4 封闭种类)。

``%LEL`` 是相对浓度(相对介质特定的爆炸下限),与体积浓度 % **不存在**
仿射换算——独立维度钉死语义边界,静默换算即是错数据。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitDef:
    factor: float
    offset: float = 0.0


_DIMENSIONS: dict[str, dict[str, UnitDef]] = {
    "pressure": {
        "Pa": UnitDef(1.0), "kPa": UnitDef(1e3), "MPa": UnitDef(1e6),
        "bar": UnitDef(1e5), "mbar": UnitDef(1e2),
    },
    "temperature": {
        "K": UnitDef(1.0), "°C": UnitDef(1.0, 273.15),
    },
    "concentration": {
        "%": UnitDef(1.0), "ppm": UnitDef(1e-4), "ppb": UnitDef(1e-7),
    },
    "rel_concentration": {
        "%LEL": UnitDef(1.0),
    },
    "flow": {
        "m³/h": UnitDef(1.0), "L/s": UnitDef(3.6),
    },
    "length": {
        "m": UnitDef(1.0), "km": UnitDef(1e3),
        "cm": UnitDef(1e-2), "mm": UnitDef(1e-3),
    },
}

_ALIASES = {"℃": "°C", "degC": "°C", "m3/h": "m³/h"}


def _lookup(unit: str) -> tuple[str, UnitDef] | None:
    name = _ALIASES.get(unit, unit)
    for dim, units in _DIMENSIONS.items():
        d = units.get(name)
        if d is not None:
            return dim, d
    return None


def dimension_of(unit: str) -> str | None:
    """注册表内单位的维度;未知单位 None。"""
    found = _lookup(unit)
    return found[0] if found else None


def conversion(frm: str, to: str) -> tuple[float, float]:
    """组合仿射变换:``to_value = value * factor + offset``。

    同维度经规范单位中转(``f1/f2, (o1-o2)/f2``);跨维度/未知单位 ValueError。
    """
    a, b = _lookup(frm), _lookup(to)
    if a is None or b is None:
        unknown = frm if a is None else to
        raise ValueError(f"Unknown unit {unknown!r} (registry or explicit factor required)")
    dim_a, u1 = a
    dim_b, u2 = b
    if dim_a != dim_b:
        raise ValueError(
            f"Cannot convert {frm!r} ({dim_a}) → {to!r} ({dim_b}): "
            "cross-dimension conversion is not a unit transform"
        )
    if u2.factor == 0:  # pragma: no cover — registry never holds factor 0
        raise ValueError(f"Invalid target unit {to!r}: zero factor")
    return (u1.factor / u2.factor, (u1.offset - u2.offset) / u2.factor)


def convert(value: float, *, frm: str, to: str) -> float:
    factor, offset = conversion(frm, to)
    return value * factor + offset


def registry_listing() -> dict[str, dict[str, dict[str, float]]]:
    """只读清单(``GET /semantic/units``):维度 → 单位 → {factor, offset}。"""
    return {
        dim: {name: {"factor": d.factor, "offset": d.offset}
              for name, d in units.items()}
        for dim, units in _DIMENSIONS.items()
    }
