"""LLM-driven column enrichment for the data-prep workbench.

Two operations, both reading a text column, calling the configured LLM
provider concurrently, and persisting new column(s) via
``lake.add_columns_table`` (native Lance add, row-aligned, no full rewrite):

- ``label_column``: batch LLM labeling → one new string column.
- ``extract_fields``: batch structured extraction (JSON) → multiple string columns.

A provider may be injected (testing); otherwise it is built from the lake's
LLM config via ``rag.provider.create_llm_provider``. ``max_rows`` is a safety
cap — the request is rejected if the dataset exceeds it (alignment with
``add_columns_table`` requires labeling every row).

Intended to run inside ``TaskManager.run_background`` (native async support).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.rag.provider import LLMMessage, create_llm_provider

logger = structlog.get_logger(__name__)

_DEFAULT_CONCURRENCY = 8
_DEFAULT_MAX_ROWS = 5000


@dataclass
class EnrichReport:
    """Outcome of an enrichment pass."""

    operation: str
    dataset: str
    input_rows: int
    succeeded: int
    failed: int
    new_columns: list[str] = field(default_factory=list)
    sample: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "dataset": self.dataset,
            "input_rows": self.input_rows,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "new_columns": self.new_columns,
            "sample": self.sample,
        }


def _get_provider(lake: Any, provider: Any, model: str | None) -> Any:
    """Use an injected provider (tests) or build one from the lake LLM config."""
    if provider is not None:
        return provider
    config = getattr(lake, "_config", None)
    llm_config = getattr(config, "llm", None) if config is not None else None
    if llm_config is None:
        raise RuntimeError("lake has no LLM config and no provider was injected")
    if model:
        try:
            llm_config.model = model
        except Exception:  # noqa: BLE001 — best-effort override
            pass
    return create_llm_provider(llm_config)


def _check_size(n: int, max_rows: int) -> None:
    if max_rows and n > max_rows:
        raise ValueError(
            f"dataset has {n} rows > max_rows={max_rows}; filter the dataset first or raise the cap"
        )


async def _map_with_limit(sem: asyncio.Semaphore, fn: Any, items: list[Any]) -> list[Any]:
    async def bound(x: Any) -> Any:
        async with sem:
            return await fn(x)

    return await asyncio.gather(*(bound(x) for x in items))


async def label_column(
    lake: Any,
    name: str,
    column: str,
    new_column: str,
    prompt_template: str,
    *,
    model: str | None = None,
    max_rows: int = _DEFAULT_MAX_ROWS,
    concurrency: int = _DEFAULT_CONCURRENCY,
    provider: Any = None,
) -> dict[str, Any]:
    """Label each row's text via LLM; persist as one new string column.

    ``prompt_template`` should contain a ``{text}`` placeholder.
    """
    table = lake.read_dataset(name)
    if column not in table.column_names:
        raise ValueError(f"column {column!r} not found in dataset {name!r}")
    n = table.num_rows
    _check_size(n, max_rows)

    texts = table.column(column).to_pylist()
    prov = _get_provider(lake, provider, model)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def label_one(text: Any) -> str:
        if not isinstance(text, str) or not text:
            return ""
        try:
            prompt = prompt_template.format(text=text)
        except (KeyError, IndexError, ValueError):
            prompt = prompt_template
        try:
            resp = await prov.generate([LLMMessage(role="user", content=prompt)])
            return (getattr(resp, "content", "") or "").strip()
        except Exception as exc:  # noqa: BLE001 — per-row failure, keep going
            logger.warning("llm_label_row_failed", dataset=name, err=str(exc)[:160])
            return ""

    results = await _map_with_limit(sem, label_one, texts)
    succeeded = sum(1 for r in results if r != "")
    lake.add_columns_table(name, pa.table({new_column: pa.array(results, type=pa.string())}))

    sample = [{column: texts[i], new_column: results[i]} for i in range(min(5, n))]
    return EnrichReport(
        "llm_label", name, n, succeeded, n - succeeded, [new_column], sample
    ).to_dict()


async def extract_fields(
    lake: Any,
    name: str,
    column: str,
    fields: list[dict[str, str]],
    *,
    model: str | None = None,
    max_rows: int = _DEFAULT_MAX_ROWS,
    concurrency: int = _DEFAULT_CONCURRENCY,
    provider: Any = None,
) -> dict[str, Any]:
    """Extract structured fields from each row's text → multiple string columns.

    ``fields`` is a list of ``{name, type, description}`` dicts. Values are
    stored as strings (LLM JSON coerced via ``str()``) to avoid schema drift.
    """
    fields = list(fields)
    if not fields:
        raise ValueError("fields must be a non-empty list")
    field_names = [f["name"] for f in fields]

    table = lake.read_dataset(name)
    if column not in table.column_names:
        raise ValueError(f"column {column!r} not found in dataset {name!r}")
    n = table.num_rows
    _check_size(n, max_rows)

    texts = table.column(column).to_pylist()
    prov = _get_provider(lake, provider, model)
    sem = asyncio.Semaphore(max(1, concurrency))
    schema_desc = json.dumps(
        {f["name"]: f.get("description", "") for f in fields}, ensure_ascii=False
    )

    async def extract_one(text: Any) -> dict[str, str]:
        if not isinstance(text, str) or not text:
            return {fn: "" for fn in field_names}
        prompt = (
            "从下面的文本中抽取结构化字段，只返回一个 JSON 对象，"
            f"字段 schema: {schema_desc}。无法判断的字段填空字符串。文本:\n{text}"
        )
        try:
            resp = await prov.generate([LLMMessage(role="user", content=prompt)])
            data = json.loads(_extract_json(getattr(resp, "content", "") or ""))
            if not isinstance(data, dict):
                raise ValueError("response is not a JSON object")
        except Exception as exc:  # noqa: BLE001 — per-row failure, keep going
            logger.warning("llm_extract_row_failed", dataset=name, err=str(exc)[:160])
            data = {}
        return {fn: str(data.get(fn, "") or "") for fn in field_names}

    rows = await _map_with_limit(sem, extract_one, texts)
    succeeded = sum(1 for d in rows if any(d.values()))
    cols = pa.table({fn: pa.array([d[fn] for d in rows], type=pa.string()) for fn in field_names})
    lake.add_columns_table(name, cols)

    sample = [{column: texts[i], **rows[i]} for i in range(min(5, n))]
    return EnrichReport(
        "extract", name, n, succeeded, n - succeeded, field_names, sample
    ).to_dict()


def _extract_json(content: str) -> str:
    """Pull the first JSON object out of an LLM response (handles ```json fences)."""
    s = content.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        return s[start : end + 1]
    return s
