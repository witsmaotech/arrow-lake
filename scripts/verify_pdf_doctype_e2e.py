"""Real PDF → he(qwen3) → HugeGraph end-to-end with doc_type routing (v1.7.x).

Tests the full v1.7.x pipeline on a real document:
  PDF → text → chunks → KGBuilder (he backend, doc_type carried) → graph

Verifies:
  - doc_type flows ingest→chunk→extractor (DocTypeRouter.resolve called)
  - he (qwen3 via Ollama) extracts real entities from the PDF
  - entities land in a dedicated graph (double-write + typed labels)

Uses graph ``v17_pdf_e2e`` (created/cleared each run). Limits to first N chunks
for speed (qwen3 is slow per call).
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pyarrow as pa
from pypdf import PdfReader

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.builder import KGBuilder
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph.doc_type_router import DocTypeRouter
from arrow_lake.knowledge_graph.he_extractor import HyperExtractExtractor

PDF = "docs/cookbook/datas/papers/full_text/zh002_向量数据库技术选型与实践.pdf"
GRAPH = "v17_pdf_e2e_v2"
DOC_TYPE = "paper"  # explicit doc_type — would come from upload API in production
OLLAMA = os.getenv("OLLAMA_API_BASE", "http://10.100.93.100:11434/v1")
MODEL = os.getenv("HE_MODEL", "qwen3:30b-a3b")
MAX_CHUNKS = int(os.getenv("PDF_E2E_CHUNKS", "2"))
CHUNK_CHARS = 1200


def extract_pdf_chunks(path: str, limit: int) -> list[str]:
    reader = PdfReader(path)
    full = "\n".join((page.extract_text() or "") for page in reader.pages)
    full = " ".join(full.split())  # collapse whitespace
    chunks = [full[i : i + CHUNK_CHARS] for i in range(0, len(full), CHUNK_CHARS)]
    return [c for c in chunks if len(c) > 80][:limit]


class _LoggingRouter(DocTypeRouter):
    """DocTypeRouter that logs which template each doc_type resolves to."""

    def resolve(self, doc_type: str | None) -> str:  # type: ignore[override]
        tpl = super().resolve(doc_type)
        print(f"[router] doc_type={doc_type!r} -> template={tpl!r}")
        return tpl


async def main() -> None:
    print(f"[pdf] extracting chunks from {PDF} (limit {MAX_CHUNKS})")
    chunks = extract_pdf_chunks(PDF, MAX_CHUNKS)
    if not chunks:
        print("[pdf] no text extracted — abort")
        return
    print(f"[pdf] {len(chunks)} chunks, sample[0][:120]: {chunks[0][:120]!r}")

    cfg = HugeGraphConfig(
        enabled=True, host="localhost", port=8089, graph_name=GRAPH,
        build_batch_size=10,
        extractor_backend="he",
        he_model=MODEL,
        he_default_template="general/base_graph",
        he_doc_type_templates={"paper": "general/concept_graph"},
    )
    client = HugeGraphClient(cfg)
    existed = await client.graph_exists()
    if existed:
        await client.clear()
    else:
        await client.ensure_graph()
    print(f"[setup] graph {GRAPH!r} ready (pre-existed={existed})")

    llm_cfg = SimpleNamespace(model=MODEL, api_key="dummy", api_base=OLLAMA)
    router = _LoggingRouter(cfg.he_doc_type_templates, cfg.he_default_template)
    extractor = HyperExtractExtractor(llm_cfg, doc_type_router=router, model=MODEL)

    # Standalone extraction preview (proves doc_type routing + he works pre-build)
    print(f"\n[he] extracting entities with doc_type={DOC_TYPE!r} via {MODEL}")
    preview = await extractor.extract(chunks[0], chunk_id="preview", doc_type=DOC_TYPE)
    print(f"[he] chunk[0] entities={len(preview.entities)} relations={len(preview.relations)}")
    for e in preview.entities[:15]:
        print(f"     - {e.entity_type:14s} {e.name}")

    # Full build via KGBuilder (carries doc_type through chunk table)
    table = pa.table({
        "id": [f"c{i}" for i in range(len(chunks))],
        "content": chunks,
        "document_name": ["zh002_向量数据库技术选型与实践.pdf"] * len(chunks),
        "chunk_index": list(range(len(chunks))),
        "doc_type": [DOC_TYPE] * len(chunks),
    })
    builder = KGBuilder(client, extractor, cfg)
    task_id = await builder.build("pdf_e2e_ds", table)
    await builder.execute_build(task_id)
    task = builder.get_task_status(task_id)
    print(f"\n[build] status={task.status.value} entities={task.entity_count} "
          f"relations={task.relation_count} error={task.error}")

    # Verify graph contents (direct API, not gremlin — avoids default-g bug)
    g = f"http://localhost:8089/graphs/{GRAPH}/graph"
    import gzip
    import urllib.request
    import json

    def count(kind: str, label: str) -> int:
        url = f"{g}/{kind}?label={label}&limit=200"
        req = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return len(json.loads(raw).get(f"{kind}", []))
    print("\n[verify] graph contents:")
    for label in ("entity", "concept", "organization", "person", "location"):
        print(f"     vertex {label:14s} = {count('vertices', label)}")
    for label in ("references", "related_to", "belongs_to"):
        print(f"     edge   {label:14s} = {count('edges', label)}")

    await client.clear()
    print("\n[cleanup] graph cleared")
    print("✅ PDF doc_type e2e done" if task.entity_count > 0 else "⚠️  no entities extracted")


if __name__ == "__main__":
    asyncio.run(main())
