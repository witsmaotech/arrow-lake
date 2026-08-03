"""v1.10.0 P5: _snapshot_template_into_dump (C1 query-path + H2 self-contained dump)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from arrow_lake.knowledge_graph.he_extractor import HyperExtractExtractor

_YAML = "language: [zh]\nname: t\ntype: graph\n"


def _bind():
    obj = SimpleNamespace()
    return obj


def test_snapshot_copies_yaml_and_patches_metadata(tmp_path):
    src = tmp_path / "src.yaml"
    src.write_text(_YAML, encoding="utf-8")
    ka_dir = tmp_path / "ka"
    ka_dir.mkdir()
    (ka_dir / "metadata.json").write_text(json.dumps({"template": "t"}), "utf-8")

    HyperExtractExtractor._snapshot_template_into_dump(_bind(), str(src), ka_dir)

    assert (ka_dir / "template.yaml").read_text("utf-8") == _YAML
    meta = json.loads((ka_dir / "metadata.json").read_text("utf-8"))
    # absolute path ending .yaml → _build_ka_for_query guard bypasses project-dir resolve (C1)
    assert meta["template"].endswith("template.yaml")
    assert "/" in meta["template"]


def test_snapshot_creates_metadata_when_absent(tmp_path):
    src = tmp_path / "s.yaml"
    src.write_text(_YAML, encoding="utf-8")
    ka_dir = tmp_path / "ka"
    ka_dir.mkdir()

    HyperExtractExtractor._snapshot_template_into_dump(_bind(), str(src), ka_dir)

    meta = json.loads((ka_dir / "metadata.json").read_text("utf-8"))
    assert meta["template"].endswith("template.yaml")


def test_snapshot_preset_path_is_noop(tmp_path):
    ka_dir = tmp_path / "ka"
    ka_dir.mkdir()
    HyperExtractExtractor._snapshot_template_into_dump(_bind(), "general/concept_graph", ka_dir)
    assert not (ka_dir / "template.yaml").exists()
    assert not (ka_dir / "metadata.json").exists()


def test_snapshot_missing_file_is_noop(tmp_path):
    ka_dir = tmp_path / "ka"
    ka_dir.mkdir()
    HyperExtractExtractor._snapshot_template_into_dump(_bind(), "/no/such/missing.yaml", ka_dir)
    assert not (ka_dir / "template.yaml").exists()


def test_snapshot_empty_is_noop(tmp_path):
    ka_dir = tmp_path / "ka"
    ka_dir.mkdir()
    HyperExtractExtractor._snapshot_template_into_dump(_bind(), "", ka_dir)
    assert not (ka_dir / "template.yaml").exists()
