"""Preview, exploration and asset-capture services built on Playwright."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from engine.asset_manager.manager import AssetManager, register_capture
from engine.browser_automation.capture import CaptureSpec, capture
from engine.browser_automation.explorer import (
    PlanRejected,
    heuristic_plan,
    plan_prompt,
    validate_model_plan,
    PLAN_PROMPT_SYSTEM,
)
from engine.browser_automation.inspector import inspect_page
from engine.browser_automation.launcher import (
    BrowserSession,
    BrowserUnavailable,
    LaunchOptions,
    ensure_artifact_temp,
    get_viewport,
)
from engine.browser_automation.executor import ExecutorOptions, execute_plan, open_preview
from engine.ai_agent.adapters import ChatMessage
from engine.ai_agent.client import AIClient, NoProviderConfigured
from engine.core.security import SecretBox

from openlab.db import session_scope
from openlab.events import bus
from openlab.models import (
    Asset,
    BrowserSession as BrowserSessionRow,
    ExplorationPlan,
    ExplorationResult,
    ExplorationStep,
    Project,
    ProjectEnvironment,
)
from engine.task_scheduler.scheduler import TaskContext

PREVIEW_DIRNAME = "screenshots"
VIDEO_DIRNAME = "videos"
GIF_DIRNAME = "gifs"


def resolve_project_url(project_id: str) -> str | None:
    """Find the live URL of the running project from its container records."""
    from openlab.models import ProjectContainer

    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            return None
        for container in session.scalars(
            select(ProjectContainer).where(ProjectContainer.project_id == project_id)
        ):
            if container.host_port and container.service_name in ("app", "web", "main", project.name):
                return f"http://127.0.0.1:{container.host_port}"
        row = session.scalar(
            select(ProjectContainer)
            .where(ProjectContainer.project_id == project_id, ProjectContainer.host_port.isnot(None))
            .order_by(ProjectContainer.created_at.desc())
        )
        if row and row.host_port:
            return f"http://127.0.0.1:{row.host_port}"
    return None


@dataclass
class PreviewResult:
    url: str
    title: str
    snapshot: dict
    screenshot: dict | None
    console: dict

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "snapshot": self.snapshot,
            "screenshot": self.screenshot,
            "console": self.console,
        }


async def capture_preview(
    layout,
    project_id: str,
    url: str,
    viewport: str = "desktop",
    full_page: bool = True,
    label: str | None = None,
    box: SecretBox | None = None,
) -> PreviewResult:
    """Load the running project in Chromium and store a real screenshot asset."""
    ensure_artifact_temp(layout.tmp_dir)
    shot_dir = layout.project_subdir(project_id, PREVIEW_DIRNAME)
    options = ExecutorOptions(
        screenshot_dir=shot_dir,
        viewport=viewport,
        record_video=False,
        headless=True,
    )
    snapshot, collector, shot, info = await open_preview(
        url, options, screenshot_name=f"preview-{viewport}.png"
    )
    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = register_capture(
            manager,
            project_id,
            shot,
            label=label or f"Preview ({viewport})",
            source_url=url,
        )
        asset_id = asset.id
        asset_rel = asset.relative_path
    return PreviewResult(
        url=info["url"],
        title=info["title"],
        snapshot={**snapshot.as_dict(), "asset_id": asset_id, "asset_path": asset_rel},
        screenshot=shot.as_dict(),
        console=collector.summary(),
    )


async def build_exploration_plan(
    project_id: str,
    url: str,
    mode: str,
    goal: str | None,
    layout,
    box: SecretBox,
    prefer_model: bool = True,
) -> tuple[str, dict]:
    """Inspect the live app, then produce and persist an exploration plan."""
    ensure_artifact_temp(layout.tmp_dir)
    shot_dir = layout.project_subdir(project_id, PREVIEW_DIRNAME)
    options = ExecutorOptions(screenshot_dir=shot_dir, viewport="desktop", record_video=False)
    snapshot, collector, shot, info = await open_preview(
        url, options, screenshot_name="exploration-start.png"
    )

    spec = None
    if prefer_model:
        try:
            client = AIClient(_session(), box)
            try:
                data = await client.complete_json(
                    [
                        ChatMessage(role="system", content=PLAN_PROMPT_SYSTEM),
                        ChatMessage(role="user", content=plan_prompt(snapshot, mode, goal)),
                    ],
                    purpose="exploration_planning",
                    project_id=project_id,
                )
                spec = validate_model_plan(data, snapshot, url, mode, goal)
            finally:
                client.session.close()
        except NoProviderConfigured:
            spec = None
        except (PlanRejected, Exception):  # noqa: BLE001 - fall back to heuristics
            spec = None
    if spec is None:
        spec = heuristic_plan(snapshot, url, mode, goal)

    with session_scope() as session:
        plan_row = ExplorationPlan(
            project_id=project_id,
            mode=mode,
            goal=goal,
            status="planned",
            origin_url=url,
            summary=spec.summary,
            plan=spec.as_dict(),
            discovered_pages=spec.discovered_pages,
            generated_by=spec.generated_by,
        )
        session.add(plan_row)
        session.flush()
        for step in spec.steps:
            session.add(
                ExplorationStep(
                    plan_id=plan_row.id,
                    index=step.index,
                    action=step.action,
                    target=step.target,
                    value=step.value,
                    description=step.description,
                    status="pending",
                )
            )
        plan_id = plan_row.id
        manager = AssetManager(session, layout)
        register_capture(
            manager, project_id, shot, label="Exploration start page", source_url=url
        )
    bus.publish(
        "exploration.planned",
        {"project_id": project_id, "plan_id": plan_id, "mode": mode, "steps": len(spec.steps)},
    )
    return plan_id, spec.as_dict()


async def run_exploration(
    ctx: TaskContext,
    layout,
    project_id: str,
    url: str,
    mode: str,
    goal: str | None,
    box: SecretBox,
    viewport: str = "desktop",
    record_video: bool = True,
) -> dict:
    """Plan and then really execute an exploration, persisting every observation."""
    await ctx.progress(0.05, "inspecting the application")
    plan_id, plan_dict = await build_exploration_plan(
        project_id, url, mode, goal, layout, box, prefer_model=True
    )
    await ctx.log(f"exploration plan ready: {len(plan_dict['steps'])} step(s) [{plan_dict['generated_by']}]")

    from engine.browser_automation.explorer import ExplorationPlanSpec, Step

    spec = ExplorationPlanSpec(
        mode=mode,
        goal=goal,
        origin_url=url,
        summary=plan_dict.get("summary") or "",
        generated_by=plan_dict.get("generated_by") or "heuristic",
        discovered_pages=plan_dict.get("discovered_pages") or [],
        steps=[
            Step(
                index=s["index"],
                action=s["action"],
                target=s.get("target"),
                value=s.get("value"),
                description=s.get("description") or "",
                capture=bool(s.get("capture")),
                wait_ms=int(s.get("wait_ms") or 0),
            )
            for s in plan_dict["steps"]
        ],
    )
    ensure_artifact_temp(layout.tmp_dir)
    shot_dir = layout.project_subdir(project_id, PREVIEW_DIRNAME)
    video_dir = layout.project_subdir(project_id, VIDEO_DIRNAME)
    options = ExecutorOptions(
        screenshot_dir=shot_dir,
        viewport=viewport,
        record_video=record_video,
        video_dir=video_dir / f"exploration-{plan_id[:8]}",
        headless=True,
    )

    async def on_step(outcome) -> None:
        await ctx.log(
            f"step {outcome.index + 1}: {outcome.action} {outcome.target or ''} -> "
            f"{outcome.status}{' (' + outcome.error + ')' if outcome.error else ''}"
        )
        with session_scope() as session:
            step_row = session.scalar(
                select(ExplorationStep).where(
                    ExplorationStep.plan_id == plan_id, ExplorationStep.index == outcome.index
                )
            )
            if step_row is not None:
                step_row.status = outcome.status
                step_row.duration_ms = outcome.duration_ms
                step_row.error = outcome.error
                step_row.finished_at = _now()
            manager = AssetManager(session, layout)
            asset_id = None
            if outcome.screenshot is not None:
                asset = register_capture(
                    manager,
                    project_id,
                    outcome.screenshot,
                    label=outcome.description or f"Step {outcome.index + 1}",
                    source_url=outcome.url,
                )
                asset_id = asset.id
            session.add(
                ExplorationResult(
                    plan_id=plan_id,
                    step_id=step_row.id if step_row is not None else None,
                    url=outcome.url,
                    title=outcome.title,
                    status=outcome.status,
                    observation=outcome.observation,
                    console_errors=outcome.console_errors,
                    network_errors=outcome.network_errors,
                    screenshot_asset_id=asset_id,
                    features=[{"hint": h} for h in outcome.features],
                )
            )
        bus.publish(
            "exploration.step",
            {
                "project_id": project_id,
                "plan_id": plan_id,
                "index": outcome.index,
                "status": outcome.status,
                "url": outcome.url,
            },
        )

    await ctx.progress(0.2, "running the exploration plan")
    try:
        report = await execute_plan(spec, options, on_step=on_step)
    except BrowserUnavailable as exc:
        with session_scope() as session:
            row = session.get(ExplorationPlan, plan_id)
            if row is not None:
                row.status = "blocked"
                row.summary = str(exc)[:2000]
        await ctx.log(str(exc), level="error")
        return {"plan_id": plan_id, "status": "BLOCKED", "error": str(exc)}

    video_asset_id = None
    if report.video_path and Path(report.video_path).exists():
        final_video = video_dir / f"exploration-{plan_id[:8]}.webm"
        try:
            import shutil

            shutil.move(str(report.video_path), final_video)
            with session_scope() as session:
                manager = AssetManager(session, layout)
                asset = manager.register_file(
                    project_id,
                    final_video,
                    kind="video",
                    label="Exploration recording",
                    viewport=viewport,
                    source_url=url,
                )
                video_asset_id = asset.id
        except OSError as exc:
            await ctx.log(f"could not store the recording: {exc}", level="warn")

    payload = report.as_dict()
    with session_scope() as session:
        row = session.get(ExplorationPlan, plan_id)
        if row is not None:
            row.status = report.status().lower()
            row.summary = (row.summary or "") + f" | executed: {report.status()}"
    await ctx.progress(1.0, f"exploration finished: {report.status()}")
    return {
        "plan_id": plan_id,
        "status": report.status(),
        "video_asset_id": video_asset_id,
        "steps": payload["steps"],
        "console_summary": payload["console_summary"],
    }


async def capture_asset_set(
    ctx: TaskContext,
    layout,
    project_id: str,
    url: str,
    viewports: list[str],
    record_video: bool,
    make_gif: bool,
    box: SecretBox | None = None,
) -> dict:
    """One-click asset job: screenshots per viewport, an action video and a GIF."""
    from engine.media_processor.ffmpeg import MediaProcessor

    await ctx.progress(0.05, "opening the project")
    shot_dir = layout.project_subdir(project_id, PREVIEW_DIRNAME)
    created: list[dict] = []
    ensure_artifact_temp(layout.tmp_dir)
    options = ExecutorOptions(screenshot_dir=shot_dir, viewport="desktop", record_video=False)
    snapshot, collector, landing, info = await open_preview(
        url, options, screenshot_name="landing.png"
    )
    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = register_capture(manager, project_id, landing, label="Landing page", source_url=url)
        created.append({"kind": "screenshot", "id": asset.id, "label": asset.label})

    preset_viewports = viewports or ["desktop", "mobile", "tablet"]
    async with BrowserSession(LaunchOptions(headless=True, viewport=get_viewport("desktop"))) as session_browser:
        page = session_browser.page
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        for index, viewport in enumerate(preset_viewports):
            await ctx.progress(0.15 + index * 0.15, f"capturing {viewport} screenshot")
            shot = await capture(
                page,
                CaptureSpec(viewport=viewport, full_page=True, filename=f"home-{viewport}.png"),
                shot_dir,
            )
            with session_scope() as session:
                manager = AssetManager(session, layout)
                asset = register_capture(
                    manager, project_id, shot, label=f"Home ({viewport})", source_url=url
                )
                created.append({"kind": "screenshot", "id": asset.id, "label": asset.label})

    video_asset_id = None
    gif_asset_id = None
    if record_video:
        await ctx.progress(0.6, "recording an interaction sequence")
        video_dir = layout.project_subdir(project_id, VIDEO_DIRNAME)
        plan_spec = heuristic_plan(snapshot, url, "quick", None)
        recorder = ExecutorOptions(
            screenshot_dir=shot_dir,
            viewport="desktop",
            record_video=True,
            video_dir=video_dir / "capture",
            headless=True,
        )
        report = await execute_plan(plan_spec, recorder)
        if report.video_path and Path(report.video_path).exists():
            target = video_dir / "walkthrough.webm"
            import shutil

            shutil.move(str(report.video_path), target)
            with session_scope() as session:
                manager = AssetManager(session, layout)
                asset = manager.register_file(
                    project_id, target, kind="video", label="Walkthrough recording", viewport="desktop"
                )
                video_asset_id = asset.id
                created.append({"kind": "video", "id": asset.id, "label": asset.label})
            if make_gif:
                await ctx.progress(0.8, "encoding GIF")
                processor = MediaProcessor()
                gif_dir = layout.project_subdir(project_id, GIF_DIRNAME)
                gif_path = gif_dir / "walkthrough.gif"
                result = await processor.to_gif(target, gif_path, duration_s=6.0, fps=12, width=960)
                if result.ok and gif_path.exists() and gif_path.stat().st_size > 0:
                    with session_scope() as session:
                        manager = AssetManager(session, layout)
                        asset = manager.register_file(
                            project_id, gif_path, kind="gif", label="Walkthrough GIF", viewport="desktop"
                        )
                        gif_asset_id = asset.id
                        created.append({"kind": "gif", "id": asset.id, "label": asset.label})
                else:
                    await ctx.log(
                        "GIF encoding failed: "
                        + (result.tail(10) or "ffmpeg not available")
                        + f" (tool: {result.tool})",
                        level="warn",
                    )
    await ctx.progress(1.0, f"{len(created)} asset(s) captured")
    return {
        "assets": created,
        "video_asset_id": video_asset_id,
        "gif_asset_id": gif_asset_id,
        "console": collector.summary(),
        "title": info.get("title"),
    }


async def create_browser_session(layout, project_id: str, name: str, url: str, timeout_s: int = 300) -> dict:
    """Open a headed browser so the user can log in manually, then persist storage state."""
    ensure_artifact_temp(layout.tmp_dir)
    storage_dir = layout.project_dir(project_id) / "browser-sessions"
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / f"{name}.json"
    options = LaunchOptions(
        headless=False,
        viewport=get_viewport("desktop"),
        storage_state=storage_path if storage_path.exists() else None,
    )
    async with BrowserSession(options) as session:
        page = session.page
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(min(timeout_s, 300) * 1000)
        await session.save_state(storage_path)
        final_url = page.url
    with session_scope() as session:
        row = session.scalar(
            select(BrowserSessionRow).where(
                BrowserSessionRow.project_id == project_id, BrowserSessionRow.name == name
            )
        )
        if row is None:
            row = BrowserSessionRow(project_id=project_id, name=name)
            session.add(row)
        row.storage_path = str(storage_path)
        row.url = final_url
        row.last_used_at = _now()
        row.is_authenticated = True
        session_id = row.id
    return {"session_id": session_id, "storage_path": str(storage_path), "url": final_url}


def _session():
    from openlab.db import session_factory

    return session_factory()()


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def plan_summary(plan: ExplorationPlan) -> dict:
    return {
        "id": plan.id,
        "mode": plan.mode,
        "goal": plan.goal,
        "status": plan.status,
        "summary": plan.summary,
        "generated_by": plan.generated_by,
        "origin_url": plan.origin_url,
        "plan": plan.plan,
        "discovered_pages": plan.discovered_pages,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


def asset_summary(asset: Asset) -> dict:
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "kind": asset.kind,
        "label": asset.label,
        "filename": asset.filename,
        "relative_path": asset.relative_path,
        "url": f"/api/assets/{asset.id}/raw",
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
        "duration_ms": asset.duration_ms,
        "size_bytes": asset.size_bytes,
        "viewport": asset.viewport,
        "aspect_ratio": asset.aspect_ratio,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }


def spec_to_json(spec) -> str:
    return json.dumps(spec.as_dict(), indent=2, ensure_ascii=False)
