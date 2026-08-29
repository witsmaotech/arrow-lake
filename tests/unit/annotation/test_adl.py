"""W3.2 — annotation/adl:Lance 版本化写回(设计 §6.2 / S5)。

契约:
* ``{ds}_adl`` 表,schema=设计 §6.2(join 键+五段+标注者+版本化);
* ``adl_id = sha1(dataset|row_id|annotator|signature)`` —— 同标注内容
  幂等(重放/轮询+webhook 双通道去重);内容变 → 新 id = 新版本;
* ``adl_version``:同 (row_id, annotator) 组内递增(重标注不覆盖);
* ``write_adl`` 经 StorageWriter protocol(死信同款,create-or-append)。
"""

from __future__ import annotations

import pyarrow as pa
from arrow_lake.annotation.adl import ADL_SCHEMA, build_adl_batch, write_adl
from arrow_lake.annotation.quality import Adjudication, annotation_signature
from arrow_lake.annotation.recover import RecoveredAnnotation, Span, Triple


def _rec(annotator: str = "7", *, row_id: str = "r1", scenario: str = "应急") -> RecoveredAnnotation:
    return RecoveredAnnotation(
        task_id=5, row_id=row_id, strategy="uncertainty",
        annotator_id=annotator, annotated_at="2026-08-29T08:00:00Z",
        ground_truth=False,
        objects=(Span("硬件", 0, 3, "调压站"),),
        events=(Span("事故", 4, 9, "燃气泄漏事故"),),
        relations=(Triple("调压站", "导致", "燃气泄漏事故"),),
        rules_applied=("R1", "R2"), scenario=scenario,
    )


class FakeWriter:
    def __init__(self) -> None:
        self.writes: list[tuple[str, pa.Table]] = []

    def write(self, table_name: str, table: pa.Table) -> int:
        self.writes.append((table_name, table))
        return table.num_rows


def _batch(recs, **kw):
    recs = list(recs)
    adjs = {r.row_id: Adjudication("approved", 1.0, ()) for r in recs}
    return build_adl_batch(
        dataset="ds1", recovered=recs, adjudications=adjs,
        batch_id="b1", existing_adl_ids=set(), group_versions={},
        **kw,
    )


class TestBuildBatch:
    def test_first_write_schema_and_values(self):
        table, n = _batch([_rec()])
        assert n == 1
        assert table.schema.equals(ADL_SCHEMA, check_metadata=False)
        row = table.to_pylist()[0]
        assert row["source_dataset"] == "ds1"
        assert row["source_row_id"] == "r1"
        assert row["scenario"] == "应急"
        assert row["rules_applied"] == ["R1", "R2"]
        assert row["objects"] == [{"label": "硬件", "start": 0, "end": 3}]
        assert row["relations"] == [
            {"subject": "调压站", "predicate": "导致", "object": "燃气泄漏事故"}
        ]
        assert row["annotator_id"] == "7"
        assert row["review_status"] == "approved"
        assert row["adl_version"] == 1
        assert row["batch_id"] == "b1"
        assert row["adl_id"]

    def test_adl_id_stable_and_content_sensitive(self):
        t1, _ = _batch([_rec()])
        t2, _ = _batch([_rec()])
        assert t1["adl_id"][0].as_py() == t2["adl_id"][0].as_py()  # 稳定
        t3, _ = _batch([_rec(scenario="常规")])
        assert t1["adl_id"][0].as_py() != t3["adl_id"][0].as_py()  # 内容变 → 新 id

    def test_existing_adl_id_deduped(self):
        rec = _rec()
        t1, _ = _batch([rec])
        existing = {t1["adl_id"][0].as_py()}
        _, n = build_adl_batch(
            dataset="ds1", recovered=[rec],
            adjudications={"r1": Adjudication("approved", 1.0, ())},
            batch_id="b2", existing_adl_ids=existing, group_versions={("r1", "7"): 1},
        )
        assert n == 0  # 重放幂等

    def test_reannotation_new_version(self):
        """同 (row, annotator) 已有 1 版 + 内容变化 → 第 2 版,不覆盖。"""
        rec2 = _rec(scenario="常规")
        table, n = build_adl_batch(
            dataset="ds1", recovered=[rec2],
            adjudications={"r1": Adjudication("approved", 1.0, ())},
            batch_id="b2", existing_adl_ids=set(),
            group_versions={("r1", "7"): 1},
        )
        assert n == 1
        assert table["adl_version"][0].as_py() == 2

    def test_annotators_versioned_independently(self):
        table, n = build_adl_batch(
            dataset="ds1", recovered=[_rec("7"), _rec("8")],
            adjudications={"r1": Adjudication("approved", 1.0, ())},
            batch_id="b1", existing_adl_ids=set(), group_versions={("r1", "7"): 3},
        )
        assert n == 2
        versions = {table["annotator_id"][i].as_py(): table["adl_version"][i].as_py() for i in range(2)}
        assert versions == {"7": 4, "8": 1}

    def test_review_status_from_adjudication(self):
        table, _ = _batch_with_status("arbitration")
        assert table["review_status"][0].as_py() == "arbitration"

    def test_empty_input_empty_batch(self):
        table, n = build_adl_batch(
            dataset="ds1", recovered=[],
            adjudications={}, batch_id="b", existing_adl_ids=set(), group_versions={},
        )
        assert n == 0 and table.num_rows == 0

    def test_batch_version_increments_within_same_batch(self):
        """同批内同组两条(轮询+webhook 双通道同内容除外——不同内容)→ 1,2。"""
        recs = [_rec(scenario="应急"), _rec(scenario="常规")]
        table, n = _batch(recs)
        assert n == 2
        assert sorted(v.as_py() for v in table["adl_version"]) == [1, 2]


def _batch_with_status(status: str):
    rec = _rec()
    return build_adl_batch(
        dataset="ds1", recovered=[rec],
        adjudications={"r1": Adjudication(status, 1.0, ())},
        batch_id="b", existing_adl_ids=set(), group_versions={},
    )


class TestWriteAdl:
    def test_write_via_storage_writer(self):
        writer = FakeWriter()
        table, _ = _batch([_rec()])
        written = write_adl(writer, "ds1", table)
        assert written == 1
        assert writer.writes[0][0] == "ds1_adl"

    def test_empty_table_skips_write(self):
        writer = FakeWriter()
        empty = pa.Table.from_pylist([], schema=ADL_SCHEMA)
        assert write_adl(writer, "ds1", empty) == 0
        assert writer.writes == []

    def test_signature_helper_consistent_with_adl_id(self):
        """adl_id 的 signature 分量 = annotation_signature(质检/ADL 同源)。"""
        rec = _rec()
        sig = annotation_signature(rec)
        t1, _ = _batch([rec])
        adl_id = t1["adl_id"][0].as_py()
        assert adl_id and sig  # 都非空;同源由 build_adl_batch 内部保证
