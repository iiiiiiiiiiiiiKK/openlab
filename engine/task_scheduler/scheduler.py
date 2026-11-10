"""Persistent task scheduler.

Tasks are rows in SQLite, so a restart never loses queued work. The scheduler runs a
bounded number of tasks concurrently (default 1, user-configurable), supports cancel
and pause/resume, and streams every log line to the WebSocket event bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from engine.core.security import redact
from engine.core.states import TaskStatus, task_can_transition

from openlab.db import session_scope
from openlab.events import bus
from openlab.models import ProjectTask, TaskLog

log = logging.getLogger("openlab.scheduler")

TaskHandler = Callable[["TaskContext"], Awaitable[dict | None]]


class TaskCancelled(Exception):
    """Raised inside a handler when the user cancelled the task."""


@dataclass
class TaskContext:
    task_id: str
    kind: str
    project_id: str | None
    payload: dict
    scheduler: "TaskScheduler"

    async def log(self, message: str, stream: str = "stdout", level: str = "info") -> None:
        await self.scheduler.append_log(self.task_id, message, stream=stream, level=level, project_id=self.project_id)

    async def progress(self, value: float, message: str | None = None) -> None:
        await self.scheduler.set_progress(self.task_id, value, message, project_id=self.project_id)

    async def check_cancelled(self) -> None:
        if await self.scheduler.is_cancel_requested(self.task_id):
            raise TaskCancelled()

    async def emit(self, event_type: str, payload: dict) -> None:
        bus.publish(event_type, {"task_id": self.task_id, "project_id": self.project_id, **payload})


@dataclass
class SchedulerConfig:
    max_concurrency: int = 1
    poll_interval_s: float = 0.5
    max_attempts: int = 2


class TaskScheduler:
    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self._handlers: dict[str, TaskHandler] = {}
        # Highest persisted priority runs first; sequence keeps FIFO ordering for ties.
        self._queue: asyncio.PriorityQueue[tuple[int, int, str]] = asyncio.PriorityQueue()
        self._queue_sequence = 0
        self._running: dict[str, asyncio.Task] = {}
        self._workers: list[asyncio.Task] = []
        self._stopping = False
        self._seq: dict[str, int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- registration ----------------------------------------------------
    def register(self, kind: str, handler: TaskHandler) -> None:
        self._handlers[kind] = handler

    def handler_for(self, kind: str) -> TaskHandler | None:
        return self._handlers.get(kind)

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        bus.bind_loop(self._loop)
        self._stopping = False
        for index in range(max(1, self.config.max_concurrency)):
            self._workers.append(asyncio.create_task(self._worker(index), name=f"openlab-worker-{index}"))
        await self.requeue_pending()
        log.info("scheduler started with %d worker(s)", len(self._workers))

    async def stop(self) -> None:
        self._stopping = True
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._workers.clear()
        for task in list(self._running.values()):
            task.cancel()
        self._running.clear()

    def _enqueue(self, task_id: str, priority: int) -> None:
        self._queue_sequence += 1
        self._queue.put_nowait((-int(priority), self._queue_sequence, task_id))

    async def requeue_pending(self) -> int:
        """Restore queued tasks in priority order; interrupted running tasks retry."""
        queued: list[tuple[int, datetime, str]] = []
        with session_scope() as session:
            rows = list(
                session.scalars(
                    select(ProjectTask).where(ProjectTask.status.in_(["queued", "running"]))
                )
            )
            for row in rows:
                if row.status == "running":
                    row.status = "queued"
                    row.message = "requeued after restart"
                row.cancel_requested = False
                queued.append((int(row.priority or 0), row.created_at or datetime.min.replace(tzinfo=timezone.utc), row.id))
        for priority, _created_at, task_id in sorted(queued, key=lambda item: (-item[0], item[1], item[2])):
            self._enqueue(task_id, priority)
        if queued:
            log.info("requeued %d task(s) after restart", len(queued))
        return len(queued)

    # -- queue operations -------------------------------------------------
    async def submit(
        self,
        kind: str,
        project_id: str | None = None,
        payload: dict | None = None,
        priority: int = 0,
    ) -> str:
        with session_scope() as session:
            task = ProjectTask(
                kind=kind,
                project_id=project_id,
                payload=payload or {},
                status=TaskStatus.QUEUED.value,
                stage="queued",
                priority=priority,
            )
            session.add(task)
            session.flush()
            task_id = task.id
        self._enqueue(task_id, priority)
        bus.publish(
            "task.queued",
            {"task_id": task_id, "kind": kind, "project_id": project_id},
        )
        return task_id

    async def cancel(self, task_id: str) -> bool:
        with session_scope() as session:
            task = session.get(ProjectTask, task_id)
            if task is None:
                return False
            if task.status in (TaskStatus.SUCCEEDED.value, TaskStatus.CANCELLED.value):
                return False
            task.cancel_requested = True
            if task.status == TaskStatus.QUEUED.value:
                task.status = TaskStatus.CANCELLED.value
                task.finished_at = datetime.now(timezone.utc)
                task.message = "cancelled before start"
        running = self._running.get(task_id)
        if running is not None:
            running.cancel()
        bus.publish("task.cancelled", {"task_id": task_id})
        return True

    async def pause(self, task_id: str) -> bool:
        with session_scope() as session:
            task = session.get(ProjectTask, task_id)
            if task is None or not task_can_transition(task.status, TaskStatus.PAUSED.value):
                return False
            task.status = TaskStatus.PAUSED.value
            task.message = "paused by user"
        running = self._running.get(task_id)
        if running is not None:
            running.cancel()
        bus.publish("task.paused", {"task_id": task_id})
        return True

    async def resume(self, task_id: str) -> bool:
        with session_scope() as session:
            task = session.get(ProjectTask, task_id)
            if task is None or task.status != TaskStatus.PAUSED.value:
                return False
            task.status = TaskStatus.QUEUED.value
            task.message = "resumed by user"
            priority = int(task.priority or 0)
        self._enqueue(task_id, priority)
        bus.publish("task.queued", {"task_id": task_id, "resumed": True})
        return True

    async def retry(self, task_id: str) -> str | None:
        with session_scope() as session:
            task = session.get(ProjectTask, task_id)
            if task is None:
                return None
            kind = task.kind
            project_id = task.project_id
            payload = dict(task.payload or {})
            attempts = task.attempts
        new_id = await self.submit(kind, project_id=project_id, payload={**payload, "retry_of": task_id})
        with session_scope() as session:
            fresh = session.get(ProjectTask, new_id)
            if fresh is not None:
                fresh.attempts = attempts + 1
        return new_id

    # -- state helpers ----------------------------------------------------
    async def is_cancel_requested(self, task_id: str) -> bool:
        with session_scope() as session:
            task = session.get(ProjectTask, task_id)
            return bool(task and task.cancel_requested)

    async def set_progress(
        self, task_id: str, value: float, message: str | None = None, project_id: str | None = None
    ) -> None:
        with session_scope() as session:
            task = session.get(ProjectTask, task_id)
            if task is None:
                return
            task.progress = round(max(0.0, min(1.0, value)), 3)
            if message:
                task.message = redact(message)[:2000]
        bus.publish(
            "task.progress",
            {
                "task_id": task_id,
                "project_id": project_id,
                "progress": round(max(0.0, min(1.0, value)), 3),
                "message": redact(message or "")[:500],
            },
        )

    async def set_stage(self, task_id: str, stage: str, project_id: str | None = None) -> None:
        with session_scope() as session:
            task = session.get(ProjectTask, task_id)
            if task is not None:
                task.stage = stage
        bus.publish("task.stage", {"task_id": task_id, "project_id": project_id, "stage": stage})

    async def append_log(
        self,
        task_id: str,
        message: str,
        stream: str = "stdout",
        level: str = "info",
        project_id: str | None = None,
    ) -> None:
        text = redact(str(message))[:8000]
        if not text.strip():
            return
        seq = self._seq.get(task_id, 0) + 1
        self._seq[task_id] = seq
        with session_scope() as session:
            session.add(
                TaskLog(
                    task_id=task_id,
                    project_id=project_id,
                    seq=seq,
                    stream=stream,
                    level=level,
                    message=text,
                )
            )
        bus.publish(
            "task.log",
            {
                "task_id": task_id,
                "project_id": project_id,
                "seq": seq,
                "stream": stream,
                "level": level,
                "message": text,
            },
        )

    # -- worker ----------------------------------------------------------
    async def _worker(self, index: int) -> None:
        while not self._stopping:
            try:
                task_id = (await self._queue.get())[2]
            except asyncio.CancelledError:
                return
            try:
                await self._run(task_id)
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 - worker must survive
                log.exception("worker %d crashed while running %s", index, task_id)
            finally:
                self._queue.task_done()

    async def _run(self, task_id: str) -> None:
        with session_scope() as session:
            task = session.get(ProjectTask, task_id)
            if task is None or task.status == TaskStatus.CANCELLED.value:
                return
            if task.status == TaskStatus.SUCCEEDED.value:
                return
            task.status = TaskStatus.RUNNING.value
            task.started_at = datetime.now(timezone.utc)
            task.attempts += 1
            kind = task.kind
            project_id = task.project_id
            payload = dict(task.payload or {})
        handler = self.handler_for(kind)
        current = asyncio.current_task()
        if current is not None:
            self._running[task_id] = current
        await self.set_stage(task_id, "running", project_id)
        bus.publish("task.started", {"task_id": task_id, "kind": kind, "project_id": project_id})
        ctx = TaskContext(task_id=task_id, kind=kind, project_id=project_id, payload=payload, scheduler=self)
        try:
            if handler is None:
                raise RuntimeError(f"no handler registered for task kind: {kind}")
            result = await handler(ctx)
            with session_scope() as session:
                row = session.get(ProjectTask, task_id)
                if row is not None:
                    row.status = TaskStatus.SUCCEEDED.value
                    row.progress = 1.0
                    row.result = result or {}
                    row.finished_at = datetime.now(timezone.utc)
                    row.message = "completed"
            bus.publish(
                "task.finished",
                {"task_id": task_id, "project_id": project_id, "kind": kind, "status": "succeeded"},
            )
        except (asyncio.CancelledError, TaskCancelled):
            with session_scope() as session:
                row = session.get(ProjectTask, task_id)
                cancelled_by_user = False
                shutting_down = self._stopping
                if row is not None:
                    cancelled_by_user = row.cancel_requested
                    if shutting_down and not cancelled_by_user:
                        # Server shutdown is not a user pause: leave the durable task
                        # queued so the next process can resume it automatically.
                        row.status = TaskStatus.QUEUED.value
                        row.cancel_requested = False
                        row.finished_at = None
                        row.message = "requeued after shutdown"
                    else:
                        row.status = (
                            TaskStatus.CANCELLED.value if cancelled_by_user else TaskStatus.PAUSED.value
                        )
                        row.finished_at = datetime.now(timezone.utc)
                        row.message = "cancelled" if cancelled_by_user else "paused"
            if shutting_down and not cancelled_by_user:
                bus.publish("task.queued", {"task_id": task_id, "resumed": True})
            else:
                bus.publish("task.cancelled" if cancelled_by_user else "task.paused", {"task_id": task_id})
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            message = redact(str(exc))[:4000]
            trace = redact(traceback.format_exc())[:8000]
            with session_scope() as session:
                row = session.get(ProjectTask, task_id)
                if row is not None:
                    row.status = TaskStatus.FAILED.value
                    row.error = message
                    row.finished_at = datetime.now(timezone.utc)
                    row.message = message[:500]
            await self.append_log(task_id, trace, stream="stderr", level="error", project_id=project_id)
            bus.publish(
                "task.failed",
                {"task_id": task_id, "project_id": project_id, "kind": kind, "error": message},
            )
        finally:
            self._running.pop(task_id, None)
            self._seq.pop(task_id, None)

    # -- introspection ----------------------------------------------------
    def status(self) -> dict:
        return {
            "concurrency": self.config.max_concurrency,
            "queued": self._queue.qsize(),
            "running": len(self._running),
            "handlers": sorted(self._handlers.keys()),
        }


def log_row_to_dict(row: TaskLog) -> dict:
    return {
        "id": row.id,
        "seq": row.seq,
        "stream": row.stream,
        "level": row.level,
        "message": row.message,
        "at": row.created_at.isoformat() if row.created_at else None,
    }


def task_row_to_dict(row: ProjectTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "project_id": row.project_id,
        "stage": row.stage,
        "status": row.status,
        "progress": row.progress,
        "message": row.message,
        "error": row.error,
        "payload": row.payload or {},
        "result": row.result or {},
        "attempts": row.attempts,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@dataclass
class TaskSummary:
    id: str
    kind: str
    status: str
    progress: float
    message: str | None = None
    extra: dict = field(default_factory=dict)
