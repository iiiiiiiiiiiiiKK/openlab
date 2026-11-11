"""Regression tests for the defects found in the production audit.

Every test here corresponds to a real bug that shipped in the repository:

* compose build context pointed at the project folder instead of the workspace;
* an exploration step was reported VERIFIED without any evidence (a 404 page, a wait);
* the AI repair loop fed a truncated file to a "return the complete file" contract;
* a context line in a unified diff was overwritten instead of compared;
* a failed patch left the workspace half-modified;
* a paused task still executed, and cancelling one killed the only worker;
* httpx could not be constructed at all when NO_PROXY contained \"[::1]\";
* the AI budget object was never consumed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


# -- compose / dockerfile generation -----------------------------------------


def test_compose_build_context_targets_the_workspace(tmp_path: Path) -> None:
    """``context: ..`` made Docker build the folder holding source/ and screenshots/."""
    import yaml

    from engine.docker_manager.generator import generate_compose
    from engine.project_analyzer.planner import RunPlan

    plan = RunPlan(
        project_name="demo",
        project_type="node",
        language="JavaScript",
        framework=None,
        package_manager="npm",
        runtime_version="20",
        strategy="generated",
        install_command="npm ci",
        build_command=None,
        start_command="npm run start",
        port=3000,
        health_path="/",
        base_image="node:20-bookworm-slim",
    )
    project = tmp_path / "projects" / "abc"
    (project / "docker").mkdir(parents=True)
    workspace = project / "workspace"
    workspace.mkdir()
    (workspace / "package.json").write_text("{}", encoding="utf-8")

    compose_path = project / "docker" / "compose.openlab.yml"
    compose_path.write_text(
        generate_compose(
            plan,
            project,
            image_tag="openlab/demo:latest",
            host_port=41500,
            build_context="../workspace",
            project_key="abc",
        ),
        encoding="utf-8",
    )
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    context = document["services"]["app"]["build"]["context"]
    resolved = (compose_path.parent / context).resolve()
    assert resolved == workspace.resolve()
    assert resolved != project.resolve()


def test_container_names_are_unique_per_project() -> None:
    from engine.docker_manager.generator import container_name_for

    first = container_name_for("react", project_key="aaaabbbb")
    second = container_name_for("react", project_key="ccccdddd")
    assert first != second
    assert first.startswith("openlab-react-")


# -- exploration evidence ----------------------------------------------------


def test_plan_built_only_from_passive_steps_is_not_verified() -> None:
    from engine.browser_automation.executor import ExplorationReport, StepOutcome

    report = ExplorationReport(origin_url="http://x", mode="quick", goal=None, started_at=0.0)
    report.steps = [
        StepOutcome(
            index=0,
            action="wait",
            target=None,
            description="just wait",
            status="UNVERIFIED",
            url="http://x",
            title="",
            duration_ms=1,
        )
    ]
    assert report.status() == "UNVERIFIED"


def test_report_with_a_failed_step_is_failed_not_partial() -> None:
    from engine.browser_automation.executor import ExplorationReport, StepOutcome

    report = ExplorationReport(origin_url="http://x", mode="quick", goal=None, started_at=0.0)
    report.steps = [
        StepOutcome(0, "goto", None, "", "FAILED", "http://x", "", 1, error="HTTP 404"),
        StepOutcome(1, "wait", None, "", "UNVERIFIED", "http://x", "", 1),
    ]
    assert report.status() == "FAILED"


# -- patch application -------------------------------------------------------


def test_patch_context_mismatch_is_rejected(tmp_path: Path) -> None:
    from engine.ai_agent.patch import PatchError, apply_unified_diff

    target = tmp_path / "app.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,3 +1,3 @@\n"
        " alpha\n"
        "-WRONG\n"
        "+BETA\n"
        " gamma\n"
    )
    with pytest.raises(PatchError):
        apply_unified_diff(diff, tmp_path)
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"


def test_failed_patch_write_rolls_back_previous_files(tmp_path: Path) -> None:
    from engine.ai_agent.patch import FileChange, PatchBundle, apply_patch

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("original-a", encoding="utf-8")

    bundle = PatchBundle(
        changes=[
            FileChange(path="a.txt", old_content="original-a", new_content="patched-a"),
            # A directory cannot be written as a file: this makes the second write fail.
            FileChange(path="blocked/b.txt", old_content="", new_content="x"),
        ]
    )
    (workspace / "blocked").mkdir()
    (workspace / "blocked" / "b.txt").mkdir()  # forces IsADirectoryError

    with pytest.raises(Exception):
        apply_patch(bundle, workspace, tmp_path / "backup")
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "original-a"


# -- scheduler ---------------------------------------------------------------


async def test_paused_queued_task_does_not_execute(db) -> None:
    from engine.task_scheduler.scheduler import SchedulerConfig, TaskScheduler

    runs: list[str] = []

    async def handler(ctx) -> dict:
        runs.append(ctx.task_id)
        # Occupy the single worker long enough for the pause to land first.
        await asyncio.sleep(1.2)
        return {}

    scheduler = TaskScheduler(SchedulerConfig(max_concurrency=1))
    scheduler.register("slow", handler)
    await scheduler.start()
    try:
        blocking = await scheduler.submit("slow")
        await asyncio.sleep(0.1)  # the first task owns the worker now
        paused = await scheduler.submit("slow")
        await scheduler.pause(paused)
        await asyncio.sleep(2.0)
        assert paused not in runs
        assert blocking in runs
    finally:
        await scheduler.stop()


async def test_cancelling_a_task_keeps_the_worker_alive(db) -> None:
    from engine.task_scheduler.scheduler import SchedulerConfig, TaskScheduler

    runs: list[str] = []

    async def handler(ctx) -> dict:
        runs.append(ctx.task_id)
        await asyncio.sleep(0.4)
        return {}

    scheduler = TaskScheduler(SchedulerConfig(max_concurrency=1))
    scheduler.register("job", handler)
    await scheduler.start()
    try:
        first = await scheduler.submit("job")
        await asyncio.sleep(0.05)
        await scheduler.cancel(first)
        await asyncio.sleep(0.4)
        second = await scheduler.submit("job")
        await asyncio.sleep(1.5)
        assert second in runs
    finally:
        await scheduler.stop()


# -- proxy normalisation -----------------------------------------------------


def test_no_proxy_with_bracketed_ipv6_is_sanitised(monkeypatch) -> None:
    from engine.core.http import build_async_client, sanitize_no_proxy

    assert sanitize_no_proxy("localhost,127.0.0.1,::1,[::1]") == "localhost,127.0.0.1"
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,::1,[::1]")
    client = build_async_client(timeout=5)
    assert client is not None
    import asyncio as _asyncio

    _asyncio.get_event_loop_policy()  # keep the import used under all Python builds


def test_httpx_client_survives_a_broken_no_proxy(monkeypatch) -> None:
    import httpx

    from engine.core.http import build_client

    monkeypatch.setenv("NO_PROXY", "[::1]")
    # The raw constructor is what used to explode before any request was made.
    with pytest.raises(Exception):
        with monkeypatch.context():
            httpx.Client()
    assert build_client() is not None


# -- budget ------------------------------------------------------------------


async def test_budget_is_consumed_by_client_calls() -> None:
    from engine.ai_agent.client import Budget
    from engine.ai_agent.adapters import CompletionResult

    budget = Budget(max_requests=2)
    result = CompletionResult(
        text="{}",
        model="m",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        usage_known=True,
    )
    budget.consume(result)
    assert budget.requests_used == 1
    budget.consume(result)
    with pytest.raises(Exception):
        budget.check()


# -- container bind-address diagnosis ---------------------------------------


class _FakeContainer:
    def __init__(self, service: str, cid: str) -> None:
        self.service = service
        self.id = cid


class _FakeResult:
    def __init__(self, ok: bool) -> None:
        self.ok = ok


class _FakeRunner:
    """Answers the two in-container probes the way a real container would."""

    def __init__(self, loopback_ok: bool, external_ok: bool) -> None:
        self.loopback_ok = loopback_ok
        self.external_ok = external_ok
        self.commands: list[str] = []

    async def compose_ps(self, compose_path, project_name):
        return [_FakeContainer("app", "cid-1")]

    async def container_ip(self, container_id):
        return "172.18.0.2"

    async def exec_in(self, container, command, on_line=None, timeout=300):
        self.commands.append(command)
        return _FakeResult(self.loopback_ok if "127.0.0.1" in command else self.external_ok)


async def test_loopback_only_bind_is_diagnosed() -> None:
    """An app listening on 127.0.0.1 inside a container is unreachable via a published
    port; the deployment must say so instead of reporting a bare timeout."""
    from openlab.services.deploy import _diagnose_bind_address

    runner = _FakeRunner(loopback_ok=True, external_ok=False)
    message = await _diagnose_bind_address(
        runner, Path("compose.yml"), "proj", {"project_type": "node", "port": 3000}
    )
    assert message is not None
    assert "127.0.0.1:3000" in message
    assert "0.0.0.0" in message
    assert "172.18.0.2" in message


async def test_reachable_app_produces_no_diagnosis() -> None:
    from openlab.services.deploy import _diagnose_bind_address

    runner = _FakeRunner(loopback_ok=True, external_ok=True)
    assert await _diagnose_bind_address(
        runner, Path("compose.yml"), "proj", {"project_type": "node", "port": 3000}
    ) is None


async def test_dead_port_is_reported_separately() -> None:
    from openlab.services.deploy import _diagnose_bind_address

    runner = _FakeRunner(loopback_ok=False, external_ok=False)
    message = await _diagnose_bind_address(
        runner, Path("compose.yml"), "proj", {"project_type": "python", "port": 8000}
    )
    assert message is not None
    assert "not accepting HTTP" in message
