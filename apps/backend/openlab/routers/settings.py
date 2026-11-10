"""Settings endpoints: resource limits, network policy, stored tokens."""

from __future__ import annotations

from fastapi import APIRouter, Request

from engine.core.security import SecretBox

from openlab.db import session_scope
from openlab.models import AppSetting
from openlab.schemas import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])

EDITABLE = (
    "max_parallel_deployments",
    "container_cpu_limit",
    "container_memory_limit_mb",
    "container_disk_limit_mb",
    "container_pids_limit",
    "container_run_timeout_min",
    "idle_stop_after_min",
    "allow_container_network",
    "allow_host_runtime",
    "allow_lan",
    "max_repair_attempts",
    "ai_default_timeout_s",
    "max_download_mb",
    "browser_channel",
    "playwright_headed",
)


def _snapshot(settings, box: SecretBox, secret_store: dict) -> dict:
    return {
        **{key: getattr(settings, key) for key in EDITABLE},
        "host": settings.host,
        "port": settings.port,
        "data_dir": str(settings.data_dir),
        "has_github_token": bool(secret_store.get("github_token") or settings.github_token),
        "github_token_masked": box.mask(secret_store["github_token"]) if secret_store.get("github_token") else None,
        "security": {
            "bind": "0.0.0.0 (LAN)" if settings.allow_lan else "127.0.0.1 (local only)",
            "container_network": "allowed" if settings.allow_container_network else "blocked by default",
            "container_user": "non-root (uid 10001)",
            "privileged": False,
            "docker_socket": "never mounted",
            "host_mounts": "none",
        },
    }


@router.get("")
async def get_settings(request: Request) -> dict:
    state = request.app.state
    return _snapshot(state.settings, state.box, state.secret_store)


@router.patch("")
async def update_settings(request: Request, body: SettingsUpdate) -> dict:
    state = request.app.state
    settings = state.settings
    data = body.model_dump(exclude_unset=True)
    if data.pop("clear_github_token", False):
        state.secret_store.pop("github_token", None)
        from openlab.app import persist_github_token

        persist_github_token(None, state.box)
    token = data.pop("github_token", None)
    if token:
        state.secret_store["github_token"] = token
        from openlab.app import persist_github_token

        persist_github_token(token, state.box)
    for key in EDITABLE:
        if key in data and data[key] is not None:
            setattr(settings, key, data[key])
    if "max_parallel_deployments" in data and data["max_parallel_deployments"]:
        scheduler = state.scheduler
        scheduler.config.max_concurrency = max(1, int(data["max_parallel_deployments"]))
    with session_scope() as session:
        for key, value in data.items():
            if value is None:
                continue
            row = session.query(AppSetting).filter(AppSetting.key == key).one_or_none()
            if row is None:
                session.add(AppSetting(key=key, value={"value": value}))
            else:
                row.value = {"value": value}
    return _snapshot(settings, state.box, state.secret_store)
