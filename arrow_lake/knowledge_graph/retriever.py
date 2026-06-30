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

            for neighbor in neighbors:
                n_label = neighbor.get("label", "")
                n_props = neighbor.get("properties", {})
                n_name = n_props.get("name", neighbor.get("id", ""))

                predicate = f"related_to_{n_label}"
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
