"""Project import: GitHub, ZIP upload and local folder.

The import always writes the pristine tree to ``source/`` and mirrors it into
``workspace/`` so the AI can patch the workspace without ever touching originals.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from engine.core.paths import DataLayout
from engine.project_importer.archive import ArchiveRejected, copy_local_tree, extract_zip
from engine.project_importer.github import (
    ImportFailed,
    download_source,
    fetch_metadata,
    find_readme,
)
from engine.project_importer.repo_url import RepoRef, default_project_name, parse_repo_url, slugify
from engine.project_analyzer.analyzer import analyze_project

from openlab.models import Project, ProjectEnvironment, ProjectRepository


@dataclass
class ImportResult:
    project_id: str
    name: str
    source_type: str
    source_url: str | None
    files: int
    bytes: int
    analysis: dict = field(default_factory=dict)


async def import_from_github(
    session: Session,
    layout: DataLayout,
    repo_url: str,
    token: str | None = None,
    log=None,
    max_download_mb: int = 512,
    max_unpacked_mb: int = 2048,
) -> ImportResult:
    ref = parse_repo_url(repo_url)
    if log:
        await log(f"resolved repository {ref.slug}")
    meta = await fetch_metadata(ref, token=token)
    if log:
        await log(
            f"metadata: {meta.full_name} | stars {meta.stars} | license {meta.license or 'none'} "
            f"| default branch {meta.default_branch}"
        )

    project_id = uuid.uuid4().hex
    layout.ensure_project(project_id)
    source_dir = layout.project_subdir(project_id, "source")
    workspace_dir = layout.project_subdir(project_id, "workspace")

    async def progress(total: int) -> None:
        if log and total % (5 * 1024 * 1024) < 262144:
            await log(f"downloaded {total // (1024 * 1024)}MB of source archive")

    bundle = await download_source(
        ref,
        source_dir,
        branch=meta.default_branch,
        commit_sha=meta.commit_sha,
        token=token,
        max_download_mb=max_download_mb,
        max_unpacked_mb=max_unpacked_mb,
        progress=progress,
    )
    if log:
        await log(f"extracted {bundle.stats.files} files ({bundle.stats.bytes // 1024}KB) into source/")
    _mirror(source_dir, workspace_dir)

    analysis = analyze_project(source_dir, project_name=slugify(meta.name))
    project = Project(
        id=project_id,
        name=slugify(meta.name),
        display_name=meta.name,
        description=meta.description,
        source_type="github",
        source_url=meta.html_url or ref.html_url,
        license=meta.license,
        author=meta.author,
        stars=meta.stars,
        forks=meta.forks,
        language=meta.language or analysis.scan.primary_language,
        topics=meta.topics,
        data_dir=str(layout.project_dir(project_id)),
        deploy_status="PENDING",
        asset_count=0,
    )
    session.add(project)
    session.add(
        ProjectRepository(
            project_id=project_id,
            host=ref.host,
            owner=ref.owner,
            repo=ref.repo,
            html_url=meta.html_url,
            clone_url=meta.clone_url,
            default_branch=meta.default_branch,
            commit_sha=meta.commit_sha,
            is_private=meta.is_private,
            readme_excerpt=(meta.readme or analysis.readme_excerpt or "")[:20000],
            raw_metadata=meta.raw,
        )
    )
    session.add(_environment_row(project_id, analysis))
    session.commit()
    _write_metadata(layout, project_id, project, meta.as_dict(), analysis.as_dict())
    return ImportResult(
        project_id=project_id,
        name=meta.name,
        source_type="github",
        source_url=meta.html_url,
        files=bundle.stats.files,
        bytes=bundle.stats.bytes,
        analysis=analysis.as_dict(),
    )


def import_from_zip(
    session: Session,
    layout: DataLayout,
    archive_path: Path,
    display_name: str | None = None,
    max_files: int = 60000,
    max_unpacked_mb: int = 2048,
) -> ImportResult:
    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise ImportFailed(f"archive not found: {archive_path}")
    project_id = uuid.uuid4().hex
    layout.ensure_project(project_id)
    source_dir = layout.project_subdir(project_id, "source")
    workspace_dir = layout.project_subdir(project_id, "workspace")
    try:
        stats = extract_zip(
            archive_path,
            source_dir,
            max_files=max_files,
            max_unpacked_mb=max_unpacked_mb,
        )
    except ArchiveRejected as exc:
        raise ImportFailed(str(exc)) from exc
    _mirror(source_dir, workspace_dir)
    name = display_name or (stats.top_level or archive_path.stem)
    analysis = analyze_project(source_dir, project_name=slugify(name))
    project = Project(
        id=project_id,
        name=slugify(name),
        display_name=name,
        description=f"Imported from ZIP archive {archive_path.name}",
        source_type="zip",
        source_url=None,
        language=analysis.scan.primary_language,
        data_dir=str(layout.project_dir(project_id)),
        deploy_status="PENDING",
    )
    session.add(project)
    session.add(
        ProjectRepository(
            project_id=project_id,
            host="local",
            owner=None,
            repo=name,
            readme_excerpt=analysis.readme_excerpt,
            raw_metadata={"archive": archive_path.name},
        )
    )
    session.add(_environment_row(project_id, analysis))
    session.commit()
    _write_metadata(layout, project_id, project, {"name": name, "source": "zip"}, analysis.as_dict())
    return ImportResult(
        project_id=project_id,
        name=name,
        source_type="zip",
        source_url=None,
        files=stats.files,
        bytes=stats.bytes,
        analysis=analysis.as_dict(),
    )


def import_from_folder(
    session: Session,
    layout: DataLayout,
    folder: Path,
    display_name: str | None = None,
    max_files: int = 60000,
    max_unpacked_mb: int = 2048,
) -> ImportResult:
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ImportFailed(f"folder not found: {folder}")
    project_id = uuid.uuid4().hex
    layout.ensure_project(project_id)
    source_dir = layout.project_subdir(project_id, "source")
    workspace_dir = layout.project_subdir(project_id, "workspace")
    stats = copy_local_tree(folder, source_dir, max_files=max_files, max_unpacked_mb=max_unpacked_mb)
    _mirror(source_dir, workspace_dir)
    name = display_name or folder.name
    analysis = analyze_project(source_dir, project_name=slugify(name))
    project = Project(
        id=project_id,
        name=slugify(name),
        display_name=name,
        description=f"Imported from local folder {folder}",
        source_type="local",
        source_url=str(folder),
        language=analysis.scan.primary_language,
        data_dir=str(layout.project_dir(project_id)),
        deploy_status="PENDING",
    )
    session.add(project)
    session.add(
        ProjectRepository(
            project_id=project_id,
            host="local",
            repo=name,
            readme_excerpt=analysis.readme_excerpt,
            raw_metadata={"folder": str(folder)},
        )
    )
    session.add(_environment_row(project_id, analysis))
    session.commit()
    _write_metadata(layout, project_id, project, {"name": name, "source": "folder"}, analysis.as_dict())
    return ImportResult(
        project_id=project_id,
        name=name,
        source_type="local",
        source_url=str(folder),
        files=stats.files,
        bytes=stats.bytes,
        analysis=analysis.as_dict(),
    )


def _environment_row(project_id: str, analysis) -> ProjectEnvironment:
    plan = analysis.plan
    return ProjectEnvironment(
        project_id=project_id,
        strategy=plan.strategy,
        base_image=plan.base_image,
        runtime_language=plan.language,
        runtime_version=plan.runtime_version,
        package_manager=plan.package_manager,
        install_command=plan.install_command,
        build_command=plan.build_command,
        start_command=plan.start_command,
        port=plan.port,
        health_path=plan.health_path,
        runtime_plan=plan.as_dict(),
        env_vars=plan.env_vars,
        services=[s.__dict__ for s in plan.services],
        gpu_required=plan.gpu_required,
        external_apis=plan.external_apis,
        confidence=plan.confidence,
        notes="; ".join(plan.warnings) or None,
    )


def _mirror(source_dir: Path, workspace_dir: Path) -> None:
    """Copy the pristine source tree into the workspace (the only writable copy)."""
    if workspace_dir.exists():
        for child in workspace_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for child in source_dir.iterdir():
        target = workspace_dir / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def reset_workspace(layout: DataLayout, project_id: str) -> int:
    """Restore the workspace from the pristine source (discards AI patches)."""
    source_dir = layout.project_subdir(project_id, "source")
    workspace_dir = layout.project_subdir(project_id, "workspace")
    _mirror(source_dir, workspace_dir)
    return sum(1 for _ in workspace_dir.rglob("*") if _.is_file())


def _write_metadata(layout: DataLayout, project_id: str, project: Project, repo_meta: dict, analysis: dict) -> None:
    layout.write_metadata(
        project_id,
        {
            "project": {
                "id": project.id,
                "name": project.name,
                "display_name": project.display_name,
                "source_type": project.source_type,
                "source_url": project.source_url,
                "license": project.license,
            },
            "repository": repo_meta,
            "analysis": analysis,
        },
    )


def read_readme(layout: DataLayout, project_id: str) -> str | None:
    source_dir = layout.project_subdir(project_id, "source")
    return find_readme(source_dir)


def repo_ref_for(url: str) -> RepoRef:
    return parse_repo_url(url)
