"""SQLAlchemy models for OPENLAB.

SQLite is the only supported store: it is local-first, requires no server and is
easy to snapshot. Every table carries a UUID primary key plus created/updated
timestamps so the task engine can resume after a restart.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200), index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), default="github")  # github|zip|local
    source_url: Mapped[str | None] = mapped_column(String(500))
    license: Mapped[str | None] = mapped_column(String(120))
    author: Mapped[str | None] = mapped_column(String(200))
    stars: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str | None] = mapped_column(String(80))
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    deploy_status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    deploy_status_detail: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    asset_count: Mapped[int] = mapped_column(Integer, default=0)
    data_dir: Mapped[str | None] = mapped_column(String(500))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    repositories: Mapped[list["ProjectRepository"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    environments: Mapped[list["ProjectEnvironment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    containers: Mapped[list["ProjectContainer"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["ProjectTask"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    assets: Mapped[list["Asset"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    reports: Mapped[list["Report"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    patches: Mapped[list["Patch"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    snapshots: Mapped[list["EnvironmentSnapshot"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    plans: Mapped[list["ExplorationPlan"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    browser_sessions: Mapped[list["BrowserSession"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectRepository(TimestampMixin, Base):
    __tablename__ = "project_repositories"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    host: Mapped[str] = mapped_column(String(60), default="github")
    owner: Mapped[str | None] = mapped_column(String(200))
    repo: Mapped[str | None] = mapped_column(String(200))
    html_url: Mapped[str | None] = mapped_column(String(500))
    clone_url: Mapped[str | None] = mapped_column(String(500))
    default_branch: Mapped[str | None] = mapped_column(String(200))
    commit_sha: Mapped[str | None] = mapped_column(String(80))
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    readme_excerpt: Mapped[str | None] = mapped_column(Text)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="repositories")


class ProjectEnvironment(TimestampMixin, Base):
    __tablename__ = "project_environments"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(32), default="docker")  # docker|compose|host|native
    base_image: Mapped[str | None] = mapped_column(String(200))
    runtime_language: Mapped[str | None] = mapped_column(String(60))
    runtime_version: Mapped[str | None] = mapped_column(String(40))
    package_manager: Mapped[str | None] = mapped_column(String(40))
    install_command: Mapped[str | None] = mapped_column(Text)
    build_command: Mapped[str | None] = mapped_column(Text)
    start_command: Mapped[str | None] = mapped_column(Text)
    port: Mapped[int | None] = mapped_column(Integer)
    health_path: Mapped[str | None] = mapped_column(String(200))
    health_status: Mapped[str | None] = mapped_column(String(40))
    runtime_plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    env_vars: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    services: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    gpu_required: Mapped[bool] = mapped_column(Boolean, default=False)
    external_apis: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="environments")


class ProjectContainer(TimestampMixin, Base):
    __tablename__ = "project_containers"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    service_name: Mapped[str] = mapped_column(String(120))
    container_id: Mapped[str | None] = mapped_column(String(80))
    container_name: Mapped[str | None] = mapped_column(String(200))
    image: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), default="created")
    host_port: Mapped[int | None] = mapped_column(Integer)
    container_port: Mapped[int | None] = mapped_column(Integer)
    health_state: Mapped[str | None] = mapped_column(String(40))
    cpu_limit: Mapped[float | None] = mapped_column(Float)
    memory_limit_mb: Mapped[int | None] = mapped_column(Integer)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="containers")


class ProjectTask(TimestampMixin, Base):
    __tablename__ = "project_tasks"

    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    stage: Mapped[str] = mapped_column(String(48), default="queued")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project | None] = relationship(back_populates="tasks")
    logs: Mapped[list["TaskLog"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class TaskLog(TimestampMixin, Base):
    __tablename__ = "task_logs"

    task_id: Mapped[str] = mapped_column(ForeignKey("project_tasks.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    stream: Mapped[str] = mapped_column(String(24), default="stdout")
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)

    task: Mapped[ProjectTask] = relationship(back_populates="logs")


class AIProvider(TimestampMixin, Base):
    __tablename__ = "ai_providers"

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40), default="openai")  # openai|anthropic|gemini|deepseek|compatible
    base_url: Mapped[str | None] = mapped_column(String(400))
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    api_key_hint: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    cheap_model: Mapped[str | None] = mapped_column(String(160))
    api_format: Mapped[str] = mapped_column(String(32), default="openai-chat")
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    timeout_s: Mapped[int] = mapped_column(Integer, default=180)
    price_in_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    price_out_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_status: Mapped[str | None] = mapped_column(String(40))
    last_check_error: Mapped[str | None] = mapped_column(Text)
    extra_headers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    usage_records: Mapped[list["AIUsageRecord"]] = relationship(back_populates="provider")


class AIUsageRecord(TimestampMixin, Base):
    __tablename__ = "ai_usage_records"

    provider_id: Mapped[str | None] = mapped_column(ForeignKey("ai_providers.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), index=True)
    purpose: Mapped[str] = mapped_column(String(60), default="analysis")
    model: Mapped[str | None] = mapped_column(String(160))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=1)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    usage_is_known: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)

    provider: Mapped[AIProvider | None] = relationship(back_populates="usage_records")


class ExplorationPlan(TimestampMixin, Base):
    __tablename__ = "exploration_plans"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(32), default="quick")  # quick|deep|custom
    goal: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    origin_url: Mapped[str | None] = mapped_column(String(400))
    summary: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    discovered_pages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(40), default="heuristic")

    project: Mapped[Project] = relationship(back_populates="plans")
    steps: Mapped[list["ExplorationStep"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    results: Mapped[list["ExplorationResult"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class ExplorationStep(TimestampMixin, Base):
    __tablename__ = "exploration_steps"

    plan_id: Mapped[str] = mapped_column(ForeignKey("exploration_plans.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(40), default="goto")
    target: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    plan: Mapped[ExplorationPlan] = relationship(back_populates="steps")


class ExplorationResult(TimestampMixin, Base):
    __tablename__ = "exploration_results"

    plan_id: Mapped[str] = mapped_column(ForeignKey("exploration_plans.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(600))
    title: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED")
    observation: Mapped[str | None] = mapped_column(Text)
    console_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    network_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    screenshot_asset_id: Mapped[str | None] = mapped_column(String(32))
    video_asset_id: Mapped[str | None] = mapped_column(String(32))
    features: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    plan: Mapped[ExplorationPlan] = relationship(back_populates="results")


class BrowserSession(TimestampMixin, Base):
    __tablename__ = "browser_sessions"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="default")
    storage_path: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(600))
    is_authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="browser_sessions")


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # screenshot|video|gif
    label: Mapped[str | None] = mapped_column(String(200))
    relative_path: Mapped[str] = mapped_column(String(600))
    filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(120))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    viewport: Mapped[str | None] = mapped_column(String(40))
    aspect_ratio: Mapped[str | None] = mapped_column(String(20))
    step_id: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(String(600))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="assets")


class Report(TimestampMixin, Base):
    __tablename__ = "reports"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED")
    title: Mapped[str | None] = mapped_column(String(300))
    markdown_path: Mapped[str | None] = mapped_column(String(600))
    html_path: Mapped[str | None] = mapped_column(String(600))
    json_path: Mapped[str | None] = mapped_column(String(600))
    pdf_path: Mapped[str | None] = mapped_column(String(600))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_by: Mapped[str] = mapped_column(String(40), default="engine")

    project: Mapped[Project] = relationship(back_populates="reports")


class EnvironmentSnapshot(TimestampMixin, Base):
    __tablename__ = "environment_snapshots"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String(200))
    compose_path: Mapped[str | None] = mapped_column(String(600))
    dockerfile_path: Mapped[str | None] = mapped_column(String(600))
    env_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    start_commands: Mapped[list[str]] = mapped_column(JSON, default=list)
    ports: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    browser_tasks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="snapshots")


class Patch(TimestampMixin, Base):
    __tablename__ = "patches"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str | None] = mapped_column(Text)
    diagnosis: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[str | None] = mapped_column(Text)
    diff_path: Mapped[str | None] = mapped_column(String(600))
    files_touched: Mapped[list[str]] = mapped_column(JSON, default=list)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_by: Mapped[str] = mapped_column(String(40), default="ai")
    model: Mapped[str | None] = mapped_column(String(160))

    project: Mapped[Project] = relationship(back_populates="patches")


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_app_settings_key"),)

    key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DownloadCache(TimestampMixin, Base):
    """Small bookkeeping table for imported archives so re-imports are cheap."""

    __tablename__ = "download_cache"

    url: Mapped[str] = mapped_column(String(600), index=True)
    etag: Mapped[str | None] = mapped_column(String(200))
    sha256: Mapped[str | None] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str | None] = mapped_column(String(600))
