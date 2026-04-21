"""Knowledge graph module (M3).

Provides HugeGraph REST client, schema definition, entity extraction,
KG builder, and graph retrieval for GraphRAG.
"""

from arrow_lake.knowledge_graph.builder import KGBuilder, KGBuildStatus, KGBuildTask
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph.extractor import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from arrow_lake.knowledge_graph.retriever import GraphRetrievalResult, GraphTriplet, KGRetriever

__all__ = [
    "EntityExtractor",
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractionResult",
    "GraphRetrievalResult",
    "GraphTriplet",
    "HugeGraphClient",
    "KGBuildStatus",
    "KGBuildTask",
    "KGBuilder",
    "KGRetriever",
]
