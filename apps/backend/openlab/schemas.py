"""Pydantic schemas for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator



class GitHubImportRequest(BaseModel):
    url: str = Field(..., description="GitHub repository URL or owner/repo")
    token: str | None = None
    display_name: str | None = None

    @field_validator("url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("repository url is required")
        return text


class LocalImportRequest(BaseModel):
    path: str
    display_name: str | None = None


class ProjectUpdate(BaseModel):
    display_name: str | None = None
    favorite: bool | None = None
    tags: list[str] | None = None
    description: str | None = None


class DeployRequest(BaseModel):
    rebuild: bool = True


class ProviderCreate(BaseModel):
    name: str
    kind: Literal["openai", "anthropic", "gemini", "deepseek", "compatible"] = "openai"
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    cheap_model: str | None = None
    api_format: str | None = None
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_s: int = 180
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    enabled: bool = True
    is_default: bool = False
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    name: str | None = None
    kind: Literal["openai", "anthropic", "gemini", "deepseek", "compatible"] | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    cheap_model: str | None = None
    api_format: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_s: int | None = None
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    extra_headers: dict[str, str] | None = None


class PreviewRequest(BaseModel):
    viewport: str = "desktop"
    full_page: bool = True
    url: str | None = None


class ExploreRequest(BaseModel):
    mode: Literal["quick", "deep", "custom"] = "quick"
    goal: str | None = None
    viewport: str = "desktop"
    record_video: bool = True
    plan_only: bool = False
    url: str | None = None


class AssetCaptureRequest(BaseModel):
    viewports: list[str] = Field(default_factory=lambda: ["desktop", "mobile", "tablet"])
    record_video: bool = True
    make_gif: bool = True
    url: str | None = None


class GifRequest(BaseModel):
    asset_id: str
    start_s: float = 0.0
    duration_s: float = 6.0
    fps: int = 12
    width: int = 960
    max_size_kb: int | None = 5000


class VideoConvertRequest(BaseModel):
    asset_id: str
    format: Literal["mp4", "webm"] = "mp4"
    max_width: int | None = 1280


class AssetRename(BaseModel):
    label: str


class AssetExportRequest(BaseModel):
    asset_ids: list[str]
    target_dir: str


class BrowserSessionRequest(BaseModel):
    name: str = "default"
    url: str | None = None
    wait_seconds: int = 120


class SettingsUpdate(BaseModel):
    max_parallel_deployments: int | None = None
    container_cpu_limit: float | None = None
    container_memory_limit_mb: int | None = None
    container_disk_limit_mb: int | None = None
    container_pids_limit: int | None = None
    container_run_timeout_min: int | None = None
    idle_stop_after_min: int | None = None
    allow_container_network: bool | None = None
    allow_host_runtime: bool | None = None
    allow_lan: bool | None = None
    max_repair_attempts: int | None = None
    ai_default_timeout_s: int | None = None
    max_download_mb: int | None = None
    github_token: str | None = None
    clear_github_token: bool = False
    browser_channel: str | None = None
    playwright_headed: bool | None = None


class TaskAction(BaseModel):
    action: Literal["cancel", "pause", "resume", "retry"]


class RollbackRequest(BaseModel):
    patch_id: str | None = None


class PatchApplyRequest(BaseModel):
    diff: str
    reason: str = "manual patch"


class SnapshotRequest(BaseModel):
    label: str | None = None


class CustomTaskRequest(BaseModel):
    kind: Literal["deploy", "explore", "assets", "report"]
    payload: dict[str, Any] = Field(default_factory=dict)
