"""Task queue endpoints: list, inspect logs, cancel/pause/resume/retry."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from engine.task_scheduler.scheduler import log_row_to_dict, task_row_to_dict

from openlab.db import session_scope
from openlab.models import ProjectTask, TaskLog
from openlab.schemas import TaskAction

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    with session_scope() as session:
        query = select(ProjectTask)
        if project_id:
            query = query.where(ProjectTask.project_id == project_id)
        if status:
            query = query.where(ProjectTask.status == status)
        rows = list(session.scalars(query.order_by(ProjectTask.created_at.desc()).limit(limit)))
        return {"items": [task_row_to_dict(r) for r in rows]}


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    with session_scope() as session:
        row = session.get(ProjectTask, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        logs = list(
            session.scalars(
                select(TaskLog).where(TaskLog.task_id == task_id).order_by(TaskLog.seq.asc())
            )
        )
        payload = task_row_to_dict(row)
        payload["logs"] = [log_row_to_dict(row) for row in logs]
        return payload


@router.get("/{task_id}/logs")
async def task_logs(task_id: str, since_seq: int = 0, limit: int = 2000) -> dict:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(TaskLog)
                .where(TaskLog.task_id == task_id, TaskLog.seq > since_seq)
                .order_by(TaskLog.seq.asc())
                .limit(limit)
            )
        )
        return {"items": [log_row_to_dict(r) for r in rows]}


@router.post("/{task_id}/action")
async def task_action(request: Request, task_id: str, body: TaskAction) -> dict:
    scheduler = request.app.state.scheduler
    if body.action == "cancel":
        ok = await scheduler.cancel(task_id)
    elif body.action == "pause":
        ok = await scheduler.pause(task_id)
    elif body.action == "resume":
        ok = await scheduler.resume(task_id)
    else:
        new_id = await scheduler.retry(task_id)
        return {"ok": new_id is not None, "new_task_id": new_id}
    if not ok:
        raise HTTPException(status_code=409, detail=f"cannot {body.action} task {task_id}")
    return {"ok": True}


@router.get("/queue/status")
async def queue_status(request: Request) -> dict:
    return request.app.state.scheduler.status()
