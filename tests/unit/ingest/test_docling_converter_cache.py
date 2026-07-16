"""Tests for the process-level Docling DocumentConverter cache (audit P1/P2).

``DocumentParser`` used to hold the converter as an instance attribute, but the
parser is recreated per ingest request (``_ingest_files.py``) → Docling layout /
table / OCR models were reloaded on every request (10-30s). The converter is now
a process-level singleton keyed by a config signature, with a per-converter lock
guarding ``convert()`` (Docling inference is not guaranteed thread-safe; the
router serves concurrent ingests from a thread pool).

These tests exercise the cache/lock wiring with a fake build, so no real Docling
models are loaded.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from arrow_lake.ingest import document as doc_mod
from arrow_lake.ingest.document import DocumentParser


def _cfg(engine: str = "rapidocr", langs: tuple[str, ...] = ("ch_sim",)) -> SimpleNamespace:
    return SimpleNamespace(
        docling_pipeline_type="standard",
        docling_vlm_preset=None,
        docling_ocr_engine=engine,
        docling_ocr_languages=list(langs),
    )


@pytest.fixture(autouse=True)
def _reset_converter_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with an empty process cache + docling marked available."""
    monkeypatch.setattr(doc_mod, "_DOCLING_AVAILABLE", True)
    doc_mod._DOCLING_CONVERTERS.clear()


def _fake_build_counter(monkeypatch: pytest.MonkeyPatch) -> itertools.count:
    """Patch ``_build_docling_converter`` to return a unique sentinel per call."""
    counter = itertools.count()

    def _fake(self: DocumentParser) -> str:
        return f"converter-{next(counter)}"

    monkeypatch.setattr(DocumentParser, "_build_docling_converter", _fake)
    return counter


def test_same_config_shares_converter(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_build_counter(monkeypatch)
    p1 = DocumentParser(_cfg())  # type: ignore[arg-type]
    p2 = DocumentParser(_cfg())  # type: ignore[arg-type]

    c1, lock1 = p1._get_docling_converter()
    c2, lock2 = p2._get_docling_converter()

    assert c1 is c2           # process-level singleton: same object
    assert lock1 is lock2     # same per-converter lock


def test_different_config_yields_different_converter(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _fake_build_counter(monkeypatch)
    p_std = DocumentParser(_cfg(engine="rapidocr"))  # type: ignore[arg-type]
    p_easy = DocumentParser(_cfg(engine="easyocr", langs=("en",)))  # type: ignore[arg-type]

    c_std, _ = p_std._get_docling_converter()
    c_easy, _ = p_easy._get_docling_converter()

    assert c_std is not c_easy           # different signature → different converter
    assert next(counter) >= 2            # build called once per distinct config


def test_build_called_once_for_repeated_same_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _fake_build_counter(monkeypatch)
    cfg = _cfg()
    for _ in range(5):
        DocumentParser(cfg)._get_docling_converter()  # type: ignore[arg-type]
    assert next(counter) == 1  # only the first call built; rest hit cache


def test_returns_distinct_locks_for_distinct_converters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_build_counter(monkeypatch)
    p_std = DocumentParser(_cfg(engine="rapidocr"))  # type: ignore[arg-type]
    p_easy = DocumentParser(_cfg(engine="easyocr", langs=("en",)))  # type: ignore[arg-type]

    _, lock_std = p_std._get_docling_converter()
    _, lock_easy = p_easy._get_docling_converter()

    assert lock_std is not lock_easy  # per-converter lock granularity


def test_unavailable_docling_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doc_mod, "_DOCLING_AVAILABLE", False)
    p = DocumentParser(_cfg())  # type: ignore[arg-type]
    with pytest.raises(doc_mod.DocumentError):
        p._get_docling_converter()
