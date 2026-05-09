"""Tests for CLI — package structure and commands."""

from __future__ import annotations

from arrow_lake.cli import main
from click.testing import CliRunner


def _make_local_runner(tmp_path):
    """Create a CliRunner that forces LOCAL storage backend."""
    import os

    env = dict(os.environ)
    env["ARROW_LAKE__STORAGE__BACKEND"] = "local"
    env["ARROW_LAKE__STORAGE__S3_ACCESS_KEY"] = ""
    env["ARROW_LAKE__STORAGE__S3_SECRET_KEY"] = ""
    return CliRunner(env=env)


class TestCLIHelp:
    """Test CLI help and top-level commands."""

    def test_main_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Arrow Lake" in result.output
        assert "catalog" in result.output
        assert "ingest" in result.output
        assert "search" in result.output
        assert "query" in result.output
        assert "backup" in result.output
        assert "kg" in result.output
        assert "rag" in result.output

    def test_version_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version", "--help"])
        assert result.exit_code == 0
        assert "version" in result.output.lower()

    def test_ingest_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["ingest", "--help"])
        assert result.exit_code == 0
        assert "files" in result.output
        assert "http" in result.output

    def test_search_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["search", "--help"])
        assert result.exit_code == 0
        assert "vector" in result.output
        assert "fts" in result.output
        assert "hybrid" in result.output

    def test_catalog_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["catalog", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "info" in result.output
        assert "delete" in result.output

    def test_status_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["status", "--help"])
        assert result.exit_code == 0


class TestCLIVersion:
    """Test version command."""

    def test_version_prints_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "arrow-lake" in result.output
        assert "python" in result.output.lower()


class TestCLIStatus:
    """Test status command (alias for catalog list)."""

    def test_status_empty_lake(self, tmp_path) -> None:
        runner = _make_local_runner(tmp_path)
        result = runner.invoke(main, ["--base-uri", str(tmp_path), "status"])
        assert result.exit_code == 0
        assert "No datasets" in result.output

    def test_status_with_dataset(self, tmp_path) -> None:
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table({"id": ["1", "2"], "text_content": ["hello", "world"]})
        storage.create_dataset("test_ds", table)

        runner = _make_local_runner(tmp_path)
        result = runner.invoke(main, ["--base-uri", str(tmp_path), "status"])
        assert result.exit_code == 0
        assert "test_ds" in result.output


class TestCLICatalog:
    """Test catalog commands."""

    def test_catalog_list_empty(self, tmp_path) -> None:
        runner = _make_local_runner(tmp_path)
        result = runner.invoke(main, ["--base-uri", str(tmp_path), "catalog", "list"])
        assert result.exit_code == 0
        assert "No datasets" in result.output

    def test_catalog_info(self, tmp_path) -> None:
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table({"id": ["1", "2"], "text_content": ["hello", "world"]})
        storage.create_dataset("info_ds", table)

        runner = _make_local_runner(tmp_path)
        result = runner.invoke(main, ["--base-uri", str(tmp_path), "catalog", "info", "info_ds"])
        assert result.exit_code == 0
        assert "info_ds" in result.output
        assert "2" in result.output

    def test_catalog_delete_needs_confirmation(self, tmp_path) -> None:
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table({"id": ["1"], "text_content": ["hello"]})
        storage.create_dataset("del_ds", table)

        runner = _make_local_runner(tmp_path)
        runner.invoke(main, ["--base-uri", str(tmp_path), "catalog", "delete", "del_ds"], input="n\n")
        assert "del_ds" in runner.invoke(main, ["--base-uri", str(tmp_path), "catalog", "list"]).output


class TestCLIIngest:
    """Test ingest commands."""

    def test_ingest_files_missing_source(self, tmp_path) -> None:
        runner = _make_local_runner(tmp_path)
        result = runner.invoke(
            main,
            ["--base-uri", str(tmp_path), "ingest", "files", "my_data", "/nonexistent/path.csv"],
        )
        assert result.exit_code != 0

    def test_ingest_files_creates_dataset(self, tmp_path) -> None:
        source_csv = tmp_path / "source.csv"
        source_csv.write_text("id,text\n1,hello\n2,world\n")

        runner = _make_local_runner(tmp_path)
        result = runner.invoke(
            main,
            ["--base-uri", str(tmp_path), "ingest", "files", "ingested", str(source_csv)],
        )
        assert result.exit_code == 0


class TestCLISearch:
    """Test search commands (help only — actual search needs Lake instance)."""

    def test_search_vector_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["search", "vector", "--help"])
        assert result.exit_code == 0
        assert "--query" in result.output
        assert "--top-k" in result.output

    def test_search_fts_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["search", "fts", "--help"])
        assert result.exit_code == 0
        assert "--query" in result.output

    def test_search_hybrid_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["search", "hybrid", "--help"])
        assert result.exit_code == 0
        assert "--query" in result.output
