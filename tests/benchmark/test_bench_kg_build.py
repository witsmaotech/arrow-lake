"""Knowledge Graph build benchmarks — M4.

Benchmarks KG schema creation, vertex/edge insertion, and traversal queries.
Uses a mock HugeGraphClient to avoid external dependencies.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


class _MockHugeGraphClient:
    """In-memory mock of HugeGraphClient for benchmarking KG builder overhead."""

    def __init__(self) -> None:
        self._vertices: dict[str, list[dict]] = {}
        self._edges: list[dict] = []
        self._schema_created = False
        self._index_created = False

    async def execute_schema(self, schema_groovy: str) -> dict:
        self._schema_created = True
        return {"result": "ok"}

    async def ensure_schema(self, schema: dict[str, Any]) -> None:
        self._schema_created = True

    async def add_vertex(self, label: str, vertex: dict) -> dict:
        self._vertices.setdefault(label, []).append(vertex)
        return {"id": vertex.get("id", f"v{len(self._edges)}")}

    async def add_edge(self, label: str, src: str, tgt: str, props: dict | None = None) -> dict:
        self._edges.append({"label": label, "source": src, "target": tgt, "properties": props or {}})
        return {"id": f"e{len(self._edges)}"}

    async def add_vertices(self, vertices: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        for v in vertices:
            label = v.get("label", "vertex")
            props = v.get("properties", {})
            self._vertices.setdefault(label, []).append(props)
            ids.append(f"v{len(self._edges) + len(ids)}")
        return ids

    async def add_edges(self, edges: list[dict[str, Any]]) -> int:
        for e in edges:
            self._edges.append(e)
        return len(edges)

    async def create_vertex_index(self, label: str, field: str) -> dict:
        self._index_created = True
        return {"result": "ok"}

    async def gremlin_query(self, query: str) -> dict:
        # Simple mock: return empty results
        return {"result": []}


class _MockExtractedEntity:
    """Mock entity compatible with both dict-style and attribute access."""

    def __init__(self, name: str, entity_type: str, confidence: float = 0.9) -> None:
        self.name = name
        self.entity_type = entity_type
        self.confidence = confidence
        self._dict = {"name": name, "type": entity_type, "confidence": confidence}

    def __getitem__(self, key: str) -> Any:
        return self._dict[key]


class _MockExtractedRelation:
    """Mock relation compatible with both dict-style and attribute access."""

    def __init__(self, source: str, target: str, relation: str, confidence: float = 0.8) -> None:
        self.source = source
        self.target = target
        self.relation = relation
        self.properties = ()
        self.confidence = confidence
        self._dict = {"source": source, "target": target, "relation": relation, "confidence": confidence}

    def __getitem__(self, key: str) -> Any:
        return self._dict[key]


class _MockExtractionResult:
    """Mock extraction result compatible with both dict-style and attribute access."""

    def __init__(self, entities: list, relations: list) -> None:
        self.entities = tuple(entities)
        self.relations = tuple(relations)
        self.raw_text = ""
        self._dict = {"entities": entities, "relations": relations}

    def __getitem__(self, key: str) -> Any:
        return self._dict[key]


class _MockEntityExtractor:
    """Mock entity extractor that returns deterministic results."""

    def __init__(self, entities_per_chunk: int = 3, relations_per_chunk: int = 2) -> None:
        self._entities_per_chunk = entities_per_chunk
        self._relations_per_chunk = relations_per_chunk

    async def extract(self, text: str, **kwargs: Any) -> _MockExtractionResult:
        entities = [
            _MockExtractedEntity(name=f"Entity_{i}", entity_type="concept")
            for i in range(self._entities_per_chunk)
        ]
        relations = [
            _MockExtractedRelation(
                source="Entity_0", target=f"Entity_{i + 1}", relation="related_to",
            )
            for i in range(min(self._relations_per_chunk, self._entities_per_chunk - 1))
        ]
        return _MockExtractionResult(entities, relations)


def _make_chunks_table(n_chunks: int, chunk_size: int = 200) -> pa.Table:
    """Create a table of text chunks for KG construction."""
    return pa.table(
        {
            "chunk_id": [f"chunk_{i:04d}" for i in range(n_chunks)],
            "document_name": [f"doc_{i // 5}" for i in range(n_chunks)],
            "chunk_index": [i % 5 for i in range(n_chunks)],
            "text_content": [
                f"Chunk {i} content about machine learning, neural networks, "
                f"and data science concepts for benchmarking purposes." * (chunk_size // 80)
                for i in range(n_chunks)
            ],
        }
    )


@pytest.mark.benchmark
class TestKGBuildBenchmark:
    """Benchmark Knowledge Graph construction pipeline."""

    def test_kg_schema_creation(self) -> None:
        """Benchmark: KG schema creation overhead."""

        client = _MockHugeGraphClient()
        _MockEntityExtractor()
        from arrow_lake.config import HugeGraphConfig

        HugeGraphConfig()

        report = BenchmarkReport("kg_schema_creation")

        async def _run() -> None:
            # Re-create schema each iteration
            client._schema_created = False
            await client.execute_schema("mock_schema")

        import asyncio

        report.measure(
            "schema creation",
            lambda: asyncio.get_event_loop().run_until_complete(_run()),
            repeats=10,
            warmup=2,
        )
        report.print_summary()
        print(report.to_json())

    def test_kg_vertex_edge_insertion(self) -> None:
        """Benchmark: vertex and edge insertion throughput."""
        from arrow_lake.config import HugeGraphConfig

        client = _MockHugeGraphClient()
        extractor = _MockEntityExtractor(entities_per_chunk=3, relations_per_chunk=2)
        HugeGraphConfig()

        report = BenchmarkReport("kg_vertex_edge_insertion")
        n_chunks = 100

        import asyncio

        async def _run() -> None:
            client._vertices.clear()
            client._edges.clear()
            for i in range(n_chunks):
                chunk_text = f"Chunk {i} about machine learning concepts"
                result = await extractor.extract(chunk_text)
                for ent in result["entities"]:
                    await client.add_vertex("entity", {"name": ent["name"], "type": ent["type"]})
                for rel in result["relations"]:
                    await client.add_edge("related_to", rel["source"], rel["target"], {"relation": rel["relation"]})

        report.measure(
            f"insert {n_chunks} chunks (3 entities + 2 relations each)",
            lambda: asyncio.get_event_loop().run_until_complete(_run()),
            rows=n_chunks,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())

    def test_kg_full_build_overhead(self) -> None:
        """Benchmark: full KGBuilder.build() overhead (excluding LLM)."""
        from arrow_lake.config import HugeGraphConfig
        from arrow_lake.knowledge_graph.builder import KGBuilder

        client = _MockHugeGraphClient()
        extractor = _MockEntityExtractor()
        config = HugeGraphConfig()

        builder = KGBuilder(client=client, extractor=extractor, config=config)
        table = _make_chunks_table(50)

        report = BenchmarkReport("kg_full_build")

        import asyncio

        async def _run() -> None:
            client._vertices.clear()
            client._edges.clear()
            client._schema_created = False
            await builder.build("bench_kg", table)

        report.measure(
            "full build (50 chunks, mock LLM)",
            lambda: asyncio.get_event_loop().run_until_complete(_run()),
            rows=50,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())

    def test_kg_traversal_query(self) -> None:
        """Benchmark: traversal query on a built graph."""
        client = _MockHugeGraphClient()
        extractor = _MockEntityExtractor(entities_per_chunk=3, relations_per_chunk=2)

        # Pre-populate graph
        import asyncio

        async def _populate() -> None:
            await client.execute_schema("mock_schema")
            for i in range(200):
                result = await extractor.extract(f"Chunk {i}")
                for ent in result["entities"]:
                    await client.add_vertex("entity", {"name": ent["name"], "type": ent["type"]})
                for rel in result["relations"]:
                    await client.add_edge("related_to", rel["source"], rel["target"])

        asyncio.get_event_loop().run_until_complete(_populate())

        report = BenchmarkReport("kg_traversal")

        async def _query() -> None:
            await client.gremlin_query("g.V().has('type', 'concept').outE().limit(100)")

        report.measure(
            "traversal query (200 chunks graph)",
            lambda: asyncio.get_event_loop().run_until_complete(_query()),
            repeats=50,
            warmup=5,
        )
        report.print_summary()
        print(report.to_json())
