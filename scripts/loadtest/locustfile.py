"""Locust load test for Arrow Lake REST API.

Usage:
    uv run locust -f scripts/loadtest/locustfile.py --host=http://localhost:8000

Set LOAD_TEST_API_KEY env var for authenticated tests.
"""

from __future__ import annotations

import os
import random
import uuid

from locust import HttpUser, between, task

API_KEY = os.environ.get("LOAD_TEST_API_KEY", "")


class ArrowLakeUser(HttpUser):
    """Simulates search and ingestion workloads against Arrow Lake API."""

    min_wait = 0.5
    max_wait = 2.0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if API_KEY:
            headers["X-API-Key"] = API_KEY
        return headers

    @task(4)
    def vector_search(self) -> None:
        """Vector similarity search."""
        dataset = "benchmark"
        payload = {
            "query_vector": [0.1] * 768,
            "top_k": 10,
            "dataset_name": dataset,
        }
        name = f"vector_search_{dataset}"
        self.client.post(
            f"/api/v2/search/vector",
            json=payload,
            headers=self._headers(),
            name=name,
            catch_response=True,
        )

    @task(3)
    def fts_search(self) -> None:
        """Full-text search."""
        payload = {
            "query": "test document search",
            "top_k": 10,
            "dataset_name": "benchmark",
        }
        self.client.post(
            "/api/v2/search/fts",
            json=payload,
            headers=self._headers(),
            name="fts_search",
            catch_response=True,
        )

    @task(2)
    def hybrid_search(self) -> None:
        """Hybrid (vector + BM25) search."""
        payload = {
            "query": "hybrid test query",
            "query_vector": [0.05] * 768,
            "top_k": 10,
            "dataset_name": "benchmark",
            "alpha": 0.5,
        }
        self.client.post(
            "/api/v2/search/hybrid",
            json=payload,
            headers=self._headers(),
            name="hybrid_search",
            catch_response=True,
        )

    @task(1)
    def ingest_document(self) -> None:
        """Simulate document ingestion."""
        payload = {
            "file_paths": [f"/tmp/loadtest_{uuid.uuid4().hex[:8]}.json"],
        }
        dataset = "loadtest"
        self.client.post(
            f"/api/v1/datasets/{dataset}/ingest",
            json=payload,
            headers=self._headers(),
            name="ingest_document",
            catch_response=True,
        )
