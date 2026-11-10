"""System/health routers: host capabilities, Docker status, events and stats."""

from __future__ import annotations

import platform
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from engine.browser_automation.launcher import browser_inventory, ensure_artifact_temp
from engine.docker_manager.docker_cli import docker_status, host_report
from engine.media_processor.ffmpeg import MediaProcessor

from openlab import __version__
from openlab.events import bus
from openlab.services.overview import build_overview

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    scheduler = request.app.state.scheduler
    return {
        "ok": True,
        "app": settings.app_name,
        "version": __version__,
        "time": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(settings.data_dir),
        "scheduler": scheduler.status(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


@router.get("/system/host")
async def system_host() -> dict:
    return (await host_report()).as_dict()


@router.get("/system/docker")
async def system_docker() -> dict:
    return (await docker_status()).as_dict()


@router.get("/system/browsers")
async def system_browsers(request: Request) -> dict:
    ensure_artifact_temp(request.app.state.settings.resolve_data().tmp_dir)
    inventory = browser_inventory()
    processor = MediaProcessor()
    return {
        **inventory,
        "ffmpeg": processor.binary,
        "ffmpeg_available": processor.available,
        "ffmpeg_version": await processor.version(),
    }


@router.get("/system/events")
async def recent_events(limit: int = 50) -> dict:
    return {"events": bus.recent(limit)}


@router.get("/overview")
async def overview() -> dict:
    return build_overview()
