"""Application settings for the OPENLAB backend.

Everything is overridable through environment variables or a ``.env`` file so the
desktop bundle and the local web mode share the same code path.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from engine.core.paths import DataLayout, default_data_dir

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENLAB_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OPENLAB"
    host: str = "127.0.0.1"
    port: int = 3210
    allow_lan: bool = False
    log_level: str = "info"

    data_dir: Path = Field(default_factory=default_data_dir)
    web_dist: Path = Field(default_factory=lambda: REPO_ROOT / "apps" / "web" / "dist")

    # Docker / runtime defaults
    max_parallel_deployments: int = 1
    container_cpu_limit: float = 2.0
    container_memory_limit_mb: int = 4096
    container_disk_limit_mb: int = 8192
    container_pids_limit: int = 512
    container_run_timeout_min: int = 180
    allow_container_network: bool = False
    # Address the published container port binds to. Loopback is right for a local
    # Docker engine; when the engine runs inside a VM (WSL2) or on another host a
    # loopback binding is invisible from here, so this can be set to 0.0.0.0.
    container_publish_host: str = "127.0.0.1"
    idle_stop_after_min: int = 30
    allow_host_runtime: bool = False

    # AI
    max_repair_attempts: int = 3
    ai_default_timeout_s: int = 180
    ai_max_tokens_default: int = 4096
    budget_default_usd: float = 0.0

    # Import guards
    max_download_mb: int = 512
    max_unpacked_mb: int = 2048
    max_file_count: int = 60000
    port_range_start: int = 41000
    port_range_end: int = 41999

    # Browser automation
    playwright_headed: bool = False
    browser_channel: str | None = None
    browser_viewport_width: int = 1440
    browser_viewport_height: int = 900

    github_token: str | None = None

    def resolve_data(self) -> DataLayout:
        return DataLayout.build(self.data_dir)

    @property
    def bind_host(self) -> str:
        return "0.0.0.0" if self.allow_lan else "127.0.0.1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir = Path(settings.data_dir).expanduser().resolve()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    layout = settings.resolve_data()
    # Only point Playwright at OPENLAB's own browser folder when a Chromium build is
    # actually there. Unconditionally setting it redirected Playwright away from a
    # working global install and every preview/exploration failed with
    # "Executable doesn't exist at <data>/browsers/chromium-.../chrome.exe".
    managed = layout.browsers_dir
    if any(child.is_dir() and child.name.startswith("chromium") for child in managed.iterdir()):
        existing = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if not existing or Path(existing) == managed:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(managed)
    return settings


def get_layout() -> DataLayout:
    return get_settings().resolve_data()
