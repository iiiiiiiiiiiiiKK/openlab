"""Unit tests for the scheduler state handling (no Docker or browser needed)."""

from __future__ import annotations

import asyncio

import pytest

from engine.task_scheduler.scheduler import SchedulerConfig, TaskContext, TaskScheduler


async def _make_scheduler(handlers=None, concurrency: int = 1) -> TaskScheduler:
    scheduler = TaskScheduler(SchedulerConfig(max_concurrency=concurrency))
    for kind, handler in (handlers or {}).items():
        scheduler.register(kind, handler)
    await scheduler.start()
    return scheduler


async def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def _task_status(task_id: str) -> str | None:
    from openlab.db import session_scope
    from openlab.models import ProjectTask

    with session_scope() as session:
        row = session.get(ProjectTask, task_id)
        return row.status if row else None


async def test_task_runs_and_persists_logs(db) -> None:
    async def handler(ctx: TaskContext) -> dict:
        await ctx.log("working")
        await ctx.progress(0.5, "half way")
        return {"value": 42}

    scheduler = await _make_scheduler({"demo": handler})
    try:
        task_id = await scheduler.submit("demo", payload={"x": 1})
        assert await _wait_for(lambda: _is_succeeded(task_id))
        from openlab.db import session_scope
        from openlab.models import ProjectTask, TaskLog

        with session_scope() as session:
            row = session.get(ProjectTask, task_id)
            assert row is not None
            assert row.status == "succeeded"
            assert row.result == {"value": 42}
            assert row.progress == 1.0
            logs = session.query(TaskLog).filter(TaskLog.task_id == task_id).all()
            assert any("working" in log.message for log in logs)
    finally:
        await scheduler.stop()


async def _is_succeeded(task_id: str) -> bool:
    return await _task_status(task_id) == "succeeded"


async def test_failing_task_is_marked_failed(db) -> None:
    async def handler(ctx: TaskContext) -> dict:
        raise RuntimeError("kaboom")

    scheduler = await _make_scheduler({"boom": handler})
    try:
        task_id = await scheduler.submit("boom")
        assert await _wait_for(lambda: _status_is(task_id, "failed"))
        from openlab.db import session_scope
        from openlab.models import ProjectTask

        with session_scope() as session:
            row = session.get(ProjectTask, task_id)
            assert "kaboom" in (row.error or "")
    finally:
        await scheduler.stop()


async def _status_is(task_id: str, expected: str) -> bool:
    return await _task_status(task_id) == expected


async def test_cancel_running_task(db) -> None:
    started = asyncio.Event()

    async def handler(ctx: TaskContext) -> dict:
        started.set()
        for _ in range(100):
            await ctx.check_cancelled()
            await asyncio.sleep(0.05)
        return {}

    scheduler = await _make_scheduler({"slow": handler})
    try:
        task_id = await scheduler.submit("slow")
        await asyncio.wait_for(started.wait(), timeout=5)
        assert await scheduler.cancel(task_id) is True
        assert await _wait_for(lambda: _status_is(task_id, "cancelled"))
    finally:
        await scheduler.stop()


async def test_cancel_queued_task_without_handler(db) -> None:
    async def handler(ctx: TaskContext) -> dict:
        await asyncio.sleep(0.01)
        return {}

    scheduler = await _make_scheduler({"noop": handler})
    scheduler.register("missing", handler)
    try:
        task_id = await scheduler.submit("missing")
        await scheduler.cancel(task_id)
        status = await _task_status(task_id)
        assert status in {"cancelled", "succeeded"}
    finally:
        await scheduler.stop()


async def test_retry_creates_new_task(db) -> None:
    async def handler(ctx: TaskContext) -> dict:
        return {}

    scheduler = await _make_scheduler({"again": handler})
    try:
        task_id = await scheduler.submit("again")
        await _wait_for(lambda: _is_succeeded(task_id))
        new_id = await scheduler.retry(task_id)
        assert new_id and new_id != task_id
        await _wait_for(lambda: _is_succeeded(new_id))
    finally:
        await scheduler.stop()


async def test_pause_and_resume(db) -> None:
    gate = asyncio.Event()

    async def handler(ctx: TaskContext) -> dict:
        gate.set()
        for _ in range(200):
            await ctx.check_cancelled()
            await asyncio.sleep(0.03)
        return {}

    scheduler = await _make_scheduler({"pausable": handler})
    try:
        task_id = await scheduler.submit("pausable")
        await asyncio.wait_for(gate.wait(), timeout=5)
        assert await scheduler.pause(task_id) is True
        assert await _wait_for(lambda: _status_is(task_id, "paused"))
        assert await scheduler.resume(task_id) is True
        assert await _wait_for(lambda: _status_is(task_id, "succeeded"), timeout=20)
    finally:
        await scheduler.stop()


async def test_requeue_after_restart(db) -> None:
    async def handler(ctx: TaskContext) -> dict:
        return {}

    scheduler = await _make_scheduler({"resume": handler})
    await scheduler.stop()

    from openlab.db import session_scope
    from openlab.models import ProjectTask

    with session_scope() as session:
        session.add(ProjectTask(kind="resume", status="running", stage="running", payload={}))

    fresh = TaskScheduler(SchedulerConfig(max_concurrency=1))
    fresh.register("resume", handler)
    await fresh.start()
    try:
        assert await _wait_for(lambda: _any_succeeded(), timeout=15)
    finally:
        await fresh.stop()


async def _any_succeeded() -> bool:
    from openlab.db import session_scope
    from openlab.models import ProjectTask

    with session_scope() as session:
        return any(
            row.status == "succeeded" for row in session.query(ProjectTask).filter(ProjectTask.kind == "resume")
        )


async def test_higher_priority_queued_task_runs_first(db) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def handler(ctx: TaskContext) -> dict:
        label = str(ctx.payload["label"])
        if label == "first":
            started.set()
            await release.wait()
        order.append(label)
        return {}

    scheduler = await _make_scheduler({"priority": handler})
    try:
        first = await scheduler.submit("priority", payload={"label": "first"}, priority=0)
        await asyncio.wait_for(started.wait(), timeout=5)
        low = await scheduler.submit("priority", payload={"label": "low"}, priority=1)
        high = await scheduler.submit("priority", payload={"label": "high"}, priority=10)
        release.set()
        assert await _wait_for(lambda: _all_succeeded([first, low, high]))
        assert order == ["first", "high", "low"]
    finally:
        release.set()
        await scheduler.stop()


async def _all_succeeded(task_ids: list[str]) -> bool:
    return all([await _task_status(task_id) == "succeeded" for task_id in task_ids])


async def test_equal_priority_tasks_preserve_fifo_order(db) -> None:
    gate = asyncio.Event()
    started = asyncio.Event()
    order: list[str] = []

    async def handler(ctx: TaskContext) -> dict:
        label = str(ctx.payload["label"])
        if label == "first":
            started.set()
            await gate.wait()
        order.append(label)
        return {}

    scheduler = await _make_scheduler({"fifo": handler})
    try:
        first = await scheduler.submit("fifo", payload={"label": "first"})
        await asyncio.wait_for(started.wait(), timeout=5)
        second = await scheduler.submit("fifo", payload={"label": "second"}, priority=4)
        third = await scheduler.submit("fifo", payload={"label": "third"}, priority=4)
        gate.set()
        assert await _wait_for(lambda: _all_succeeded([first, second, third]))
        assert order == ["first", "second", "third"]
    finally:
        gate.set()
        await scheduler.stop()


async def test_shutdown_requeues_interrupted_running_task(db) -> None:
    started = asyncio.Event()
    calls = 0

    async def handler(ctx: TaskContext) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.Event().wait()
        return {"calls": calls}

    scheduler = await _make_scheduler({"shutdown": handler})
    task_id = await scheduler.submit("shutdown")
    await asyncio.wait_for(started.wait(), timeout=5)
    await scheduler.stop()
    assert await _task_status(task_id) == "queued"

    fresh = TaskScheduler(SchedulerConfig(max_concurrency=1))
    fresh.register("shutdown", handler)
    await fresh.start()
    try:
        assert await _wait_for(lambda: _is_succeeded(task_id), timeout=10)
        assert calls == 2
    finally:
        await fresh.stop()


async def test_scheduler_reports_status(db) -> None:
    scheduler = await _make_scheduler({"x": lambda ctx: None}, concurrency=2)
    try:
        status = scheduler.status()
        assert status["concurrency"] == 2
        assert "x" in status["handlers"]
    finally:
        await scheduler.stop()
