"""Report assembly: pulls every recorded fact for a project into ReportInput."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from engine.core.security import SecretBox
from engine.report_generator.report import ReportInput, WrittenReport, write_reports

from openlab.db import session_scope
from openlab.models import (
    AIUsageRecord,
    Asset,
    ExplorationPlan,
    ExplorationResult,
    ExplorationStep,
    Patch,
    Project,
    ProjectContainer,
    ProjectEnvironment,
    ProjectRepository,
    Report,
)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _probe_http_status(port: int | None, timeout: float = 5.0) -> int | None:
    """Ask the running service for its real status code; None when it does not answer.

    Reports used to print "answered HTTP health checks (status 200)" purely because
    the project row said RUNNING. Anything written into a report has to be observed.
    """
    if not port:
        return None
    import socket

    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(("127.0.0.1", int(port)))
    except OSError:
        return None
    finally:
        connection.close()
    try:
        import urllib.error
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/", timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:  # 4xx/5xx still proves the service answered
        return int(exc.code)
    except Exception:  # noqa: BLE001 - a probe must never break report generation
        return None


def collect_report_input(project_id: str, box: SecretBox) -> ReportInput:
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        repository = session.scalar(
            select(ProjectRepository).where(ProjectRepository.project_id == project_id)
        )
        environment = session.scalar(
            select(ProjectEnvironment)
            .where(ProjectEnvironment.project_id == project_id)
            .order_by(ProjectEnvironment.created_at.desc())
        )
        containers = list(
            session.scalars(select(ProjectContainer).where(ProjectContainer.project_id == project_id))
        )
        patches = list(
            session.scalars(
                select(Patch)
                .where(Patch.project_id == project_id)
                .order_by(Patch.created_at.asc())
            )
        )
        plan = session.scalar(
            select(ExplorationPlan)
            .where(ExplorationPlan.project_id == project_id)
            .order_by(ExplorationPlan.created_at.desc())
        )
        results: list[ExplorationResult] = []
        steps: list[ExplorationStep] = []
        if plan is not None:
            results = list(
                session.scalars(
                    select(ExplorationResult)
                    .where(ExplorationResult.plan_id == plan.id)
                    .order_by(ExplorationResult.created_at.asc())
                )
            )
            steps = list(
                session.scalars(
                    select(ExplorationStep)
                    .where(ExplorationStep.plan_id == plan.id)
                    .order_by(ExplorationStep.index.asc())
                )
            )
        assets = list(
            session.scalars(
                select(Asset)
                .where(Asset.project_id == project_id)
                .order_by(Asset.created_at.asc())
            )
        )
        usage_rows = list(
            session.scalars(select(AIUsageRecord).where(AIUsageRecord.project_id == project_id))
        )

        env_plan = (environment.runtime_plan if environment else {}) or {}
        app_container = next(
            (c for c in containers if c.service_name in ("app", "web", "main")), None
        ) or (containers[0] if containers else None)
        port = app_container.host_port if app_container else env_plan.get("port")
        running = project.deploy_status == "RUNNING"

        deploy = {
            "success": running,
            "status": project.deploy_status,
            "stage": project.deploy_status,
            "port": port,
            # The status code is probed, never assumed: asserting "HTTP 200" from the
            # deploy flag alone turned the report into a claim nothing had verified.
            "http_status": _probe_http_status(port) if running else None,
            "url": f"http://127.0.0.1:{port}" if port and running else None,
            "error": project.last_error,
            "blocked": project.deploy_status in ("WAITING_FOR_USER",),
            "strategy": environment.strategy if environment else None,
            "duration_s": None,
            "health_url": f"http://127.0.0.1:{port}{environment.health_path or '/'}" if port else None,
            "containers": [
                {
                    "service": c.service_name,
                    "image": c.image,
                    "status": c.status,
                    "host_port": c.host_port,
                }
                for c in containers
            ],
        }

        features: list[dict] = []
        if plan is not None:
            step_map = {s.id: s for s in steps}
            per_step_shots: dict[str, str] = {}
            for asset in assets:
                if asset.step_id:
                    per_step_shots[asset.step_id] = asset.relative_path
            grouped: list[dict] = []
            for result in results:
                step = step_map.get(result.step_id) if result.step_id else None
                screenshots: list[str] = []
                if result.screenshot_asset_id:
                    shot = next((a for a in assets if a.id == result.screenshot_asset_id), None)
                    if shot is not None:
                        screenshots.append(shot.relative_path)
                grouped.append(
                    {
                        "index": step.index if step else len(grouped),
                        "action": step.action if step else "goto",
                        "target": step.target if step else result.url,
                        "status": result.status,
                        "url": result.url,
                        "observation": result.observation,
                        "screenshots": screenshots,
                        "console_errors": result.console_errors or [],
                        "network_errors": result.network_errors or [],
                    }
                )
            verified = sum(1 for g in grouped if g["status"] in ("VERIFIED", "PARTIALLY_VERIFIED"))
            feature_status = (
                "VERIFIED"
                if grouped and verified == len(grouped)
                else "PARTIALLY_VERIFIED"
                if verified
                else "FAILED"
                if grouped
                else "UNVERIFIED"
            )
            features.append(
                {
                    "name": f"Exploration ({plan.mode})",
                    "description": plan.summary or plan.goal or "Automated exploration",
                    "status": feature_status,
                    "steps": grouped,
                    "screenshots": [s for g in grouped for s in g["screenshots"]],
                    "failure_reason": None
                    if feature_status in ("VERIFIED", "PARTIALLY_VERIFIED")
                    else "One or more exploration steps failed",
                }
            )

        technical = dict(env_plan.get("tech_stack") or {})
        if not technical:
            technical = {
                "primary_language": project.language,
                "framework": env_plan.get("framework"),
                "services": [s.get("name") for s in (env_plan.get("services") or [])],
                "databases": [
                    s.get("name")
                    for s in (env_plan.get("services") or [])
                    if s.get("name") in {"postgres", "mysql", "mariadb", "sqlite", "mongodb"}
                ],
                "external_apis": env_plan.get("external_apis") or [],
                "gpu_required": env_plan.get("gpu_required", False),
                "has_dockerfile": env_plan.get("strategy") in ("dockerfile", "compose"),
                "has_compose": env_plan.get("strategy") == "compose",
                "dependencies": [],
            }
        technical.setdefault("package_manager", environment.package_manager if environment else None)
        technical.setdefault("runtime_version", environment.runtime_version if environment else None)

        environment_payload = {
            "strategy": environment.strategy if environment else None,
            "base_image": environment.base_image if environment else None,
            "runtime_language": environment.runtime_language if environment else None,
            "runtime_version": environment.runtime_version if environment else None,
            "package_manager": environment.package_manager if environment else None,
            "install_command": environment.install_command if environment else None,
            "build_command": environment.build_command if environment else None,
            "start_command": environment.start_command if environment else None,
            "port": environment.port if environment else None,
            "health_path": environment.health_path if environment else None,
            "env_vars": environment.env_vars if environment else [],
            "confidence": environment.confidence if environment else None,
        }

        usage = {
            "requests": sum(r.requests for r in usage_rows),
            "tokens": sum(r.total_tokens for r in usage_rows),
            "cost_usd": round(sum(r.cost_usd for r in usage_rows), 6),
            "records_with_unknown_usage": sum(1 for r in usage_rows if not r.usage_is_known),
        }

        return ReportInput(
            project={
                "name": project.name,
                "display_name": project.display_name,
                "description": project.description,
                "source_url": project.source_url,
                "stars": project.stars,
                "license": project.license,
                "language": project.language,
                "author": project.author,
            },
            repository={
                "owner": repository.owner if repository else None,
                "repo": repository.repo if repository else None,
                "default_branch": repository.default_branch if repository else None,
                "commit_sha": repository.commit_sha if repository else None,
            },
            environment=environment_payload,
            deploy=deploy,
            features=features,
            technical=technical,
            assets=[
                {
                    "kind": a.kind,
                    "filename": a.filename,
                    "relative_path": a.relative_path,
                    "width": a.width,
                    "height": a.height,
                    "size_bytes": a.size_bytes,
                }
                for a in assets
            ],
            patches=[
                {
                    "attempt": p.attempt,
                    "diagnosis": p.diagnosis,
                    "files": p.files_touched,
                    "diff": (p.diff or "")[:8000],
                }
                for p in patches
            ],
            usage=usage,
            exploration={"plan_id": plan.id, "mode": plan.mode, "status": plan.status}
            if plan
            else {},
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )


def generate_report(project_id: str, layout, box: SecretBox) -> dict:
    data = collect_report_input(project_id, box)
    reports_dir = layout.project_subdir(project_id, "reports")
    version = 1
    with session_scope() as session:
        existing = session.scalar(
            select(Report).where(Report.project_id == project_id).order_by(Report.version.desc())
        )
        if existing is not None:
            version = existing.version + 1
    written: WrittenReport = write_reports(data, reports_dir, basename=f"report-v{version}")
    with session_scope() as session:
        payload = json.loads(written.json_path.read_text(encoding="utf-8"))
        row = Report(
            project_id=project_id,
            version=version,
            status=written.status,
            title=written.title,
            markdown_path=str(written.markdown),
            html_path=str(written.html),
            json_path=str(written.json_path),
            data=payload,
            generated_by="engine",
        )
        session.add(row)
        session.flush()
        report_id = row.id
    return {
        "id": report_id,
        "version": version,
        "status": written.status,
        "title": written.title,
        "markdown_path": str(written.markdown),
        "html_path": str(written.html),
        "json_path": str(written.json_path),
        "data": payload,
    }


def report_row_summary(row: Report) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "version": row.version,
        "status": row.status,
        "title": row.title,
        "markdown_path": row.markdown_path,
        "html_path": row.html_path,
        "json_path": row.json_path,
        "created_at": _iso(row.created_at),
        "data": row.data or {},
    }
