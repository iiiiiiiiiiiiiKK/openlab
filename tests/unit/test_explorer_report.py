"""Unit tests for exploration plan validation and report generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.browser_automation.explorer import (
    PlanRejected,
    heuristic_plan,
    is_risky,
    quick_plan,
    validate_model_plan,
)
from engine.browser_automation.inspector import PageSnapshot
from engine.report_generator.report import (
    ReportInput,
    overall_status,
    render_html,
    render_markdown,
    write_reports,
)


def _snapshot() -> PageSnapshot:
    return PageSnapshot(
        url="http://127.0.0.1:41000/",
        title="Demo App",
        headings=[{"level": "h1", "text": "Demo App"}],
        nav=[
            {"text": "Home", "href": "http://127.0.0.1:41000/", "selector": "a"},
            {"text": "About", "href": "http://127.0.0.1:41000/about", "selector": "a"},
            {"text": "External", "href": "https://example.com/x", "selector": "a"},
        ],
        buttons=[
            {"text": "Run counter", "selector": "#tick", "type": "button"},
            {"text": "Delete account", "selector": "#delete", "type": "button"},
        ],
        inputs=[{"tag": "input", "type": "text", "name": "name", "selector": "#name", "label": "Name"}],
        forms=[],
        text_length=420,
        body_excerpt="Demo App body",
    )


def test_risky_labels() -> None:
    assert is_risky("Delete account")
    assert is_risky("Pay now")
    assert is_risky("发布内容")
    assert not is_risky("Run counter")


def test_quick_plan_keeps_internal_pages() -> None:
    plan = quick_plan(_snapshot(), "http://127.0.0.1:41000/")
    assert plan.mode == "quick"
    assert plan.steps[0].action == "goto"
    assert plan.steps[0].capture is True
    urls = [page["url"] for page in plan.discovered_pages]
    assert all("example.com" not in url for url in urls)
    assert any("/about" in url for url in urls)


def test_deep_plan_skips_risky_buttons() -> None:
    plan = heuristic_plan(_snapshot(), "http://127.0.0.1:41000/", "deep", None)
    clicks = [step for step in plan.steps if step.action == "click"]
    assert clicks
    assert all(step.target != "#delete" for step in clicks)


def test_custom_plan_matches_goal_keywords() -> None:
    plan = heuristic_plan(_snapshot(), "http://127.0.0.1:41000/", "custom", "about page")
    assert plan.mode == "custom"
    assert any("about" in (step.target or "").lower() or "About" in step.description for step in plan.steps)


def test_validate_model_plan_accepts_safe_steps() -> None:
    data = {
        "summary": "look around",
        "steps": [
            {"action": "goto", "target": "http://127.0.0.1:41000/", "capture": True},
            {"action": "click", "target": "#tick", "description": "run the counter", "capture": True},
            {"action": "fill", "target": "#name", "value": "openlab", "capture": False},
        ],
    }
    spec = validate_model_plan(data, _snapshot(), "http://127.0.0.1:41000/", "custom", "test")
    assert spec.generated_by == "model"
    assert len(spec.steps) == 3


@pytest.mark.parametrize(
    "step",
    [
        {"action": "click", "target": "#delete", "description": "delete everything"},
        {"action": "goto", "target": "https://evil.example.com/"},
        {"action": "navigate", "target": "http://127.0.0.1:41000/"},
        {"action": "click", "target": "#not-on-page"},
    ],
)
def test_validate_model_plan_rejects_unsafe_steps(step: dict) -> None:
    with pytest.raises(PlanRejected):
        validate_model_plan(
            {"steps": [{"action": "goto", "target": "http://127.0.0.1:41000/"}, step]},
            _snapshot(),
            "http://127.0.0.1:41000/",
            "deep",
            None,
        )


def test_validate_model_plan_requires_steps() -> None:
    with pytest.raises(PlanRejected):
        validate_model_plan({"steps": []}, _snapshot(), "http://127.0.0.1:41000/", "quick", None)


def _report_input(status_ok: bool = True) -> ReportInput:
    return ReportInput(
        project={
            "name": "demo",
            "display_name": "Demo App",
            "description": "A demo",
            "source_url": "https://github.com/x/demo",
            "stars": 12,
            "license": "MIT",
            "language": "TypeScript",
            "author": "x",
        },
        environment={
            "strategy": "generated",
            "base_image": "node:20-bookworm-slim",
            "package_manager": "npm",
            "install_command": "npm ci",
            "build_command": "npm run build",
            "start_command": "npm run start",
            "port": 3000,
            "env_vars": [{"name": "DATABASE_URL", "required": True, "configured": False, "description": "db"}],
        },
        deploy={
            "success": status_ok,
            "port": 41000,
            "http_status": 200 if status_ok else None,
            "stage": "HEALTH_CHECK",
            "error": None if status_ok else "container exited",
        },
        features=[
            {
                "name": "Exploration (quick)",
                "description": "auto",
                "status": "VERIFIED" if status_ok else "FAILED",
                "steps": [
                    {"index": 0, "action": "goto", "target": "http://127.0.0.1:41000/", "status": "VERIFIED", "url": "http://127.0.0.1:41000/"}
                ],
                "screenshots": ["screenshots/home.png"],
            }
        ],
        technical={
            "framework": "Next.js",
            "languages": [{"language": "TypeScript", "share": 92.0}],
            "services": [],
            "databases": [],
            "external_apis": [],
            "gpu_required": False,
            "has_dockerfile": False,
            "has_compose": False,
            "dependencies": ["next", "react"],
        },
        assets=[
            {"kind": "screenshot", "filename": "home.png", "relative_path": "screenshots/home.png", "width": 1440, "height": 900, "size_bytes": 20480}
        ],
        patches=[],
        usage={"requests": 3, "tokens": 1200, "cost_usd": 0.0012, "records_with_unknown_usage": 0},
    )


def test_overall_status_verified() -> None:
    assert overall_status(_report_input()) == "VERIFIED"


def test_overall_status_failed_when_deploy_failed() -> None:
    assert overall_status(_report_input(status_ok=False)) == "FAILED"


def test_overall_status_blocked() -> None:
    data = _report_input(status_ok=False)
    data.deploy["blocked"] = True
    data.features = []
    assert overall_status(data) == "BLOCKED"


def test_markdown_report_contains_evidence() -> None:
    markdown = render_markdown(_report_input())
    assert "# Demo App — OPENLAB experience report" in markdown
    assert "VERIFIED" in markdown
    assert "41000" in markdown
    assert "DATABASE_URL" in markdown
    assert "screenshots/home.png" in markdown
    assert "README claims were not treated as evidence" in markdown


def test_html_report_is_standalone() -> None:
    html = render_html(_report_input())
    assert "<!doctype html>" in html
    assert "Demo App" in html
    assert "home.png" in html
    assert "<style>" in html


def test_write_reports_creates_three_files(tmp_path: Path) -> None:
    written = write_reports(_report_input(), tmp_path, basename="report-v1")
    assert written.markdown.exists()
    assert written.html.exists()
    assert written.json_path.exists()
    payload = written.json_path.read_text(encoding="utf-8")
    assert '"status": "VERIFIED"' in payload
    assert written.status == "VERIFIED"


def test_report_redacts_secrets() -> None:
    data = _report_input()
    data.deploy["error"] = "failed with sk-abcdefghijklmnopqrstuvwxyz012345"
    text = render_markdown(data)
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in text


def test_host_strategy_is_labelled_as_unsandboxed() -> None:
    data = _report_input()
    data.environment["strategy"] = "host"
    markdown = render_markdown(data)
    assert "Isolation:** NONE" in markdown
    assert "without container isolation" in markdown
    assert "host runtime was enabled" in markdown


def test_docker_strategy_has_no_isolation_warning() -> None:
    data = _report_input()
    data.environment["strategy"] = "generated"
    markdown = render_markdown(data)
    assert "Isolation:** NONE" not in markdown
