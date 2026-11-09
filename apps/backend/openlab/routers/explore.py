"""Preview, exploration and browser session endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from engine.browser_automation.launcher import BrowserUnavailable

from openlab.db import session_scope
from openlab.models import (
    BrowserSession,
    ExplorationPlan,
    ExplorationResult,
    ExplorationStep,
    Project,
)
from openlab.schemas import (
    BrowserSessionRequest,
    CustomTaskRequest,
    ExploreRequest,
    PreviewRequest,
)
from openlab.services.explore import (
    build_exploration_plan,
    capture_preview,
    create_browser_session,
    plan_summary,
    resolve_project_url,
    run_exploration,
)

router = APIRouter(prefix="/api/projects", tags=["explore"])


def _require_project(project_id: str) -> Project:
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project


def _project_url(project_id: str, override: str | None) -> str:
    if override:
        return override
    url = resolve_project_url(project_id)
    if not url:
        raise HTTPException(
            status_code=409,
            detail="the project is not running; deploy it first or pass an explicit url",
        )
    return url


@router.post("/{project_id}/preview")
async def preview(request: Request, project_id: str, body: PreviewRequest) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    url = _project_url(project_id, body.url)
    try:
        result = await capture_preview(
            layout, project_id, url, viewport=body.viewport, full_page=body.full_page
        )
    except BrowserUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result.as_dict()


@router.post("/{project_id}/explore/plan")
async def explore_plan(request: Request, project_id: str, body: ExploreRequest) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    url = _project_url(project_id, body.url)
    try:
        plan_id, plan = await build_exploration_plan(
            project_id, url, body.mode, body.goal, layout, request.app.state.box
        )
    except BrowserUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"plan_id": plan_id, "plan": plan}


@router.post("/{project_id}/explore")
async def explore(request: Request, project_id: str, body: ExploreRequest) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    url = _project_url(project_id, body.url)
    if body.plan_only:
        plan_id, plan = await build_exploration_plan(
            project_id, url, body.mode, body.goal, layout, request.app.state.box
        )
        return {"plan_id": plan_id, "plan": plan, "executed": False}
    task_id = await request.app.state.scheduler.submit(
        "explore",
        project_id=project_id,
        payload={
            "mode": body.mode,
            "goal": body.goal,
            "viewport": body.viewport,
            "record_video": body.record_video,
            "url": url,
        },
    )
    return {"task_id": task_id, "kind": "explore", "url": url}


@router.get("/{project_id}/explore/plans")
async def list_plans(project_id: str, limit: int = 20) -> dict:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ExplorationPlan)
                .where(ExplorationPlan.project_id == project_id)
                .order_by(ExplorationPlan.created_at.desc())
                .limit(limit)
            )
        )
        return {"items": [plan_summary(r) for r in rows]}


@router.get("/{project_id}/explore/plans/{plan_id}")
async def get_plan(project_id: str, plan_id: str) -> dict:
    with session_scope() as session:
        row = session.get(ExplorationPlan, plan_id)
        if row is None or row.project_id != project_id:
            raise HTTPException(status_code=404, detail="plan not found")
        steps = list(
            session.scalars(
                select(ExplorationStep)
                .where(ExplorationStep.plan_id == plan_id)
                .order_by(ExplorationStep.index.asc())
            )
        )
        results = list(
            session.scalars(
                select(ExplorationResult)
                .where(ExplorationResult.plan_id == plan_id)
                .order_by(ExplorationResult.created_at.asc())
            )
        )
        payload = plan_summary(row)
        payload["steps"] = [
            {
                "id": s.id,
                "index": s.index,
                "action": s.action,
                "target": s.target,
                "value": s.value,
                "description": s.description,
                "status": s.status,
                "duration_ms": s.duration_ms,
                "error": s.error,
            }
            for s in steps
        ]
        payload["results"] = [
            {
                "id": r.id,
                "url": r.url,
                "title": r.title,
                "status": r.status,
                "observation": r.observation,
                "console_errors": r.console_errors,
                "network_errors": r.network_errors,
                "screenshot_asset_id": r.screenshot_asset_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in results
        ]
        return payload


@router.post("/{project_id}/browser-sessions")
async def open_browser_session(request: Request, project_id: str, body: BrowserSessionRequest) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    url = _project_url(project_id, body.url)
    try:
        return await create_browser_session(
            layout, project_id, body.name, url, timeout_s=body.wait_seconds
        )
    except BrowserUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{project_id}/browser-sessions")
async def list_browser_sessions(project_id: str) -> dict:
    with session_scope() as session:
        rows = list(
            session.scalars(select(BrowserSession).where(BrowserSession.project_id == project_id))
        )
        return {
            "items": [
                {
                    "id": r.id,
                    "name": r.name,
                    "url": r.url,
                    "is_authenticated": r.is_authenticated,
                    "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                }
                for r in rows
            ]
        }
