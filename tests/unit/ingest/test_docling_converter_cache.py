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
        docling_vlm_endpoint="",
        docling_vlm_model="",
        docling_ocr_engine=engine,
        docling_ocr_languages=list(langs),
        docling_heading_hierarchy=True,
        docling_picture_description=False,
        docling_picture_description_endpoint="",
        docling_picture_description_model="",
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


def test_heading_hierarchy_flips_signature() -> None:
    """heading hierarchy 开关参与 converter 签名——不同值不得共享缓存的 converter。"""
    cfg_on = _cfg()
    cfg_off = _cfg()
    cfg_off.docling_heading_hierarchy = False
    assert (
        DocumentParser(cfg_on)._docling_signature()  # type: ignore[arg-type]
        != DocumentParser(cfg_off)._docling_signature()  # type: ignore[arg-type]
    )


def test_vlm_endpoint_flips_signature() -> None:
    """vlm endpoint 参与 converter 签名——inline 与 API 型(及不同端点)各自分桶。"""
    cfg_inline = _cfg()
    cfg_api = _cfg()
    cfg_api.docling_vlm_endpoint = "http://vlm:8000/v1/chat/completions"
    cfg_api2 = _cfg()
    cfg_api2.docling_vlm_endpoint = "http://other:9000/v1/chat/completions"
    sig_i = DocumentParser(cfg_inline)._docling_signature()  # type: ignore[arg-type]
    sig_a = DocumentParser(cfg_api)._docling_signature()  # type: ignore[arg-type]
    sig_b = DocumentParser(cfg_api2)._docling_signature()  # type: ignore[arg-type]
    assert len({sig_i, sig_a, sig_b}) == 3  # inline / api-a / api-b 三桶


def test_picture_description_flips_signature() -> None:
    """图片描述开关+端点参与签名——关/开(端点A)/开(端点B)各自分桶。"""
    cfg_off = _cfg()
    cfg_on = _cfg()
    cfg_on.docling_picture_description = True
    cfg_on.docling_picture_description_endpoint = "https://a.example/v1/chat/completions"
    cfg_on.docling_picture_description_model = "qwen-vl-max"
    cfg_on2 = _cfg()
    cfg_on2.docling_picture_description = True
    cfg_on2.docling_picture_description_endpoint = "https://b.example/v1/chat/completions"
    cfg_on2.docling_picture_description_model = "qwen-vl-max"
    sigs = {
        DocumentParser(c)._docling_signature()  # type: ignore[arg-type]
        for c in (cfg_off, cfg_on, cfg_on2)
    }
    assert len(sigs) == 3


# ── M4/M5(v1.11.6.6)──────────────────────────────────────────────────────


def test_page_batch_set_at_convergence_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """M4:page_batch 赋值在 standard/VLM 汇合点——VLM 档同样生效(此前
    只在 standard 管线构建内赋值,VLM 永不执行,docling 默认 4 静默封顶
    引擎并发 min(concurrency, page_batch))。"""
    _fake_build_counter(monkeypatch)
    fake_settings = SimpleNamespace(perf=SimpleNamespace(page_batch_size=4))
    monkeypatch.setattr(doc_mod, "_docling_settings", fake_settings)
    monkeypatch.setenv("ARROW_LAKE_DOCLING_PAGE_BATCH", "32")
    cfg = _cfg()
    cfg.docling_pipeline_type = "vlm"  # VLM 档(修复前永不赋值)
    DocumentParser(cfg)._get_docling_converter()  # type: ignore[arg-type]
    assert fake_settings.perf.page_batch_size == 32
    # 缓存命中路径同样保持(幂等重设)
    DocumentParser(cfg)._get_docling_converter()  # type: ignore[arg-type]
    assert fake_settings.perf.page_batch_size == 32


def test_parse_cache_key_includes_docling_signature(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """M5:parse 缓存 key 含 docling 签名——同内容同基础配置,picture_
    description 开关翻转后各命中各自缓存,不混用旧解析。"""
    from arrow_lake.config import DocumentConfig
    from arrow_lake.config._enums import OcrBackend
    from arrow_lake.ingest.document import ParsedDocument

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-fake-m5")
    monkeypatch.setattr(doc_mod, "_docling_handles", lambda fp: True)

    calls: list[bool] = []

    def _fake_parse_docling(self, fp, max_pages):
        calls.append(self._config.docling_picture_description)
        return ParsedDocument(
            text=f"desc={self._config.docling_picture_description}",
            pages=[(1, "x")], page_count=1, backend="docling",
        )

    monkeypatch.setattr(DocumentParser, "_parse_docling", _fake_parse_docling)

    cfg_off = DocumentConfig(ocr_backend=OcrBackend.DOCLING)
    cfg_on = DocumentConfig(
        ocr_backend=OcrBackend.DOCLING, docling_picture_description=True
    )
    r1 = DocumentParser(cfg_off).parse(f)
    r2 = DocumentParser(cfg_off).parse(f)  # 同 config → 缓存命中
    assert calls == [False]
    assert r1 is r2  # 缓存对象同一性
    r3 = DocumentParser(cfg_on).parse(f)  # 签名翻转 → 不命中,重解析
    assert calls == [False, True]
    assert r3.text == "desc=True"
