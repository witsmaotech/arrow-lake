"""系统运行表命名约定与判断(集中单点,便于未来扩展)。

系统运行表(audit trail / lineage events 等系统运行依赖的 Lance 表)统一用
``sys_`` 前缀命名,与 ``_quality_*`` 等临时/内部表命名空间分离:

* ``sys_*``  —— 系统运行表:受保护(REST 不可删),对非 admin 隐藏。
* ``_*``     —— 临时/内部表(如 template-quality 的 ``_quality_<hex>``):
                 对非 admin 隐藏,但有专门清理路径,可删。

**新增系统表时**:只要用 ``sys_`` 前缀命名 + 其 config 默认值写 ``sys_xxx``,
即自动获得 REST 层保护 + 非 admin 隐藏,无需改 ``_system_tables.py`` 之外的
代码。delete/list 端点与本模块的判断保持单一来源。
"""

from __future__ import annotations

SYSTEM_TABLE_PREFIX: str = "sys_"
INTERNAL_TABLE_PREFIX: str = "_"
# 质量门控死信表后缀(v1.10.7 起写入 ``_{ds}_dead_letter``;旧命名 ``{ds}_dead_letter``
# 的存量表同样按内部表处理 — 拒收行往往恰是被策略拒掉的最敏感行)。
DEAD_LETTER_SUFFIX: str = "_dead_letter"


def is_system_table(name: str) -> bool:
    """系统运行表(sys_ 前缀)→ 受保护,不可删除。

    大小写不敏感:数据集名允许大写,DuckDB 标识符解析也不区分大小写,
    精确匹配会让 ``SYS_`` 变体绕过删除保护(R-01 同型)。"""
    return name.lower().startswith(SYSTEM_TABLE_PREFIX)


def is_internal_table(name: str) -> bool:
    """内部表(sys_ / _ 前缀,或死信表命名)→ 对非 admin 隐藏(大小写不敏感)。"""
    n = name.lower()
    return n.startswith((INTERNAL_TABLE_PREFIX, SYSTEM_TABLE_PREFIX)) or n.endswith(DEAD_LETTER_SUFFIX)
