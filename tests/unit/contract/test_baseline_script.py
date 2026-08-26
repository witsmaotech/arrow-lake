"""W5.1 — 契约门禁基线脚本(无契约 vs 有契约 两口径)用合成 Lance 数据钉住。

口径:(a) 无契约 = 现状 0 拦截;(b) 有契约 = 编译行约束(OR 合并)reject。
reject 率是切 enforce 的决策依据(门槛 <10%);引用完整性是信息级
(gate W3.1 未接线引用约束),不计入 reject 率。
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa

CONTRACT_YAML = """
dataset: gas_net
tables:
  segments:
    object_class: 管段
    identifier:
      column: seg_id
      pattern: "GAS.SEG.{n}"
    columns:
      - name: material
        enum: [PE, steel]
      - name: pressure
        range: [0, 4000]
      - name: ghost_col
        type: date
  stations:
    columns:
      - name: id
        required: true
references:
  - {from: segments.station_id, to: stations.id}
"""

_SEGMENTS = pa.table({
    #            seg_id      material  pressure  station_id
    "seg_id": ["GAS.SEG.1", "BAD-1", "GAS.SEG.2", None],
    "material": ["PE", "steel", "PVC", None],
    "pressure": [100.0, 5000.0, -1.0, None],
    "station_id": ["S-1", "S-1", "S-X", "S-1"],
})
_STATIONS = pa.table({"id": ["S-1"], "name": ["北门站"]})


def _storage(tmp_path: Path):
    from arrow_lake.ingest.storage import LanceStorageManager

    return LanceStorageManager(str(tmp_path / "lake"))


def test_baseline_container_counts_and_decision(tmp_path: Path) -> None:
    from scripts.contract_gate_baseline import run_baseline

    storage = _storage(tmp_path)
    storage.create_dataset("gas_net", _SEGMENTS, table="segments")
    storage.create_dataset("gas_net", _STATIONS, table="stations")

    report = run_baseline("gas_net", CONTRACT_YAML, storage)

    assert report["mode"] == "container"
    seg = report["tables"]["segments"]
    assert seg["rows"] == 4
    by = {(c["column"], c["kind"]): c for c in seg["constraints"]}
    assert by[("seg_id", "pattern")]["violations"] == 1          # BAD-1
    assert by[("material", "enum")]["violations"] == 1           # PVC
    assert by[("pressure", "range")]["violations"] == 2          # 5000 / -1
    assert "ghost_col" not in by                                  # type-only → 无行 SQL
    assert by[("material", "enum")]["samples"] == ["PVC"]
    # NULL 全放行(domain 检查不查空;row3 三列全 NULL 过)
    assert seg["reject_rows"] == 2                                # row1 + row2
    assert seg["reject_rate"] == 0.5
    # 未知契约列 → warn 级 schema 提示(不产生行约束)
    kinds = {n["kind"] for n in seg["schema_notes"]}
    assert "unknown_column" in kinds

    st = report["tables"]["stations"]
    assert st["rows"] == 1 and st["reject_rows"] == 0

    # 引用完整性:信息级,S-X 缺失 1 条,不计入 reject
    refs = report["references"]
    assert len(refs) == 1
    assert refs[0]["violations"] == 1
    assert refs[0]["samples"] == ["S-X"]

    # 两口径 + 决策:2/5 = 0.4 ≥ 0.10 → 不建议切 enforce
    assert report["calibers"] == {"a_no_contract": 0, "b_contract": 2}
    assert report["totals"] == {"rows": 5, "reject_rows": 2}
    assert report["decision"]["reject_rate"] == 0.4
    assert report["decision"]["enforce_ready"] is False


def test_baseline_legacy_single_table(tmp_path: Path) -> None:
    from scripts.contract_gate_baseline import run_baseline

    yml = """
dataset: plain_ds
ontology:
  columns:
    - name: grade
      enum: [A, B]
"""
    storage = _storage(tmp_path)
    storage.create_dataset("plain_ds", pa.table({"grade": ["A", "C", None]}))
    report = run_baseline("plain_ds", yml, storage)

    assert report["mode"] == "single_table"
    entry = report["tables"]["plain_ds"]
    assert entry["rows"] == 3
    assert entry["reject_rows"] == 1        # 'C';NULL 放行
    assert report["decision"]["reject_rate"] == round(1 / 3, 4)
    assert report["references"] == []


def test_baseline_missing_section_and_uncontracted_table(tmp_path: Path) -> None:
    from scripts.contract_gate_baseline import run_baseline

    storage = _storage(tmp_path)
    storage.create_dataset("gas_net", _SEGMENTS, table="segments")
    storage.create_dataset("gas_net", pa.table({"v": [1]}), table="valves")

    report = run_baseline("gas_net", CONTRACT_YAML, storage)
    # 契约节 stations 在存储无对应表 → 标注 no_data,不炸
    assert report["tables"]["stations"]["note"] == "no_data"
    # 存量表 valves 不在契约 → 提示未覆盖
    assert report["uncontracted_tables"] == ["valves"]
    # 引用目标不可得 → 信息级 note
    assert report["references"][0]["note"]


def test_baseline_clean_dataset_enforce_ready(tmp_path: Path) -> None:
    from scripts.contract_gate_baseline import run_baseline

    storage = _storage(tmp_path)
    clean = pa.table({
        "seg_id": ["GAS.SEG.1", "GAS.SEG.2"],
        "material": ["PE", "steel"],
        "pressure": [100.0, 200.0],
        "station_id": ["S-1", "S-1"],
    })
    storage.create_dataset("gas_net", clean, table="segments")
    storage.create_dataset("gas_net", _STATIONS, table="stations")

    report = run_baseline("gas_net", CONTRACT_YAML, storage)
    assert report["decision"]["reject_rate"] == 0.0
    assert report["decision"]["enforce_ready"] is True
    assert report["references"][0]["violations"] == 0


# ── P1-10 (review 2026-08-26): 假绿灯与 OOM 护栏 ──


def test_container_contract_on_single_table_not_enforce_ready(tmp_path: Path) -> None:
    """容器形契约打单表集:除同名节外全部 skipped —— 历史上这些节不计入
    决策,0 行 0 拒也能亮 enforce_ready=true。现在跳过节强制 false。"""
    from scripts.contract_gate_baseline import run_baseline

    storage = _storage(tmp_path)
    storage.create_dataset("gas_net", pa.table({"v": [1]}))  # 单表集,无容器表
    report = run_baseline("gas_net", CONTRACT_YAML, storage)

    assert report["mode"] == "single_table"
    # 单表集只评估同名节(无约束);segments/stations 两节全 skipped
    assert report["tables"]["segments"]["note"] == "skipped_not_container"
    assert report["tables"]["stations"]["note"] == "skipped_not_container"
    d = report["decision"]
    assert d["skipped_sections"] == 2
    assert d["enforce_ready"] is False         # 旧逻辑这里会是 true(假绿灯)
    assert "未评估" in d["note"]


def test_no_data_section_blocks_enforce_ready(tmp_path: Path) -> None:
    """容器缺表(no_data)同样强制 false —— 未评估的约束不能默认放行。"""
    from scripts.contract_gate_baseline import run_baseline

    storage = _storage(tmp_path)
    clean = pa.table({
        "seg_id": ["GAS.SEG.1"],
        "material": ["PE"],
        "pressure": [100.0],
        "station_id": ["S-1"],
    })
    storage.create_dataset("gas_net", clean, table="segments")  # stations 缺
    report = run_baseline("gas_net", CONTRACT_YAML, storage)
    assert report["decision"]["reject_rate"] == 0.0
    assert report["decision"]["enforce_ready"] is False
    assert report["decision"]["skipped_sections"] == 1


def test_max_rows_partial_report(tmp_path: Path) -> None:
    """--max-rows 闸:截断扫描 + partial 标记 + 不给绿灯。"""
    from scripts.contract_gate_baseline import run_baseline

    storage = _storage(tmp_path)
    bad = pa.table({
        "seg_id": [f"GAS.SEG.{i}" for i in range(10)],
        "material": ["PVC"] * 10,               # 全违规
        "pressure": [100.0] * 10,
        "station_id": ["S-1"] * 10,
    })
    storage.create_dataset("gas_net", bad, table="segments")
    storage.create_dataset("gas_net", _STATIONS, table="stations")

    report = run_baseline("gas_net", CONTRACT_YAML, storage, max_rows=4)
    assert report["partial"] is True
    assert report["max_rows"] == 4
    assert report["tables"]["segments"]["rows"] == 4     # 截断后计数
    assert report["tables"]["segments"]["reject_rows"] == 4
    assert report["decision"]["reject_rate"] == 0.8  # 4 拒 / (4+stations 1) 行
    assert report["decision"]["enforce_ready"] is False  # partial 永不给绿灯
    assert "样本截断" in report["decision"]["note"]


def test_max_rows_above_rowcount_not_partial_gate(tmp_path: Path) -> None:
    """max_rows ≥ 行数时数据完整,partial 标记仍在但绿灯只受 rate/skipped 管。"""
    from scripts.contract_gate_baseline import run_baseline

    storage = _storage(tmp_path)
    clean = pa.table({
        "seg_id": ["GAS.SEG.1", "GAS.SEG.2"],
        "material": ["PE", "steel"],
        "pressure": [100.0, 200.0],
        "station_id": ["S-1", "S-1"],
    })
    storage.create_dataset("gas_net", clean, table="segments")
    storage.create_dataset("gas_net", _STATIONS, table="stations")

    report = run_baseline("gas_net", CONTRACT_YAML, storage, max_rows=1000)
    assert report["partial"] is True            # 口径仍是样本口径
    assert report["decision"]["enforce_ready"] is False
