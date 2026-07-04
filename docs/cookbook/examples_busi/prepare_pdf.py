#!/usr/bin/env python3
"""宿主预处理：PDF → pypdf 提取文本 → tiktoken 递归切块 → jsonl

为什么需要：arrow-lake:1.8.6 镜像未安装 kreuzberg（PDF 解析库），
lake.ingest_documents 对 PDF 会直接失败。这里在宿主用 pypdf 提取真实
PDF 文本（芜湖方案 552 页，文字层完整），切成 1024-token 块，产出 jsonl
供容器内 lake.ingest 摄入。内容是真实 PDF 文本，非模拟。

运行（宿主 .venv，有 pypdf + tiktoken + 代理）：
    TIKTOKEN_CACHE_DIR=/tmp/tikcache .venv/bin/python3 \\
        docs/cookbook/examples_busi/prepare_pdf.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pypdf
import tiktoken

EXAMPLES_BUSI = Path(__file__).resolve().parent  # docs/cookbook/examples_busi
COOKBOOK = EXAMPLES_BUSI.parent  # docs/cookbook
PDF = COOKBOOK / "datas" / "5.芜湖市城市生命线安全工程一期建设方案.pdf"
OUT = EXAMPLES_BUSI / "datas" / "wuhu_lifeline.jsonl"

CHUNK_TOKENS = 1024
OVERLAP_TOKENS = 128
SOURCE_NAME = "芜湖市城市生命线安全工程一期建设方案"


def extract_pages(pdf: Path) -> list[tuple[int, str]]:
    """逐页提取文本，返回 [(page_no, text), ...]。"""
    reader = pypdf.PdfReader(str(pdf))
    pages: list[tuple[int, str]] = []
    n = len(reader.pages)
    t0 = time.time()
    for i, page in enumerate(reader.pages):
        try:
            txt = page.extract_text() or ""
        except Exception as e:
            print(f"  [warn] page {i + 1} 提取失败: {e}", file=sys.stderr)
            txt = ""
        # 清理：去掉多余空白，保留段落结构
        txt = re.sub(r"[ \t]+", " ", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        pages.append((i + 1, txt))
        if (i + 1) % 100 == 0:
            print(f"  已提取 {i + 1}/{n} 页, 累计 {sum(len(t) for _, t in pages)} 字符 ({time.time() - t0:.0f}s)")
    print(f"  提取完成: {n} 页, {sum(len(t) for _, t in pages)} 字符, {time.time() - t0:.0f}s")
    return pages


def chunk_page(text: str, enc, max_tokens: int = CHUNK_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[str]:
    """页内递归切块：按段落/句号粗分，再按 token 累积成块。"""
    if not text.strip():
        return []
    # 按双换行 / 中文章节号 / 句号切粗段
    paras = re.split(r"\n\s*\n|(?<=[。！？；])\n?", text)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for p in paras:
        p = p.strip()
        if not p:
            continue
        pl = len(enc.encode(p))
        if pl > max_tokens:
            # 单段超长 → 按 token 硬切
            if cur:
                chunks.append("\n".join(cur))
                cur, cur_len = [], 0
            tokens = enc.encode(p)
            step = max_tokens - overlap
            for j in range(0, len(tokens), step):
                chunks.append(enc.decode(tokens[j:j + max_tokens]))
        elif cur_len + pl > max_tokens and cur:
            chunks.append("\n".join(cur))
            cur, cur_len = [p], pl
        else:
            cur.append(p)
            cur_len += pl
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


def main() -> None:
    if not PDF.exists():
        raise SystemExit(f"PDF 不存在: {PDF}")
    print("=" * 60)
    print("PDF 预处理 (pypdf + tiktoken 切块 → jsonl)")
    print(f"  PDF: {PDF.name} ({PDF.stat().st_size / 1024 / 1024:.1f} MB)")
    print("=" * 60)

    pages = extract_pages(PDF)
    enc = tiktoken.get_encoding("o200k_base")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    idx = 0
    for pno, text in pages:
        for ch in chunk_page(text, enc):
            idx += 1
            records.append({
                "id": f"wuhu_{idx:04d}",
                "source": SOURCE_NAME,
                "title": f"芜湖城市生命线方案-第{pno}页-块{idx}",
                "text_content": ch,
                "category": "城市生命线工程",
                "tags": ["芜湖", "城市生命线", "安全工程", f"page_{pno}"],
                "page": pno,
            })

    with OUT.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 统计
    lens = [len(enc.encode(r["text_content"])) for r in records]
    pages_covered = len({r["page"] for r in records})
    print(f"\n  产出: {len(records)} 块 → {OUT}")
    print(f"  覆盖页: {pages_covered}/{len(pages)}")
    print(f"  token: min={min(lens)} max={max(lens)} mean={sum(lens) // len(lens)}")
    print(f"  文件大小: {OUT.stat().st_size / 1024:.0f} KB")
    print("  [预处理完成]")


if __name__ == "__main__":
    main()
