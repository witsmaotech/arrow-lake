"""Docling convert() throughput benchmark — v1.10.3 M0 (G6).

Standalone script (NOT a pytest test): docling + GPU live only in the API
container, and the prod image bakes neither ``tests/`` nor pytest. Run via
``docker cp`` + ``docker exec``::

    docker cp tests/benchmark/docling_convert_bench.py arrow-lake-api-1:/tmp/
    docker exec arrow-lake-api-1 python3 /tmp/docling_convert_bench.py \\
        --mode both --pdf /data/lake/wuhu_report.pdf
    # quick subset (first 30 pages):
    docker exec arrow-lake-api-1 python3 /tmp/docling_convert_bench.py \\
        --mode both --pdf /data/lake/wuhu_report.pdf --max-pages 30 --profile

Purpose (M0): before touching ``ingest/document.py``, validate that the P0
config (``ThreadedPdfPipelineOptions`` + ``page_batch_size`` + RapidOCR torch)
actually beats the v1.10.2 baseline (plain ``PdfPipelineOptions`` + RapidOCR
ONNX) on real hardware. Two converters are built INLINE (independent of
``document.py``) so the gain is measured without a code change — de-risking P0.

Each mode reports: total wall time, page count, pages/sec, and the docling
confidence ``mean_grade`` (so quality regressions surface alongside speed).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any


def _accelerator() -> tuple[Any, Any]:
    """CUDA if available, else AUTO. Mirrors ingest/document.py:643-646."""
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )

    try:
        import torch

        dev = AcceleratorDevice.CUDA if torch.cuda.is_available() else AcceleratorDevice.AUTO
    except Exception:
        dev = AcceleratorDevice.AUTO
    return dev, AcceleratorOptions(device=dev)


def _tableformer(pipeline: Any) -> None:
    """TableFormer ACCURATE + do_cell_matching=False — mirrors document.py:634-637."""
    try:
        from docling.datamodel.pipeline_options import TableFormerMode

        pipeline.table_structure_options.mode = TableFormerMode.ACCURATE
        pipeline.table_structure_options.do_cell_matching = False
    except Exception as e:  # noqa: BLE001
        print(f"[warn] TableFormer config skipped: {e}", file=sys.stderr)


def _ocr_options(engine: str, gpu: bool) -> Any:
    """RapidOCR options. baseline=False → ONNX CPU (current); gpu=True → torch GPU (P0)."""
    from docling.datamodel.pipeline_options import RapidOcrOptions

    if gpu:
        return RapidOcrOptions(backend="torch")  # P0-2: GPU OCR (R1 validates here)
    return RapidOcrOptions()  # current default = ONNX CPU


def build_baseline_converter(ocr_gpu: bool = False) -> Any:
    """v1.10.2 baseline: plain PdfPipelineOptions. ocr_gpu=False mirrors current ONNX CPU."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    dev, accel = _accelerator()
    pipeline = PdfPipelineOptions(
        do_ocr=True,
        ocr_options=_ocr_options("rapidocr", gpu=ocr_gpu),
        do_table_structure=True,
    )
    _tableformer(pipeline)
    pipeline.accelerator_options = accel
    ocr_tag = "rapidocr/torch-gpu" if ocr_gpu else "rapidocr/onnx-cpu"
    print(f"[baseline] accelerator={dev.value} ocr={ocr_tag} pipeline=PdfPipelineOptions")
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)}
    )


def build_threaded_converter(page_batch: int = 64, ocr_gpu: bool = False) -> Any:
    """P0-1 config: ThreadedPdfPipelineOptions + page_batch_size. ocr_gpu toggles P0-2 (torch GPU).

    ocr_gpu defaults False because RapidOCR torch backend needs .pth models that are
    not baked into the read-only image (R1: Errno 30). Page batching (P0-1) is measured
    in isolation with OCR on ONNX CPU; P0-2 is a separate task (bake .pth / volume mount).
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions
    from docling.datamodel.settings import settings
    from docling.document_converter import DocumentConverter, PdfFormatOption

    dev, accel = _accelerator()
    settings.perf.page_batch_size = page_batch  # default 4 → 64 on GPU
    pipeline = ThreadedPdfPipelineOptions(
        do_ocr=True,
        ocr_options=_ocr_options("rapidocr", gpu=ocr_gpu),
        do_table_structure=True,
        ocr_batch_size=64,
        layout_batch_size=64,
        table_batch_size=4,
    )
    _tableformer(pipeline)
    pipeline.accelerator_options = accel
    ocr_tag = "rapidocr/torch-gpu" if ocr_gpu else "rapidocr/onnx-cpu"
    print(
        f"[threaded] accelerator={dev.value} ocr={ocr_tag} "
        f"pipeline=ThreadedPdfPipelineOptions page_batch={page_batch} "
        f"ocr/layout/table batch=64/64/4"
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)}
    )


def _convert_timed(converter: Any, pdf: str, max_pages: int) -> dict[str, Any]:
    """convert() with optional page_range subset; return timing + page count + grade."""
    kwargs: dict[str, Any] = {}
    if max_pages > 0:
        kwargs["page_range"] = (1, max_pages)  # docling page_range is 1-based inclusive
    t0 = time.perf_counter()
    result = converter.convert(pdf, **kwargs)
    elapsed = time.perf_counter() - t0
    doc = result.document
    np_attr = getattr(doc, "num_pages", None)
    if callable(np_attr):
        n_pages = int(np_attr())
    elif isinstance(np_attr, int):
        n_pages = np_attr
    else:
        n_pages = len(getattr(doc, "pages", []) or [1])
    conf = getattr(result, "confidence", None)
    mean_grade = getattr(conf, "mean_grade", None) if conf is not None else None
    md_len = len(doc.export_to_markdown() or "")
    return {
        "elapsed_s": round(elapsed, 3),
        "pages": int(n_pages),
        "pages_per_sec": round(n_pages / elapsed, 3) if elapsed > 0 else 0,
        "mean_grade": str(mean_grade) if mean_grade is not None else None,
        "markdown_chars": md_len,
    }


def _convert_chunked(converter: Any, pdf: str, chunk_size: int) -> dict[str, Any]:
    """Convert a large PDF in page-range chunks to bound memory (O(chunk) not O(doc)).

    Each ``convert(page_range=...)`` returns a fresh result; we discard it
    (``del`` + ``gc.collect``) before the next chunk so peak memory ≈ one chunk,
    not the whole doc. Reuses one converter (models loaded once). This is the
    fix for the 552-page host-OOM: a single convert() holds all page rasters +
    the full DoclingDocument; chunking caps that to ``chunk_size`` pages at a time.
    """
    import gc

    elapsed_total = 0.0
    pages_done = 0
    chunks = 0
    start = 1
    while True:
        end = start + chunk_size - 1
        t0 = time.perf_counter()
        result = converter.convert(pdf, page_range=(start, end))
        dt = time.perf_counter() - t0
        doc = result.document
        np_attr = getattr(doc, "num_pages", None)
        if callable(np_attr):
            n = int(np_attr())
        elif isinstance(np_attr, int):
            n = np_attr
        else:
            n = len(getattr(doc, "pages", []) or [1])
        if n == 0:
            break  # past last page
        md_len = len(doc.export_to_markdown() or "")
        elapsed_total += dt
        pages_done += n
        chunks += 1
        print(f"  chunk {chunks} [{start}-{start + n - 1}] {n}p: {dt:6.2f}s  md={md_len}")
        last = n < chunk_size
        del result, doc
        gc.collect()  # force free before next chunk → bounded peak memory
        if last:
            break
        start += chunk_size
    return {
        "elapsed_s": round(elapsed_total, 3),
        "pages": pages_done,
        "pages_per_sec": round(pages_done / elapsed_total, 3) if elapsed_total > 0 else 0,
        "chunks": chunks,
        "chunk_size": chunk_size,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="docling convert() throughput bench (v1.10.3 M0)")
    ap.add_argument("--pdf", required=True, help="PDF path (container path, e.g. /data/lake/wuhu_report.pdf)")
    ap.add_argument(
        "--mode",
        choices=["baseline", "threaded", "both"],
        default="both",
        help="baseline=v1.10.2 plain; threaded=P0 Threaded+batch; both=compare",
    )
    ap.add_argument("--max-pages", type=int, default=0, help="subset first N pages (0=all). quick iteration")
    ap.add_argument("--page-batch", type=int, default=64, help="settings.perf.page_batch_size (threaded)")
    ap.add_argument(
        "--ocr",
        choices=["onnx", "torch"],
        default="onnx",
        help="OCR backend: onnx=CPU (default, works), torch=GPU (R1: needs .pth bake/volume)",
    )
    ap.add_argument("--profile", action="store_true", help="enable profile_pipeline_timings (stage breakdown)")
    ap.add_argument(
        "--chunk-pages",
        type=int,
        default=0,
        help="chunked convert: split doc into N-page ranges (memory-bounded, O(chunk) not O(doc)). "
        "0=single convert. >0 implies threaded + loops page_range chunks (fixes 552p host-OOM)",
    )
    args = ap.parse_args()
    ocr_gpu = args.ocr == "torch"

    if args.profile:
        from docling.datamodel.settings import settings

        settings.debug.profile_pipeline_timings = True
        print("[profile] profile_pipeline_timings=True (stage timings will print to stderr)")

    print(f"[bench] pdf={args.pdf} mode={args.mode} max_pages={args.max_pages or 'all'}\n")
    out: dict[str, Any] = {"pdf": args.pdf, "max_pages": args.max_pages, "results": {}}

    try:
        # Chunked mode: page-range chunks to bound memory (fixes large-doc host-OOM).
        # Implies threaded; bypasses the single-convert baseline/both path.
        if args.chunk_pages > 0:
            cv = build_threaded_converter(args.page_batch, ocr_gpu=ocr_gpu)
            print(f"[chunked] chunk_size={args.chunk_pages}p (memory-bounded O(chunk))\n")
            r = _convert_chunked(cv, args.pdf, args.chunk_pages)
            out["results"]["threaded_chunked"] = r
            print(f"\n  TOTAL: {r['elapsed_s']:8.1f}s  {r['pages']:>4}p  "
                  f"{r['pages_per_sec']:>7.2f} p/s  ({r['chunks']} chunks)")
            print(f"\n[json] {json.dumps(out, ensure_ascii=False)}")
            return 0
        if args.mode in ("baseline", "both"):
            cv = build_baseline_converter(ocr_gpu=ocr_gpu)
            r = _convert_timed(cv, args.pdf, args.max_pages)
            out["results"]["baseline"] = r
            print(f"  baseline:   {r['elapsed_s']:>8.3f}s  {r['pages']:>4}p  "
                  f"{r['pages_per_sec']:>7.2f} p/s  grade={r['mean_grade']}")

        if args.mode in ("threaded", "both"):
            cv = build_threaded_converter(args.page_batch, ocr_gpu=ocr_gpu)
            r = _convert_timed(cv, args.pdf, args.max_pages)
            out["results"]["threaded"] = r
            print(f"  threaded:   {r['elapsed_s']:>8.3f}s  {r['pages']:>4}p  "
                  f"{r['pages_per_sec']:>7.2f} p/s  grade={r['mean_grade']}")

        if args.mode == "both" and "baseline" in out["results"] and "threaded" in out["results"]:
            b, t = out["results"]["baseline"], out["results"]["threaded"]
            if b["elapsed_s"] > 0 and t["elapsed_s"] > 0:
                speedup = b["elapsed_s"] / t["elapsed_s"]
                delta = (t["elapsed_s"] - b["elapsed_s"]) / b["elapsed_s"] * 100
                print(f"\n  [compare] threaded vs baseline: {speedup:.2f}x  "
                      f"({delta:+.1f}% time, {'✓ G1 met ≥40% drop' if delta <= -40 else '✗ G1 not met'})")
                out["speedup"] = round(speedup, 3)
                out["time_delta_pct"] = round(delta, 1)
    except Exception as e:  # noqa: BLE001 — surface R1/etc. loudly
        print(f"\n[FAIL] {type(e).__name__}: {e}", file=sys.stderr)
        out["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(out, ensure_ascii=False))
        return 1

    print(f"\n[json] {json.dumps(out, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
