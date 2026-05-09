"""Tests for Arrow Lake CLI commands.

The CLI's ``_lake()`` factory (in ``arrow_lake/cli/__init__.py``) imports
``Lake`` lazily from the ``arrow_lake`` package on every call.  We mock
``arrow_lake.Lake`` so that *every* invocation of ``_lake()`` in any
submodule returns the same ``MagicMock`` instance.

Commands that do not touch ``_lake`` (embed, config init) are tested via
``--help`` existence checks only.

No real Lake instances, no disk I/O, no network -- fast unit-level tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.cli import main
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def mock_lake() -> tuple[MagicMock, MagicMock]:
    """Patch ``arrow_lake.Lake`` and return ``(lake_instance, MockLake)``.

    Because ``_lake()`` does ``from arrow_lake import Lake`` inside the
    function body on *every* call, patching at the package level catches
    all submodules regardless of where they imported ``_lake`` from.

    ``ArrowLakeConfig.from_yaml`` is also patched so commands accepting
    ``--config`` do not attempt to read a real YAML file.

    ``lake_instance`` is pre-populated with common return values; callers
    may override specific attributes before invoking a command.
    """
    with patch("arrow_lake.Lake") as MockLake, \
         patch("arrow_lake.ArrowLakeConfig"):
        lake = MagicMock()
        lake.list_datasets.return_value = ["ds1", "ds2"]
        lake.ingest.return_value = _make_report(rows_ingested=3, dataset_name="ds1")
        lake.ingest_http.return_value = _make_report(rows_ingested=1, dataset_name="ds1")
        lake.ingest_images.return_value = _make_report(rows_ingested=2, dataset_name="ds1")
        lake.ingest_documents.return_value = _make_report(rows_ingested=1, dataset_name="ds1")
        lake.ingest_videos.return_value = _make_report(rows_ingested=1, dataset_name="ds1")
        lake.export.return_value = None
        lake.olap_query.return_value = _make_arrow_result(
            {"category": ["ml"], "cnt": [10]},
        )
        lake.materialize.return_value = 42
        lake.create_vector_index.return_value = "ivf_pq_index"
        lake.create_fts_index.return_value = None
        lake.delete_dataset.return_value = None
        lake.deduplicate.return_value = _make_report(
            total_rows=100, removed_rows=5, kept_rows=95, strategy="exact", action="flag",
        )
        lake.quality_filter.return_value = _make_report(
            total_rows=100, passed_rows=90, filtered_rows=10, filters_applied="null_check",
        )
        MockLake.return_value = lake
        yield lake, MockLake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(**kwargs: Any) -> MagicMock:
    """Create a mock ingestion / dedup / quality report object."""
    report = MagicMock()
    for k, v in kwargs.items():
        setattr(report, k, v)
    return report


def _make_arrow_result(columns: dict[str, list[Any]]) -> MagicMock:
    """Create a mock PyArrow table suitable for ``olap_query`` results."""
    table = MagicMock()
    table.num_rows = len(next(iter(columns.values())))
    table.column_names = list(columns.keys())

    def _column(name: str) -> MagicMock:
        values = columns.get(name, [])
        col = MagicMock()
        # Each item needs ``.as_py()`` because Rich display calls it.
        wrapped = [MagicMock(as_py=lambda v=v: v) for v in values]
        col.__getitem__ = lambda self, idx: wrapped[idx]  # type: ignore[assignment]
        return col

    table.column = _column
    return table


def _invoke(
    runner: CliRunner,
    args: list[str],
    catch_exceptions: bool = False,
) -> Any:
    """Invoke the main CLI group with *args*."""
    return runner.invoke(main, args, catch_exceptions=catch_exceptions)


# ===================================================================
# Main group: top-level commands
# ===================================================================


class TestMainGroup:
    """Tests for the root ``arrow-lake`` group and its built-in commands."""

    def test_help_shows_subgroups(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["--help"])
        assert result.exit_code == 0
        for name in (
            "catalog", "ingest", "search", "index", "query",
            "export", "embed", "quality", "backup", "kg", "rag", "config",
        ):
            assert name in result.output

    def test_status_delegates_to_catalog_list(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["status"])
        assert result.exit_code == 0
        lake.list_datasets.assert_called()

    def test_version_command(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["version"])
        # version tries importlib.metadata -- may fail in test env but must not crash
        assert result.exit_code == 0


# ===================================================================
# Catalog group
# ===================================================================


class TestCatalogGroup:
    """Tests for ``arrow-lake catalog`` commands."""

    def test_catalog_list(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["catalog", "list"])
        assert result.exit_code == 0
        lake.list_datasets.assert_called()
        assert "ds1" in result.output
        assert "ds2" in result.output

    def test_catalog_list_json(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        result = _invoke(runner, ["catalog", "list", "--json"])
        assert result.exit_code == 0

    def test_catalog_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["catalog", "--help"])
        assert "list" in result.output
        assert "info" in result.output
        assert "delete" in result.output

    def test_catalog_delete_yes(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["catalog", "delete", "ds1", "--yes"])
        assert result.exit_code == 0
        lake.delete_dataset.assert_called_once_with("ds1")


# ===================================================================
# Ingest group
# ===================================================================


class TestIngestGroup:
    """Tests for ``arrow-lake ingest`` commands."""

    def test_ingest_files(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["ingest", "files", "ds1", "a.csv", "b.csv"])
        assert result.exit_code == 0
        lake.ingest.assert_called_once_with("ds1", ["a.csv", "b.csv"])

    def test_ingest_http(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["ingest", "http", "ds1", "https://example.com/data.json"])
        assert result.exit_code == 0
        lake.ingest_http.assert_called_once_with("ds1", ["https://example.com/data.json"])

    def test_ingest_images(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["ingest", "images", "ds1", "img1.png", "img2.jpg"])
        assert result.exit_code == 0
        lake.ingest_images.assert_called_once_with("ds1", ["img1.png", "img2.jpg"])

    def test_ingest_documents(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["ingest", "documents", "ds1", "doc.pdf"])
        assert result.exit_code == 0
        lake.ingest_documents.assert_called_once_with("ds1", ["doc.pdf"])

    def test_ingest_videos(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["ingest", "videos", "ds1", "vid.mp4"])
        assert result.exit_code == 0
        lake.ingest_videos.assert_called_once_with("ds1", ["vid.mp4"])

    def test_ingest_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["ingest", "--help"])
        for cmd in ("files", "http", "images", "documents", "videos"):
            assert cmd in result.output

    def test_ingest_failure_exits_1(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        lake.ingest.side_effect = RuntimeError("boom")
        result = _invoke(runner, ["ingest", "files", "ds1", "bad.csv"])
        assert result.exit_code == 1
        assert "boom" in result.output


# ===================================================================
# Search group
# ===================================================================


class TestSearchGroup:
    """Tests for ``arrow-lake search`` commands."""

    def test_search_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["search", "--help"])
        for cmd in ("vector", "fts", "hybrid"):
            assert cmd in result.output

    def test_search_fts(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        lake.text_search.return_value = MagicMock(
            row_count=0,
            table=_make_arrow_result({"id": [], "_score": []}),
        )
        result = _invoke(runner, ["search", "fts", "ds1", "--query", "hello"])
        assert result.exit_code == 0
        lake.text_search.assert_called_once()

    def test_search_fts_failure(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        lake.text_search.side_effect = RuntimeError("fts error")
        result = _invoke(runner, ["search", "fts", "ds1", "--query", "hello"])
        assert result.exit_code == 1
        assert "fts error" in result.output


# ===================================================================
# Index group
# ===================================================================


class TestIndexGroup:
    """Tests for ``arrow-lake index`` commands."""

    def test_index_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["index", "--help"])
        for cmd in ("vector", "fts"):
            assert cmd in result.output

    def test_index_vector(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["index", "vector", "ds1"])
        assert result.exit_code == 0
        lake.create_vector_index.assert_called_once()

    def test_index_fts(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["index", "fts", "ds1"])
        assert result.exit_code == 0
        lake.create_fts_index.assert_called_once()


# ===================================================================
# Query group
# ===================================================================


class TestQueryGroup:
    """Tests for ``arrow-lake query`` commands."""

    def test_query_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["query", "--help"])
        for cmd in ("sql", "materialize"):
            assert cmd in result.output

    def test_query_sql(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["query", "sql", "ds1", "--sql", "SELECT 1"])
        assert result.exit_code == 0
        lake.olap_query.assert_called_once()

    def test_query_materialize(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, [
            "query", "materialize", "ds1",
            "--sql", "SELECT * FROM ds1",
            "--name", "mv_test",
        ])
        assert result.exit_code == 0
        lake.materialize.assert_called_once()


# ===================================================================
# Export command
# ===================================================================


class TestExportCommand:
    """Tests for ``arrow-lake export`` command."""

    def test_export_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["export", "--help"])
        assert "dataset" in result.output
        assert "output" in result.output

    def test_export(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, ["export", "ds1", "--output", "/tmp/out.parquet"])
        assert result.exit_code == 0
        lake.export.assert_called_once_with("ds1", "/tmp/out.parquet", format=None, columns=None)

    def test_export_with_columns(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, [
            "export", "ds1", "--output", "/tmp/out.csv",
            "--format", "csv", "--columns", "id,name",
        ])
        assert result.exit_code == 0
        lake.export.assert_called_once_with("ds1", "/tmp/out.csv", format="csv", columns=["id", "name"])


# ===================================================================
# Quality group
# ===================================================================


class TestQualityGroup:
    """Tests for ``arrow-lake quality`` commands."""

    def test_quality_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["quality", "--help"])
        for cmd in ("dedup", "filter"):
            assert cmd in result.output

    def test_quality_dedup(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, [
            "quality", "dedup", "ds1",
            "--strategy", "exact", "--action", "flag",
        ])
        assert result.exit_code == 0
        lake.deduplicate.assert_called_once()

    def test_quality_filter(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        lake, _ = mock_lake
        result = _invoke(runner, [
            "quality", "filter", "ds1", "--filters", "null_check",
        ])
        assert result.exit_code == 0
        lake.quality_filter.assert_called_once()


# ===================================================================
# Backup group
# ===================================================================


class TestBackupGroup:
    """Tests for ``arrow-lake backup`` commands.

    Backup commands import ``BackupManager`` lazily and access
    ``lake._storage``. We mock ``BackupManager`` at import time.
    """

    def test_backup_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["backup", "--help"])
        for cmd in ("create", "list", "restore", "delete"):
            assert cmd in result.output

    def test_backup_list(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        mock_bm = MagicMock()
        mock_bm.list_backups.return_value = []
        with patch("arrow_lake.ops.backup.BackupManager", return_value=mock_bm):
            result = _invoke(runner, ["backup", "list"])
        assert result.exit_code == 0
        mock_bm.list_backups.assert_called_once()

    def test_backup_create(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        mock_info = MagicMock(backup_id="bk1", created_at="now", total_size=1024)
        mock_bm = MagicMock()
        mock_bm.create_backup.return_value = mock_info
        with patch("arrow_lake.ops.backup.BackupManager", return_value=mock_bm):
            result = _invoke(runner, ["backup", "create", "--datasets", "ds1"])
        assert result.exit_code == 0
        mock_bm.create_backup.assert_called_once_with(
            dataset_names=["ds1"], backup_id=None,
        )


# ===================================================================
# KG group (async commands)
# ===================================================================


class TestKgGroup:
    """Tests for ``arrow-lake kg`` commands (async via ``_run_async``).

    We mock ``_run_async`` so no actual event loop runs.
    """

    def test_kg_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["kg", "--help"])
        for cmd in ("build", "status", "stats", "query", "neighbors", "delete"):
            assert cmd in result.output

    def test_kg_stats(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        with patch("arrow_lake.cli.kg._run_async", return_value={"nodes": 10, "edges": 25}):
            result = _invoke(runner, ["kg", "stats"])
        assert result.exit_code == 0
        assert "10" in result.output

    def test_kg_build(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        with patch("arrow_lake.cli.kg._run_async", return_value="task-001"):
            result = _invoke(runner, ["kg", "build", "ds1"])
        assert result.exit_code == 0
        assert "task-001" in result.output


# ===================================================================
# RAG group (async commands)
# ===================================================================


class TestRagGroup:
    """Tests for ``arrow-lake rag`` commands (async via ``_run_async``)."""

    def test_rag_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["rag", "--help"])
        for cmd in ("query", "templates"):
            assert cmd in result.output

    def test_rag_query(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        mock_response = MagicMock(
            answer="The answer is 42.",
            citations=[],
            latency_ms=150,
            context_tokens=500,
        )
        with patch("arrow_lake.cli.rag._run_async", return_value=mock_response):
            result = _invoke(runner, ["rag", "query", "ds1", "What is the meaning?"])
        assert result.exit_code == 0
        assert "42" in result.output

    def test_rag_templates_no_registry(self, runner: CliRunner) -> None:
        """rag templates handles ImportError when PromptRegistry is missing."""
        result = _invoke(runner, ["rag", "templates"], catch_exceptions=False)
        # Either succeeds with templates listed or shows "not available"
        assert result.exit_code == 0 or "not available" in result.output


# ===================================================================
# Embed group (no _lake usage)
# ===================================================================


class TestEmbedGroup:
    """Tests for ``arrow-lake embed`` commands.

    Embed commands use ``LocalEmbeddingEncoder`` directly -- no ``_lake``.
    We only test that the commands exist and accept --help.
    """

    def test_embed_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["embed", "--help"])
        for cmd in ("text", "image"):
            assert cmd in result.output

    def test_embed_text_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["embed", "text", "--help"])
        assert "text" in result.output.lower() or "TEXT" in result.output
        assert "--model" in result.output

    def test_embed_image_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["embed", "image", "--help"])
        assert "--model" in result.output


# ===================================================================
# Config group
# ===================================================================


class TestConfigGroup:
    """Tests for ``arrow-lake config`` commands."""

    def test_config_help(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["config", "--help"])
        for cmd in ("show", "init"):
            assert cmd in result.output

    def test_config_show(self, runner: CliRunner) -> None:
        """config show creates ArrowLakeConfig directly -- test it exists."""
        result = _invoke(runner, ["config", "show"])
        assert result.exit_code == 0

    def test_config_init(self, runner: CliRunner, tmp_path: Any) -> None:
        """config init writes a YAML file."""
        output = str(tmp_path / "arrow-lake.yaml")
        result = _invoke(runner, ["config", "init", "--output", output])
        assert result.exit_code == 0
        import os

        assert os.path.exists(output)


# ===================================================================
# Integration-style: base-uri and config-path passthrough
# ===================================================================


class TestContextPassThrough:
    """Verify that ``--base-uri`` and ``--config`` reach the Lake constructor."""

    def test_base_uri_forwarded(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        _, MockLake = mock_lake
        _invoke(runner, ["--base-uri", "/tmp/test_lake", "catalog", "list"])
        MockLake.assert_called()
        _, kwargs = MockLake.call_args
        assert kwargs.get("base_uri") == "/tmp/test_lake"

    def test_config_path_forwarded(self, runner: CliRunner, mock_lake: tuple[MagicMock, MagicMock]) -> None:
        _, MockLake = mock_lake
        _invoke(runner, ["--config", "/etc/al.yaml", "catalog", "list"])
        MockLake.assert_called()
        # When config_path is set, _lake calls Lake(base_uri=..., config=<ArrowLakeConfig>)
        _, kwargs = MockLake.call_args
        assert kwargs.get("config") is not None
