"""Arrow Lake query layer — Stories 3.7, 3.8, 3.9, 5.1, 5.2, 5.3, 5.4, 5.5, 7.6, 8.1."""

from arrow_lake.query.streaming import StreamingResult

__all__ = [
    "DuckDBSessionManager",
    "EnsembleSearchBridge",
    "EnsembleSearchResult",
    "ExportBridge",
    "ExportResult",
    "FacetCount",
    "FacetedSearchBridge",
    "FacetedSearchResult",
    "MetadataQueryResult",
    "MetadataSearchBridge",
    "OlapQueryResult",
    "OlapSearchBridge",
    "SessionPoolStats",
    "StreamingResult",
]


def __getattr__(name: str):
    """Lazy imports for query types to avoid circular imports."""
    _lazy_map = {
        "DuckDBSessionManager": ("arrow_lake.query.session_manager", "DuckDBSessionManager"),
        "EnsembleSearchBridge": ("arrow_lake.query.ensemble", "EnsembleSearchBridge"),
        "EnsembleSearchResult": ("arrow_lake.query.ensemble", "EnsembleSearchResult"),
        "ExportBridge": ("arrow_lake.query.export", "ExportBridge"),
        "ExportResult": ("arrow_lake.query.export", "ExportResult"),
        "FacetCount": ("arrow_lake.query.faceted", "FacetCount"),
        "FacetedSearchBridge": ("arrow_lake.query.faceted", "FacetedSearchBridge"),
        "FacetedSearchResult": ("arrow_lake.query.faceted", "FacetedSearchResult"),
        "MetadataQueryResult": ("arrow_lake.query.metadata", "MetadataQueryResult"),
        "MetadataSearchBridge": ("arrow_lake.query.metadata", "MetadataSearchBridge"),
        "OlapQueryResult": ("arrow_lake.query.olap", "OlapQueryResult"),
        "OlapSearchBridge": ("arrow_lake.query.olap", "OlapSearchBridge"),
        "SessionPoolStats": ("arrow_lake.query.session_manager", "SessionPoolStats"),
    }
    if name in _lazy_map:
        import importlib

        module_path, attr = _lazy_map[name]
        module = importlib.import_module(module_path)
        globals()[name] = getattr(module, attr)
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
