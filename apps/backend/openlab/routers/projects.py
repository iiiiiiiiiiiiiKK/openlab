"""Project import, listing and lifecycle endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select

from engine.core.paths import UnsafePath
from engine.core.security import redact
from engine.project_analyzer.analyzer import analyze_project
from engine.project_importer.github import ImportFailed
from engine.project_importer.repo_url import InvalidRepoUrl

from openlab.db import session_scope
from openlab.models import (
    EnvironmentSnapshot,
    Patch,
    Project,
    ProjectContainer,
    ProjectEnvironment,
    ProjectRepository,
    ProjectTask,
)
from openlab.schemas import (
    DeployRequest,
    GitHubImportRequest,
    LocalImportRequest,
    ProjectUpdate,
    SnapshotRequest,
)
from openlab.services import importer
from openlab.services.deploy import reset_project_workspace, stop_project
from openlab.services.lifecycle import (
    LifecycleError,
    delete_project_data,
    restore_snapshot,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _project_payload(project: Project, environment: ProjectEnvironment | None = None) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "display_name": project.display_name,
        "description": project.description,
        "source_type": project.source_type,
        "source_url": project.source_url,
        "license": project.license,
        "author": project.author,
        "stars": project.stars,
        "forks": project.forks,
        "language": project.language,
        "topics": project.topics or [],
        "favorite": project.favorite,
        "tags": project.tags or [],
        "archived": project.archived,
        "deploy_status": project.deploy_status,
        "deploy_status_detail": project.deploy_status_detail,
        "last_error": project.last_error,
        "last_run_at": project.last_run_at.isoformat() if project.last_run_at else None,
        "asset_count": project.asset_count,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "environment": {
            "strategy": environment.strategy,
            "runtime_language": environment.runtime_language,
            "runtime_version": environment.runtime_version,
            "package_manager": environment.package_manager,
            "framework": (environment.runtime_plan or {}).get("framework"),
            "port": environment.port,
            "confidence": environment.confidence,
            "gpu_required": environment.gpu_required,
            "services": environment.services or [],
        }
        if environment
        else None,
    }


@router.get("")
async def list_projects(
    q: str | None = None,
    status: str | None = None,
    favorite: bool | None = None,
    sort: str = "updated",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    with session_scope() as session:
        query = select(Project)
        if q:
            like = f"%{q.lower()}%"
            query = query.where(
                (Project.name.ilike(like))
                | (Project.display_name.ilike(like))
                | (Project.description.ilike(like))
                | (Project.source_url.ilike(like))
            )
        if status:
            query = query.where(Project.deploy_status == status)
        if favorite is not None:
            query = query.where(Project.favorite.is_(favorite))
        order = {
            "updated": Project.updated_at.desc(),
            "created": Project.created_at.desc(),
            "name": Project.name.asc(),
            "stars": Project.stars.desc(),
            "status": Project.deploy_status.asc(),
        }.get(sort, Project.updated_at.desc())
        rows = list(session.scalars(query.order_by(order).offset(offset).limit(limit)))
        environments = {
            env.project_id: env
            for env in session.scalars(
                select(ProjectEnvironment)
                .where(ProjectEnvironment.project_id.in_([r.id for r in rows] or [""]))
                .order_by(ProjectEnvironment.created_at.asc())
            )
        }
        return {
            "items": [_project_payload(row, environments.get(row.id)) for row in rows],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
        }


@router.post("/import/github")
async def import_github(request: Request, body: GitHubImportRequest) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    token = body.token or request.app.state.secret_store.get("github_token") or settings.github_token
    try:
        with session_scope() as session:
            result = await importer.import_from_github(
                session,
                layout,
                body.url,
                token=token,
                max_download_mb=settings.max_download_mb,
                max_unpacked_mb=settings.max_unpacked_mb,
            )
    except (InvalidRepoUrl, ImportFailed) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "project_id": result.project_id,
        "name": result.name,
        "files": result.files,
        "bytes": result.bytes,
        "analysis": result.analysis,
    }


@router.post("/import/zip")
async def import_zip(
    request: Request,
    file: UploadFile = File(...),
    display_name: str | None = Form(None),
) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    suffix = Path(file.filename or "upload.zip").suffix or ".zip"
    tmp_dir = layout.tmp_dir
    tmp_path = tmp_dir / f"upload-{uuid.uuid4().hex}{suffix}"
    try:
        with tmp_path.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                fh.write(chunk)
        with session_scope() as session:
            result = importer.import_from_zip(
                session,
                layout,
                tmp_path,
                display_name=display_name or Path(file.filename or "archive").stem,
                max_files=settings.max_file_count,
                max_unpacked_mb=settings.max_unpacked_mb,
            )
    except ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    return {
        "project_id": result.project_id,
        "name": result.name,
        "files": result.files,
        "bytes": result.bytes,
        "analysis": result.analysis,
    }


@router.post("/import/local")
async def import_local(request: Request, body: LocalImportRequest) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    try:
        with session_scope() as session:
            result = importer.import_from_folder(
                session,
                layout,
                Path(body.path),
                display_name=body.display_name,
                max_files=settings.max_file_count,
                max_unpacked_mb=settings.max_unpacked_mb,
            )
    except (ImportFailed, UnsafePath) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "project_id": result.project_id,
        "name": result.name,
        "files": result.files,
        "bytes": result.bytes,
        "analysis": result.analysis,
    }


@router.get("/{project_id}")
async def get_project(project_id: str) -> dict:
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        environment = session.scalar(
            select(ProjectEnvironment)
            .where(ProjectEnvironment.project_id == project_id)
            .order_by(ProjectEnvironment.created_at.desc())
        )
        repository = session.scalar(
            select(ProjectRepository).where(ProjectRepository.project_id == project_id)
        )
        containers = list(
            session.scalars(select(ProjectContainer).where(ProjectContainer.project_id == project_id))
        )
        tasks = list(
            session.scalars(
                select(ProjectTask)
                .where(ProjectTask.project_id == project_id)
                .order_by(ProjectTask.created_at.desc())
                .limit(20)
            )
        )
        payload = _project_payload(project, environment)
        payload["repository"] = (
            {
                "owner": repository.owner,
                "repo": repository.repo,
                "html_url": repository.html_url,
                "default_branch": repository.default_branch,
                "commit_sha": repository.commit_sha,
                "readme_excerpt": (repository.readme_excerpt or "")[:8000],
            }
            if repository
            else None
        )
        payload["plan"] = environment.runtime_plan if environment else {}
        payload["containers"] = [
            {
                "service_name": c.service_name,
                "status": c.status,
                "host_port": c.host_port,
                "container_port": c.container_port,
                "image": c.image,
                "health_state": c.health_state,
            }
            for c in containers
        ]
        payload["tasks"] = [
            {
                "id": t.id,
                "kind": t.kind,
                "status": t.status,
                "stage": t.stage,
                "progress": t.progress,
                "message": t.message,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tasks
        ]
        return payload


@router.patch("/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate) -> dict:
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        if body.display_name is not None:
            project.display_name = redact(body.display_name)[:200]
        if body.favorite is not None:
            project.favorite = body.favorite
        if body.tags is not None:
            project.tags = [redact(t)[:40] for t in body.tags][:20]
        if body.description is not None:
            project.description = redact(body.description)[:2000]
        session.commit()
        return _project_payload(project)


@router.post("/{project_id}/deploy")
async def deploy_project(request: Request, project_id: str, body: DeployRequest | None = None) -> dict:
    scheduler = request.app.state.scheduler
    with session_scope() as session:
        if session.get(Project, project_id) is None:
            raise HTTPException(status_code=404, detail="project not found")
    task_id = await scheduler.submit(
        "deploy", project_id=project_id, payload={"rebuild": bool(body.rebuild) if body else True}
    )
    return {"task_id": task_id, "kind": "deploy", "project_id": project_id}


@router.post("/{project_id}/stop")
async def stop(request: Request, project_id: str) -> dict:
    settings = request.app.state.settings
    try:
        return await stop_project(project_id, settings.resolve_data(), settings)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{project_id}/reset-workspace")
async def reset_workspace(request: Request, project_id: str) -> dict:
    settings = request.app.state.settings
    return await reset_project_workspace(project_id, settings.resolve_data())


@router.post("/{project_id}/analyze")
async def reanalyze(request: Request, project_id: str) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    try:
        workspace = layout.project_subdir(project_id, "workspace")
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    analysis = analyze_project(workspace, project_name=project_id)
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        environment = session.scalar(
            select(ProjectEnvironment)
            .where(ProjectEnvironment.project_id == project_id)
            .order_by(ProjectEnvironment.created_at.desc())
        )
        if environment is not None:
            environment.runtime_plan = analysis.plan.as_dict()
            environment.confidence = analysis.plan.confidence
            environment.strategy = analysis.plan.strategy
            environment.start_command = analysis.plan.start_command
            environment.install_command = analysis.plan.install_command
            environment.build_command = analysis.plan.build_command
            environment.port = analysis.plan.port
            environment.env_vars = analysis.plan.env_vars
            environment.services = [s.__dict__ for s in analysis.plan.services]
        session.commit()
    return {"analysis": analysis.as_dict()}


@router.get("/{project_id}/tree")
async def project_tree(project_id: str, request: Request, path: str = "", depth: int = 2) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    workspace = layout.project_subdir(project_id, "workspace")
    target = (workspace / path).resolve()
    if workspace.resolve() not in target.parents and target != workspace.resolve():
        raise HTTPException(status_code=400, detail="path escapes the workspace")
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")
    return {"entries": _walk_entries(target, workspace, depth)}


def _walk_entries(root: Path, base: Path, depth: int) -> list[dict]:
    ignore = {".git", "node_modules", ".venv", "__pycache__", ".next", "dist", "build"}
    entries: list[dict] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return entries
    for child in children:
        if child.name in ignore:
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        entry = {
            "name": child.name,
            "path": child.relative_to(base).as_posix(),
            "type": "dir" if child.is_dir() else "file",
            "size": 0 if child.is_dir() else stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }
        if child.is_dir() and depth > 1:
            entry["children"] = _walk_entries(child, base, depth - 1)
        entries.append(entry)
    return entries


@router.get("/{project_id}/file")
async def project_file(project_id: str, request: Request, path: str) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    workspace = layout.project_subdir(project_id, "workspace")
    target = (workspace / path).resolve()
    if workspace.resolve() not in target.parents:
        raise HTTPException(status_code=400, detail="path escapes the workspace")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if target.stat().st_size > 1_000_000:
        raise HTTPException(status_code=413, detail="file too large to display")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"path": path, "content": redact(content), "size": target.stat().st_size}


@router.get("/{project_id}/patches")
async def list_patches(project_id: str) -> dict:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(Patch).where(Patch.project_id == project_id).order_by(Patch.created_at.asc())
            )
        )
        return {
            "items": [
                {
                    "id": p.id,
                    "attempt": p.attempt,
                    "reason": p.reason,
                    "diagnosis": p.diagnosis,
                    "files_touched": p.files_touched,
                    "applied": p.applied,
                    "rolled_back": p.rolled_back,
                    "model": p.model,
                    "diff": p.diff,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
                for p in rows
            ]
        }


@router.post("/{project_id}/snapshots")
async def create_snapshot(request: Request, project_id: str, body: SnapshotRequest | None = None) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        environment = session.scalar(
            select(ProjectEnvironment)
            .where(ProjectEnvironment.project_id == project_id)
            .order_by(ProjectEnvironment.created_at.desc())
        )
        containers = list(
            session.scalars(select(ProjectContainer).where(ProjectContainer.project_id == project_id))
        )
        plan = (environment.runtime_plan if environment else {}) or {}
        compose_path = layout.project_dir(project_id) / "docker" / "compose.openlab.yml"
        snapshot = EnvironmentSnapshot(
            project_id=project_id,
            label=(body.label if body else None) or "manual snapshot",
            compose_path=str(compose_path) if compose_path.exists() else None,
            env_names=[v.get("name") for v in plan.get("env_vars") or [] if isinstance(v, dict)],
            start_commands=[plan.get("start_command") or ""],
            ports={c.service_name: c.host_port for c in containers},
            browser_tasks=[],
            snapshot={
                "plan": plan,
                "compose": compose_path.read_text(encoding="utf-8", errors="replace")[:20000]
                if compose_path.exists()
                else "",
                "containers": [
                    {"service": c.service_name, "image": c.image, "status": c.status}
                    for c in containers
                ],
            },
        )
        session.add(snapshot)
        session.flush()
        return {"id": snapshot.id, "label": snapshot.label}


@router.get("/{project_id}/snapshots")
async def list_snapshots(project_id: str) -> dict:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(EnvironmentSnapshot)
                .where(EnvironmentSnapshot.project_id == project_id)
                .order_by(EnvironmentSnapshot.created_at.desc())
            )
        )
        return {
            "items": [
                {
                    "id": s.id,
                    "label": s.label,
                    "compose_path": s.compose_path,
                    "env_names": s.env_names,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in rows
            ]
        }


@router.delete("/{project_id}")
async def delete_project(
    request: Request,
    project_id: str,
    keep_assets: bool = True,
    keep_reports: bool = True,
) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    try:
        return delete_project_data(
            layout, project_id, keep_assets=keep_assets, keep_reports=keep_reports
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{project_id}/snapshots/{snapshot_id}/restore")
async def restore_project_snapshot(request: Request, project_id: str, snapshot_id: str) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    try:
        return restore_snapshot(layout, project_id, snapshot_id)
    except LifecycleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/usage")
async def project_usage(request: Request, project_id: str) -> dict:
    client = request.app.state.ai_client()
    try:
        return client.usage_summary(project_id=project_id)
    finally:
        client.session.close()
