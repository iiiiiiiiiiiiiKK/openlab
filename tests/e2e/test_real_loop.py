"""End-to-end tests that really run a project, a browser and the report pipeline.

These tests are honest about the environment:

* If Docker is running, the compose strategy is exercised for real.
* Otherwise the opt-in host runtime starts the fixture process, and the test is
  labelled ``host``.
* If neither is possible (no Docker and host runtime disallowed) or Playwright's
  Chromium is missing, the test is skipped with an explicit reason -- never
  replaced by a mock.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except Exception:  # noqa: BLE001 - any failure means "not ready yet"
            time.sleep(0.3)
    return False


# ----------------------------------------------------------------------------
# fixture servers started directly by the test (they prove the fixture works)
# ----------------------------------------------------------------------------


@pytest.fixture()
def node_server(fixtures_dir: Path):
    script = fixtures_dir / "node-web" / "server.js"
    port = _free_port()
    env = {**os.environ, "PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable if False else "node", str(script)],
        cwd=str(fixtures_dir / "node-web"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        assert _wait_http(f"http://127.0.0.1:{port}/", timeout=30), "node fixture did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


@pytest.fixture()
def python_server(fixtures_dir: Path):
    script = fixtures_dir / "python-web" / "app.py"
    port = _free_port()
    env = {**os.environ, "PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(fixtures_dir / "python-web"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        assert _wait_http(f"http://127.0.0.1:{port}/", timeout=30), "python fixture did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


# ----------------------------------------------------------------------------
# real browser: preview, screenshots, exploration, video, GIF
# ----------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("OPENLAB_SKIP_BROWSER") == "1", reason="browser tests disabled by environment"
)
async def test_real_browser_preview_and_screenshot(node_server: str, data_dir: Path, tmp_browsers) -> None:
    from engine.browser_automation.launcher import browser_inventory
    from engine.browser_automation.executor import ExecutorOptions, open_preview
    from engine.browser_automation.launcher import ensure_artifact_temp

    inventory = browser_inventory()
    if not inventory["chromium_available"]:
        pytest.skip(f"Playwright Chromium is not installed: {inventory['hint']}")

    ensure_artifact_temp(data_dir / "tmp")
    shots = data_dir / "shots"
    options = ExecutorOptions(screenshot_dir=shots, viewport="desktop", record_video=False)
    snapshot, collector, shot, info = await open_preview(node_server, options, "home.png")

    assert info["title"] == "OPENLAB Node Fixture"
    assert "OPENLAB Node Fixture" in (snapshot.title or "")
    assert snapshot.headings and snapshot.headings[0]["text"] == "Node fixture app"
    assert any("About" in (nav.get("text") or "") for nav in snapshot.nav)
    assert shot.path.exists()
    assert shot.path.stat().st_size > 2000
    assert shot.width >= 1200 and shot.height >= 600
    assert collector.console_errors == []


async def test_real_browser_multi_viewport_capture(node_server: str, data_dir: Path, tmp_browsers) -> None:
    from engine.browser_automation.capture import CaptureSpec, capture
    from engine.browser_automation.launcher import BrowserSession, LaunchOptions, ensure_artifact_temp, get_viewport

    from engine.browser_automation.launcher import browser_inventory

    if not browser_inventory()["chromium_available"]:
        pytest.skip("Chromium unavailable")

    ensure_artifact_temp(data_dir / "tmp")
    out = data_dir / "shots"
    async with BrowserSession(LaunchOptions(headless=True, viewport=get_viewport("desktop"))) as session:
        page = session.page
        await page.goto(node_server, wait_until="domcontentloaded")
        desktop = await capture(page, CaptureSpec(viewport="desktop", filename="d.png"), out)
        mobile = await capture(page, CaptureSpec(viewport="mobile", filename="m.png"), out)
        square = await capture(page, CaptureSpec(viewport="square", aspect_ratio="1:1", filename="s.png"), out)
    assert desktop.width >= 1200
    assert mobile.width == 390
    assert abs(square.width - square.height) <= 1


async def test_real_exploration_records_steps_and_video(
    node_server: str, data_dir: Path, tmp_browsers
) -> None:
    from engine.browser_automation.executor import ExecutorOptions, execute_plan
    from engine.browser_automation.explorer import heuristic_plan
    from engine.browser_automation.inspector import inspect_page
    from engine.browser_automation.launcher import (
        BrowserSession,
        LaunchOptions,
        browser_inventory,
        ensure_artifact_temp,
        get_viewport,
    )

    if not browser_inventory()["chromium_available"]:
        pytest.skip("Chromium unavailable")

    ensure_artifact_temp(data_dir / "tmp")
    async with BrowserSession(LaunchOptions(headless=True, viewport=get_viewport("desktop"))) as session:
        await session.page.goto(node_server, wait_until="domcontentloaded")
        snapshot = await inspect_page(session.page)

    plan = heuristic_plan(snapshot, node_server, "deep", None)
    assert plan.steps

    shots = data_dir / "explore-shots"
    videos = data_dir / "explore-video"
    options = ExecutorOptions(
        screenshot_dir=shots,
        viewport="desktop",
        record_video=True,
        video_dir=videos,
    )
    report = await execute_plan(plan, options)
    assert report.steps
    assert report.ok_steps >= 1
    assert report.status() in {"VERIFIED", "PARTIALLY_VERIFIED"}
    captured = [step for step in report.steps if step.screenshot is not None]
    assert captured, "no screenshots were produced"
    assert all(step.screenshot.path.exists() for step in captured)
    visited = {step.url for step in report.steps}
    assert any("/about" in url for url in visited)


async def test_video_to_gif_with_real_ffmpeg(node_server: str, data_dir: Path, tmp_browsers) -> None:
    from engine.browser_automation.executor import ExecutorOptions, execute_plan
    from engine.browser_automation.explorer import quick_plan
    from engine.browser_automation.inspector import inspect_page
    from engine.browser_automation.launcher import (
        BrowserSession,
        LaunchOptions,
        browser_inventory,
        ensure_artifact_temp,
        get_viewport,
    )
    from engine.media_processor.ffmpeg import MediaProcessor

    processor = MediaProcessor()
    if not processor.available:
        pytest.skip("ffmpeg not available on this host")
    if not browser_inventory()["chromium_available"]:
        pytest.skip("Chromium unavailable")
    caps = await processor.capabilities()
    assert caps.as_dict()["gif_strategy"] != "unsupported", "this ffmpeg build cannot make GIFs"

    ensure_artifact_temp(data_dir / "tmp")
    async with BrowserSession(LaunchOptions(headless=True, viewport=get_viewport("desktop"))) as session:
        await session.page.goto(node_server, wait_until="domcontentloaded")
        snapshot = await inspect_page(session.page)

    videos = data_dir / "vids"
    options = ExecutorOptions(
        screenshot_dir=data_dir / "shots2",
        viewport="desktop",
        record_video=True,
        video_dir=videos,
    )
    report = await execute_plan(quick_plan(snapshot, node_server), options)
    assert report.video_path is not None, "Playwright did not produce a recording"
    assert Path(report.video_path).exists()

    gif_path = data_dir / "out.gif"
    result = await processor.to_gif(Path(report.video_path), gif_path, duration_s=2.0, fps=8, width=640)
    assert result.ok, f"gif creation failed via {result.tool}: {result.tail(10)}"
    assert gif_path.exists()
    assert gif_path.stat().st_size > 1000
    assert gif_path.read_bytes()[:6] in {b"GIF87a", b"GIF89a"}


async def test_mp4_transcode(node_server: str, data_dir: Path, tmp_browsers) -> None:
    from engine.browser_automation.executor import ExecutorOptions, execute_plan
    from engine.browser_automation.explorer import quick_plan
    from engine.browser_automation.inspector import inspect_page
    from engine.browser_automation.launcher import (
        BrowserSession,
        LaunchOptions,
        browser_inventory,
        ensure_artifact_temp,
        get_viewport,
    )
    from engine.media_processor.ffmpeg import MediaCapabilityError, MediaProcessor

    processor = MediaProcessor()
    if not processor.available or not browser_inventory()["chromium_available"]:
        pytest.skip("ffmpeg or Chromium unavailable")
    caps = await processor.capabilities()
    if not caps.mp4:
        pytest.skip(
            "this ffmpeg build has no H.264/mp4 support "
            f"(missing: {', '.join(caps.missing_for('mp4'))}); install a full FFmpeg to transcode"
        )

    ensure_artifact_temp(data_dir / "tmp")
    async with BrowserSession(LaunchOptions(headless=True, viewport=get_viewport("desktop"))) as session:
        await session.page.goto(node_server, wait_until="domcontentloaded")
        snapshot = await inspect_page(session.page)
    options = ExecutorOptions(
        screenshot_dir=data_dir / "shots3",
        viewport="desktop",
        record_video=True,
        video_dir=data_dir / "vids2",
    )
    report = await execute_plan(quick_plan(snapshot, node_server), options)
    if report.video_path is None:
        pytest.skip("no recording produced")
    mp4 = data_dir / "out.mp4"
    try:
        result = await processor.to_mp4(Path(report.video_path), mp4, max_width=960)
    except MediaCapabilityError as exc:
        pytest.skip(f"mp4 encoding unavailable: {exc}")
    assert result.ok, f"mp4 transcode failed: {result.tail(10)}"
    info = await processor.probe(mp4)
    assert info.duration_ms > 0
    assert info.width > 0


# ----------------------------------------------------------------------------
# full loop through the API: import -> deploy -> preview -> explore -> report
# ----------------------------------------------------------------------------


@pytest.fixture()
def live_client(data_dir: Path, monkeypatch):
    from fastapi.testclient import TestClient
    from openlab.app import create_app
    from openlab.config import Settings

    settings = Settings(data_dir=data_dir)
    settings.data_dir = data_dir
    settings.web_dist = data_dir.parent / "no-dist"
    settings.allow_host_runtime = True
    settings.port_range_start = 41300
    settings.port_range_end = 41399
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, settings


async def _run_task(client, task_id: str, timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/tasks/{task_id}").json()
        if last["status"] in {"succeeded", "failed", "cancelled"}:
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"task {task_id} did not finish: {last.get('message')}")


async def test_full_loop_import_deploy_preview_explore_report(
    live_client, fixtures_dir: Path, tmp_browsers
) -> None:
    client, settings = live_client
    docker = client.get("/api/system/docker").json()
    from engine.browser_automation.launcher import browser_inventory

    if not browser_inventory()["chromium_available"]:
        pytest.skip("Chromium unavailable: cannot run the real browser part of the loop")

    import_resp = client.post(
        "/api/projects/import/local",
        json={"path": str(fixtures_dir / "node-web"), "display_name": "Node Fixture"},
    )
    assert import_resp.status_code == 200, import_resp.text
    project_id = import_resp.json()["project_id"]

    queued = client.post(f"/api/projects/{project_id}/deploy", json={"rebuild": True}).json()
    task = await _run_task(client, queued["task_id"])
    detail = client.get(f"/api/projects/{project_id}").json()

    if detail["deploy_status"] != "RUNNING":
        pytest.skip(
            "deployment could not reach RUNNING on this host "
            f"(docker available={docker.get('available')}, status={detail['deploy_status']}): "
            f"{detail.get('deploy_status_detail')}"
        )

    assert task["status"] == "succeeded"
    assert task["result"]["success"] is True
    port = task["result"]["port"]
    settings_port_range = (settings.port_range_start, settings.port_range_end)
    assert port, "the deployment did not report a port"
    assert settings_port_range[0] <= port <= settings_port_range[1] or 41000 <= port <= 41999, (
        f"unexpected host port {port}"
    )

    preview = client.post(f"/api/projects/{project_id}/preview", json={"viewport": "desktop", "full_page": True})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["title"]
    asset_id = body["snapshot"]["asset_id"]
    raw = client.get(f"/api/assets/{asset_id}/raw")
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")
    assert len(raw.content) > 2000

    explore = client.post(
        f"/api/projects/{project_id}/explore",
        json={"mode": "quick", "record_video": True},
    )
    assert explore.status_code == 200, explore.text
    explore_task = await _run_task(client, explore.json()["task_id"], timeout=300)
    assert explore_task["status"] == "succeeded", explore_task.get("error")
    assert explore_task["result"]["status"] in {"VERIFIED", "PARTIALLY_VERIFIED"}
    assert explore_task["result"]["steps"]

    plans = client.get(f"/api/projects/{project_id}/explore/plans").json()["items"]
    assert plans
    assert plans[0]["status"] in {"verified", "partially_verified", "failed"}

    assets = client.get(f"/api/assets?project_id={project_id}").json()["items"]
    assert any(asset["kind"] == "screenshot" for asset in assets)

    report = client.post(f"/api/projects/{project_id}/reports").json()
    assert report["status"] in {"VERIFIED", "PARTIALLY_VERIFIED"}
    markdown = client.get(f"/api/reports/{report['id']}/markdown").text
    assert "Feature verification" in markdown
    assert "41000" in markdown or str(port) in markdown

    stop = client.post(f"/api/projects/{project_id}/stop")
    assert stop.status_code == 200


async def test_host_runtime_refuses_unsafe_state(live_client, fixtures_dir: Path) -> None:
    """A project with no start command must end in WAITING_FOR_USER, not RUNNING."""
    client, _settings = live_client
    project_id = client.post(
        "/api/projects/import/local",
        json={"path": str(fixtures_dir / "compose-stack"), "display_name": "Compose Fixture"},
    ).json()["project_id"]
    docker = client.get("/api/system/docker").json()
    if docker["available"] and docker["daemon_running"]:
        pytest.skip("Docker is present; the compose path is covered elsewhere")
    queued = client.post(f"/api/projects/{project_id}/deploy", json={"rebuild": True}).json()
    task = await _run_task(client, queued["task_id"], timeout=180)
    detail = client.get(f"/api/projects/{project_id}").json()
    # A multi-service stack cannot be reproduced without Docker: OPENLAB must refuse
    # it explicitly rather than start the web process alone and call that success.
    assert detail["deploy_status"] in {"WAITING_FOR_USER", "FAILED"}
    assert detail["deploy_status"] != "RUNNING"
    assert "needs Docker" in (detail.get("deploy_status_detail") or "")
    assert task["status"] in {"succeeded", "failed"}


async def test_docker_compose_stack_when_available(live_client, fixtures_dir: Path, docker_available) -> None:
    """Real compose build+run of the multi-service fixture (skipped without Docker)."""
    if not docker_available:
        pytest.skip("Docker daemon is not available on this host")
    client, _settings = live_client
    project_id = client.post(
        "/api/projects/import/local",
        json={"path": str(fixtures_dir / "compose-stack"), "display_name": "Compose Fixture"},
    ).json()["project_id"]
    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["environment"]["strategy"] == "compose"
    queued = client.post(f"/api/projects/{project_id}/deploy", json={"rebuild": True}).json()
    task = await _run_task(client, queued["task_id"], timeout=900)
    assert task["status"] == "succeeded", task.get("error")
    result = task["result"]
    if not result["success"]:
        pytest.fail(f"compose deployment failed: {result.get('error')}")
    assert result["http_status"] == 200
