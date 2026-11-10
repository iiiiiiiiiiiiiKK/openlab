"""Terminal and log endpoints backed by the real deploy transcripts."""

from __future__ import annotations


from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from engine.core.security import redact

from openlab.db import session_scope
from openlab.models import Project, TaskLog

router = APIRouter(prefix="/api/projects", tags=["terminal"])

LOG_FILES = {
    "deploy": "deploy.log",
    "build": "deploy.log",
    "browser": "browser.log",
    "host-install": "host-install.log",
    "host-runtime": "host-runtime.log",
}


@router.get("/{project_id}/logs")
async def project_logs(
    request: Request,
    project_id: str,
    name: str = Query("deploy", pattern=r"^(deploy|build|browser|host-install|host-runtime)$"),
    tail: int = Query(500, ge=1, le=5000),
) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    filename = LOG_FILES[name]
    log_dir = layout.logs_dir(project_id).resolve()
    path = (log_dir / filename).resolve()
    if path.parent != log_dir:
        raise HTTPException(status_code=400, detail="invalid log name")
    if not path.exists():
        return {"name": filename, "lines": [], "exists": False}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    lines = text.splitlines()[-tail:]
    return {
        "name": filename,
        "lines": [redact(line) for line in lines],
        "exists": True,
        "size": path.stat().st_size,
    }


@router.get("/{project_id}/logs/files")
async def log_files(request: Request, project_id: str) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    folder = layout.logs_dir(project_id)
    items = []
    for path in sorted(folder.glob("*")):
        if path.is_file():
            items.append({"name": path.name, "size": path.stat().st_size})
    return {"items": items}


@router.get("/{project_id}/terminal/history")
async def terminal_history(project_id: str, limit: int = 200) -> dict:
    """Recent command/task log lines formatted for the terminal panel."""
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(TaskLog)
                .where(TaskLog.project_id == project_id)
                .order_by(TaskLog.created_at.desc(), TaskLog.seq.desc())
                .limit(limit)
            )
        )
        rows.reverse()
        return {
            "lines": [
                {
                    "seq": r.seq,
                    "stream": r.stream,
                    "level": r.level,
                    "message": r.message,
                    "at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }


@router.post("/{project_id}/terminal/exec")
async def terminal_exec(request: Request, project_id: str, command: str = "") -> dict:
    """Run a command inside the project's running container (never on the host)."""
    from openlab.services.explore import resolve_project_url

    settings = request.app.state.settings
    if not command.strip():
        raise HTTPException(status_code=400, detail="command is required")
    if not resolve_project_url(project_id):
        raise HTTPException(status_code=409, detail="project is not running")
    from engine.docker_manager.runner import DockerRunner

    with session_scope() as session:
        project = session.get(Project, project_id)
        project_name = project.name if project else project_id
    runner = DockerRunner()
    compose_path = settings.resolve_data().project_dir(project_id) / "docker" / "compose.openlab.yml"
    containers = await runner.compose_ps(compose_path, project_name) if compose_path.exists() else []
    target = next((c.id for c in containers if c.service in ("app", "web", "main")), None)
    if not target and containers:
        target = containers[0].id
    if not target:
        raise HTTPException(status_code=409, detail="no running container for this project")
    result = await runner.exec_in(target, command, timeout=300)
    return {
        "ok": result.ok,
        "code": result.returncode,
        "stdout": redact(result.stdout)[-20000:],
        "stderr": redact(result.stderr)[-20000:],
        "duration_ms": result.duration_ms,
    }
