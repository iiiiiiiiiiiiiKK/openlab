"""Unit tests for secret storage, redaction and the deploy state machine."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.core.security import SecretBox, redact
from engine.core.states import (
    DeployStatus,
    InvalidTransition,
    TaskStatus,
    assert_transition,
    can_transition,
    task_can_transition,
)


def test_secret_box_roundtrip(tmp_path: Path) -> None:
    box = SecretBox(tmp_path / "secrets")
    token = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    encrypted = box.encrypt(token)
    assert encrypted != token
    assert box.decrypt(encrypted) == token


def test_secret_box_mask_and_fingerprint(tmp_path: Path) -> None:
    box = SecretBox(tmp_path / "secrets")
    encrypted = box.encrypt("ghp_1234567890abcdefghij")
    masked = box.mask(encrypted)
    assert masked.startswith("ghp")
    assert "*" in masked
    assert len(masked) < 20
    fingerprint = box.fingerprint(encrypted)
    assert fingerprint is not None
    assert fingerprint["length"] == len("ghp_1234567890abcdefghij")


def test_secret_box_persists_key(tmp_path: Path) -> None:
    first = SecretBox(tmp_path / "secrets")
    encrypted = first.encrypt("hello-secret")
    second = SecretBox(tmp_path / "secrets")
    assert second.decrypt(encrypted) == "hello-secret"


def test_decrypt_invalid_token_returns_empty(tmp_path: Path) -> None:
    box = SecretBox(tmp_path / "secrets")
    assert box.decrypt("not-a-token") == ""
    assert box.fingerprint("not-a-token") is None


def test_redaction_patterns() -> None:
    text = "key=sk-1234567890abcdefghijklmnop token: ghp_abcdefghijklmnopqrstuvwx"
    cleaned = redact(text)
    assert "sk-1234567890abcdefghijklmnop" not in cleaned
    assert "ghp_abcdefghijklmnopqrstuvwx" not in cleaned
    assert "REDACTED" in cleaned


def test_redaction_known_secret() -> None:
    secret = "my-super-secret-value"
    assert secret not in redact(f"api used {secret} here", known_secrets=[secret])
    assert redact("") == ""


def test_deploy_state_machine_allows_documented_path() -> None:
    path = [
        DeployStatus.PENDING,
        DeployStatus.ANALYZING,
        DeployStatus.PLANNING,
        DeployStatus.PREPARING,
        DeployStatus.INSTALLING,
        DeployStatus.BUILDING,
        DeployStatus.STARTING,
        DeployStatus.HEALTH_CHECK,
        DeployStatus.RUNNING,
    ]
    for current, target in zip(path, path[1:]):
        assert can_transition(current.value, target.value), f"{current} -> {target}"
        assert_transition(current.value, target.value)


def test_deploy_state_machine_rejects_shortcuts() -> None:
    assert not can_transition("PENDING", "RUNNING")
    assert not can_transition("BUILDING", "RUNNING")
    assert not can_transition("FAILED", "RUNNING")
    with pytest.raises(InvalidTransition):
        assert_transition("PENDING", "RUNNING")


def test_deploy_state_machine_allows_recovery_and_cancel() -> None:
    assert can_transition("HEALTH_CHECK", "WAITING_FOR_USER")
    assert can_transition("WAITING_FOR_USER", "PREPARING")
    assert can_transition("RUNNING", "STOPPED")
    assert can_transition("STOPPED", "PREPARING")
    assert can_transition("ANALYZING", "CANCELLED")
    assert not can_transition("CANCELLED", "RUNNING")


def test_task_transitions() -> None:
    assert task_can_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    assert task_can_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
    assert task_can_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
    assert not task_can_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)
    assert TaskStatus.SUCCEEDED.is_terminal
    assert not TaskStatus.RUNNING.is_terminal


def test_unknown_status_is_rejected() -> None:
    assert not can_transition("PENDING", "NOT_A_STATUS")
    assert not can_transition("NOT_A_STATUS", "RUNNING")
