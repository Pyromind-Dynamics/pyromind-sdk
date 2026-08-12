"""In-process Docker events bus for GET /events."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DockerEvent:
    type: str  # container, image, ...
    action: str  # create, start, die, destroy, kill, rename, ...
    actor_id: str
    actor_attributes: dict[str, str] = field(default_factory=dict)
    time: float = field(default_factory=time.time)

    def to_docker_json(self) -> dict[str, Any]:
        return {
            "Type": self.type,
            "Action": self.action,
            "Actor": {
                "ID": self.actor_id,
                "Attributes": {
                    **self.actor_attributes,
                    "name": self.actor_attributes.get("name", ""),
                },
            },
            "time": int(self.time),
            "timeNano": int(self.time * 1_000_000_000),
        }


class EventBus:
    """Ring buffer + fan-out to long-polling subscribers."""

    def __init__(self, capacity: int = 256) -> None:
        self._events: deque[DockerEvent] = deque(maxlen=capacity)
        self._subscribers: list[asyncio.Queue[DockerEvent | None]] = []
        self._lock = asyncio.Lock()

    async def emit(
        self,
        *,
        type: str,
        action: str,
        actor_id: str,
        attributes: dict[str, str] | None = None,
    ) -> None:
        event = DockerEvent(
            type=type,
            action=action,
            actor_id=actor_id,
            actor_attributes=attributes or {},
        )
        async with self._lock:
            self._events.append(event)
            dead: list[asyncio.Queue[DockerEvent | None]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass

    def since(self, since_ts: float = 0.0) -> list[DockerEvent]:
        return [e for e in self._events if e.time >= since_ts]

    async def subscribe(self) -> asyncio.Queue[DockerEvent | None]:
        q: asyncio.Queue[DockerEvent | None] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[DockerEvent | None]) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass
