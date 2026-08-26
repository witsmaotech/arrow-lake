"""Contract YAML parsing guards (P1-8) — see TestContractYamlGuards."""

from __future__ import annotations

import pytest

from arrow_lake.contract.schema import parse_contract




class TestContractYamlGuards:
    """P1-8 (review 2026-08-26): guarded loader for ADMIN-submitted YAML.

    Empirical note: PyYAML aliases are reference-shared (no billion-laughs
    amplification); the real vectors are deep nesting (RecursionError →
    500) and node-count blowups. Both must surface as ValueError (→422),
    and the SafeLoader constructor set must stay intact.
    """

    def test_deep_nesting_value_error(self) -> None:
        with pytest.raises(ValueError, match="nesting"):
            parse_contract("dataset: x\ntables: {a: " + "[" * 300 + "]" * 300 + "}")

    def test_python_object_tag_rejected(self) -> None:
        with pytest.raises(ValueError):
            parse_contract(
                "dataset: x\ntables: {a: !!python/object/apply:os.system [echo hi]}"
            )

    def test_aliases_still_parse(self) -> None:
        text = (
            "dataset: gas\n"
            "_anchor: &cols [{name: material, enum: [PE]}]\n"
            "tables:\n"
            "  segments:\n"
            "    columns: *cols\n"
        )
        contract = parse_contract(text)
        assert contract.tables["segments"].columns[0].name == "material"
