"""F4.8 — 标注脱敏前置:L2 泛化 + L3 假名(v1.11.3 MS4 W2.3)。

时点红线(设计 §8):dispatch 到 LS **之前**——标注者永远不见原始敏感
值。因此 fail-closed:有实体要假名但 HMAC key 缺失 → raise,宁拒发。

* L2 泛化:值域收窄(regex → replacement 按序 re.sub,如精确地址→区县);
* L3 假名:HMAC 稳定假名(同 key 同实体同假名 → 保留等值连接性);
  算法/密钥约定复用 :mod:`arrow_lake.quality.masking_engine`
  (``ARROW_LAKE__MASKING__HMAC_KEY`` + hmac-sha256,双 .env 同步坑适用)。

组合顺序:先泛化后假名。dispatch 链 = 脱敏 → 预标注(HE 在脱敏文本上
抽取,span 基于标注者所见文本,自洽)。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Sequence

__all__ = ["AnnotationMaskingError", "apply_annotation_masking"]

_HMAC_KEY_ENV = "ARROW_LAKE__MASKING__HMAC_KEY"


class AnnotationMaskingError(RuntimeError):
    """脱敏前置失败(fail-closed;dispatch → 422)。"""


def _alias(name: str, key: bytes) -> str:
    """稳定假名:首字符保留可读性 + hmac-sha256 前 10 hex(等值连接)。"""
    digest = hmac.new(key, name.encode("utf-8"), hashlib.sha256).hexdigest()[:10]
    return f"{name[0]}_{digest}"


def apply_annotation_masking(
    text: str,
    *,
    generalize_rules: Sequence[tuple[str, str]] = (),
    entity_names: Sequence[str] = (),
    hmac_key: bytes | None = None,
) -> str:
    """L2 泛化 + L3 假名(顺序:泛化 → 假名);无规则无实体 = 透传。

    Args:
        text: 待脱敏文本(源行拼接)。
        generalize_rules: ``(regex, replacement)`` 有序列表。
        entity_names: 要假名化的实体名(L3)。
        hmac_key: HMAC 密钥;None 时读 ``ARROW_LAKE__MASKING__HMAC_KEY``。

    Raises:
        AnnotationMaskingError: 有实体要假名但密钥缺失(fail-closed)。
    """
    out = text
    for pattern, replacement in generalize_rules:
        out = re.sub(pattern, replacement, out)
    if not entity_names:
        return out
    key = hmac_key
    if key is None:
        env_key = os.environ.get(_HMAC_KEY_ENV, "")
        key = env_key.encode() if env_key else None
    if not key:
        raise AnnotationMaskingError(
            f"pseudonymization requested but {_HMAC_KEY_ENV} is not set — "
            "refusing to dispatch unmasked annotation tasks (fail-closed)"
        )
    # 长名先替换:防止"张三丰"先被"张三"的假名破坏(子串包含)。
    for name in sorted(set(entity_names), key=len, reverse=True):
        if name:
            out = out.replace(name, _alias(name, key))
    return out
