#!/usr/bin/env python3
"""E2E 全量端到端验证 — 真实 PDF/TXT 文件 + MinIO + OCR + Lance + DuckDB + RAG + GraphRAG.

完整链路:
  0. 智能识别文件类型 (文本PDF / 扫描PDF / TXT)，选择最佳分块策略
  1. 上传原始文件到 MinIO (BlobStore)
  2. 解析文件 (Kreuzberg / TurboOCR OCR) → 智能分块 → 写入 Lance dataset
  3. 通过 DuckDB SQL 全文检索 (LIKE)
  4. 构建 FTS 索引 → 全文搜索 (BM25)
  5. API 层检索 (模拟 POST /api/v1/datasets/{name}/search)
  6. RAG 问答 (Ollama/OpenAI LLM + FTS 检索上下文)
  7. GraphRAG 问答 (知识图谱增强 RAG, 自动检测 HugeGraph 可用性)

前置条件:
  - uv sync --extra document --extra fts --extra rag
  - MinIO: docker compose --profile core up -d minio minio-init  (可选, --local 模式跳过)
  - LLM (RAG): Ollama 本地模型 或 OpenAI API
  - HugeGraph (GraphRAG): docker compose --profile kg up -d hugegraph  (可选, 不可用自动降级)

用法:
    # 本地模式 (无需 MinIO)
    uv run python examples/chunking/e2e_full_pipeline.py --local

    # 完整模式 (需要 MinIO)
    S3_ENDPOINT=http://localhost:9000 AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
      uv run python examples/chunking/e2e_full_pipeline.py

    # 启用 RAG + GraphRAG (需要 Ollama, HugeGraph 可选)
    uv run python examples/chunking/e2e_full_pipeline.py --local --rag --llm-base http://172.19.0.40:11434

    # 指定 HugeGraph 地址
    uv run python examples/chunking/e2e_full_pipeline.py --local --rag --kg-host 172.19.0.40 --kg-port 8080

    # 使用 OpenAI 做 RAG
    uv run python examples/chunking/e2e_full_pipeline.py --local --rag --llm-provider openai --llm-model gpt-4o-mini

    # 跳过 RAG (默认)
    uv run python examples/chunking/e2e_full_pipeline.py --local --no-rag
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(__file__).resolve().parent / "data"


def _safe_dataset_name(stem: str, prefix: str) -> str:
    """Convert a file stem to a valid dataset name (ASCII only)."""
    ascii_stem = re.sub(r"[^\w]", "_", stem.encode("ascii", "replace").decode("ascii"))
    ascii_stem = re.sub(r"_+", "_", ascii_stem).strip("_")
    short_hash = hashlib.md5(stem.encode()).hexdigest()[:6]
    return f"{prefix}_{ascii_stem}_{short_hash}"


# ===========================================================================
# OCR availability detection
# ===========================================================================


def detect_ocr_tools() -> dict[str, bool]:
    """Detect available OCR tools."""
    result: dict[str, bool] = {"kreuzberg": False, "turbo_ocr": False}

    try:
        import kreuzberg  # noqa: F401
        result["kreuzberg"] = True
    except ImportError:
        pass

    try:
        import httpx  # noqa: F401
        resp = httpx.get("http://localhost:8002/health", timeout=3.0)
        result["turbo_ocr"] = resp.status_code == 200
    except Exception:
        pass

    return result


# ===========================================================================
# File type detection + strategy recommendation
# ===========================================================================


@dataclass
class FileInfo:
    path: Path
    category: str
    strategy: str
    file_size_kb: float
    is_scanned_pdf: bool = False
    is_txt: bool = False


def analyze_file(filepath: Path) -> FileInfo:
    """Analyze a file and recommend chunking strategy."""
    from kreuzberg import ExtractionConfig, extract_file_sync

    name = filepath.name.lower()
    file_size_kb = filepath.stat().st_size / 1024.0
    is_txt = filepath.suffix.lower() == ".txt"

    # --- TXT files: read first 500 chars for category detection ---
    first_text = ""
    if is_txt:
        first_text = filepath.read_text(encoding="utf-8", errors="replace")[:500]

    # --- Detect if scanned PDF (no extractable text) ---
    is_scanned = False
    if filepath.suffix.lower() == ".pdf":
        try:
            result = extract_file_sync(str(filepath))
            full_content = result.content or ""
            first_text = full_content[:500]
            if len(full_content.strip()) < 200:
                is_scanned = True
        except Exception:
            is_scanned = True

    # --- Category detection ---
    category = "general"
    name_keywords = {
        "report": ["可行性", "报告", "report", "分析", "方案", "规划", "工程"],
        "literature": ["小说", "文学", "故事", "平凡", "世界", "活着", "literature"],
        "guide": ["手册", "guide", "指南", "规范", "标准", "大纲"],
        "academic": ["论文", "paper", "研究", "实验"],
    }

    if is_scanned:
        for cat, kws in name_keywords.items():
            if any(kw in name for kw in kws):
                category = cat
                break
        if category == "general":
            category = "scanned_document"
    else:
        for cat, kws in name_keywords.items():
            if any(kw in name for kw in kws):
                category = cat
                break
        # Content-based fallback for all non-scanned files (PDF + TXT)
        if category == "general" and first_text:
            if any(kw in first_text for kw in ["可行性研究", "工程", "投资", "建设"]):
                category = "report"
            elif any(kw in first_text for kw in ["小说", "章节", "第", "人物", "路遥", "少安"]):
                category = "literature"
            elif any(kw in first_text for kw in ["指南", "诊断", "治疗", "标准"]):
                category = "guide"

    # --- Strategy recommendation ---
    strategy_map = {
        "report": "recursive",
        "literature": "recursive",
        "guide": "paragraph",
        "academic": "recursive",
        "scanned_document": "recursive",
        "general": "recursive",
    }
    strategy = strategy_map.get(category, "recursive")

    return FileInfo(
        path=filepath,
        category=category,
        strategy=strategy,
        file_size_kb=file_size_kb,
        is_scanned_pdf=is_scanned,
        is_txt=is_txt,
    )


# ===========================================================================
# Step 1: Upload to MinIO
# ===========================================================================


def step_upload_minio(file_path: Path, config, dataset_name: str) -> str | None:
    """Upload file to MinIO, return blob_key. Returns None on failure."""
    from arrow_lake.storage.blob_store import BlobStoreManager

    try:
        blob_store = BlobStoreManager(config=config)
        ascii_stem = file_path.stem.encode("ascii", "replace").decode("ascii")
        safe_name = re.sub(r"[^\w.\-]", "_", ascii_stem) + file_path.suffix
        blob_key = f"documents/{dataset_name}/{safe_name}"
        file_bytes = file_path.read_bytes()
        content_type = "application/pdf" if file_path.suffix.lower() == ".pdf" else "text/plain"

        result = blob_store.upload(blob_key, file_bytes, content_type=content_type)
        print(f"  [1/7] MinIO 上传: {blob_key} ({len(file_bytes):,} bytes)")
        print(f"         ETag={result.etag}")
        return blob_key
    except Exception as exc:
        print(f"  [1/7] MinIO 不可用 (跳过): {exc}")
        return None


# ===========================================================================
# Step 2: Parse → Chunk → Lance
# ===========================================================================


def step_ingest_txt(
    txt_path: Path, dataset_name: str, lake, strategy: str, chunk_size: int,
) -> dict | None:
    """Ingest a TXT file: read → chunk → write to Lance."""
    from arrow_lake.config._enums import ChunkStrategy
    from arrow_lake.ingest.chunker import DocumentChunker
    import hashlib
    import pyarrow as pa

    text = txt_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        print("  [2/7] TXT 文件为空, 跳过")
        return None

    # Split into pages (every 2000 chars as a virtual page)
    page_size = 2000
    pages: list[tuple[int, str]] = []
    for i in range(0, len(text), page_size):
        page_text = text[i:i + page_size].strip()
        if page_text:
            pages.append((len(pages) + 1, page_text))

    if not pages:
        print("  [2/7] TXT 无有效内容, 跳过")
        return None

    chunker = DocumentChunker(
        strategy=ChunkStrategy(strategy),
        chunk_size=chunk_size,
        chunk_overlap=max(64, chunk_size // 8),
    )

    t0 = time.monotonic()
    chunks = chunker.chunk(pages)
    elapsed = time.monotonic() - t0

    if not chunks:
        print("  [2/7] TXT 分块结果为空, 跳过")
        return None

    doc_id = hashlib.sha256(str(txt_path.resolve()).encode()).hexdigest()[:16]
    table = pa.table({
        "text": [c.text for c in chunks],
        "page_number": [c.page_number for c in chunks],
        "chunk_index": [c.chunk_index for c in chunks],
        "document_id": [doc_id] * len(chunks),
        "blob_key": [""] * len(chunks),
    })

    lake.create_dataset(dataset_name, table)
    print(f"  [2/7] TXT→分块→Lance: {len(chunks)} chunks ({elapsed:.2f}s)")
    print(f"         Pages: {len(pages)}, Strategy: {strategy}")
    return {"total_rows": len(chunks), "total_files": 1, "elapsed_s": round(elapsed, 2)}


def step_ingest_pdf(
    pdf_path: Path, dataset_name: str, lake, strategy: str, chunk_size: int,
    parse_mode: str = "auto",
) -> dict | None:
    """Parse PDF (with OCR fallback) and write chunks to Lance."""
    from arrow_lake.config._enums import ChunkStrategy, OcrBackend, PdfParseMode
    from arrow_lake.config.document import DocumentConfig

    chunk_strategy = ChunkStrategy(strategy)
    doc_config = DocumentConfig(
        chunk_strategy=chunk_strategy,
        chunk_size=chunk_size,
        chunk_overlap=max(64, chunk_size // 8),
        store_raw_pdf=False,
        pdf_parse_mode=PdfParseMode(parse_mode),
        ocr_backend=OcrBackend.KREUZBERG,
        ocr_fallback_enabled=True,
    )

    t0 = time.monotonic()
    try:
        report = lake.ingest_documents(
            dataset_name=dataset_name,
            pdf_paths=[str(pdf_path)],
            doc_config=doc_config,
        )
    except Exception as exc:
        print(f"  [2/7] 文档解析失败: {exc}")
        return None

    elapsed = time.monotonic() - t0
    print(f"  [2/7] 解析→分块→Lance: {report.total_rows} chunks ({elapsed:.2f}s)")
    for src in report.sources:
        print(f"         {src.path}: {src.row_count} chunks")
    return {
        "total_rows": report.total_rows,
        "total_files": report.total_files,
        "elapsed_s": round(elapsed, 2),
    }


# ===========================================================================
# Step 3: DuckDB SQL search
# ===========================================================================


def step_duckdb_search(lake, dataset_name: str, keyword: str) -> int:
    """DuckDB SQL LIKE search. Return result count."""
    print(f'  [3/7] DuckDB SQL: "{keyword}"')
    try:
        result = lake.olap_query(
            dataset_name=dataset_name,
            sql=f"SELECT text, page_number, document_id FROM {dataset_name} WHERE text LIKE '%{keyword}%' LIMIT 10",
        )
        n = result.table.num_rows
        print(f"         -> {n} results")
        for i in range(min(n, 3)):
            preview = result.table.column("text")[i].as_py()[:100]
            page = result.table.column("page_number")[i].as_py()
            print(f"         - [p{page}] {preview}...")
        return n
    except Exception as exc:
        print(f"         -> 失败: {exc}")
        return 0


# ===========================================================================
# Step 4: FTS index + search
# ===========================================================================


def step_fts_search(lake, dataset_name: str, query: str) -> int:
    """Build FTS index and search. Return result count."""
    print(f'  [4/7] FTS 全文检索: "{query}"')
    try:
        t0 = time.monotonic()
        lake.create_fts_index(dataset_name, fts_column="text", replace=True)
        idx_time = time.monotonic() - t0

        result = lake.text_search(dataset_name=dataset_name, query=query, top_k=5, fts_column="text")
        n = result.table.num_rows
        print(f"         索引: {idx_time:.2f}s, 结果: {n}")
        if n > 0 and "_score" in result.table.column_names:
            scores = result.table.column("_score").to_pylist()
            texts = result.table.column("text").to_pylist() if "text" in result.table.column_names else []
            for i in range(min(n, 5)):
                preview = (texts[i][:120] + "...") if texts else ""
                print(f"         [{i+1}] score={scores[i]:.4f} | {preview}")
        return n
    except Exception as exc:
        print(f"         -> 失败: {exc}")
        return 0


# ===========================================================================
# Step 5: API-style search (same code path as HTTP endpoint)
# ===========================================================================


def step_api_search(lake, dataset_name: str, query: str, top_k: int = 5) -> int:
    """Simulate API endpoint behavior."""
    print(f"  [5/7] API 检索 (模拟 HTTP 200): query=\"{query}\" top_k={top_k}")
    try:
        result = lake.text_search(dataset_name=dataset_name, query=query, top_k=top_k, fts_column="text")
        n = result.table.num_rows
        print(f"         -> HTTP 200, {n} results")
        if n > 0 and "_score" in result.table.column_names:
            scores = result.table.column("_score").to_pylist()
            texts = result.table.column("text").to_pylist() if "text" in result.table.column_names else []
            for i in range(min(n, 5)):
                preview = (texts[i][:120] + "...") if texts else ""
                print(f"         [{i+1}] score={scores[i]:.4f} | {preview}")
        return n
    except Exception as exc:
        print(f"         -> 失败: {exc}")
        return 0


# ===========================================================================
# Step 6: RAG query (LLM + FTS retrieval)
# ===========================================================================


async def step_rag_query(lake, dataset_name: str, question: str, llm_config) -> dict | None:
    """RAG: retrieve via FTS, generate answer via LLM."""
    print(f'  [6/7] RAG 问答: "{question}"')
    try:
        t0 = time.monotonic()
        result = await lake.rag_query(
            question=question,
            dataset_name=dataset_name,
            top_k=5,
        )
        elapsed = time.monotonic() - t0

        print(f"         回答 ({elapsed:.2f}s):")
        answer_preview = result.answer[:300]
        print(f"         {answer_preview}")
        if len(result.answer) > 300:
            print(f"         ... ({len(result.answer)} chars total)")

        if result.citations:
            print(f"         引用: {len(result.citations)} chunks")
            for i, cite in enumerate(result.citations[:3]):
                excerpt = cite.text_excerpt
                preview = excerpt[:80] + "..." if len(excerpt) > 80 else excerpt
                print(f"           [{i+1}] score={cite.score:.4f} | {preview}")

        print(f"         retrieval={result.retrieval_count}, "
              f"context_tokens={result.context_tokens}, "
              f"latency={result.latency_ms:.0f}ms")
        if result.llm_usage:
            print(f"         llm_usage={result.llm_usage}")

        return {
            "answer_len": len(result.answer),
            "citations": len(result.citations),
            "latency_ms": result.latency_ms,
            "elapsed_s": round(elapsed, 2),
        }
    except Exception as exc:
        print(f"         -> 失败: {exc}")
        return None


# ===========================================================================
# Step 7: GraphRAG query (knowledge graph augmented RAG)
# ===========================================================================


async def step_graphrag_query(
    lake, dataset_name: str, question: str, llm_config,
) -> dict | None:
    """GraphRAG: knowledge graph augmented RAG.

    When HugeGraph is available, uses GraphRAGPipeline which extracts
    entities from the question, retrieves graph triplets, and merges
    them into the context window for enhanced generation.

    Gracefully degrades to vector RAG when KG is unavailable.
    """
    print(f'  [7/7] GraphRAG 问答: "{question}"')
    try:
        t0 = time.monotonic()
        result = await lake.rag_query(
            question=question,
            dataset_name=dataset_name,
            top_k=5,
        )
        elapsed = time.monotonic() - t0

        print(f"         回答 ({elapsed:.2f}s):")
        answer_preview = result.answer[:300]
        print(f"         {answer_preview}")
        if len(result.answer) > 300:
            print(f"         ... ({len(result.answer)} chars total)")

        if result.citations:
            print(f"         引用: {len(result.citations)} chunks")
            for i, cite in enumerate(result.citations[:3]):
                excerpt = cite.text_excerpt
                preview = excerpt[:80] + "..." if len(excerpt) > 80 else excerpt
                print(f"           [{i+1}] score={cite.score:.4f} | {preview}")

        print(f"         retrieval={result.retrieval_count}, "
              f"context_tokens={result.context_tokens}, "
              f"latency={result.latency_ms:.0f}ms")
        if result.llm_usage:
            print(f"         llm_usage={result.llm_usage}")

        return {
            "answer_len": len(result.answer),
            "citations": len(result.citations),
            "latency_ms": result.latency_ms,
            "elapsed_s": round(elapsed, 2),
        }
    except Exception as exc:
        print(f"         -> 失败: {exc}")
        return None


def show_schema(lake, dataset_name: str) -> None:
    """Print dataset schema."""
    try:
        ds = lake.read_dataset(dataset_name)
        schema = ds.schema
        print(f"    Schema:")
        for i in range(len(schema)):
            f = schema.field(i)
            print(f"      {f.name}: {f.type}")
        print(f"    Rows: {ds.num_rows}")
    except Exception as exc:
        print(f"    无法读取: {exc}")


# ===========================================================================
# Main
# ===========================================================================


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="E2E: MinIO -> OCR -> chunk -> Lance -> SQL -> FTS -> API -> RAG -> GraphRAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--files", nargs="+", default=None)
    parser.add_argument("--local", action="store_true", help="Local storage (no MinIO)")
    parser.add_argument("--strategy", default="auto", help="Chunk strategy (auto/recursive/paragraph)")
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--dataset", default="e2e_pipeline_test")
    parser.add_argument("--search", default=None, help="Custom search keyword")
    parser.add_argument("--parse-mode", default="auto", choices=["auto", "ocr", "text"],
                        help="PDF parse mode: auto (try text, fallback OCR), ocr (force OCR), text (text only, no OCR)")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--rag", action="store_true", default=None,
                        help="Enable RAG validation step (needs Ollama or OpenAI)")
    parser.add_argument("--no-rag", action="store_true",
                        help="Explicitly disable RAG validation")
    parser.add_argument("--llm-provider", default="ollama",
                        choices=["ollama", "openai", "anthropic"],
                        help="LLM provider for RAG")
    parser.add_argument("--llm-base", default=None,
                        help="LLM API base URL (default: Ollama http://localhost:11434)")
    parser.add_argument("--llm-model", default="qwen3.5:9b",
                        help="LLM model name (qwen3.x needs --llm-max-tokens 4096+)")
    parser.add_argument("--llm-max-tokens", type=int, default=4096,
                        help="LLM max_tokens for response generation")
    parser.add_argument("--rag-question", default=None,
                        help="Custom RAG question (auto-generated per file if omitted)")
    parser.add_argument("--kg-host", default="localhost",
                        help="HugeGraph host (GraphRAG, auto-detected)")
    parser.add_argument("--kg-port", type=int, default=8091,
                        help="HugeGraph port (GraphRAG, auto-detected)")
    args = parser.parse_args()

    # --- OCR availability ---
    ocr_tools = detect_ocr_tools()
    print("=" * 80)
    print("OCR 工具检测")
    print("=" * 80)
    for tool, available in ocr_tools.items():
        status = "OK" if available else "NOT AVAILABLE"
        print(f"  {tool:<15} {status}")

    has_ocr = ocr_tools["kreuzberg"] or ocr_tools["turbo_ocr"]
    if not has_ocr:
        print("\n  [!] 需要 Kreuzberg 文档解析库:")
        print("      uv sync --extra document")
        print("      docker compose --profile ocr up -d turbo-ocr  (可选 TurboOCR, 需要 GPU)")

    # --- File discovery ---
    if args.files:
        file_paths = [Path(f) for f in args.files]
    else:
        file_paths = sorted(DATA_DIR.glob("*.pdf"))
        file_paths.extend(sorted(DATA_DIR.glob("*.txt")))

    if not file_paths:
        print("\nERROR: No files found. Place PDF/TXT in examples/chunking/data/")
        sys.exit(1)

    # --- Analyze all files ---
    file_infos: list[FileInfo] = []
    print(f"\n{'=' * 80}")
    print("文件分析")
    print("=" * 80)
    for p in file_paths:
        info = analyze_file(p)
        file_infos.append(info)
        scan_tag = " [SCANNED]" if info.is_scanned_pdf else ""
        txt_tag = " [TXT]" if info.is_txt else ""
        print(f"  {info.path.name:<55} {info.category:<20} {info.strategy:<12} "
              f"{info.file_size_kb:>7.0f}KB{scan_tag}{txt_tag}")

    scanned = [i for i in file_infos if i.is_scanned_pdf]
    if scanned and not has_ocr and args.parse_mode != "text":
        print(f"\n  [!] {len(scanned)} 个扫描版 PDF — 需要 Kreuzberg 才能解析")
        if args.parse_mode == "auto":
            print("      auto 模式下 Kreuzberg 将自动判断是否需要 OCR")

    # --- Config ---
    # NOTE: Lake always uses LOCAL backend because lancedb FTS only works
    # on the local filesystem.  MinIO upload (step 1) is a separate operation.
    from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig

    s3_config = None
    if not args.local:
        s3_config = StorageConfig(
            s3_endpoint=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
            s3_access_key=os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin"),
            s3_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin"),
            s3_bucket=os.environ.get("S3_BUCKET", "arrow-lake"),
        )

    config = ArrowLakeConfig()
    config.storage = StorageConfig(backend=StorageBackend.LOCAL)

    # --- RAG configuration ---
    enable_rag = args.rag if args.rag is not None else False
    if args.no_rag:
        enable_rag = False

    if enable_rag:
        from arrow_lake.config import LLMConfig, LLMProviderType, RAGConfig

        provider_map = {
            "ollama": LLMProviderType.OLLAMA,
            "openai": LLMProviderType.OPENAI,
            "anthropic": LLMProviderType.ANTHROPIC,
        }
        provider = provider_map[args.llm_provider]

        # Default base URLs (Ollama needs /v1 suffix for OpenAI compat)
        if args.llm_base:
            api_base = args.llm_base.rstrip("/")
            if provider == LLMProviderType.OLLAMA and not api_base.endswith("/v1"):
                api_base += "/v1"
        elif provider == LLMProviderType.OLLAMA:
            api_base = "http://localhost:11434/v1"
        elif provider == LLMProviderType.OPENAI:
            api_base = "https://api.openai.com/v1"
        else:
            api_base = ""

        config.llm = LLMConfig(
            provider=provider,
            model=args.llm_model,
            api_base=api_base,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            temperature=0.3,
            max_tokens=args.llm_max_tokens,
            timeout_seconds=120.0,
        )
        config.rag = RAGConfig(
            enabled=True,
            default_top_k=5,
            max_context_chunks=10,
            enable_citations=True,
        )

        # Test LLM connectivity
        print(f"\n{'=' * 80}")
        print("RAG / LLM 检测")
        print("=" * 80)
        try:
            import httpx as _httpx
            if provider == LLMProviderType.OLLAMA:
                health_url = f"{api_base}/api/version"
            else:
                health_url = f"{api_base}/models"
            resp = _httpx.get(health_url, timeout=5.0)
            print(f"  provider:  {args.llm_provider}")
            print(f"  model:     {args.llm_model}")
            print(f"  base_url:  {api_base}")
            print(f"  status:    OK (HTTP {resp.status_code})")
        except Exception as exc:
            print(f"  provider:  {args.llm_provider}")
            print(f"  model:     {args.llm_model}")
            print(f"  base_url:  {api_base}")
            print(f"  status:    NOT REACHABLE: {exc}")
            enable_rag = False

    # --- HugeGraph / GraphRAG configuration ---
    enable_graphrag = False
    if enable_rag:
        from arrow_lake.config import HugeGraphConfig

        kg_host = args.kg_host
        kg_port = args.kg_port

        print(f"\n{'=' * 80}")
        print("知识图谱 (HugeGraph) 检测")
        print("=" * 80)
        try:
            import httpx as _httpx
            kg_resp = _httpx.get(f"http://{kg_host}:{kg_port}", timeout=3.0)
            print(f"  host:     {kg_host}:{kg_port}")
            print(f"  status:   OK (HTTP {kg_resp.status_code})")
            enable_graphrag = True
        except Exception as exc:
            print(f"  host:     {kg_host}:{kg_port}")
            print(f"  status:   NOT REACHABLE: {exc}")
            print(f"  -> GraphRAG 将降级为普通 RAG")

        if enable_graphrag:
            config.hugegraph = HugeGraphConfig(
                enabled=False,
                host=kg_host,
                port=kg_port,
                graph_name=f"e2e_{args.dataset}",
            )

    # --- Cleanup old test data ---
    test_dir = Path(f"./data/{args.dataset}")
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)

    from arrow_lake import Lake

    lake = Lake(base_uri=f"./data/{args.dataset}", config=config)

    # --- Process each file ---
    file_results: list[dict] = []

    for info in file_infos:
        print(f"\n{'─' * 80}")
        print(f">>> {info.path.name} ({info.file_size_kb:.1f} KB) "
              f"[{info.category}] [strategy={info.strategy}]")

        ds_name = _safe_dataset_name(info.path.stem, args.dataset)

        # 1. MinIO upload
        if not args.local and s3_config is not None:
            step_upload_minio(info.path, s3_config, ds_name)
        else:
            print("  [1/7] MinIO: 跳过 (--local 模式)")

        # 2. Ingest
        if info.is_txt:
            stats = step_ingest_txt(info.path, ds_name, lake,
                                    strategy=info.strategy, chunk_size=args.chunk_size)
        else:
            stats = step_ingest_pdf(info.path, ds_name, lake,
                                    strategy=info.strategy, chunk_size=args.chunk_size,
                                    parse_mode=args.parse_mode)

        if stats is not None:
            show_schema(lake, ds_name)
        else:
            file_results.append({
                "file": info.path.name, "ok": False,
                "detail": "ingest failed",
            })
            continue

        # Generate search queries
        search_kw = args.search
        if not search_kw:
            kw_map = {
                "report": ["投资", "建设", "工程"],
                "literature": ["少安", "少平", "世界"],
                "guide": ["标准", "管理", "要求"],
                "academic": ["方法", "实验", "数据"],
                "scanned_document": ["数据"],
                "general": ["管理", "技术", "项目"],
            }
            search_kw = kw_map.get(info.category, ["数据"])[0]

        # 3. DuckDB SQL
        n_sql = step_duckdb_search(lake, ds_name, search_kw)

        # 4. FTS
        n_fts = step_fts_search(lake, ds_name, search_kw)

        # 5. API
        n_api = step_api_search(lake, ds_name, search_kw)

        # 6. RAG
        rag_stats = None
        if enable_rag:
            rag_question = args.rag_question
            if not rag_question:
                rag_q_map = {
                    "report": f"这个项目的投资估算和建设周期分别是什么？",
                    "literature": f"介绍一下{info.path.stem}的主要人物和故事背景",
                    "guide": "这个文件的主要内容和管理要求是什么？",
                    "academic": "这个文件的研究方法和实验设计是什么？",
                    "scanned_document": "这份文件的主要内容是什么？",
                    "general": "这份文件的主要内容是什么？",
                }
                rag_question = rag_q_map.get(info.category, "这份文件的主要内容是什么？")
            rag_stats = await step_rag_query(lake, ds_name, rag_question, config.llm)

        # 7. GraphRAG (temporarily enable HugeGraph for this call only)
        graphrag_stats = None
        if enable_graphrag:
            config.hugegraph.enabled = True
            graphrag_stats = await step_graphrag_query(
                lake, ds_name, rag_question, config.llm,
            )
            config.hugegraph.enabled = False
        elif enable_rag:
            print("  [7/7] GraphRAG: 跳过 (HugeGraph 不可用)")

        # PASS criteria: ingest succeeded + at least one search method returned results
        all_ok = (n_sql > 0 or n_fts > 0) and stats is not None
        rag_detail = ""
        if rag_stats:
            rag_detail = f" RAG={rag_stats['answer_len']}chars {rag_stats['latency_ms']:.0f}ms"
        graphrag_detail = ""
        if graphrag_stats:
            graphrag_detail = f" GraphRAG={graphrag_stats['answer_len']}chars {graphrag_stats['latency_ms']:.0f}ms"
        elif enable_rag:
            graphrag_detail = " GraphRAG=N/A"
        file_results.append({
            "file": info.path.name,
            "ok": all_ok,
            "detail": (
                f"ingest={stats['total_rows']} chunks, "
                f"SQL={n_sql} FTS={n_fts} API={n_api}"
                f"{rag_detail}{graphrag_detail}"
            ),
            "sql": n_sql, "fts": n_fts, "api": n_api,
            "rag": rag_stats, "graphrag": graphrag_stats,
        })

    # --- Summary ---
    print(f"\n{'=' * 80}")
    print(f"Pipeline 验证总结  ({len(file_infos)} 文件)")
    print(f"{'=' * 80}")
    for r in file_results:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"  [{status}] {r['file']:<55} {r['detail']}")

    total_passed = sum(1 for r in file_results if r["ok"])
    print(f"\n结果: {total_passed}/{len(file_results)} 完全通过, "
          f"{len(file_results) - total_passed} 失败")

    if args.cleanup and test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"\n  已清理: {test_dir}")

    sys.exit(0 if total_passed == len(file_infos) else 1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
