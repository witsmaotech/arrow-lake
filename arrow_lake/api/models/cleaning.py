"""Structured data cleaning pipeline models.

语义化清洗 steps → 后端翻译成 DuckDB SQL → 可选写回数据集(restore_dataset)。
覆盖结构化清洗算子:类型规整 / 缺失值 / 标准化 / 值映射 / 列拆合 / 正则 等。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CleanStep(BaseModel):
    """一个清洗步骤。type 决定后端如何翻译成 DuckDB 表达式。"""

    type: str = Field(
        ...,
        description="cast|fillna|trim|lower|upper|case|regex_replace|split|concat|rename|drop",
    )
    column: str | None = Field(default=None, description="目标列(多数 type 必填)")
    params: dict[str, Any] = Field(
        default_factory=dict, description="type 特定参数(dtype/value/sep/mapping...)"
    )


class CleanFilter(BaseModel):
    """行过滤(满足条件的行保留)。"""

    column: str
    op: str = Field(..., pattern=r"^(>|>=|<|<=|=|!=|is_null|is_not_null)$")
    value: Any = None


class CleanRequest(BaseModel):
    steps: list[CleanStep] = Field(default_factory=list)
    filters: list[CleanFilter] = Field(default_factory=list)
    write_back: bool = False
    limit: int | None = Field(
        default=None, ge=1, le=100000, description="可选:只处理前 N 行(否则全表)"
    )


class CleanResponse(BaseModel):
    success: bool = True
    input_rows: int = 0
    output_rows: int = 0
    columns: list[str] = []
    written_back: bool = False
    preview: list[dict[str, Any]] = []
