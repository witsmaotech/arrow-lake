"""Arrow Lake REST API package (v0.2.0).

Thin FastAPI layer that delegates all data operations to the Lake SDK.
Every endpoint maps 1:1 to a Lake SDK method.
"""

from arrow_lake.api.app import create_app

__all__ = ["create_app"]
