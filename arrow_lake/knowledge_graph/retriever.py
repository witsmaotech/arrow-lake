"""KG Retriever for GraphRAG context injection.

Retrieves relevant subgraphs from HugeGraph and serializes them into
triplets suitable for injection into RAG prompts.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph._naming import graph_name_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphTriplet:
    """An immutable knowledge graph triplet (subject, predicate, object)."""

    subject: str
    predicate: str
    object_: str
    properties: tuple = ()


@dataclass(frozen=True)
class GraphRetrievalResult:
    """Result of a graph retrieval operation for RAG."""

    query_entities: tuple[str, ...]
    triplets: tuple[GraphTriplet, ...]
    traversal_depth: int
    vertex_count: int
    edge_count: int


class KGRetriever:
    """Retrieve relevant subgraphs from HugeGraph for RAG context injection.

    Given a question and extracted entities, finds matching vertices,
    expands neighbors via multi-hop traversal, and serializes the
    result into triplets.
    """

    def __init__(self, client: HugeGraphClient, config: HugeGraphConfig) -> None:
        self._client = client
        self._config = config
        self._snapshot_cache: dict[str, tuple[float, list[dict]]] = {}

    async def retrieve(
        self,
        question: str,
        *,
        extracted_entities: list[str] | None = None,
        traversal_depth: int | None = None,
        max_triplets: int = 50,
        dataset_name: str | None = None,
    ) -> GraphRetrievalResult:
        """Retrieve relevant subgraph triplets for a given question.

        Steps:
        1. If no entities extracted, return empty result.
        2. For each entity, locate the vertex via REST ``get_vertex``.
        3. For found vertices, run ``traverser_kneighbor`` to get neighbors.
        4. Build triplets from neighbor data.
        5. Truncate to ``max_triplets``.

        Args:
            dataset_name: Optional lake path — scopes retrieval to ``kg_{ds}``.
                When omitted, the configured default graph is used.
        """
        del question  # question is context for upstream logging / future use
        # v1.8.6: per-dataset graph isolation
        g = graph_name_for(dataset_name) if dataset_name else None

        entities = extracted_entities if extracted_entities is not None else []
        if not entities:
            return GraphRetrievalResult(
                query_entities=(),
                triplets=(),
                traversal_depth=0,
                vertex_count=0,
                edge_count=0,
            )

        depth = (
            traversal_depth
            if traversal_depth is not None
            else self._config.default_traversal_depth
        )
        depth = min(depth, self._config.max_traversal_depth)

        # v1.9.11 #2: entity snapshot for char-overlap fallback (name-miss due
        # to paraphrased entity names). Cached per graph with a short TTL.
        snapshot = await self._entity_snapshot(g)

        all_triplets: list[GraphTriplet] = []
        vertex_ids: list[str] = []

        async def _retrieve_entity(entity_name: str) -> None:
            vertex = None
            for label_prefix in ("3", "2", "1", "4"):
                vid = f"{label_prefix}:{entity_name}"
                try:
                    vertex = await self._client.get_vertex(vid, graph_name=g)
                    break
                except Exception:
                    continue

            if vertex is None:
                # v1.9.11 #2: char-overlap fallback on entity snapshot (query
                # entity name may differ from the canonical graph name).
                vertex = self._char_overlap_vertex(entity_name, snapshot)
                if vertex is None:
                    logger.debug("Entity not found in HugeGraph: %s", entity_name)
                    return

            vid = vertex.get("id", "")
            if not vid:
                return
            vertex_ids.append(vid)
            v_name = entity_name

            neighbors = await self._client.traverser_kneighbor(
                source=vid, depth=depth, graph_name=g
            )
            # v1.9.11 #1: recover edge relation_type — traverser_kneighbor
            # returns only neighbor vertices, so fetch OUT edges of the source
            # and build neighbor_id → relation_type (kneighbor depth-1 endpoints
            # are exactly the OUT edge targets). Falls back to related_to_{label}
            # when the edge lookup misses (consistent with kg.html semantics).
            edge_rel: dict[str, str] = {}
            try:
                edges_out = await self._client.get_vertex_edges(
                    vid, graph_name=g, direction="OUT", limit=500,
                )
                for e in edges_out:
                    nid = e.get("inV")
                    eprops = e.get("properties") or {}
                    rt = eprops.get("relation_type") or e.get("label")
                    if nid and rt:
                        edge_rel[str(nid)] = rt
            except Exception:
                pass

            for neighbor in neighbors:
                n_label = neighbor.get("label", "")
                n_props = neighbor.get("properties", {})
                n_id = str(neighbor.get("id", ""))
                n_name = n_props.get("name", neighbor.get("id", ""))

                predicate = edge_rel.get(n_id) or f"related_to_{n_label}"
                all_triplets.append(
                    GraphTriplet(
                        subject=v_name,
                        predicate=predicate,
                        object_=n_name,
                        properties=tuple(sorted(n_props.items())),
                    )
                )

        await asyncio.gather(*(
            _retrieve_entity(e) for e in entities
        ))

        truncated = tuple(all_triplets[:max_triplets])
        vertex_count = len(vertex_ids)
        edge_count = len(truncated)

        return GraphRetrievalResult(
            query_entities=tuple(entities),
            triplets=truncated,
            traversal_depth=depth,
            vertex_count=vertex_count,
            edge_count=edge_count,
        )

    async def _entity_snapshot(self, graph_name: str | None) -> list[dict]:
        """Cached entity-vertex snapshot for char-overlap fallback (v1.9.11 #2).

        Mirrors ``_lake_kg._cached_graph_snapshot`` but lives on the retriever
        (which only holds client+config). Short TTL so a fresh build is picked up.
        """
        import time
        key = graph_name or ""
        now = time.monotonic()
        cached = self._snapshot_cache.get(key)
        if cached and now - cached[0] < 60.0:
            return cached[1]
        try:
            vertices, _ = await self._client.get_graph_snapshot(
                graph_name=graph_name, label="entity", limit=10000,
            )
        except Exception:
            vertices = []
        self._snapshot_cache[key] = (now, vertices)
        return vertices

    @staticmethod
    def _char_overlap_vertex(name: str, snapshot: list[dict]) -> dict | None:
        """Best entity vertex by char-overlap: exact name win, else ≥60% of
        candidate name chars present in the query name (mirrors
        ``_lake_kg._retrieve_hg_entities`` L1116-1123). Returns None if no match.
        """
        if not name or not snapshot:
            return None
        nset = set(name)
        best: dict | None = None
        best_key: tuple[int, int] = (0, 0)
        for v in snapshot:
            props = v.get("properties") or {}
            cname = str(props.get("name") or "")
            if len(cname) < 2:
                continue
            if cname == name:
                return v
            matched = sum(1 for ch in cname if ch in nset)
            if matched >= len(cname) * 0.6 and matched > 0:
                key = (matched, len(cname))
                if key > best_key:
                    best_key = key
                    best = v
        return best

    def triplets_to_text(self, result: GraphRetrievalResult) -> str:
        """Render triplets as text for RAG context injection.

        Format: one triplet per line as ``subject --predicate--> object``.
        """
        if not result.triplets:
            return ""

        lines: list[str] = []
        for t in result.triplets:
            lines.append(f"{t.subject} --{t.predicate}--> {t.object_}")
        return "\n".join(lines)
