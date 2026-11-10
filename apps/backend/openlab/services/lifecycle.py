"""Project lifecycle helpers: cleanup with optional preservation and snapshot restore.

Two operations live here because both touch the on-disk project folder and the
database together:

* :func:`delete_project_data` — removes a project but can keep the media it produced
  and/or its reports, copying them into ``OPENLAB_DATA/preserved/`` first.
* :func:`restore_snapshot` — puts a stored environment snapshot back on disk (compose
  file + environment row) so the project can be rebuilt or re-run later.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from engine.core.paths import DataLayout
from engine.core.security import redact

from openlab.db import session_scope
from openlab.models import EnvironmentSnapshot, Project, ProjectEnvironment

PRESERVABLE_DIRS = ("screenshots", "videos", "gifs", "reports")


class LifecycleError(RuntimeError):
    """Raised when a lifecycle operation cannot be completed."""


def _safe_project_dir(layout: DataLayout, project_id: str) -> Path:
    return layout.project_dir(project_id)


def preserve_outputs(
    layout: DataLayout, project_id: str, project_name: str, keep_assets: bool, keep_reports: bool
) -> dict:
    """Copy the requested artefact folders out of the project directory."""
    if not keep_assets and not keep_reports:
        return {"path": None, "kind": "none", "files": 0}

    project_dir = _safe_project_dir(layout, project_id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = redact(project_name)[:40].replace(" ", "-").lower() or "project"
    root = layout.root / "preserved" / f"{stamp}-{slug}-{project_id[:8]}"
    root.mkdir(parents=True, exist_ok=True)

    wanted: list[str] = []
    if keep_assets:
        wanted.extend(["screenshots", "videos", "gifs"])
    if keep_reports:
        wanted.append("reports")

    copied = 0
    copied_dirs: list[str] = []
    for name in wanted:
        source = project_dir / name
        if not source.is_dir():
            continue
        # `ensure_project` pre-creates every subdirectory, so check for real content
        # instead of merely checking that the folder exists.
        files = [path for path in source.rglob("*") if path.is_file()]
        if not files:
            continue
        copied_dirs.append(name)
        target = root / name
        target.mkdir(parents=True, exist_ok=True)
        for path in files:
            relative = path.relative_to(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(path, destination)
                copied += 1
            except OSError:
                continue

    manifest = {
        "project_id": project_id,
        "project_name": project_name,
        "kept_assets": keep_assets,
        "kept_reports": keep_reports,
        "folders": copied_dirs,
        "files": copied,
        "preserved_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"path": str(root), "kind": "+".join(copied_dirs), "files": copied}


def delete_project_data(
    layout: DataLayout,
    project_id: str,
    keep_assets: bool = True,
    keep_reports: bool = True,
) -> dict:
    """Delete a project row (and its folder) while optionally preserving artefacts."""
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise LifecycleError(f"project {project_id} not found")
        project_name = project.display_name or project.name or project_id
        data_dir = Path(project.data_dir) if project.data_dir else layout.project_dir(project_id)

    preserved = preserve_outputs(layout, project_id, project_name, keep_assets, keep_reports)

    with session_scope() as session:
        row = session.get(Project, project_id)
        if row is not None:
            session.delete(row)

    removed: list[str] = []
    # `project.data_dir` is a database column: if it ever held a parent path (a bad
    # write, a hand-edited row, a restored backup), a blind rmtree would delete far
    # more than the project. Only paths that are a real child of projects/ may go.
    allowed_root = (layout.root / "projects").resolve()
    for candidate in {data_dir, layout.project_dir(project_id)}:
        if not candidate:
            continue
        resolved = Path(candidate).resolve()
        if resolved == allowed_root or allowed_root not in resolved.parents:
            continue
        if resolved.exists():
            shutil.rmtree(resolved, ignore_errors=True)
            removed.append(str(resolved))

    return {
        "deleted": True,
        "removed_paths": removed,
        "preserved_path": preserved["path"],
        "preserved_files": preserved["files"],
        "kept_assets": keep_assets,
        "kept_reports": keep_reports,
    }


def restore_snapshot(
    layout: DataLayout, project_id: str, snapshot_id: str
) -> dict:
    """Write a stored snapshot back to disk and refresh the environment row."""
    with session_scope() as session:
        snapshot = session.get(EnvironmentSnapshot, snapshot_id)
        if snapshot is None or snapshot.project_id != project_id:
            raise LifecycleError(f"snapshot {snapshot_id} not found for project {project_id}")
        project = session.get(Project, project_id)
        if project is None:
            raise LifecycleError(f"project {project_id} not found")

        payload = snapshot.snapshot or {}
        plan = payload.get("plan") or {}
        compose_text = payload.get("compose") or ""
        env_names = list(snapshot.env_names or [])
        ports = dict(snapshot.ports or {})
        label = snapshot.label

        docker_dir = layout.project_dir(project_id) / "docker"
        docker_dir.mkdir(parents=True, exist_ok=True)
        compose_path = docker_dir / "compose.openlab.yml"
        wrote_compose = False
        if compose_text.strip():
            compose_path.write_text(compose_text, encoding="utf-8")
            wrote_compose = True

        environment = session.scalar(
            select(ProjectEnvironment)
            .where(ProjectEnvironment.project_id == project_id)
            .order_by(ProjectEnvironment.created_at.desc())
        )
        if environment is None:
            environment = ProjectEnvironment(project_id=project_id)
            session.add(environment)
        environment.runtime_plan = plan or environment.runtime_plan
        environment.strategy = str(plan.get("strategy") or environment.strategy or "compose")
        if plan:
            environment.start_command = plan.get("start_command") or environment.start_command
            environment.install_command = plan.get("install_command") or environment.install_command
            environment.build_command = plan.get("build_command") or environment.build_command
            environment.port = plan.get("port") or environment.port
            environment.health_path = plan.get("health_path") or environment.health_path
        project.deploy_status_detail = redact(
            f"restored from snapshot “{label or snapshot_id[:8]}”"
        )[:2000]

    return {
        "restored": True,
        "snapshot_id": snapshot_id,
        "label": label,
        "compose_path": str(compose_path) if wrote_compose else None,
        "env_names": env_names,
        "ports": ports,
        "plan": plan,
        "next_step": "run deploy to rebuild and start the restored environment",
    }


def list_preserved(layout: DataLayout) -> list[dict]:
    """Enumerate the preserved artefact bundles (newest first)."""
    root = layout.root / "preserved"
    if not root.exists():
        return []
    items: list[dict] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        manifest = child / "manifest.json"
        entry: dict = {"path": str(child), "name": child.name}
        if manifest.exists():
            try:
                entry.update(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        items.append(entry)
    return items


def _port_reachable(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def reconcile_runtime_state(layout: DataLayout) -> dict:
    """Fix projects left ``RUNNING`` by a previous session.

    After a reboot (or a crash) the containers and host processes are gone, but the
    database still says ``RUNNING``. Reporting that would be a lie, so on startup
    every ``RUNNING`` project is re-checked against its real port and demoted to
    ``STOPPED`` with an explanation when nothing is listening.
    """
    from openlab.db import session_scope
    from openlab.models import Project, ProjectContainer

    checked = 0
    demoted: list[dict] = []
    with session_scope() as session:
        projects = list(session.scalars(select(Project).where(Project.deploy_status == "RUNNING")))
        for project in projects:
            checked += 1
            containers = list(
                session.scalars(
                    select(ProjectContainer).where(
                        ProjectContainer.project_id == project.id,
                        ProjectContainer.host_port.isnot(None),
                    )
                )
            )
            ports = sorted({c.host_port for c in containers if c.host_port})
            alive = any(_port_reachable(port) for port in ports)
            if alive:
                continue
            project.deploy_status = "STOPPED"
            project.deploy_status_detail = redact(
                "The previous session ended (reboot or shutdown) and the service is no longer "
                "listening on its port. Deploy again to bring it back."
            )[:2000]
            for container in containers:
                container.status = "stopped"
                container.last_stopped_at = datetime.now(timezone.utc)
            demoted.append({"project_id": project.id, "name": project.name, "ports": ports})
    return {"checked": checked, "demoted": demoted}
