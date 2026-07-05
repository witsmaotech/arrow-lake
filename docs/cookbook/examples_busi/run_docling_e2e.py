#!/usr/bin/env python3
"""芜湖 PDF — docling 直摄端到端验收（P2）

验证 P2 docling 库内嵌直摄路径 lake.ingest_documents(pdf, doc_config=ocr_backend="docling")，
覆盖 standard / vlm(GraniteDocling) 流水线 × recursive / docling_hybrid 分块组合。

下游 embed/index/search 走已验证的 Lake facade（与 run_e2e.py 同模式）。RAG/KG 在上会话已验证，
本脚本聚焦"docling 直摄可用 + P2 新配置不崩 + 产出能喂嵌入"。

用法（容器内，docling 在镜像里）：
  docker run --rm --network host -e HTTPS_PROXY=http://127.0.0.1:7887 \
    -e ARROW_LAKE__LLM__PROVIDER=ollama -e ARROW_LAKE__LLM__MODEL=qwen2.5:14b \
    -e ARROW_LAKE__LLM__API_BASE=http://10.100.93.100:11434/v1 \
    -e ARROW_LAKE__LLM__API_KEY=ollama \
    -e BUSI_EXAMPLES_DIR=/cookbook/examples_busi \
    -v <repo>/docs/cookbook:/cookbook:ro \
    -v /tmp/busi_results:/results -w /cookbook/examples_busi arrow-lake:1.8.6 \
    .venv/bin/python run_docling_e2e.py [--max-pages N] [--pipeline standard|vlm] \
                                       [--chunk recursive|docling_hybrid] [--dataset NAME]

  --max-pages 0 = 全量 552（默认 12 先实测速度）。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from arrow_lake import Lake
from arrow_lake.config._enums import ChunkStrategy, DoclingPipelineType, OcrBackend
from arrow_lake.config.document import DocumentConfig

EXAMPLES_BUSI_DIR = Path(os.environ.get("BUSI_EXAMPLES_DIR", Path(__file__).resolve().parent))
COOKBOOK_DIR = EXAMPLES_BUSI_DIR.parent  # docs/cookbook（容器内 /cookbook）
PDF_PATH = Path(os.environ.get(
    "BUSI_PDF_PATH",
    COOKBOOK_DIR / "datas" / "5.芜湖市城市生命线安全工程一期建设方案.pdf",
))
RESULTS = Path(os.environ.get("BUSI_RESULTS_DIR", EXAMPLES_BUSI_DIR / "results"))
BASE_URI = "/tmp/al-docling"

# ingest_documents 产出的列名是 text（非 jsonl 摄入的 text_content）
TEXT_COL = "text"
EMBED_COL = "text_embedding"

SEARCH_KEYWORDS = ["城市生命线", "燃气安全", "桥梁监测"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(step: str, data: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"docling_{step}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="wuhu_docling")
    ap.add_argument("--max-pages", type=int, default=12, help="0=全量 552")
    ap.add_argument("--pipeline", default="standard")
    ap.add_argument("--chunk", default="recursive")
    ap.add_argument("--ocr", default="auto", help="docling OCR 引擎: auto/rapidocr/easyocr/tesseract/none（文字层 PDF 用 none 最快）")
    ap.add_argument("--ingest-only", action="store_true", help="只跑 STEP1 摄入（验 P2 路径+测速）")
    args = ap.parse_args()

    if not PDF_PATH.exists():
        print(f"[FATAL] PDF 不存在: {PDF_PATH}")
        return 2

    cfg = DocumentConfig(
        ocr_backend=OcrBackend.DOCLING,
        docling_pipeline_type=DoclingPipelineType(args.pipeline),
        docling_ocr_engine=args.ocr,
        chunk_strategy=ChunkStrategy(args.chunk),
        max_pages=args.max_pages,
        store_raw_pdf=False,
    )
    print(f"[cfg] backend=docling pipeline={args.pipeline} ocr={args.ocr} "
          f"chunk={args.chunk} max_pages={args.max_pages}")

    lake = Lake(base_uri=BASE_URI)

    # ---- STEP 1: docling 直摄 ----
    t0 = time.time()
    report = lake.ingest_documents(args.dataset, [str(PDF_PATH)], doc_config=cfg)
    ingest_s = time.time() - t0
    n_rows = report.total_rows
    pages = args.max_pages if args.max_pages > 0 else 552
    pps = pages / ingest_s if ingest_s else None
    print(f"[STEP1] docling 摄入 {n_rows} 行 / {ingest_s:.1f}s ({pps:.2f} 页/s)" if pps
          else f"[STEP1] docling 摄入 {n_rows} 行 / {ingest_s:.1f}s")

    # 抽样文本确认质量
    sample_text = ""
    try:
        tbl = lake.read_table(args.dataset)
        if tbl.num_rows > 0:
            sample_text = str(tbl.column(TEXT_COL)[0])[:200]
            print(f"[STEP1] sample: {sample_text[:80]}…")
    except Exception as e:
        print(f"[STEP1] sample read skipped: {e}")

    _save("step1_ingest", {
        "ts": _now(), "dataset": args.dataset, "rows": n_rows, "elapsed_s": round(ingest_s, 1),
        "pages": pages, "pages_per_sec": round(pps, 3) if pps else None,
        "pipeline": args.pipeline, "chunk": args.chunk, "backend": "docling",
        "sample": sample_text,
    })

    if args.ingest_only:
        print(f"[DONE] ingest-only rows={n_rows} pages/s={pps:.2f}" if pps
              else f"[DONE] ingest-only rows={n_rows}")
        return 0

    # ---- STEP 2: 嵌入 + 索引 ----
    t0 = time.time()
    n_emb = lake.embed_and_add(args.dataset, text_column=TEXT_COL, embedding_column=EMBED_COL)
    idx_v = idx_f = False
    try:
        lake.create_vector_index(args.dataset, vector_column=EMBED_COL, num_sub_vectors=32)
        idx_v = True
    except Exception as e:
        print(f"[STEP2] vector_index skipped: {e}")
    try:
        lake.create_fts_index(args.dataset, fts_column=TEXT_COL)
        idx_f = True
    except Exception as e:
        print(f"[STEP2] fts_index skipped: {e}")
    print(f"[STEP2] embed {n_emb} 行 + vector_idx={idx_v} fts_idx={idx_f} ({time.time()-t0:.1f}s)")

    # ---- STEP 3: 检索验证（text_search 不需预算向量，最简） ----
    search_out: dict[str, int] = {}
    for kw in SEARCH_KEYWORDS:
        try:
            r = lake.text_search(args.dataset, kw, top_k=3, fts_column=TEXT_COL)
            rc = len(r.rows) if hasattr(r, "rows") else len(r)
            search_out[kw] = rc
            print(f"[STEP3] text_search '{kw}': {rc} hits")
        except Exception as e:
            print(f"[STEP3] text_search '{kw}' 失败: {e}")
            search_out[kw] = -1

    _save("summary", {
        "ts": _now(), "dataset": args.dataset, "rows": n_rows, "ingest_s": round(ingest_s, 1),
        "pages_per_sec": round(pps, 3) if pps else None,
        "embedded": n_emb, "vector_index": idx_v, "fts_index": idx_f,
        "search_hits": search_out, "pipeline": args.pipeline, "chunk": args.chunk,
    })
    print(f"[DONE] rows={n_rows} pages/s={pps:.2f}" if pps else f"[DONE] rows={n_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
