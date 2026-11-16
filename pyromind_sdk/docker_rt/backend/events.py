"""In-process Docker events ring buffer.

``GET /events`` 目前回 501（k8s 侧拿不到可靠的事件源），这里只保留
「最近发生了什么」的环形缓冲，供排查和测试读取（``since()``）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class DockerEvent:
    type: str  # container, image, ...
    action: str  # create, start, die, destroy, kill, rename, ...
    actor_id: str
    actor_attributes: dict[str, str] = field(default_factory=dict)
    time: float = field(default_factory=time.time)


class EventBus:
    """Ring buffer of the most recent events."""

    def __init__(self, capacity: int = 256) -> None:
        self._events: deque[DockerEvent] = deque(maxlen=capacity)

    async def emit(
        self,
        *,
        type: str,
        action: str,
        actor_id: str,
        attributes: dict[str, str] | None = None,
    ) -> None:
        self._events.append(
            DockerEvent(
                type=type,
                action=action,
                actor_id=actor_id,
                actor_attributes=attributes or {},
            )
        )

    def since(self, since_ts: float = 0.0) -> list[DockerEvent]:
        return [e for e in self._events if e.time >= since_ts]
