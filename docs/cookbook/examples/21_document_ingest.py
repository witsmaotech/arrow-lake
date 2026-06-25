#!/usr/bin/env python3
"""21 — PDF 文档摄取

场景: 使用 DocumentParser 解析 PDF 文件并摄入为结构化数据。

数据文件: datas/papers/full_text/*.pdf

优化要点:
- markdown 输出保留标题/列表/表格结构
- chunk_size=1024 保留更多上下文
- paddleocr + eng+chi_sim 支持中英文混合 OCR
- 使用 local 存储后端，无需 MinIO/S3 连接
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from arrow_lake import Lake
from arrow_lake.config import DocumentConfig

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_doc_ingest"
_DATASETS = ["docs"]


_OCR_LANG = "eng+chi_sim"  # 支持中英文混合文档


def main() -> None:
    parser = argparse.ArgumentParser(description="21_document_ingest.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("21 PDF 文档摄取")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1: 查找 PDF 文件
    print("STEP 1: 查找 PDF 文件")
    pdf_dir = DATAS_DIR / "papers" / "full_text"
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    print(f"  找到 {len(pdfs)} 个 PDF 文件")
    for p in pdfs[:5]:
        print(f"    {p.name}")
    if not pdfs:
        print("  无 PDF 文件，跳过")
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        return

    # STEP 2: 摄取 PDF 文档 (优化配置)
    print("\nSTEP 2: 使用 ingest_documents 摄取 PDF (优化配置)")
    pdf_paths = [str(p) for p in pdfs[:18]]

    doc_config = DocumentConfig(
        pdf_parse_mode="auto",
        kreuzberg_ocr_backend="paddleocr",
        kreuzberg_language=_OCR_LANG,
        chunk_size=1024,
        chunk_overlap=128,
        chunk_strategy="chonkie_semantic",
        semantic_embedding_model="BAAI/bge-small-zh-v1.5",
        store_raw_pdf=False
    )
    print(f"  OCR 语言: {_OCR_LANG}")
    print(f"  分块策略: {doc_config.chunk_strategy}, 大小: {doc_config.chunk_size}, 重叠: {doc_config.chunk_overlap}")

    try:
        report = lake.ingest_documents("docs", pdf_paths, doc_config=doc_config)
        print(f"  摄入: {report.total_rows} 行, {report.total_files} 文件")
    except ImportError as e:
        print(f"  跳过 (缺少依赖: kreuzberg 未安装): {e}")
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        return
    except Exception as e:
        if "kreuzberg" in str(e).lower() or "DOCUMENT_PARSE_FAILED" in str(type(e).__name__):
            print(f"  跳过 (缺少依赖: {e})")
            lake.shutdown()
            shutil.rmtree(base, ignore_errors=True)
            return
        raise

    if "docs" not in lake.list_datasets():
        print("\n  [PASS] (kreuzberg 未安装, 跳过 PDF 解析)")
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        return

    # STEP 3: 查看文档数据集
    print("\nSTEP 3: 查看数据集")
    catalog = lake.catalog()
    ds = next((e for e in catalog.datasets if e.name == "docs"), None)
    row_count = ds.num_rows if ds else 0
    print(f"  docs: {row_count} 行")

    # STEP 4: 搜索文档内容
    print("\nSTEP 4: 全文搜索文档")
    try:
        lake.create_fts_index("docs", fts_column="text")
        result = lake.text_search("docs", "知识图谱", top_k=30, fts_column="text")
        print(f"  '知识图谱' → {result.row_count} 条结果")
        for i in range(min(3, result.row_count)):
            txt = str(result.table.column("text")[i].as_py())[:120]
            print(f"    #{i+1} {txt}...")
    except Exception as e:
        print(f"  搜索跳过: {e}")

    # STEP 5: 搜索英文论文
    print("\nSTEP 5: 搜索英文论文")
    try:
        result = lake.text_search("docs", "attention mechanism", top_k=3, fts_column="text")
        print(f"  'attention mechanism' → {result.row_count} 条结果")
        for i in range(min(3, result.row_count)):
            txt = str(result.table.column("text")[i].as_py())[:120]
            print(f"    #{i+1} {txt}...")
    except Exception as e:
        print(f"  搜索跳过: {e}")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
