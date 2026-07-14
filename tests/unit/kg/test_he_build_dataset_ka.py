"""Unit tests for ``HyperExtractExtractor.build_dataset_ka`` (per-dataset KA).

Uses a fake AutoGraph injected via ``_create_ka`` so no hyper-extract / LLM /
embedder is loaded — tests are hermetic and fast. Covers the feed loop,
first-appearance provenance, checkpoint dumps, empty-chunk skip, and
failed-feed skip + continue.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from arrow_lake.knowledge_graph.extractor import ExtractionResult
from arrow_lake.knowledge_graph.he_extractor import (
    DatasetKA,
    HyperExtractExtractor,
)


class _FakeNode:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeKA:
    """Fake AutoGraph: appends per-chunk names on feed_text, counts ops."""

    def __init__(self, per_chunk_names: list[set[str]], fail_on=None) -> None:
        self._per_chunk = per_chunk_names
        self._fail_on = set(fail_on or ())
        self._call = 0
        self.nodes: list[_FakeNode] = []
        self.feed_count = 0
        self.build_index_count = 0
        self.dump_dirs: list[Path] = []

    def feed_text(self, _text: str) -> None:
        if self._call in self._fail_on:
            self._call += 1
            raise RuntimeError(f"injected feed failure at call {self._call - 1}")
        if self._call < len(self._per_chunk):
            for name in self._per_chunk[self._call]:
                self.nodes.append(_FakeNode(name))
        self._call += 1
        self.feed_count += 1

    def build_index(self) -> None:
        self.build_index_count += 1

    def dump(self, dir) -> None:
        self.dump_dirs.append(Path(dir))

    def empty(self) -> bool:
        return len(self.nodes) == 0


def _make_extractor(fake_ka: _FakeKA) -> HyperExtractExtractor:
    """Bypass ``__init__`` (avoids ChatOpenAI/env side effects); inject fake KA."""
    ext = HyperExtractExtractor.__new__(HyperExtractExtractor)
    ext._language = "zh"
    ext._create_ka = lambda _path: fake_ka
    return ext


class TestBuildDatasetKa:
    def test_feeds_all_chunks_builds_index_dumps_once(self, tmp_path):
        # Arrange — per-chunk new names: c0={A,B}, c1={C}, c2={D}
        fake = _FakeKA(per_chunk_names=[{"A", "B"}, {"C"}, {"D"}])
        ext = _make_extractor(fake)
        chunks = [("c0", "t0"), ("c1", "t1"), ("c2", "t2")]

        # Act
        dka = asyncio.run(
            ext.build_dataset_ka("general/concept_graph", chunks, tmp_path / "ka", checkpoint_every=0)
        )

        # Assert
        assert fake.feed_count == 3
        assert fake.build_index_count == 1
        assert len(fake.dump_dirs) == 1            # final dump only (checkpoint off)
        assert isinstance(dka, DatasetKA)
        assert isinstance(dka.result, ExtractionResult)

    def test_provenance_records_first_appearance_chunk(self, tmp_path):
        # Arrange — ent_b appears in c0 and c1; first appearance is c0 only.
        # Names are multi-char: _ka_to_extraction_result filters len<=1 names
        # (e3b4f09 single-char LLM-noise filter), so single letters would drop.
        fake = _FakeKA(per_chunk_names=[{"ent_a", "ent_b"}, {"ent_b", "ent_c"}, {"ent_d"}])
        ext = _make_extractor(fake)
        chunks = [("c0", "t0"), ("c1", "t1"), ("c2", "t2")]

        dka = asyncio.run(
            ext.build_dataset_ka("tpl", chunks, tmp_path / "ka", checkpoint_every=0)
        )

        # first-appearance: ent_a→c0, ent_b→c0 (NOT c1), ent_c→c1, ent_d→c2
        assert dka.entity_chunks["ent_a"] == ["c0"]
        assert dka.entity_chunks["ent_b"] == ["c0"]
        assert dka.entity_chunks["ent_c"] == ["c1"]
        assert dka.entity_chunks["ent_d"] == ["c2"]
        assert {e.name for e in dka.result.entities} == {"ent_a", "ent_b", "ent_c", "ent_d"}

    def test_empty_chunks_are_skipped(self, tmp_path):
        fake = _FakeKA(per_chunk_names=[{"A"}, {"B"}])
        ext = _make_extractor(fake)
        chunks = [("c0", "t0"), ("c1", ""), ("c2", "   "), ("c3", "t3")]

        dka = asyncio.run(
            ext.build_dataset_ka("tpl", chunks, tmp_path / "ka", checkpoint_every=0)
        )

        assert fake.feed_count == 2                # c1/c2 (empty) skipped

    def test_failed_feed_is_skipped_and_loop_continues(self, tmp_path):
        # fail_on={1} → c1 feed raises; ent_a(c0) and ent_c(c2) still extracted,
        # ent_b(c1) lost. Multi-char names (single-char filtered, see above).
        fake = _FakeKA(per_chunk_names=[{"ent_a"}, {"ent_b"}, {"ent_c"}], fail_on={1})
        ext = _make_extractor(fake)
        chunks = [("c0", "t0"), ("c1", "t1"), ("c2", "t2")]

        dka = asyncio.run(
            ext.build_dataset_ka("tpl", chunks, tmp_path / "ka", checkpoint_every=0)
        )

        assert fake.feed_count == 2                # c1 failed, not counted
        names = {e.name for e in dka.result.entities}
        assert "ent_a" in names and "ent_c" in names
        assert "ent_b" not in names                # ent_b's chunk (c1) failed

    def test_checkpoint_dumps_at_interval_plus_final(self, tmp_path):
        fake = _FakeKA(per_chunk_names=[{"A"}, {"B"}, {"C"}])
        ext = _make_extractor(fake)
        chunks = [("c0", "t0"), ("c1", "t1"), ("c2", "t2")]

        asyncio.run(
            ext.build_dataset_ka("tpl", chunks, tmp_path / "ka", checkpoint_every=2)
        )

        # checkpoint at i+1=2 (2 % 2 == 0) + final dump → ≥ 2 dumps
        assert len(fake.dump_dirs) >= 2
