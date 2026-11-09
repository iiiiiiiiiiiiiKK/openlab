"""In-process event bus used to stream task progress to the UI over WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from engine.core.security import redact


class EventBus:
    """Fan-out of JSON events to any number of connected websocket queues."""

    def __init__(self, max_queue: int = 500) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._max_queue = max_queue
        self._loop: asyncio.AbstractEventLoop | None = None
        self._history: list[dict[str, Any]] = []

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, channel: str = "*") -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers[channel].add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]], channel: str = "*") -> None:
        self._subscribers.get(channel, set()).discard(queue)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._history[-limit:]

    def publish(self, event_type: str, payload: dict[str, Any] | None = None, channel: str = "*") -> None:
        event = {
            "type": event_type,
            "channel": channel,
            "at": datetime.now(timezone.utc).isoformat(),
            "payload": _sanitise(payload or {}),
        }
        self._history.append(event)
        if len(self._history) > 1000:
            del self._history[:200]
        targets = set(self._subscribers.get(channel, set())) | set(self._subscribers.get("*", set()))
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - slow consumer
                with contextlib.suppress(Exception):
                    queue.get_nowait()
                with contextlib.suppress(Exception):
                    queue.put_nowait(event)
        self._relay_to_loop(event)

    def _relay_to_loop(self, event: dict[str, Any]) -> None:
        if self._loop is None or not self._loop.is_running():
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop:
            return

        def _push() -> None:
            self.publish_local(event)

        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(_push)

    def publish_local(self, event: dict[str, Any]) -> None:
        """Deliver an already-built event to local subscribers (loop thread only)."""
        targets = set(self._subscribers.get(event.get("channel", "*"), set())) | set(
            self._subscribers.get("*", set())
        )
        for queue in targets:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)


def _sanitise(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        text = json.dumps(payload, default=str, ensure_ascii=False)
    except TypeError:
        return {"raw": str(payload)}
    return json.loads(redact(text))


bus = EventBus()
