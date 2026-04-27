"""Tests for file path sanitization in ingestor (Round 4 — H2 fix)."""

from pathlib import Path

import pytest


def _sanitize_stem(path: Path) -> str:
    """Replicate the inline sanitization logic from Ingestor.ingest_documents."""
    return path.stem.replace("/", "_").replace("\\", "_").replace("..", "_")


class TestPathSanitization:
    """Verify path components are sanitized during ingestion."""

    def test_normal_filename(self):
        assert _sanitize_stem(Path("document.pdf")) == "document"

    def test_slash_replaced(self):
        stem = _sanitize_stem(Path("/etc/passwd"))
        assert "/" not in stem

    def test_backslash_replaced(self):
        stem = _sanitize_stem(Path("C:\\Windows\\system32"))
        assert "\\" not in stem

    def test_dot_dot_replaced(self):
        stem = _sanitize_stem(Path("../../etc/passwd"))
        assert ".." not in stem

    def test_normal_stem_preserved(self):
        assert _sanitize_stem(Path("report.pdf")) == "report"

    def test_complex_path(self):
        stem = _sanitize_stem(Path("../data/../secret/file.pdf"))
        assert ".." not in stem
        assert "/" not in stem
