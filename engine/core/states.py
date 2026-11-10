"""Shared enums and state machines for OPENLAB.

The deploy state machine is intentionally explicit: a project only reaches
``RUNNING`` after a real HTTP health check succeeded, and every failure ends in
``FAILED`` or ``WAITING_FOR_USER`` -- never an endless retry loop.
"""

from __future__ import annotations

from enum import StrEnum


class DeployStatus(StrEnum):
    PENDING = "PENDING"
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    PREPARING = "PREPARING"
    INSTALLING = "INSTALLING"
    BUILDING = "BUILDING"
    STARTING = "STARTING"
    HEALTH_CHECK = "HEALTH_CHECK"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


DEPLOY_ORDER: tuple[DeployStatus, ...] = (
    DeployStatus.PENDING,
    DeployStatus.ANALYZING,
    DeployStatus.PLANNING,
    DeployStatus.PREPARING,
    DeployStatus.INSTALLING,
    DeployStatus.BUILDING,
    DeployStatus.STARTING,
    DeployStatus.HEALTH_CHECK,
    DeployStatus.RUNNING,
)

TERMINAL_STATES: frozenset[DeployStatus] = frozenset(
    {
        DeployStatus.RUNNING,
        DeployStatus.FAILED,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.STOPPED,
        DeployStatus.CANCELLED,
    }
)

_ALLOWED: dict[DeployStatus, set[DeployStatus]] = {
    DeployStatus.PENDING: {
        DeployStatus.ANALYZING,
        # A redeploy of an already-imported project skips analysis and goes straight
        # to PREPARING; refusing that transition only produced a warning on every run.
        DeployStatus.PREPARING,
        DeployStatus.CANCELLED,
        DeployStatus.FAILED,
    },
    DeployStatus.ANALYZING: {
        DeployStatus.PLANNING,
        DeployStatus.FAILED,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.CANCELLED,
    },
    DeployStatus.PLANNING: {
        DeployStatus.PREPARING,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.FAILED,
        DeployStatus.CANCELLED,
    },
    DeployStatus.PREPARING: {
        DeployStatus.INSTALLING,
        DeployStatus.BUILDING,
        DeployStatus.STARTING,
        DeployStatus.FAILED,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.CANCELLED,
    },
    DeployStatus.INSTALLING: {
        DeployStatus.BUILDING,
        DeployStatus.STARTING,
        DeployStatus.FAILED,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.CANCELLED,
    },
    DeployStatus.BUILDING: {
        DeployStatus.STARTING,
        DeployStatus.FAILED,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.CANCELLED,
    },
    DeployStatus.STARTING: {
        DeployStatus.HEALTH_CHECK,
        # The host runtime has no container layer to wait for: once the process
        # answers HTTP it moves straight from STARTING to RUNNING.
        DeployStatus.RUNNING,
        DeployStatus.FAILED,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.CANCELLED,
    },
    DeployStatus.HEALTH_CHECK: {
        DeployStatus.RUNNING,
        DeployStatus.FAILED,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.CANCELLED,
    },
    DeployStatus.RUNNING: {
        DeployStatus.STOPPED,
        DeployStatus.FAILED,
        DeployStatus.ANALYZING,
        DeployStatus.HEALTH_CHECK,
    },
    DeployStatus.FAILED: {
        DeployStatus.PENDING,
        DeployStatus.ANALYZING,
        DeployStatus.PREPARING,
        DeployStatus.WAITING_FOR_USER,
        DeployStatus.CANCELLED,
    },
    DeployStatus.WAITING_FOR_USER: {
        DeployStatus.PENDING,
        DeployStatus.ANALYZING,
        DeployStatus.PREPARING,
        DeployStatus.CANCELLED,
        DeployStatus.FAILED,
    },
    DeployStatus.STOPPED: {DeployStatus.PREPARING, DeployStatus.STARTING, DeployStatus.PENDING},
    DeployStatus.CANCELLED: {DeployStatus.PENDING, DeployStatus.ANALYZING, DeployStatus.PREPARING},
}


class InvalidTransition(ValueError):
    """Raised when a deploy state change is not allowed."""


def can_transition(current: str, target: str) -> bool:
    try:
        current_enum = DeployStatus(current)
        target_enum = DeployStatus(target)
    except ValueError:
        return False
    if current_enum is target_enum:
        return True
    return target_enum in _ALLOWED.get(current_enum, set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(f"{current} -> {target} is not allowed")


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    WAITING_FOR_USER = "waiting_for_user"

    @property
    def is_terminal(self) -> bool:
        return self in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}


TASK_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.PAUSED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.PAUSED,
        TaskStatus.WAITING_FOR_USER,
    },
    TaskStatus.PAUSED: {TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.RUNNING},
    TaskStatus.WAITING_FOR_USER: {TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.RUNNING},
    TaskStatus.FAILED: {TaskStatus.QUEUED, TaskStatus.CANCELLED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.CANCELLED: set(),
}


def task_can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    return target in TASK_TRANSITIONS.get(current, set())


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ExplorationMode(StrEnum):
    QUICK = "quick"
    DEEP = "deep"
    CUSTOM = "custom"


class ProviderKind(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    DEEPSEEK = "deepseek"
    COMPATIBLE = "compatible"


PROVIDER_DEFAULTS: dict[str, dict[str, object]] = {
    ProviderKind.OPENAI: {
        "base_url": "https://api.openai.com/v1",
        "api_format": "openai-chat",
        "model": "gpt-4o-mini",
        "cheap_model": "gpt-4o-mini",
    },
    ProviderKind.ANTHROPIC: {
        "base_url": "https://api.anthropic.com/v1",
        "api_format": "anthropic-messages",
        "model": "claude-3-5-sonnet-latest",
        "cheap_model": "claude-3-5-haiku-latest",
    },
    ProviderKind.GEMINI: {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_format": "gemini-generate",
        "model": "gemini-1.5-pro",
        "cheap_model": "gemini-1.5-flash",
    },
    ProviderKind.DEEPSEEK: {
        "base_url": "https://api.deepseek.com/v1",
        "api_format": "openai-chat",
        "model": "deepseek-chat",
        "cheap_model": "deepseek-chat",
    },
    ProviderKind.COMPATIBLE: {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_format": "openai-chat",
        "model": "llama3.1",
        "cheap_model": "llama3.1",
    },
}
