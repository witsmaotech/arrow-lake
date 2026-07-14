#!/usr/bin/env python3
"""examples_busi3 / 01 — 把「京东平台研发DDD实践总结.pdf」ingest 成 lake dataset `jd_ddd`。

幂等: jd_ddd 已存在则跳过 (除非 --force 重建)。
流程: pypdf 抽页文 → DocumentChunker(RECURSIVE) 切块 → create_dataset (pa.Table)。

注: 实际项目里 ingest 走 lake.ingest_documents (Kreuzberg/Docling 解析), 这里用
pypdf 直抽是因为本 PDF 是文本型 (非扫描)、且 host .venv 无 kreuzberg, 更快更稳。
doc_type 列写 "ddd" → kg_build 时 he 路由到项目领域模板 ddd_concept_graph.yaml。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

DS = os.environ.get("BUSI3_DS", "jd_ddd")
FORCE = "--force" in sys.argv
PDF = Path(os.environ.get("BUSI3_DIR", ".")) / "京东平台研发DDD实践总结.pdf"

t0 = time.time()


def log(m: str) -> None:
    print(f"[{time.time() - t0:>5.1f}s] {m}", flush=True)


def main() -> int:
    from arrow_lake import Lake

    lake = Lake()

    existing = lake.list_datasets()
    if DS in existing and not FORCE:
        ds = lake.open_dataset(DS)
        n = ds.search().to_arrow().num_rows
        log(f"jd_ddd 已存在 ({n} rows) — 跳过 ingest (用 --force 重建)")
        lake.shutdown()
        return 0

    if DS in existing:
        lake.delete_dataset(DS)
        log(f"deleted 旧 jd_ddd (--force)")

    if not PDF.is_file():
        log(f"✗ PDF 不存在: {PDF}")
        return 1

    # 1. pypdf 抽页文
    from pypdf import PdfReader
    reader = PdfReader(str(PDF))
    pages = []
    for i, p in enumerate(reader.pages, 1):
        txt = (p.extract_text() or "").strip()
        if txt:
            pages.append((i, txt))
    log(f"pypdf 抽取 {len(pages)} 页文本 ({sum(len(t) for _, t in pages)} chars)")

    # 2. DocumentChunker 切块
    from arrow_lake.ingest.chunker import ChunkStrategy, DocumentChunker
    chunker = DocumentChunker(strategy=ChunkStrategy.RECURSIVE, chunk_size=1000, chunk_overlap=100)
    chunks = chunker.chunk(pages)
    log(f"切块 → {len(chunks)} chunks (RECURSIVE 1000/100)")

    # 3. pa.Table → create_dataset
    import pyarrow as pa
    table = pa.table({
        "content": [c.text for c in chunks],
        "page_number": [c.page_number for c in chunks],
        "chunk_index": [c.chunk_index for c in chunks],
        "document_name": [DS] * len(chunks),
        "doc_type": ["ddd"] * len(chunks),
    })
    lake.create_dataset(DS, table)
    log(f"✓ create_dataset jd_ddd ({table.num_rows} rows)")
    lake.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
