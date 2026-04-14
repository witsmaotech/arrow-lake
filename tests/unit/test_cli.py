"""Tests for Story 7.2 — CLI."""

from __future__ import annotations

from arrow_lake.cli import main
from click.testing import CliRunner


class TestCLIHelp:
    """Test CLI help and top-level commands."""

    def test_main_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Arrow Lake" in result.output

    def test_version_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version", "--help"])
        assert result.exit_code == 0
        assert "version" in result.output.lower()

    def test_ingest_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["ingest", "--help"])
        assert result.exit_code == 0
        assert "--source" in result.output
        assert "--table" in result.output

    def test_search_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["search", "--help"])
        assert result.exit_code == 0
        assert "--query" in result.output
        assert "--top-k" in result.output

    def test_status_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["status", "--help"])
        assert result.exit_code == 0
        assert "--base-uri" in result.output


class TestCLIVersion:
    """Test version command."""

    def test_version_prints_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "arrow-lake" in result.output
        assert "python" in result.output.lower()
        assert "daft" in result.output
        assert "ray" in result.output


class TestCLIStatus:
    """Test status command with temporary datasets."""

    def test_status_empty_lake(self, tmp_path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["status", "--base-uri", str(tmp_path)])
        assert result.exit_code == 0
        assert "No datasets" in result.output

    def test_status_with_dataset(self, tmp_path) -> None:
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table({"id": ["1", "2"], "text_content": ["hello", "world"]})
        storage.create_dataset("test_ds", table)

        runner = CliRunner()
        result = runner.invoke(main, ["status", "--base-uri", str(tmp_path)])
        assert result.exit_code == 0
        assert "test_ds" in result.output
        assert "2" in result.output


class TestCLIIngest:
    """Test ingest command."""

    def test_ingest_missing_source(self, tmp_path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "ingest",
                "--source",
                "/nonexistent/path.csv",
                "--table",
                "my_data",
                "--base-uri",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.output or "Error" in result.output

    def test_ingest_creates_dataset(self, tmp_path) -> None:

        # Create a source CSV
        source_csv = tmp_path / "source.csv"
        source_csv.write_text("id,text\n1,hello\n2,world\n")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "ingest",
                "--source",
                str(source_csv),
                "--table",
                "ingested",
                "--base-uri",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "Ingested" in result.output


class TestCLISearch:
    """Test search command."""

    def test_search_empty_dataset(self, tmp_path) -> None:
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table({"id": [], "text_content": []})
        storage.create_dataset("empty_ds", table)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["search", "--query", "test", "--table", "empty_ds", "--base-uri", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    def test_search_text_fallback(self, tmp_path) -> None:
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table(
            {
                "id": ["1", "2", "3"],
                "text_content": [
                    "machine learning basics",
                    "cooking recipes",
                    "advanced ML algorithms",
                ],
            }
        )
        storage.create_dataset("search_ds", table)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "search",
                "--query",
                "machine",
                "--table",
                "search_ds",
                "--top-k",
                "5",
                "--base-uri",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "Result" in result.output

    def test_search_with_vector_column(self, tmp_path) -> None:
        import numpy as np
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=str(tmp_path))
        dim = 16
        rng = np.random.RandomState(42)
        vectors = rng.randn(3, dim).astype(np.float32)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

        table = pa.table(
            {
                "id": ["1", "2", "3"],
                "text_content": ["doc about ML", "doc about cooking", "doc about ML advanced"],
                "vector": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
            }
        )
        storage.create_dataset("vec_ds", table)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "search",
                "--query",
                "ML",
                "--table",
                "vec_ds",
                "--top-k",
                "2",
                "--base-uri",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "Score" in result.output
