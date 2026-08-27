"""对象标识解析(v1.11.1 W2.2,F2.1)。

对象 ID = 契约 identifier 列值(规范形态本身即身份,如
``GAS.SEGMENT.RG01-001-S047``);``parse_identifier`` 用契约 pattern 的
extract 形态(``pattern_to_extract_regex``,命名捕获组)抽出结构组件,
**不合规值标记不炸**(matched=False 携带原值,交上层决定死信/告警)。
门禁侧的 match 形态(RE2,无捕获)与这里的 extract 形态共享同一套
pattern 语法与保存期校验(含 256 字符组帽,ReDoS 防护继承)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from arrow_lake.contract.schema import DatasetContract, pattern_to_extract_regex


@dataclass(frozen=True)
class IdentifierParse:
    """一次标识解析的结果:原值即对象 ID,matched 标记合规,components
    携带命名组件(未匹配时空 dict)。"""

    object_id: str
    matched: bool
    components: dict[str, str]
    pattern: str


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    # extract 形态在契约保存期已双试编译(schema validator);这里是
    # 查询路径的热点(W4 每行一次),按 pattern 缓存编译产物。
    return re.compile(pattern_to_extract_regex(pattern))


def parse_identifier(pattern: str, value: str) -> IdentifierParse:
    """按契约 pattern 全匹配解析一个标识值(不合规 → 标记,不抛)。"""
    m = _compile(pattern).fullmatch(value)
    if m is None:
        return IdentifierParse(
            object_id=value, matched=False, components={}, pattern=pattern,
        )
    return IdentifierParse(
        object_id=value, matched=True,
        components={k: v for k, v in m.groupdict().items() if v is not None},
        pattern=pattern,
    )


def parse_table_identifier(
    contract: DatasetContract, table: str, value: str,
) -> IdentifierParse | None:
    """表级便捷入口:表节无 identifier 声明(或表不在契约内)→ None,
    调用方回落到 entity_map 解析路径。"""
    section = contract.tables.get(table)
    if section is None or section.identifier is None:
        return None
    return parse_identifier(section.identifier.pattern, value)
