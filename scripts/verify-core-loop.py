"""Run the complete OPENLAB core loop against a live backend, over real HTTP.

    GitHub import -> analysis -> run plan -> deploy -> live preview
    -> Playwright exploration -> screenshots/video -> report

Every step talks to the running server (default ``http://127.0.0.1:3210``) and
waits for the real task to finish. Nothing is mocked; if the host cannot run a
project the script reports the truthful status instead of pretending success.

Usage::

    python scripts/verify-core-loop.py                       # local fixture project
    python scripts/verify-core-loop.py --github owner/repo   # also import a real repo
    python scripts/verify-core-loop.py --json                # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "node-web"


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def request(self, method: str, path: str, payload: dict | None = None, timeout: float = 120) -> Any:
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail[:400]}") from exc

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, payload: dict | None = None, **kwargs) -> Any:
        return self.request("POST", path, payload or {}, **kwargs)


def step(title: str) -> None:
    print(f"\n==> {title}", flush=True)


def ok(text: str) -> None:
    print(f"    OK   {text}", flush=True)


def note(text: str) -> None:
    print(f"    ..   {text}", flush=True)


def warn(text: str) -> None:
    print(f"    WARN {text}", flush=True)


def fail(text: str) -> None:
    print(f"    FAIL {text}", flush=True)


def wait_task(api: Api, task_id: str, timeout: float = 600, label: str = "task") -> dict:
    deadline = time.time() + timeout
    last_status = ""
    while time.time() < deadline:
        task = api.get(f"/api/tasks/{task_id}")
        status = task.get("status")
        if status != last_status:
            note(f"{label}: {status} {task.get('message') or ''}")
            last_status = status
        if status in {"succeeded", "failed", "cancelled", "paused"}:
            return task
        time.sleep(1.0)
    raise TimeoutError(f"{label} did not finish within {timeout:.0f}s")


def run_loop(api: Api, source: str, label: str, report: dict) -> None:
    step(f"Import ({label})")
    if source.startswith("http") or source.startswith("git@") or "/" in source and not Path(source).exists():
        imported = api.post("/api/projects/import/github", {"url": source})
    else:
        imported = api.post("/api/projects/import/local", {"path": source, "display_name": label})
    project_id = imported["project_id"]
    report["project_id"] = project_id
    ok(f"project {project_id[:8]} · {imported['files']} files")

    step("Analysis")
    detail = api.get(f"/api/projects/{project_id}")
    plan = detail.get("plan") or {}
    report["plan"] = {
        "project_type": plan.get("project_type"),
        "framework": plan.get("framework"),
        "package_manager": plan.get("package_manager"),
        "strategy": plan.get("strategy"),
        "start_command": plan.get("start_command"),
        "port": plan.get("port"),
        "confidence": plan.get("confidence"),
    }
    ok(
        f"type={plan.get('project_type')} framework={plan.get('framework')} "
        f"strategy={plan.get('strategy')} confidence={plan.get('confidence')}"
    )
    if plan.get("start_command"):
        note(f"start: {plan['start_command']}")

    step("Deploy")
    queued = api.post(f"/api/projects/{project_id}/deploy", {"rebuild": True})
    task = wait_task(api, queued["task_id"], label="deploy")
    result = task.get("result") or {}
    detail = api.get(f"/api/projects/{project_id}")
    status = detail["deploy_status"]
    report["deploy"] = {
        "task_status": task.get("status"),
        "project_status": status,
        "success": result.get("success"),
        "port": result.get("port"),
        "http_status": result.get("http_status"),
        "error": result.get("error"),
        "detail": detail.get("deploy_status_detail"),
    }
    if status != "RUNNING":
        warn(f"deployment ended as {status}: {(detail.get('deploy_status_detail') or '')[:200]}")
        report["live"] = False
        return
    ok(f"RUNNING on http://127.0.0.1:{result['port']} (HTTP {result['http_status']})")
    report["live"] = True

    step("Preview + screenshot")
    preview = api.post(f"/api/projects/{project_id}/preview", {"viewport": "desktop", "full_page": True})
    asset_id = (preview.get("snapshot") or {}).get("asset_id")
    report["preview"] = {
        "title": preview.get("title"),
        "url": preview.get("url"),
        "asset_id": asset_id,
        "console": preview.get("console"),
    }
    ok(f"title={preview.get('title')!r} asset={asset_id}")

    step("AI exploration (quick)")
    queued = api.post(f"/api/projects/{project_id}/explore", {"mode": "quick", "record_video": True})
    explore_task = wait_task(api, queued["task_id"], timeout=900, label="explore")
    explore_result = explore_task.get("result") or {}
    report["exploration"] = {
        "status": explore_result.get("status"),
        "steps": len(explore_result.get("steps") or []),
        "video_asset_id": explore_result.get("video_asset_id"),
    }
    ok(f"exploration {explore_result.get('status')} with {len(explore_result.get('steps') or [])} step(s)")

    step("Assets")
    assets = api.get(f"/api/assets?project_id={project_id}")["items"]
    by_kind: dict[str, int] = {}
    for asset in assets:
        by_kind[asset["kind"]] = by_kind.get(asset["kind"], 0) + 1
    report["assets"] = by_kind
    ok(", ".join(f"{count} {kind}" for kind, count in sorted(by_kind.items())) or "none")

    step("Report")
    generated = api.post(f"/api/projects/{project_id}/reports")
    report["report"] = {
        "id": generated["id"],
        "version": generated["version"],
        "status": generated["status"],
        "html": generated.get("html_path"),
        "markdown": generated.get("markdown_path"),
    }
    ok(f"v{generated['version']} · {generated['status']} · {generated.get('html_path')}")

    step("Stop")
    api.post(f"/api/projects/{project_id}/stop")
    ok("project stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the OPENLAB core loop end to end")
    parser.add_argument("--base", default="http://127.0.0.1:3210")
    parser.add_argument("--github", default=None, help="a real GitHub repo (owner/repo or URL)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    api = Api(args.base)
    try:
        health = api.get("/api/health")
    except Exception as exc:  # noqa: BLE001
        print(f"backend is not reachable at {args.base}: {exc}", file=sys.stderr)
        return 2

    host = api.get("/api/system/host")
    docker = host["docker"]
    print("OPENLAB core-loop verification")
    print(f"  backend   : {args.base} (v{health.get('version')})")
    print(f"  python    : {host.get('python')}")
    print(f"  docker    : {'running ' + str(docker.get('server_version')) if docker.get('daemon_running') else 'NOT AVAILABLE'}")
    print(f"  chromium  : {'yes' if host.get('browsers', {}).get('chromium_available') else 'missing'}")
    print(f"  ffmpeg    : {host.get('ffmpeg') or 'missing'}")

    report: dict = {"base": args.base, "docker": docker.get("daemon_running")}
    if not FIXTURE.exists():
        print(f"fixture missing: {FIXTURE}", file=sys.stderr)
        return 2

    run_loop(api, str(FIXTURE), "Node Fixture", report)

    if args.github:
        github_report: dict = {}
        try:
            run_loop(api, args.github, "GitHub Import", github_report)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            fail(f"github loop failed: {exc}")
            github_report["error"] = str(exc)
        report["github"] = github_report

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("\nSummary")
        print(f"  local loop  : {'live + evidence' if report.get('live') else 'deployment did not reach RUNNING'}")
        print(f"  exploration : {report.get('exploration', {}).get('status', 'n/a')}")
        print(f"  assets      : {report.get('assets', {})}")
        print(f"  report      : {report.get('report', {}).get('status', 'n/a')}")

    return 0 if report.get("live") else 1


if __name__ == "__main__":
    raise SystemExit(main())
