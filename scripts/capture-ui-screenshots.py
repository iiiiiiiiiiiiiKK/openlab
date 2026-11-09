"""Capture real UI screenshots of the running OPENLAB workbench.

This uses the same Playwright pipeline the product uses, so the images in
``docs/screenshots`` are produced by OPENLAB's own browser automation rather than
taken from a design tool.

The project workspace is included automatically: the script asks the live API for a
project (or uses ``--project``) and captures that page too, so the multi-panel
workspace — the core screen of the product — is documented with real content.

Usage::

    python scripts/capture-ui-screenshots.py                     # http://127.0.0.1:3210
    python scripts/capture-ui-screenshots.py --base http://127.0.0.1:5173
    python scripts/capture-ui-screenshots.py --project <project-id>
    python scripts/capture-ui-screenshots.py --width 1600 --height 1000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "apps" / "backend")]

PAGES: tuple[tuple[str, str], ...] = (
    ("overview", "/"),
    ("projects", "/projects"),
    ("running", "/running"),
    ("explorer", "/explorer"),
    ("assets", "/assets"),
    ("reports", "/reports"),
    ("providers", "/providers"),
    ("settings", "/settings"),
)


def api_get(base: str, path: str, timeout: float = 20) -> dict | list | None:
    url = f"{base.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def discover_project(base: str, explicit: str | None) -> tuple[str, str] | None:
    """Return ``(project_id, label)`` for the workspace page, if one exists."""
    if explicit:
        return explicit, "/projects/" + explicit
    listing = api_get(base, "/api/projects?sort=updated")
    if not listing or not isinstance(listing, dict):
        return None
    items = listing.get("items") or []
    if not items:
        return None
    # Prefer a project that has a plan and a live/stopped deployment over a brand-new import.
    def score(project: dict) -> tuple[int, str]:
        status = project.get("deploy_status") or ""
        return ({"RUNNING": 3, "STOPPED": 2, "FAILED": 1, "WAITING_FOR_USER": 1}.get(status, 0),
                project.get("updated_at") or "")

    best = sorted(items, key=score, reverse=True)[0]
    return best["id"], "/projects/" + best["id"]


async def capture(
    base: str,
    out_dir: Path,
    width: int,
    height: int,
    wait_ms: int,
    workspace: tuple[str, str] | None,
) -> int:
    from engine.browser_automation.launcher import (
        BrowserSession,
        BrowserUnavailable,
        LaunchOptions,
        ViewportPreset,
        ensure_artifact_temp,
    )

    targets: list[tuple[str, str]] = list(PAGES)
    if workspace is not None:
        targets.insert(2, ("workspace", workspace[1]))

    ensure_artifact_temp(REPO_ROOT / "OPENLAB_DATA" / "tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    preset = ViewportPreset("capture", width, height)
    captured = 0
    async with BrowserSession(LaunchOptions(headless=True, viewport=preset)) as session:
        page = session.page
        for name, route in targets:
            url = f"{base.rstrip('/')}{route}"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:  # noqa: BLE001 - the app polls its API continuously
                    pass
                await page.wait_for_timeout(wait_ms)
                target = out_dir / f"{name}.png"
                await page.screenshot(path=str(target), full_page=False, type="png")
                size = target.stat().st_size
                print(f"  {name:<10} -> {target.relative_to(REPO_ROOT)} ({size // 1024} KB)")
                captured += 1
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"  {name:<10} !! {type(exc).__name__}: {exc}")
    return captured, len(targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture OPENLAB UI screenshots")
    parser.add_argument("--base", default="http://127.0.0.1:3210", help="workbench base URL")
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "screenshots"))
    parser.add_argument("--project", default=None, help="project id for the workspace screenshot")
    parser.add_argument("--no-workspace", action="store_true", help="skip the project workspace page")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--wait-ms", type=int, default=1200, help="settle time before each shot")
    args = parser.parse_args(argv)

    workspace = None if args.no_workspace else discover_project(args.base, args.project)
    if workspace is None and not args.no_workspace:
        print("  (no project found — skipping the workspace screenshot)")
    try:
        count, total = asyncio.run(
            capture(args.base, Path(args.out), args.width, args.height, args.wait_ms, workspace)
        )
    except BrowserUnavailable as exc:
        print(f"Chromium is not available: {exc}", file=sys.stderr)
        return 2
    print(f"\ncaptured {count}/{total} page(s) into {args.out}")
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
