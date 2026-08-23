"""v1.10.7 WP5 wiring audit (review H9): every ingest construction site in
_lake_ingest.py must go through the gate-wired factory so the quality gate
cannot silently lose coverage to a new ingest path.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_every_ingest_site_uses_gate_wired_factory():
    src = (ROOT / "arrow_lake/_lake_ingest.py").read_text()
    # Bare constructions are forbidden — the only Ingestor(self._get_storage(…
    # allowed is the factory's own (identified by its quality_gate= kwarg).
    bare = re.findall(r"Ingestor\(self\._get_storage\(\)(?!, quality_gate)", src)
    assert not bare, f"{len(bare)} ingest site(s) bypass the quality gate factory"

    wired = len(re.findall(r"self\._make_ingestor\(", src))
    # 11 call sites + 1 definition
    assert wired >= 11, f"expected >=11 wired sites, found {wired}"


def test_ingestor_honors_shadow_mode():
    src = (ROOT / "arrow_lake/ingest/ingestor.py").read_text()
    i = src.index("if self._quality_gate is not None:")
    block = src[i : i + 900]
    assert 'mode", "enforce") == "enforce' in block, "shadow/enforce swap missing in _write_table"


def test_config_defaults_shadow():
    from arrow_lake.config import ArrowLakeConfig

    cfg = ArrowLakeConfig()
    assert cfg.quality.gate_mode == "shadow"
    assert cfg.quality.min_quality_score == 0.0
