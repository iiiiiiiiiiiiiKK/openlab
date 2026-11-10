"""FastAPI application factory: wiring, middleware and static serving."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from engine.ai_agent.client import AIClient
from engine.browser_automation.launcher import ensure_artifact_temp
from engine.core.http import normalize_proxy_environment
from engine.core.security import SecretBox
from engine.task_scheduler.scheduler import SchedulerConfig, TaskScheduler

from openlab import __version__
from openlab.config import Settings, get_settings
from openlab.db import init_database, session_scope
from openlab.events import bus
from openlab.handlers import Handlers
from openlab.models import AppSetting
from openlab.routers import assets, explore, projects, providers, reports, settings as settings_router
from openlab.routers import system, tasks, terminal

log = logging.getLogger("openlab.app")

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self' ws://127.0.0.1:* ws://localhost:*; "
    "frame-src 'self' http://127.0.0.1:*; "
    "font-src 'self' data:; "
    "object-src 'none'; base-uri 'self'"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    # A bracketed IPv6 entry in NO_PROXY makes httpx raise InvalidURL during client
    # construction, which broke every outbound call (GitHub import, model calls)
    # before it was attempted. Normalise the variable before anything uses it.
    normalize_proxy_environment()
    settings = settings or get_settings()
    layout = settings.resolve_data()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        init_database(layout.db_path)
        box = SecretBox(layout.secrets_dir)
        # Settings written by the UI lived only in app_settings; nothing read them
        # back, so every restart silently reset resource limits and network policy.
        _apply_persisted_settings(settings, box)
        app.state.settings = settings
        app.state.box = box
        app.state.layout = layout
        app.state.secret_store = _load_secret_store(box)
        ensure_artifact_temp(layout.tmp_dir)

        scheduler = TaskScheduler(
            SchedulerConfig(max_concurrency=max(1, settings.max_parallel_deployments))
        )
        Handlers(settings, box).register(scheduler)
        app.state.scheduler = scheduler
        # Every AIClient owns a SQLAlchemy Session. Callers must release it; the
        # helper below is the only sanctioned way to obtain one so a forgotten close
        # cannot leak a pooled connection on every usage/settings request.
        app.state.ai_client = lambda: AIClient(_new_session(), box)
        bus.bind_loop(asyncio.get_running_loop())
        await scheduler.start()
        # A reboot leaves containers gone but rows still saying RUNNING; correct that
        # before anyone reads the dashboard, so the UI never shows a dead service as live.
        try:
            from openlab.services.lifecycle import reconcile_runtime_state

            reconciled = reconcile_runtime_state(layout)
            if reconciled["demoted"]:
                log.warning(
                    "demoted %d stale RUNNING project(s) after restart: %s",
                    len(reconciled["demoted"]),
                    ", ".join(item["name"] or item["project_id"][:8] for item in reconciled["demoted"]),
                )
            # Reload persisted GitHub credentials after database initialization.
            app.state.secret_store = _load_secret_store(box)
        except Exception:  # noqa: BLE001 - never block startup on this
            log.exception("could not reconcile runtime state")
        log.info("OPENLAB backend ready on http://%s:%s", settings.bind_host, settings.port)
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(
        title="OPENLAB",
        version=__version__,
        description="Open Source Project Experience Lab — local-first deployment and体验 workbench",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.layout = layout

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:3210"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    for router in (
        system.router,
        projects.router,
        tasks.router,
        providers.router,
        explore.router,
        assets.router,
        reports.router,
        settings_router.router,
        terminal.router,
    ):
        app.include_router(router)

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket, channel: str = Query("*")) -> None:
        await websocket.accept()
        queue = bus.subscribe(channel)
        try:
            for event in bus.recent(20):
                await websocket.send_text(json.dumps(event, default=str))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                    continue
                await websocket.send_text(json.dumps(event, default=str))
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - socket teardown
            pass
        finally:
            bus.unsubscribe(queue, channel)

    @app.get("/api/events/stream")
    async def event_stream(limit: int = 100) -> JSONResponse:
        return JSONResponse({"events": bus.recent(limit)})

    web_dist = Path(settings.web_dist)
    if web_dist.exists() and (web_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            candidate = web_dist / full_path
            if full_path and candidate.is_file() and web_dist in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html")

    else:

        @app.get("/")
        async def dev_root() -> PlainTextResponse:
            return PlainTextResponse(
                "OPENLAB backend is running, but the web UI has not been built yet.\n"
                "Run `pnpm --dir apps/web build` (or `pnpm dev:web`) and reload.\n"
                f"API docs: http://{settings.bind_host}:{settings.port}/docs\n"
            )

    return app


def _new_session():
    from openlab.db import session_factory

    return session_factory()()


def _apply_persisted_settings(settings: Settings, box: SecretBox) -> None:
    """Load UI-edited settings from app_settings onto the live Settings object."""
    try:
        with session_scope() as session:
            rows = list(session.query(AppSetting).all())
    except Exception:  # noqa: BLE001 - never block startup on settings hydration
        log.exception("could not load persisted settings")
        return
    from openlab.routers.settings import EDITABLE

    for row in rows:
        if row.key not in EDITABLE:
            continue
        value = (row.value or {}).get("value")
        if value is None:
            continue
        try:
            setattr(settings, row.key, value)
        except Exception:  # noqa: BLE001 - a stale key must not break startup
            log.warning("ignoring unreadable setting %s", row.key)


def _load_secret_store(box: SecretBox) -> dict[str, Any]:
    """Load non-provider secrets (e.g. a GitHub token) from app_settings."""
    store: dict[str, Any] = {}
    with session_scope() as session:
        row = session.query(AppSetting).filter(AppSetting.key == "github_token").one_or_none()
        if row is not None:
            encrypted = (row.value or {}).get("enc")
            if encrypted:
                plain = box.decrypt(encrypted)
                if plain:
                    store["github_token"] = plain
    return store


def persist_github_token(token: str | None, box: SecretBox) -> None:
    with session_scope() as session:
        row = session.query(AppSetting).filter(AppSetting.key == "github_token").one_or_none()
        if token is None:
            if row is not None:
                session.delete(row)
            return
        encrypted = box.encrypt(token)
        if row is None:
            session.add(AppSetting(key="github_token", value={"enc": encrypted}))
        else:
            row.value = {"enc": encrypted}
