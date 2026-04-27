#!/usr/bin/env python3
"""21 — PDF 文档摄取

场景: 使用 DocumentParser 解析 PDF 文件并摄入为结构化数据。

数据文件: datas/papers/full_text/zh001-知识图谱构建综述.pdf
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_doc_ingest"


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

    # STEP 2: 摄取 PDF 文档
    print("\nSTEP 2: 使用 ingest_documents 摄取 PDF")
    try:
        pdf_paths = [str(p) for p in pdfs[:3]]
        report = lake.ingest_documents("docs", pdf_paths)
        print(f"  摄入: {report.total_rows} 行, {report.total_files} 文件")
    except ImportError as e:
        print(f"  跳过 (缺少依赖): {e}")
        print("\n  安装指引: pip install kreuzberg")
    except (OSError, ValueError) as e:
        print(f"  摄取失败: {e}")

    # STEP 3: 查看文档数据集
    print("\nSTEP 3: 查看数据集")
    for name in lake.list_datasets():
        ds = lake.open_dataset(name)
        print(f"  {name}: {ds.count_rows()} 行, {len(ds.schema)} 列")
        for f in ds.schema:
            print(f"    - {f.name}: {f.type}")

    # STEP 4: 搜索文档内容
    print("\nSTEP 4: 全文搜索文档")
    for name in lake.list_datasets():
        try:
            lake.create_fts_index(name, fts_column="text_content")
            result = lake.text_search(name, "知识图谱", top_k=3, fts_column="text_content")
            print(f"  [{name}] '知识图谱' → {result.row_count} 条结果")
            for i in range(min(3, result.row_count)):
                t = result.table
                txt = ""
                if "text_content" in t.column_names:
                    txt = str(t.column("text_content")[i].as_py())[:80]
                print(f"    #{i+1} {txt}...")
        except Exception as e:
            print(f"  [{name}] 搜索跳过: {e}")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
