"""进程内事件 pub/sub(v1.11.2 MS3 W4.3,F3.4/S7)。

零新依赖红线下的轻实现:注册表 + 同步分发;事件记录 = 审计条目(中间件
落账,不新建存储)。订阅者**异常隔离**——任一订阅者抛错记日志,不阻断
主流程与其余订阅者。``*`` 为通配订阅。Redis streams 触发条件(跨 worker
消费者出现)前不引入。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ActionEvent", "publish", "reset_subscribers", "subscribe", "subscriber_count"]


@dataclass(frozen=True)
class ActionEvent:
    """一次行动后置事件的进程内形态。"""

    name: str  # e.g. "alert.published"
    action_id: str
    dataset: str
    object_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


_Handler = Callable[[ActionEvent], None]
_subscribers: dict[str, list[_Handler]] = {}
_lock = threading.Lock()


def subscribe(event_name: str, handler: _Handler) -> Callable[[], None]:
    """注册订阅者(按事件名;``*`` 通配);返回退订函数。"""
    with _lock:
        _subscribers.setdefault(event_name, []).append(handler)

    def _unsubscribe() -> None:
        with _lock:
            listeners = _subscribers.get(event_name, [])
            if handler in listeners:
                listeners.remove(handler)

    return _unsubscribe


def publish(event: ActionEvent) -> list[str]:
    """同步分发;异常隔离(订阅者抛错不阻断)。返回送达的订阅者名单。"""
    with _lock:
        handlers = [*(_subscribers.get(event.name, [])), *(_subscribers.get("*", []))]
    delivered: list[str] = []
    for handler in handlers:
        name = getattr(handler, "__name__", repr(handler))
        try:
            handler(event)
        except Exception:
            logger.exception(
                "event_subscriber_failed",
                extra={"event": event.name, "subscriber": name},
            )
            continue
        delivered.append(name)
    return delivered


def subscriber_count(event_name: str) -> int:
    with _lock:
        return len(_subscribers.get(event_name, []))


def reset_subscribers() -> None:
    """测试隔离:清空注册表。"""
    with _lock:
        _subscribers.clear()
