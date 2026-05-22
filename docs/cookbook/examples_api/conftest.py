"""Shared API client for Arrow Lake Docker Compose integration tests.

Usage:
    from api_client import ArrowLakeClient

    client = ArrowLakeClient("http://localhost:8000", api_key="dev-api-key-for-local-testing-only")
    datasets = client.list_datasets()
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class ArrowLakeClient:
    """Lightweight REST client — zero external deps (stdlib only)."""

    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._token: str | None = None

    # -- helpers --

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if content_type:
            h["Content-Type"] = content_type
        return h

    def _request(self, method: str, path: str, body: Any = None, *, timeout: int = 30) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body else None
        ct = "application/json" if body else None
        req = Request(url, data=data, headers=self._headers(ct), method=method)
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                try:
                    return json.loads(raw) if raw else {"success": True, "status": resp.status}
                except json.JSONDecodeError:
                    return {"success": True, "status": resp.status, "body": raw[:200]}
        except HTTPError as e:
            raw = e.read().decode()
            try:
                return json.loads(raw)
            except Exception:
                return {"success": False, "status": e.code, "error": str(e), "body": raw[:200]}

    # -- upload (multipart proxy, fallback) --

    def upload_files(self, name: str, file_paths: list[str]) -> dict[str, Any]:
        """Upload files to MinIO via multipart/form-data (proxy through API).

        For better performance, prefer ``_upload_presign`` which bypasses the API.
        """
        boundary = uuid.uuid4().hex
        parts: list[bytes] = []
        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                return {"success": False, "error": f"File not found: {fp}"}
            with open(p, "rb") as f:
                data = f.read()
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            header = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files"; filename="{p.name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode()
            parts.append(header + data + b"\r\n")

        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        url = f"{self.base_url}/api/v1/datasets/{name}/upload"
        req = Request(
            url,
            data=body,
            headers={
                **self._headers(),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=120) as resp:
                raw = resp.read().decode()
                try:
                    return json.loads(raw) if raw else {"success": True}
                except json.JSONDecodeError:
                    return {"success": True, "status": resp.status}
        except HTTPError as e:
            raw = e.read().decode()
            try:
                return json.loads(raw)
            except Exception:
                return {"success": False, "status": e.code, "error": str(e)}

    # -- upload (presigned URL direct to MinIO) --

    def _upload_presign(self, name: str, file_paths: list[str]) -> dict[str, Any]:
        """Get presigned URLs and upload files directly to MinIO.

        Returns {"success": True, "blob_keys": [...]} or error dict.
        """
        filenames = [Path(fp).name for fp in file_paths]

        # 1. Get presigned PUT URLs
        presign_resp = self._request(
            "POST",
            f"/api/v1/datasets/{name}/upload/presign",
            {"filenames": filenames},
        )
        if not presign_resp.get("success") or not presign_resp.get("uploads"):
            return presign_resp

        # 2. Upload each file directly to MinIO via presigned URL
        blob_keys: list[str] = []
        for upload_info, fp in zip(presign_resp["uploads"], file_paths):
            p = Path(fp)
            if not p.exists():
                return {"success": False, "error": f"File not found: {fp}"}
            with open(p, "rb") as f:
                data = f.read()
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            put_req = Request(
                upload_info["upload_url"],
                data=data,
                headers={"Content-Type": ctype},
                method="PUT",
            )
            try:
                urlopen(put_req, timeout=120)
                blob_keys.append(upload_info["key"])
            except Exception as e:
                return {"success": False, "error": f"PUT failed for {p.name}: {e}"}

        return {"success": True, "blob_keys": blob_keys}

    def _ingest_via_upload(self, name: str, file_paths: list[str], endpoint: str) -> dict[str, Any]:
        """Upload files then ingest via blob_keys.

        Strategy: presigned URL direct upload → fallback to multipart proxy → fallback to file_paths.
        """
        # Try presigned URL direct upload (best performance)
        presign_resp = self._upload_presign(name, file_paths)
        if presign_resp.get("success") and presign_resp.get("blob_keys"):
            return self._request("POST", endpoint, {"blob_keys": presign_resp["blob_keys"]})

        # Fallback: multipart proxy upload
        upload_resp = self.upload_files(name, file_paths)
        if upload_resp.get("success") and upload_resp.get("blobs"):
            blob_keys = [b["key"] for b in upload_resp["blobs"]]
            return self._request("POST", endpoint, {"blob_keys": blob_keys})

        # Last resort: direct file_paths (works when API has local access)
        return self._request("POST", endpoint, {"file_paths": file_paths})

    # -- auth --

    def auth_token(self, username: str = "admin", password: str = "admin") -> dict:
        resp = self._request("POST", "/api/v1/auth/token",
                             {"username": username, "password": password})
        if resp.get("access_token"):
            self._token = resp["access_token"]
        return resp

    # -- health --

    def health(self) -> dict:
        return self._request("GET", "/health")

    def health_ready(self) -> dict:
        return self._request("GET", "/health/ready")

    # -- datasets --

    def list_datasets(self) -> dict:
        return self._request("GET", "/api/v1/datasets")

    def get_dataset(self, name: str) -> dict:
        return self._request("GET", f"/api/v1/datasets/{name}")

    def delete_dataset(self, name: str) -> dict:
        self._request("DELETE", f"/api/v1/datasets/{name}/upload/cleanup")
        return self._request("DELETE", f"/api/v1/datasets/{name}")

    # -- ingest (auto-upload for file paths) --

    def ingest_files(self, name: str, file_paths: list[str]) -> dict:
        return self._ingest_via_upload(name, file_paths, f"/api/v1/datasets/{name}/ingest")

    def ingest_http(self, name: str, urls: list[str]) -> dict:
        return self._request("POST", f"/api/v1/datasets/{name}/ingest/http",
                             {"urls": urls})

    def ingest_documents(self, name: str, pdf_paths: list[str]) -> dict:
        return self._ingest_via_upload(
            name, pdf_paths, f"/api/v1/datasets/{name}/ingest/documents",
        )

    def ingest_images(self, name: str, file_paths: list[str]) -> dict:
        return self._ingest_via_upload(
            name, file_paths, f"/api/v1/datasets/{name}/ingest/images",
        )

    def ingest_videos(self, name: str, file_paths: list[str]) -> dict:
        return self._ingest_via_upload(
            name, file_paths, f"/api/v1/datasets/{name}/ingest/videos",
        )

    def ingest_mixed(self, name: str, sources: list[dict]) -> dict:
        return self._request("POST", f"/api/v1/datasets/{name}/ingest/mixed",
                             {"sources": sources})

    # -- search --

    def search_vector(self, name: str, query_vector: list[float],
                      top_k: int = 10, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"query_vector": query_vector, "top_k": top_k, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/search/vector", body)

    def search_fts(self, name: str, query: str, top_k: int = 10, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"query": query, "top_k": top_k, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/search/fts", body)

    def search_hybrid(self, name: str, query_vector: list[float], query_text: str,
                      top_k: int = 10, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"query_vector": query_vector, "query_text": query_text,
                                "top_k": top_k, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/search/hybrid", body)

    def search_faceted(self, name: str, query: str, facets: list[str],
                       top_k: int = 10, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"query": query, "facets": facets, "top_k": top_k, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/search/faceted", body)

    def search_ensemble(self, name: str, query_vector: list[float], query_text: str,
                       top_k: int = 10, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"query_vector": query_vector, "query_text": query_text,
                                "top_k": top_k, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/search/ensemble", body)

    # -- index --

    def create_vector_index(self, name: str, vector_column: str = "text_embedding",
                            metric: str = "cosine", index_type: str = "IVF_PQ",
                            **kwargs: Any) -> dict:
        body: dict[str, Any] = {"vector_column": vector_column, "metric": metric,
                                "index_type": index_type, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/index/vector", body)

    def create_fts_index(self, name: str, fts_column: str = "text_content",
                         **kwargs: Any) -> dict:
        body: dict[str, Any] = {"fts_column": fts_column, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/index/fts", body)

    # -- query --

    def query_olap(self, name: str, sql: str, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"sql": sql, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/query/olap", body)

    def query_metadata(self, name: str, sql: str, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"sql": sql, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/query/metadata", body)

    def query_daft(self, name: str, **kwargs: Any) -> dict:
        return self._request("POST", f"/api/v1/datasets/{name}/query/daft", kwargs)

    # -- export --

    def export(self, name: str, format: str = "parquet", **kwargs: Any) -> dict:
        body: dict[str, Any] = {"format": format, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/export", body)

    def export_status(self, name: str, task_id: str) -> dict:
        return self._request("GET", f"/api/v1/datasets/{name}/export/{task_id}/status")

    def export_download(self, name: str, task_id: str) -> dict:
        return self._request("GET", f"/api/v1/datasets/{name}/export/{task_id}/download")

    # -- quality --

    def quality_report(self, name: str) -> dict:
        return self._request("GET", f"/api/v1/datasets/{name}/quality/report")

    def quality_filter(self, name: str, rules: list[dict], **kwargs: Any) -> dict:
        body: dict[str, Any] = {"rules": rules, **kwargs}
        return self._request("POST", f"/api/v1/datasets/{name}/quality/filter", body)

    def quality_deduplicate(self, name: str, **kwargs: Any) -> dict:
        return self._request("POST", f"/api/v1/datasets/{name}/quality/deduplicate", kwargs)

    # -- backup --

    def backup_create(self, datasets: list[str], **kwargs: Any) -> dict:
        body: dict[str, Any] = {"datasets": datasets, **kwargs}
        return self._request("POST", "/api/v1/backup/create", body)

    def backup_list(self) -> dict:
        return self._request("GET", "/api/v1/backup/list")

    def backup_restore(self, backup_id: str, **kwargs: Any) -> dict:
        return self._request("POST", "/api/v1/backup/restore", {"backup_id": backup_id, **kwargs})

    # -- embed --

    def embed_text(self, texts: list[str], model: str = "default",
                   **kwargs: Any) -> dict:
        body: dict[str, Any] = {"texts": texts, "model": model, **kwargs}
        return self._request("POST", "/api/v1/embed/text", body)

    def embed_image(self, image_paths: list[str], model: str = "clip",
                    **kwargs: Any) -> dict:
        body: dict[str, Any] = {"image_paths": image_paths, "model": model, **kwargs}
        return self._request("POST", "/api/v1/embed/image", body)

    # -- rag --

    def rag_query(self, question: str, dataset_name: str,
                  top_k: int = 5, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"question": question, "dataset_name": dataset_name,
                                "top_k": top_k, **kwargs}
        return self._request("POST", "/api/v1/rag/query", body)

    def rag_extract(self, dataset_name: str, entity_types: list[str] | None = None,
                    **kwargs: Any) -> dict:
        body: dict[str, Any] = {"dataset_name": dataset_name, **kwargs}
        if entity_types:
            body["entity_types"] = entity_types
        return self._request("POST", "/api/v1/rag/extract", body)

    def rag_templates(self) -> dict:
        return self._request("GET", "/api/v1/rag/templates")

    def rag_history(self, session_id: str) -> dict:
        return self._request("GET", f"/api/v1/rag/history/{session_id}")

    # -- knowledge graph --

    def kg_build(self, dataset_name: str, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"dataset_name": dataset_name, **kwargs}
        return self._request("POST", "/api/v1/kg/build", body, timeout=600)

    def kg_build_status(self, task_id: str) -> dict:
        return self._request("GET", f"/api/v1/kg/build/{task_id}/status")

    def kg_schema(self) -> dict:
        return self._request("GET", "/api/v1/kg/schema")

    def kg_query(self, gremlin: str, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"query": gremlin, **kwargs}
        return self._request("POST", "/api/v1/kg/query", body)

    def kg_graphrag(self, question: str, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"question": question, **kwargs}
        return self._request("POST", "/api/v1/kg/query/graphrag", body)

    def kg_neighbors(self, entity_id: str, **kwargs: Any) -> dict:
        return self._request("GET", f"/api/v1/kg/entities/{entity_id}/neighbors", kwargs)

    def kg_stats(self) -> dict:
        return self._request("GET", "/api/v1/kg/stats")

    def kg_delete_graph(self) -> dict:
        return self._request("DELETE", "/api/v1/kg/graph")

    # -- lineage --

    def lineage_record(self, dataset_name: str, operation: str,
                       inputs: list[str] | None = None,
                       outputs: list[str] | None = None,
                       **kwargs: Any) -> dict:
        body: dict[str, Any] = {"dataset_name": dataset_name, "operation": operation, **kwargs}
        if inputs:
            body["inputs"] = inputs
        if outputs:
            body["outputs"] = outputs
        return self._request("POST", "/api/v1/lineage/record", body)

    def lineage_history(self, dataset_name: str) -> dict:
        return self._request("GET", f"/api/v1/lineage/history/{dataset_name}")

    def lineage_query(self, sql: str, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"sql": sql, **kwargs}
        return self._request("POST", "/api/v1/lineage/query", body)

    # -- audit --

    def audit_record(self, dataset_name: str, action: str,
                     **kwargs: Any) -> dict:
        body: dict[str, Any] = {"dataset_name": dataset_name, "action": action, **kwargs}
        return self._request("POST", "/api/v1/audit/record", body)

    def audit_verify(self, audit_id: str) -> dict:
        return self._request("POST", "/api/v1/audit/verify", {"audit_id": audit_id})

    def audit_query(self, **kwargs: Any) -> dict:
        return self._request("GET", "/api/v1/audit/query", kwargs)

    def audit_export(self, dataset_name: str, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"dataset_name": dataset_name, **kwargs}
        return self._request("POST", "/api/v1/audit/export", body)

    # -- metadata (Gravitino) --

    def metadata_list_catalogs(self) -> dict:
        return self._request("GET", "/metadata/catalogs")

    def metadata_list_tables(self) -> dict:
        return self._request("GET", "/metadata/tables")

    def metadata_get_table(self, name: str) -> dict:
        return self._request("GET", f"/metadata/tables/{name}")

    def metadata_list_tags(self, table: str | None = None) -> dict:
        path = f"/metadata/tags?table={table}" if table else "/metadata/tags"
        return self._request("GET", path)

    def metadata_create_tag(self, name: str, comment: str = "") -> dict:
        from urllib.parse import quote
        body = json.dumps({"name": name, "comment": comment})
        return self._request("POST", f"/metadata/tags?body={quote(body)}")

    def metadata_list_policies(self) -> dict:
        return self._request("GET", "/metadata/policies")

    def metadata_create_retention_policy(self, name: str, days: int = 30) -> dict:
        from urllib.parse import quote
        body = json.dumps({"name": name, "days": days})
        return self._request("POST", f"/metadata/policies/retention?body={quote(body)}")

    def metadata_create_masking_policy(self, name: str, columns: list[str]) -> dict:
        from urllib.parse import quote
        body = json.dumps({"name": name, "columns": columns})
        return self._request("POST", f"/metadata/policies/masking?body={quote(body)}")

    def metadata_collect_statistics(self, table_name: str) -> dict:
        return self._request("POST", f"/metadata/statistics/{table_name}")

    def metadata_list_models(self) -> dict:
        return self._request("GET", "/metadata/models")

    def metadata_get_model_versions(self, model_name: str) -> dict:
        return self._request("GET", f"/metadata/models/{model_name}/versions")

    # -- metadata v1.4.2 --

    def metadata_enforce_policies(self, table: str | None = None, dry_run: bool = False) -> dict:
        path = "/metadata/policies/enforce?"
        if table:
            path += f"table={table}&"
        path += f"dry_run={'true' if dry_run else 'false'}"
        return self._request("POST", path)

    def metadata_get_lineage(self, table_name: str) -> dict:
        return self._request("GET", f"/metadata/lineage/{table_name}")

    # -- system --

    def version(self) -> dict:
        return self._request("GET", "/api/v1/version")

    def admin_users(self) -> dict:
        return self._request("GET", "/api/v1/admin/users")

    # -- helpers --

    def wait_for_export(self, name: str, task_id: str, timeout: int = 60) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            resp = self.export_status(name, task_id)
            if resp.get("status") in ("completed", "done", "success"):
                return resp
            if resp.get("success") is False and "error" in resp:
                return resp
            time.sleep(2)
        return {"success": False, "error": "TIMEOUT", "task_id": task_id}

    def _pass(self, label: str) -> None:
        print(f"  [PASS] {label}")

    def _fail(self, label: str, detail: str = "") -> None:
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def first_embedding(resp: dict) -> list | None:
    """Safely extract first embedding vector from an embed response.

    Handles both ``{"embeddings": [[...]]}`` and ``{"data": [[...]]}``
    response shapes without raising IndexError on empty lists.
    """
    vecs = resp.get("embeddings") or resp.get("data") or []
    if vecs and vecs[0]:
        return vecs[0]
    return None
