"""ETL transforms for Daft DataFrame ingestion pipelines.

Builds callable transform functions from a JSON-serializable spec, each
accepting a ``daft.DataFrame`` and returning a transformed ``daft.DataFrame``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import daft


def build_transforms(specs: list[dict[str, Any]]) -> list[Callable[[daft.DataFrame], daft.DataFrame]]:
    """Build a list of Daft transform callables from a JSON spec list.

    Args:
        specs: List of transform spec dicts, each with an ``op`` key and
               op-specific parameters. Supported ops:
               ``rename``, ``select``, ``filter``, ``cast``, ``add_constant``.

    Returns:
        List of ``daft.DataFrame -> daft.DataFrame`` callables.

    Raises:
        ValueError: If an unknown ``op`` is provided or required params missing.
    """
    builders: dict[str, Callable[[dict[str, Any]], Callable]] = {
        "rename": _build_rename,
        "select": _build_select,
        "filter": _build_filter,
        "cast": _build_cast,
        "add_constant": _build_add_constant,
        "classify_text": _build_classify_text,
        "classify_image": _build_classify_image,
        "decode_image": _build_decode_image,
        "llm_generate": _build_llm_generate,
        "prompt": _build_prompt,
        "deduplicate": _build_deduplicate,
    }
    transforms: list[Callable[[daft.DataFrame], daft.DataFrame]] = []
    for spec in specs:
        op = spec.get("op")
        if op not in builders:
            raise ValueError(f"Unknown transform op: {op!r}. Supported: {sorted(builders)}")
        transforms.append(builders[op](spec))
    return transforms


def apply_transforms(df: daft.DataFrame, transforms: list[Callable[[daft.DataFrame], daft.DataFrame]]) -> daft.DataFrame:
    """Apply a sequence of transforms to a Daft DataFrame.

    Args:
        df: Input Daft DataFrame.
        transforms: List of transform callables.

    Returns:
        Transformed Daft DataFrame.
    """
    for t in transforms:
        df = t(df)
    return df


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

def _build_rename(spec: dict[str, Any]) -> Callable:
    src = spec.get("from")
    dst = spec.get("to")
    if not src or not dst:
        raise ValueError("rename requires 'from' and 'to' fields")
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        return df.with_column_renamed(src, dst)
    return _transform


def _build_select(spec: dict[str, Any]) -> Callable:
    cols = spec.get("columns")
    if not cols:
        raise ValueError("select requires 'columns' field")
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        return df.select(*cols)
    return _transform


def _build_filter(spec: dict[str, Any]) -> Callable:
    column = spec.get("column")
    op = spec.get("op_name")
    value = spec.get("value")
    if not column or not op:
        raise ValueError("filter requires 'column' and 'op_name' fields (e.g. {\"column\": \"x\", \"op_name\": \">\", \"value\": 0})")
    _filter_ops: dict[str, Callable] = {
        ">": lambda c, v: c > v,
        ">=": lambda c, v: c >= v,
        "<": lambda c, v: c < v,
        "<=": lambda c, v: c <= v,
        "==": lambda c, v: c == v,
        "!=": lambda c, v: c != v,
        "is_null": lambda c, _v: c.is_null(),
        "is_not_null": lambda c, _v: c.is_not_null(),
    }
    if op not in _filter_ops:
        raise ValueError(f"Unknown filter op_name: {op!r}. Supported: {sorted(_filter_ops)}")
    comparator = _filter_ops[op]
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        return df.where(comparator(daft.col(column), value))
    return _transform


def _build_cast(spec: dict[str, Any]) -> Callable:
    col = spec.get("column")
    dtype = spec.get("dtype")
    if not col or not dtype:
        raise ValueError("cast requires 'column' and 'dtype' fields")
    daft_dtype = _resolve_dtype(dtype)
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        return df.with_column(col, daft.col(col).cast(daft_dtype))
    return _transform


def _build_add_constant(spec: dict[str, Any]) -> Callable:
    col = spec.get("column")
    if not col:
        raise ValueError("add_constant requires 'column' field")
    value = spec.get("value")
    dtype = spec.get("dtype")
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        lit = daft.lit(value)
        if dtype:
            lit = lit.cast(_resolve_dtype(dtype))
        return df.with_column(col, lit)
    return _transform


def _build_classify_text(spec: dict[str, Any]) -> Callable:
    column = spec.get("column")
    if not column:
        raise ValueError("classify_text requires 'column' field")
    output_column = spec.get("output_column", f"{column}_classification")
    provider = spec.get("provider", "huggingface")
    model = spec.get("model", "")
    kwargs = {k: v for k, v in spec.items() if k not in ("op", "column", "output_column", "provider", "model")}
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        import daft.functions as daft_functions
        return df.with_column(output_column,
            daft_functions.classify_text(daft.col(column), provider=provider, model=model, **kwargs))
    return _transform


def _build_classify_image(spec: dict[str, Any]) -> Callable:
    column = spec.get("column")
    if not column:
        raise ValueError("classify_image requires 'column' field")
    output_column = spec.get("output_column", f"{column}_classification")
    provider = spec.get("provider", "huggingface")
    model = spec.get("model", "")
    kwargs = {k: v for k, v in spec.items() if k not in ("op", "column", "output_column", "provider", "model")}
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        import daft.functions as daft_functions
        return df.with_column(output_column,
            daft_functions.classify_image(daft.col(column), provider=provider, model=model, **kwargs))
    return _transform


def _build_decode_image(spec: dict[str, Any]) -> Callable:
    """Decode image bytes into an image tensor (v1.8.0 #18 — VLM chain).

    Pairs with ``classify_image`` / ``prompt`` for end-to-end VLM: raw image
    bytes → decoded image tensor → classification or visual prompt.
    """
    column = spec.get("column")
    if not column:
        raise ValueError("decode_image requires 'column' field")
    output_column = spec.get("output_column", f"{column}_decoded")
    mode = spec.get("mode", "RGB")
    on_error = spec.get("on_error", "null")

    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        import daft.functions as daft_functions

        return df.with_column(
            output_column,
            daft_functions.decode_image(
                daft.col(column), on_error=on_error, mode=mode
            ),
        )

    return _transform


def _build_llm_generate(spec: dict[str, Any]) -> Callable:
    column = spec.get("column")
    if not column:
        raise ValueError("llm_generate requires 'column' field")
    output_column = spec.get("output_column", f"{column}_generated")
    provider = spec.get("provider", "openai")
    model = spec.get("model", "")
    prompt_template = spec.get("prompt_template", "")
    kwargs = {k: v for k, v in spec.items() if k not in ("op", "column", "output_column", "provider", "model", "prompt_template")}
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        import daft.functions as daft_functions
        return df.with_column(output_column,
            daft_functions.llm_generate(daft.col(column), provider=provider, model=model,
                           prompt_template=prompt_template, **kwargs))
    return _transform


def _build_prompt(spec: dict[str, Any]) -> Callable:
    column = spec.get("column")
    if not column:
        raise ValueError("prompt requires 'column' field")
    output_column = spec.get("output_column", f"{column}_response")
    provider = spec.get("provider", "openai")
    model = spec.get("model", "")
    kwargs = {k: v for k, v in spec.items() if k not in ("op", "column", "output_column", "provider", "model")}
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        import daft.functions as daft_functions
        return df.with_column(output_column,
            daft_functions.prompt(daft.col(column), provider=provider, model=model, **kwargs))
    return _transform


def _build_deduplicate(spec: dict[str, Any]) -> Callable:
    columns = spec.get("columns")
    if not columns:
        raise ValueError("deduplicate requires 'columns' field (list of columns to deduplicate on)")
    order_by = spec.get("order_by")
    desc = spec.get("desc", False)
    def _transform(df: daft.DataFrame) -> daft.DataFrame:
        if order_by:
            df = df.sort(daft.col(order_by), desc=desc)
        return df.distinct(*columns)
    return _transform


_DTYPE_MAP: dict[str, Any] = {
    "int8": daft.DataType.int8(),
    "int16": daft.DataType.int16(),
    "int32": daft.DataType.int32(),
    "int64": daft.DataType.int64(),
    "uint8": daft.DataType.uint8(),
    "uint16": daft.DataType.uint16(),
    "uint32": daft.DataType.uint32(),
    "uint64": daft.DataType.uint64(),
    "float32": daft.DataType.float32(),
    "float64": daft.DataType.float64(),
    "bool": daft.DataType.bool(),
    "string": daft.DataType.string(),
    "utf8": daft.DataType.string(),
    "date": daft.DataType.date(),
    "timestamp": daft.DataType.timestamp(daft.TimeUnit.us()),
}


def _resolve_dtype(dtype: str) -> Any:
    """Resolve a dtype string to a Daft DataType."""
    resolved = _DTYPE_MAP.get(dtype.lower())
    if resolved is None:
        raise ValueError(f"Unknown dtype: {dtype!r}. Supported: {sorted(_DTYPE_MAP)}")
    return resolved
