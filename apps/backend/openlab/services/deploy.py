"""Deployment pipeline: prepare -> build -> start -> health check -> repair.

Guarantees:
* a project only becomes ``RUNNING`` after a real HTTP response was received;
* missing Docker, a GPU-only project or a missing API key ends as ``WAITING_FOR_USER``
  with an explanation instead of a fake success;
* automatic repair is capped (default 3 attempts) and every patch is logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from engine.ai_agent.client import AIClient, Budget, NoProviderConfigured
from engine.ai_agent.repair import RepairContext, run_repair_loop
from engine.core.security import SecretBox, redact
from engine.core.states import DeployStatus, assert_transition
from engine.docker_manager.docker_cli import docker_status
from engine.docker_manager.generator import (
    PolicyViolation,
    discover_existing_compose,
    discover_existing_dockerfile,
    generate_compose,
    generate_dockerfile,
    guard_compose_text,
    normalize_compose_build_contexts,
    required_service_containers,
    write_compose,
)
from engine.docker_manager.health import allocate_port, http_ready
from engine.docker_manager.runner import DockerRunner, DockerUnavailable
from engine.project_analyzer.analyzer import analyze_project

from openlab.config import Settings
from openlab.db import session_scope
from openlab.events import bus
from openlab.models import (
    EnvironmentSnapshot,
    Patch,
    Project,
    ProjectContainer,
    ProjectEnvironment,
)
from openlab.services.importer import reset_workspace

from engine.task_scheduler.scheduler import TaskContext

log = logging.getLogger("openlab.deploy")


@dataclass
class DeployOutcome:
    success: bool
    status: str
    port: int | None = None
    http_status: int | None = None
    error: str | None = None
    stage: str | None = None
    blocked: bool = False
    patches: list[dict] = field(default_factory=list)
    duration_s: float = 0.0
    containers: list[dict] = field(default_factory=list)
    url: str | None = None

    def as_dict(self) -> dict:
        return {
            "success": self.success,
            "status": self.status,
            "port": self.port,
            "http_status": self.http_status,
            "error": self.error,
            "stage": self.stage,
            "blocked": self.blocked,
            "patches": self.patches,
            "duration_s": self.duration_s,
            "containers": self.containers,
            "url": self.url,
        }


def _set_status(project_id: str, target: str, detail: str | None = None, error: str | None = None) -> None:
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            return
        try:
            assert_transition(project.deploy_status, target)
        except Exception as exc:  # noqa: BLE001 - transitions are advisory at this layer
            # Keep the write (the pipeline must be able to recover from a stale row),
            # but leave a trace instead of silently bypassing the state machine.
            log.warning(
                "deploy status %s -> %s for %s bypassed the state machine: %s",
                project.deploy_status,
                target,
                project_id,
                exc,
            )
        project.deploy_status = target
        project.deploy_status_detail = redact(detail)[:2000] if detail else None
        if error is not None:
            project.last_error = redact(error)[:4000]
        if target == DeployStatus.RUNNING.value:
            project.last_run_at = datetime.now(timezone.utc)
    bus.publish("project.status", {"project_id": project_id, "status": target, "detail": detail})


def load_environment(session, project_id: str) -> ProjectEnvironment | None:
    return session.scalar(
        select(ProjectEnvironment)
        .where(ProjectEnvironment.project_id == project_id)
        .order_by(ProjectEnvironment.created_at.desc())
    )


async def prepare_environment(
    ctx: TaskContext,
    project_id: str,
    settings: Settings,
    layout,
) -> dict:
    """Create Dockerfile/compose in ``projects/<id>/docker`` and return the plan."""
    workspace_dir = layout.project_subdir(project_id, "workspace")
    docker_dir = layout.project_subdir(project_id, "docker")

    analysis = analyze_project(workspace_dir, project_name=project_id)
    plan = analysis.plan
    await ctx.log(
        f"detected: type={plan.project_type} language={plan.language} framework={plan.framework} "
        f"pm={plan.package_manager} strategy={plan.strategy} confidence={plan.confidence}"
    )
    if plan.gpu_required:
        await ctx.log(
            "GPU requirement detected: " + "; ".join(plan.gpu_notes or ["unspecified"]), level="warn"
        )

    with session_scope() as session:
        env_row = load_environment(session, project_id)
        if env_row is None:
            env_row = ProjectEnvironment(project_id=project_id)
            session.add(env_row)
        env_row.runtime_plan = plan.as_dict()
        env_row.strategy = plan.strategy
        env_row.base_image = plan.base_image
        env_row.runtime_language = plan.language
        env_row.runtime_version = plan.runtime_version
        env_row.package_manager = plan.package_manager
        env_row.install_command = plan.install_command
        env_row.build_command = plan.build_command
        env_row.start_command = plan.start_command
        env_row.port = plan.port
        env_row.health_path = plan.health_path
        env_row.env_vars = plan.env_vars
        env_row.services = [s.__dict__ for s in plan.services]
        env_row.gpu_required = plan.gpu_required
        env_row.external_apis = plan.external_apis
        env_row.confidence = plan.confidence
        env_row.notes = "; ".join(plan.warnings) or None

    # The compose file must be generated relative to the workspace context.
    compose_target_name = "compose.openlab.yml"
    dockerfile_name = "Dockerfile"

    if plan.strategy == "compose":
        existing = discover_existing_compose(workspace_dir)
        if existing is not None:
            text = existing.read_text(encoding="utf-8", errors="replace")
            problems = guard_compose_text(text)
            if problems:
                raise PolicyViolation("; ".join(problems))
            text = normalize_compose_build_contexts(
                text,
                existing.parent,
                workspace_dir,
                output_compose_dir=layout.project_dir(project_id) / "docker",
            )
            compose_path = write_compose(project_id_dir(layout, project_id), compose_target_name, text)
            await ctx.log(f"using repository compose file: {existing.name}")
        else:
            compose_path = await _generate_compose_files(ctx, layout, project_id, plan, settings)
    elif plan.strategy == "dockerfile":
        write_compose(
            project_id_dir(layout, project_id),
            dockerfile_name,
            build_dockerfile_from_repo(workspace_dir),
        )
        await ctx.log(f"using repository Dockerfile -> docker/{dockerfile_name}")
        compose_path = await _generate_compose_files(
            ctx, layout, project_id, plan, settings, dockerfile=dockerfile_name
        )
    else:
        generated = generate_dockerfile(plan)
        write_compose(project_id_dir(layout, project_id), dockerfile_name, generated)
        await ctx.log(f"generated docker/{dockerfile_name} from the run plan")
        compose_path = await _generate_compose_files(
            ctx, layout, project_id, plan, settings, dockerfile=dockerfile_name
        )

    return {"plan": plan.as_dict(), "compose_path": str(compose_path), "docker_dir": str(docker_dir)}


def project_id_dir(layout, project_id: str) -> Path:
    return layout.project_dir(project_id)


def _build_context_relpath(layout, project_id: str) -> str:
    """Compose-relative path from <project>/docker to the writable workspace copy.

    Compose resolves ``build.context`` against the directory that holds the compose
    file. Using ``..`` pointed Docker at <project>/, which contains source/,
    screenshots/, logs/ and the old build output, so ``COPY . /app`` never received
    the application at the image root. The workspace is the only tree the AI may
    modify, so it is also the correct build context.
    """
    docker_dir = layout.project_dir(project_id) / "docker"
    workspace_dir = layout.project_subdir(project_id, "workspace")
    relative = os.path.relpath(workspace_dir, docker_dir).replace(os.sep, "/")
    return relative or "."


def build_dockerfile_from_repo(workspace_dir: Path) -> str:
    """Copy the repository Dockerfile but force non-root execution when absent."""
    source = discover_existing_dockerfile(workspace_dir)
    text = source.read_text(encoding="utf-8", errors="replace") if source else ""
    if not text.strip():
        raise PolicyViolation("repository Dockerfile is empty")
    lines = text.splitlines()
    has_user = any(line.strip().upper().startswith("USER ") for line in lines)
    if not has_user:
        lines.append("")
        lines.append("# OPENLAB: run as an unprivileged user (security baseline)")
        lines.append("RUN id -u openlab >/dev/null 2>&1 || useradd --create-home --uid 10001 openlab")
        lines.append("USER openlab")
    return "\n".join(lines) + "\n"


async def _generate_compose_files(
    ctx: TaskContext,
    layout,
    project_id: str,
    plan,
    settings: Settings,
    dockerfile: str = "Dockerfile",
) -> Path:
    host_port = allocate_port(settings.port_range_start, settings.port_range_end)
    extra = required_service_containers(plan, project_key=project_id)
    content = generate_compose(
        plan,
        layout.project_dir(project_id),
        image_tag=f"openlab/{plan.project_name}:latest",
        host_port=host_port,
        cpu_limit=settings.container_cpu_limit,
        memory_limit_mb=settings.container_memory_limit_mb,
        pids_limit=settings.container_pids_limit,
        allow_network=settings.allow_container_network,
        dockerfile=dockerfile,
        extra_services=extra,
        # The compose file lives in <project>/docker, so the workspace is a sibling.
        build_context=_build_context_relpath(layout, project_id),
        project_key=project_id,
    )
    path = write_compose(layout.project_dir(project_id), "compose.openlab.yml", content)
    await ctx.log(
        f"generated compose file with host port {host_port} "
        f"(cpu {settings.container_cpu_limit}, mem {settings.container_memory_limit_mb}MB)"
    )
    return path


def _compose_host_port(compose_path: Path) -> int | None:
    import re

    text = compose_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"127\.0\.0\.1:(\d{2,5}):", text)
    return int(match.group(1)) if match else None


async def deploy_project(ctx: TaskContext, settings: Settings, layout, box: SecretBox) -> DeployOutcome:
    """Full deployment pipeline for one project."""
    started = time.monotonic()
    project_id = ctx.project_id or str(ctx.payload.get("project_id") or "")
    if not project_id:
        raise ValueError("deploy task requires a project_id")

    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        project_name = project.name or project_id
        name_for_compose = project_name

    status = await docker_status()
    if not status.available or not status.daemon_running:
        message = (
            "Docker is required to run projects. "
            + (status.error or "Docker CLI is not available.")
            + (" " + (status.install_hint or "") if status.install_hint else "")
        )
        if settings.allow_host_runtime:
            await ctx.log(
                "Docker is unavailable; allowing_host_runtime is enabled so OPENLAB will try the "
                "unsandboxed host runtime instead.",
                level="warn",
            )
            return await deploy_on_host(ctx, settings, layout, project_id, project_name)
        _set_status(project_id, DeployStatus.WAITING_FOR_USER.value, message)
        await ctx.log(message, level="error")
        await ctx.emit("deploy.blocked", {"reason": "docker_unavailable", "message": message})
        return DeployOutcome(
            False, DeployStatus.WAITING_FOR_USER.value, error=message, blocked=True,
            duration_s=round(time.monotonic() - started, 1),
        )

    _set_status(project_id, DeployStatus.ANALYZING.value, "checking environment")
    await ctx.progress(0.05, "checking environment")
    await ctx.log(
        f"Docker {status.client_version} (server {status.server_version}), "
        f"compose {status.compose_version or 'unknown'}"
    )

    _set_status(project_id, DeployStatus.PLANNING.value, "building run plan")
    await ctx.progress(0.12, "building run plan")
    prepared = await prepare_environment(ctx, project_id, settings, layout)
    plan = prepared["plan"]
    compose_path = Path(prepared["compose_path"])
    if not compose_path.exists():
        raise FileNotFoundError(f"compose file missing: {compose_path}")

    host_port = _compose_host_port(compose_path) or allocate_port(
        settings.port_range_start, settings.port_range_end
    )
    url = f"http://127.0.0.1:{host_port}"
    await ctx.log(f"service URL will be {url}")

    _set_status(project_id, DeployStatus.PREPARING.value, "removing previous containers")
    await ctx.progress(0.2, "cleaning previous containers")
    runner = DockerRunner()
    await runner.compose_down(compose_path, name_for_compose, on_line=ctx.log)

    transcript = layout.logs_dir(project_id) / "deploy.log"
    _set_status(project_id, DeployStatus.BUILDING.value, "docker compose up --build")
    await ctx.progress(0.35, "building images and starting containers")
    env_values = _environment_values(settings, layout, project_id)
    up = await runner.compose_up(
        compose_path,
        name_for_compose,
        env=env_values,
        on_line=ctx.log,
        transcript=transcript,
    )
    if not up.ok:
        tail = up.tail(80)
        await ctx.log("docker compose up failed", level="error")
        await ctx.log(tail, level="error")

    _set_status(project_id, DeployStatus.STARTING.value, "waiting for containers")
    await ctx.progress(0.55, "containers started, waiting for the service")

    health_url = url + (plan.get("health_path") or "/")
    health = await http_ready(
        health_url,
        timeout_s=min(180, settings.container_run_timeout_min * 4),
        interval_s=2.0,
        on_attempt=lambda attempt, code: ctx.scheduler.set_progress(
            ctx.task_id, min(0.85, 0.55 + attempt * 0.01), f"health check #{attempt} ({code})"
        ),
    )
    if health.ok:
        containers = await runner.compose_ps(compose_path, name_for_compose)
        _record_containers(project_id, containers, host_port, settings)
        _set_status(project_id, DeployStatus.HEALTH_CHECK.value, f"HTTP {health.status_code}")
        _set_status(project_id, DeployStatus.RUNNING.value, f"listening on {url}")
        _snapshot_environment(layout, project_id, compose_path, plan, host_port)
        await ctx.progress(1.0, f"running at {url}")
        await ctx.log(f"health check passed: HTTP {health.status_code} after {health.elapsed_ms}ms")
        await ctx.emit("deploy.running", {"project_id": project_id, "url": url, "port": host_port})
        return DeployOutcome(
            True,
            DeployStatus.RUNNING.value,
            port=host_port,
            http_status=health.status_code,
            duration_s=round(time.monotonic() - started, 1),
            containers=[c.raw for c in containers],
            url=url,
        )

    # -- failure path ---------------------------------------------------
    if up.ok:
        await ctx.log("containers are up but the HTTP endpoint never answered", level="error")
    logs = await runner.compose_logs(compose_path, name_for_compose, tail=400)
    combined = "\n".join(
        [
            "=== docker compose up ===",
            up.tail(120),
            "=== container logs ===",
            logs.tail(200),
            "=== health check ===",
            health.error or "",
        ]
    )
    await ctx.log(combined[-8000:], level="error")

    _set_status(project_id, DeployStatus.FAILED.value, "health check failed", error=health.error)
    attempts: list[dict] = []

    ai_client = AIClient(_session(), box)
    try:
        budget = Budget(max_requests=settings.max_repair_attempts * 2 + 2)
        workspace_dir = layout.project_subdir(project_id, "workspace")
        source_dir = layout.project_subdir(project_id, "source")
        patch_dir = layout.project_subdir(project_id, "patches")
        repair_ctx = RepairContext(
            project_id=project_id,
            project_name=project_name,
            stage=DeployStatus.HEALTH_CHECK.value if up.ok else DeployStatus.BUILDING.value,
            command=f"docker compose -f {compose_path.name} up -d --build",
            logs=combined,
            plan=plan,
            workspace_dir=workspace_dir,
            source_dir=source_dir,
            patch_dir=patch_dir,
            max_attempts=settings.max_repair_attempts,
        )

        async def retry_step() -> dict:
            await ctx.log("re-running deployment after AI patch", level="warn")
            rebuilt = await prepare_environment(ctx, project_id, settings, layout)
            new_compose = Path(rebuilt["compose_path"])
            new_port = _compose_host_port(new_compose) or host_port
            await runner.compose_down(new_compose, name_for_compose, on_line=ctx.log)
            again = await runner.compose_up(
                new_compose,
                name_for_compose,
                env=env_values,
                on_line=ctx.log,
                transcript=transcript,
            )
            fresh_logs = await runner.compose_logs(new_compose, name_for_compose, tail=300)
            recheck = await http_ready(f"http://127.0.0.1:{new_port}/", timeout_s=120)
            return {
                "ok": again.ok and recheck.ok,
                "error": recheck.error or again.tail(40),
                "logs": "\n".join([again.tail(80), fresh_logs.tail(150), recheck.error or ""]),
                "command": f"docker compose up (retry, port {new_port})",
            }

        outcome = await run_repair_loop(
            ai_client, repair_ctx, retry_step, budget=budget, on_event=_repair_events(ctx)
        )
        attempts = [a.as_dict() for a in outcome.attempts]
        _persist_patches(project_id, layout, outcome.attempts)
        if outcome.resolved:
            # The repair may have regenerated the compose file with a different port,
            # so re-read it instead of reporting the port from before the fix.
            recovered_port = _compose_host_port(compose_path) or host_port
            recovered_url = f"http://127.0.0.1:{recovered_port}"
            health = await http_ready(f"{recovered_url}/", timeout_s=30, interval_s=2)
            if not health.ok:
                _set_status(
                    project_id,
                    DeployStatus.WAITING_FOR_USER.value,
                    "repair reported success but the service is not answering",
                    error=health.error,
                )
                return DeployOutcome(
                    False,
                    DeployStatus.WAITING_FOR_USER.value,
                    port=recovered_port,
                    error=health.error,
                    stage="HEALTH_CHECK",
                    blocked=True,
                    patches=attempts,
                    duration_s=round(time.monotonic() - started, 1),
                    url=recovered_url,
                )
            containers = await runner.compose_ps(compose_path, name_for_compose)
            _record_containers(project_id, containers, recovered_port, settings)
            _set_status(
                project_id, DeployStatus.RUNNING.value, f"recovered after AI repair -> {recovered_url}"
            )
            await ctx.log("deployment recovered after an AI-assisted repair")
            return DeployOutcome(
                True,
                DeployStatus.RUNNING.value,
                port=recovered_port,
                http_status=health.status_code,
                patches=attempts,
                duration_s=round(time.monotonic() - started, 1),
                containers=[c.raw for c in containers],
                url=recovered_url,
            )
        _set_status(
            project_id,
            DeployStatus.WAITING_FOR_USER.value,
            "automatic repair exhausted",
            error=outcome.reason,
        )
        await ctx.log(outcome.reason or "repair failed", level="error")
        return DeployOutcome(
            False,
            DeployStatus.WAITING_FOR_USER.value,
            port=host_port,
            error=outcome.reason or health.error,
            stage="HEALTH_CHECK" if up.ok else "BUILDING",
            blocked=True,
            patches=attempts,
            duration_s=round(time.monotonic() - started, 1),
            url=url,
        )
    except NoProviderConfigured as exc:
        message = f"deployment failed and no AI provider is configured for repair: {exc}"
        await ctx.log(message, level="error")
        _set_status(project_id, DeployStatus.WAITING_FOR_USER.value, message, error=health.error)
        return DeployOutcome(
            False,
            DeployStatus.WAITING_FOR_USER.value,
            port=host_port,
            error=message,
            stage="HEALTH_CHECK",
            blocked=True,
            patches=attempts,
            duration_s=round(time.monotonic() - started, 1),
            url=url,
        )
    finally:
        ai_client.session.close()


def _session():
    from openlab.db import session_factory

    return session_factory()()


def _environment_values(settings: Settings, layout, project_id: str) -> dict[str, str]:
    """Environment variables passed to compose. Secrets come from the local store only."""
    values: dict[str, str] = {}
    env_file = layout.project_subdir(project_id, "workspace") / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    values.setdefault("NODE_ENV", "production")
    values.setdefault("PORT", str(settings.port))
    return values


def _record_containers(project_id: str, containers, host_port: int, settings: Settings) -> None:
    from engine.docker_manager.runner import parse_port_mappings

    with session_scope() as session:
        for info in containers:
            row = session.scalar(
                select(ProjectContainer).where(
                    ProjectContainer.project_id == project_id,
                    ProjectContainer.service_name == (info.service or info.name),
                )
            )
            mappings = parse_port_mappings(info.ports)
            if row is None:
                row = ProjectContainer(
                    project_id=project_id,
                    service_name=info.service or info.name or "app",
                )
                session.add(row)
            row.container_id = info.id
            row.container_name = info.name
            row.image = info.image
            row.status = info.state or info.status or "unknown"
            row.health_state = info.health
            row.cpu_limit = settings.container_cpu_limit
            row.memory_limit_mb = settings.container_memory_limit_mb
            if mappings:
                row.host_port = mappings[0]["host_port"]
                row.container_port = mappings[0]["container_port"]
            else:
                row.host_port = host_port
            row.last_started_at = datetime.now(timezone.utc)
            row.extra = info.raw


def _snapshot_environment(layout, project_id: str, compose_path: Path, plan: dict, host_port: int) -> None:
    with session_scope() as session:
        snapshot = EnvironmentSnapshot(
            project_id=project_id,
            label=f"auto snapshot {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            compose_path=str(compose_path),
            env_names=[v.get("name") for v in plan.get("env_vars") or [] if v.get("name")],
            start_commands=[plan.get("start_command") or ""],
            ports={"host": host_port, "container": plan.get("port")},
            snapshot={
                "plan": plan,
                "compose": compose_path.read_text(encoding="utf-8", errors="replace")[:20000]
                if compose_path.exists()
                else "",
            },
        )
        session.add(snapshot)


def _persist_patches(project_id: str, layout, attempts) -> None:
    patch_dir = layout.project_subdir(project_id, "patches")
    with session_scope() as session:
        for attempt in attempts:
            diff_path = patch_dir / f"attempt-{attempt.attempt}.diff"
            session.add(
                Patch(
                    project_id=project_id,
                    attempt=attempt.attempt,
                    reason=attempt.stage,
                    diagnosis=attempt.diagnosis,
                    diff=attempt.diff[:200000] if attempt.diff else None,
                    diff_path=str(diff_path) if diff_path.exists() else None,
                    files_touched=attempt.files,
                    applied=attempt.applied,
                    rolled_back=attempt.rolled_back,
                    generated_by="ai",
                    model=attempt.model,
                )
            )


def _repair_events(ctx: TaskContext):
    async def handler(event_type: str, payload: dict) -> None:
        await ctx.log(f"[repair] {event_type}: {json.dumps(payload, ensure_ascii=False)[:500]}")
        await ctx.emit(event_type, payload)

    return handler


def _persist_plan(project_id: str, plan) -> None:
    """Store the derived run plan on the project's environment row."""
    with session_scope() as session:
        env_row = load_environment(session, project_id)
        if env_row is None:
            env_row = ProjectEnvironment(project_id=project_id)
            session.add(env_row)
        env_row.runtime_plan = plan.as_dict()
        env_row.strategy = "host"
        env_row.base_image = None
        env_row.runtime_language = plan.language
        env_row.runtime_version = plan.runtime_version
        env_row.package_manager = plan.package_manager
        env_row.install_command = plan.install_command
        env_row.build_command = plan.build_command
        env_row.start_command = plan.start_command
        env_row.port = plan.port
        env_row.health_path = plan.health_path
        env_row.env_vars = plan.env_vars
        env_row.services = [s.as_dict() for s in plan.services]
        env_row.gpu_required = plan.gpu_required
        env_row.external_apis = plan.external_apis
        env_row.confidence = plan.confidence
        env_row.notes = "; ".join(plan.warnings) or None


async def deploy_on_host(
    ctx: TaskContext, settings: Settings, layout, project_id: str, project_name: str
) -> DeployOutcome:
    """Unsandboxed fallback used only when the user explicitly enabled it."""
    from engine.runtime.host import HostRuntime, environment_warning, find_service_port, runtime_available

    started = time.monotonic()
    warning = environment_warning()
    await ctx.log(warning, level="warn")
    _set_status(project_id, DeployStatus.PREPARING.value, "host runtime (no isolation)")

    workspace = layout.project_subdir(project_id, "workspace")
    # The host runtime does not need a Dockerfile/compose file. Deriving the run plan
    # directly keeps "no start command" the only real blocker, instead of failing on
    # an artefact that is irrelevant for this execution mode.
    analysis = analyze_project(workspace, project_name=project_id)
    plan = analysis.plan.as_dict()
    _persist_plan(project_id, analysis.plan)
    start_command = plan.get("start_command") or ""
    if plan.get("project_type") == "native" or not start_command:
        # Prefer the planner's explanation when it identified a compiled/desktop project;
        # it is far more useful than a generic "no start command" message.
        blocking = [item for item in (plan.get("blocking_requirements") or []) if item]
        message = (
            blocking[0]
            if blocking
            else (
                "No start command could be derived from the repository manifests, so it cannot be "
                "started automatically. Add a start script (or a Dockerfile) and try again."
            )
        )
        _set_status(project_id, DeployStatus.WAITING_FOR_USER.value, message)
        await ctx.log(message, level="error")
        return DeployOutcome(False, DeployStatus.WAITING_FOR_USER.value, error=message, blocked=True)

    # A multi-service stack (compose + backing services) cannot be reproduced
    # faithfully without containers: running only the web process would report a
    # success for an application that is actually missing its database.
    if plan.get("strategy") == "compose" or plan.get("services"):
        services = ", ".join(
            str(service.get("name")) for service in (plan.get("services") or []) if service.get("name")
        )
        message = (
            "This project is defined as a multi-service stack"
            + (f" ({services})" if services else "")
            + " and needs Docker to run faithfully. OPENLAB will not claim success by starting only "
            "the application process on the host. Install Docker Desktop / Docker Engine and deploy again."
        )
        _set_status(project_id, DeployStatus.WAITING_FOR_USER.value, message)
        await ctx.log(message, level="error")
        return DeployOutcome(
            False, DeployStatus.WAITING_FOR_USER.value, error=message, blocked=True
        )

    ok, reason = runtime_available(start_command)
    if not ok:
        message = f"Cannot run on the host: {reason}"
        _set_status(project_id, DeployStatus.WAITING_FOR_USER.value, message)
        return DeployOutcome(False, DeployStatus.WAITING_FOR_USER.value, error=message, blocked=True)

    log_dir = layout.logs_dir(project_id)
    runtime = HostRuntime(layout.tmp_dir / "host-processes")
    _set_status(project_id, DeployStatus.INSTALLING.value, "installing dependencies on host")
    await ctx.progress(0.3, "installing dependencies (host)")
    install = await runtime.prepare(workspace, plan.get("install_command"), log_dir / "host-install.log")
    if not install.get("ok"):
        await ctx.log("dependency installation failed on the host", level="error")
        await ctx.log(install.get("tail") or "", level="error")

    _set_status(project_id, DeployStatus.STARTING.value, "starting process on host")
    await ctx.progress(0.55, "starting the process (host)")
    port = plan.get("port") or 3000
    proc = runtime.start(
        project_id,
        workspace,
        start_command,
        port=port if isinstance(port, int) and port >= 1024 else None,
        port_range=(settings.port_range_start, settings.port_range_end),
        log_path=log_dir / "host-runtime.log",
    )
    health_path = plan.get("health_path") or "/"
    health = await http_ready(f"{proc.url}{health_path}", timeout_s=90, interval_s=2)
    if not health.ok and proc.pid:
        # The framework may have ignored PORT/--port. Find the port it really opened
        # instead of reporting a false failure.
        actual = find_service_port(proc.pid, preferred=proc.port)
        if actual and actual != proc.port:
            await ctx.log(
                f"the app ignored the requested port and is listening on {actual}; "
                f"verifying that port instead",
                level="warn",
            )
            proc.port = actual
            health = await http_ready(f"{proc.url}{health_path}", timeout_s=60, interval_s=2)
    if health.ok:
        _set_status(
            project_id,
            DeployStatus.RUNNING.value,
            f"running on the host (no isolation) at {proc.url}",
        )
        await ctx.log(f"host process is answering HTTP {health.status_code} at {proc.url}", level="warn")
        with session_scope() as session:
            session.query(ProjectContainer).filter(ProjectContainer.project_id == project_id).delete()
            session.add(
                ProjectContainer(
                    project_id=project_id,
                    service_name="app",
                    container_id=str(proc.pid),
                    container_name=f"host:{proc.pid}",
                    image="host-process (no isolation)",
                    status="running",
                    host_port=proc.port,
                    container_port=proc.port,
                    extra={"runtime": "host", "warning": warning, "command": proc.command},
                )
            )
            project = session.get(Project, project_id)
            if project is not None:
                project.deploy_status_detail = warning
                project.last_run_at = datetime.now(timezone.utc)
        return DeployOutcome(
            True,
            DeployStatus.RUNNING.value,
            port=proc.port,
            http_status=health.status_code,
            duration_s=round(time.monotonic() - started, 1),
            url=proc.url,
        )

    runtime.stop(project_id)
    message = (
        f"The host process did not answer HTTP in time: {health.error}. "
        f"See {proc.log_path} for the real output. Docker is recommended for isolation and reliability."
    )
    _set_status(project_id, DeployStatus.FAILED.value, message, error=health.error)
    await ctx.log(message, level="error")
    return DeployOutcome(
        False,
        DeployStatus.FAILED.value,
        port=proc.port,
        error=message,
        stage="HEALTH_CHECK",
        duration_s=round(time.monotonic() - started, 1),
    )


async def stop_project(project_id: str, layout, settings: Settings) -> dict:
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        project_name = project.name or project_id
    compose_candidates = [
        layout.project_dir(project_id) / "docker" / "compose.openlab.yml",
        layout.project_subdir(project_id, "source") / "docker-compose.yml",
        layout.project_subdir(project_id, "source") / "compose.yaml",
    ]
    stopped: list[str] = []
    # Host processes must be stopped even when Docker is missing, so this runs
    # before any Docker work and never depends on the CLI being installed.
    from engine.runtime.host import HostRuntime

    host_runtime = HostRuntime(layout.tmp_dir / "host-processes")
    if host_runtime.stop(project_id):
        stopped.append("host process: stopped")

    runner = DockerRunner()
    if runner.available:
        for path in compose_candidates:
            if path.exists():
                try:
                    result = await runner.compose_down(path, project_name)
                    stopped.append(f"{path.name}: {'ok' if result.ok else 'failed'}")
                except DockerUnavailable as exc:
                    stopped.append(f"{path.name}: docker unavailable ({exc})")
        try:
            await runner.prune_project(project_name)
        except DockerUnavailable:
            pass
    with session_scope() as session:
        for row in session.scalars(select(ProjectContainer).where(ProjectContainer.project_id == project_id)):
            row.status = "stopped"
            row.last_stopped_at = datetime.now(timezone.utc)
    _set_status(project_id, DeployStatus.STOPPED.value, "stopped by user")
    return {"stopped": stopped}


async def reset_project_workspace(project_id: str, layout) -> dict:
    files = reset_workspace(layout, project_id)
    with session_scope() as session:
        for patch in session.scalars(
            select(Patch).where(Patch.project_id == project_id, Patch.rolled_back.is_(False))
        ):
            patch.rolled_back = True
    return {"restored_files": files}
