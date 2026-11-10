"""Task handlers that connect the engine to the scheduler."""

from __future__ import annotations

import logging

from engine.core.security import SecretBox
from engine.task_scheduler.scheduler import TaskContext

from openlab.config import Settings
from openlab.db import session_scope
from openlab.events import bus
from openlab.models import Project
from openlab.services import deploy as deploy_service
from openlab.services import explore as explore_service
from openlab.services.reporting import generate_report

log = logging.getLogger("openlab.handlers")


class Handlers:
    def __init__(self, settings: Settings, box: SecretBox) -> None:
        self.settings = settings
        self.box = box
        self.layout = settings.resolve_data()

    def register(self, scheduler) -> None:
        scheduler.register("deploy", self.deploy)
        scheduler.register("explore", self.explore)
        scheduler.register("assets", self.assets)
        scheduler.register("report", self.report)

    async def deploy(self, ctx: TaskContext) -> dict:
        project_id = ctx.project_id or ""
        await ctx.progress(0.02, "starting deployment")
        outcome = await deploy_service.deploy_project(ctx, self.settings, self.layout, self.box)
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is not None and not outcome.success:
                project.last_error = outcome.error
        await ctx.emit(
            "deploy.finished",
            {"project_id": project_id, "success": outcome.success, "status": outcome.status},
        )
        return outcome.as_dict()

    async def explore(self, ctx: TaskContext) -> dict:
        project_id = ctx.project_id or ""
        url = str(ctx.payload.get("url") or "")
        if not url:
            url = explore_service.resolve_project_url(project_id) or ""
        if not url:
            raise RuntimeError(
                "the project is not running; deploy it before running an exploration"
            )
        mode = str(ctx.payload.get("mode") or "quick")
        goal = ctx.payload.get("goal")
        viewport = str(ctx.payload.get("viewport") or "desktop")
        record_video = bool(ctx.payload.get("record_video", True))
        await ctx.progress(0.02, f"preparing {mode} exploration")
        return await explore_service.run_exploration(
            ctx,
            self.layout,
            project_id,
            url,
            mode,
            goal,
            self.box,
            viewport=viewport,
            record_video=record_video,
        )

    async def assets(self, ctx: TaskContext) -> dict:
        project_id = ctx.project_id or ""
        url = str(ctx.payload.get("url") or "") or (explore_service.resolve_project_url(project_id) or "")
        if not url:
            raise RuntimeError("the project is not running; deploy it before capturing assets")
        viewports = ctx.payload.get("viewports") or ["desktop", "mobile", "tablet"]
        return await explore_service.capture_asset_set(
            ctx,
            self.layout,
            project_id,
            url,
            viewports=list(viewports),
            record_video=bool(ctx.payload.get("record_video", True)),
            make_gif=bool(ctx.payload.get("make_gif", True)),
            box=self.box,
        )

    async def report(self, ctx: TaskContext) -> dict:
        project_id = ctx.project_id or ""
        await ctx.progress(0.1, "collecting evidence")
        result = generate_report(project_id, self.layout, self.box)
        await ctx.progress(1.0, f"report {result['version']} generated ({result['status']})")
        bus.publish(
            "report.generated",
            {"project_id": project_id, "report_id": result["id"], "status": result["status"]},
        )
        return {
            "report_id": result["id"],
            "version": result["version"],
            "status": result["status"],
            "html_path": result["html_path"],
            "markdown_path": result["markdown_path"],
        }
