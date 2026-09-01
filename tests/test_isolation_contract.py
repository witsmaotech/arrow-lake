"""v1.11.5-W1 hermetic-suite storage contracts.

Pin the conftest storage anchor (tests/conftest.py): the repo-root ``.env``
deployment config sets ``ARROW_LAKE__STORAGE__BACKEND=minio`` with real
credentials, and pydantic-settings loads it into every bare
``ArrowLakeConfig()`` — including ``Lake.__init__``'s
``config or ArrowLakeConfig()`` fallback. With a remote backend,
``LanceStorageManager`` derives its connect URI from the S3 config and
**ignores ``base_uri`` entirely** (ingest/storage.py ``_connect_uri``), so
facade tests like ``Lake(base_uri=tmp_path)`` silently write to the shared
dev minio: rows accumulate across runs, "dataset exists" errors surface,
and the full-suite failure set drifts run to run.

These contracts fail the moment that leak comes back.
"""

from __future__ import annotations

from pathlib import Path


class TestHermeticStorageAnchor:
    """A bare config anywhere in the suite must resolve to LOCAL storage."""

    def test_bare_config_resolves_local(self) -> None:
        from arrow_lake.config import ArrowLakeConfig, StorageBackend

        assert ArrowLakeConfig().storage.backend is StorageBackend.LOCAL

    def test_lake_base_uri_is_honored(self, tmp_path: Path) -> None:
        """Explicit base_uri must address the local tmp dir, not an s3:// URI."""
        from arrow_lake import Lake

        lake = Lake(base_uri=str(tmp_path))
        uri = lake._get_storage().dataset_uri("isolation_canary")

        assert not uri.startswith("s3://"), (
            f"storage hijacked by remote backend config: {uri}"
        )
        assert uri == str(tmp_path / "isolation_canary.lance")
