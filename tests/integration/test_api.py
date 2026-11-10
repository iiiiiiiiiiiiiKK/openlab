"""Integration tests: the real FastAPI app against a real SQLite database.

The app is started through its lifespan handler (so the scheduler, event bus and
secret box are all wired exactly as in production) and exercised over HTTP with a
fresh data directory per test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openlab.config import Settings


def _settings(data_dir: Path, web_dist: Path | None = None) -> Settings:
    settings = Settings(data_dir=data_dir)
    settings.data_dir = data_dir
    settings.web_dist = web_dist or (data_dir.parent / "no-web-dist")
    settings.max_parallel_deployments = 1
    return settings


@pytest.fixture()
def client(data_dir: Path):
    from openlab.app import create_app

    app = create_app(_settings(data_dir))
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client: TestClient) -> None:
    payload = client.get("/api/health").json()
    assert payload["ok"] is True
    assert payload["app"] == "OPENLAB"
    assert "scheduler" in payload
    assert set(payload["scheduler"]["handlers"]) == {"deploy", "explore", "assets", "report"}


def test_overview_empty(client: TestClient) -> None:
    payload = client.get("/api/overview").json()
    assert payload["stats"]["projects"] == 0
    assert payload["recent_projects"] == []


def test_system_host_reports_real_probes(client: TestClient) -> None:
    payload = client.get("/api/system/host").json()
    assert "docker" in payload
    assert "browsers" in payload
    assert isinstance(payload["docker"]["available"], bool)
    assert payload["cpu_count"] >= 1
    assert "ffmpeg" in payload


def test_import_invalid_github_url(client: TestClient) -> None:
    response = client.post("/api/projects/import/github", json={"url": "https://gitlab.com/a/b"})
    assert response.status_code == 400
    assert "unsupported repository host" in response.json()["detail"]


def test_import_local_folder_then_read_project(client: TestClient, fixtures_dir: Path) -> None:
    response = client.post(
        "/api/projects/import/local",
        json={"path": str(fixtures_dir / "node-web"), "display_name": "Node Fixture"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    project_id = body["project_id"]
    assert body["files"] > 0

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["display_name"] == "Node Fixture"
    assert detail["source_type"] == "local"
    assert detail["deploy_status"] == "PENDING"
    assert detail["plan"]["project_type"] == "node"
    assert detail["plan"]["start_command"] == "npm run start"
    assert detail["environment"]["package_manager"] == "npm"

    listing = client.get("/api/projects").json()
    assert any(item["id"] == project_id for item in listing["items"])

    tree = client.get(f"/api/projects/{project_id}/tree").json()
    names = {entry["name"] for entry in tree["entries"]}
    assert {"package.json", "server.js"} <= names

    file_payload = client.get(f"/api/projects/{project_id}/file?path=package.json").json()
    assert "openlab-fixture-node" in file_payload["content"]


def test_project_logs_reject_paths_outside_log_directory(client: TestClient, fixtures_dir: Path) -> None:
    project_id = client.post(
        "/api/projects/import/local", json={"path": str(fixtures_dir / "node-web")}
    ).json()["project_id"]
    settings = client.app.state.settings
    log_dir = settings.resolve_data().logs_dir(project_id)
    (log_dir / "deploy.log").write_text("deploy log\n", encoding="utf-8")

    assert client.get(f"/api/projects/{project_id}/logs?name=deploy").json()["lines"] == ["deploy log"]
    assert client.get(f"/api/projects/{project_id}/logs?name=host-runtime").status_code == 200
    assert client.get(f"/api/projects/{project_id}/logs?name=..%2F..%2F..%2F..%2FREADME.md").status_code == 422
    assert client.get(f"/api/projects/{project_id}/logs?name=C%3A%5CWindows%5Cwin.ini").status_code == 422
    assert client.get(f"/api/projects/{project_id}/logs?name=deploy%2F..%2F..%2FREADME.md").status_code == 422
    assert client.get(f"/api/projects/{project_id}/logs?name=deploy&tail=0").status_code == 422


def test_project_log_symlink_cannot_escape_log_directory(client: TestClient, fixtures_dir: Path, tmp_path: Path) -> None:
    project_id = client.post(
        "/api/projects/import/local", json={"path": str(fixtures_dir / "node-web")}
    ).json()["project_id"]
    outside = tmp_path / "outside.log"
    outside.write_text("secret outside project\n", encoding="utf-8")
    log_dir = client.app.state.settings.resolve_data().logs_dir(project_id)
    try:
        (log_dir / "deploy.log").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available for this account/platform")
    response = client.get(f"/api/projects/{project_id}/logs?name=deploy")
    assert response.status_code == 400
    assert "secret outside project" not in response.text




def test_import_zip(client: TestClient, fixtures_dir: Path, tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in (fixtures_dir / "node-web").rglob("*"):
            if path.is_file():
                zf.write(path, f"node-web/{path.relative_to(fixtures_dir / 'node-web').as_posix()}")
    with archive.open("rb") as handle:
        response = client.post(
            "/api/projects/import/zip",
            files={"file": ("fixture.zip", handle, "application/zip")},
        )
    assert response.status_code == 200, response.text
    assert response.json()["files"] >= 2


def test_provider_crud_and_key_masking(client: TestClient) -> None:
    created = client.post(
        "/api/providers",
        json={
            "name": "Local OpenAI",
            "kind": "openai",
            "api_key": "sk-test-1234567890abcdefghij",
            "model": "gpt-4o-mini",
            "is_default": True,
        },
    )
    assert created.status_code == 200, created.text
    provider = created.json()
    assert provider["has_api_key"] is True
    assert "sk-test" not in json.dumps(provider)
    assert provider["api_key_masked"].startswith("sk-")
    assert provider["is_default"] is True

    listing = client.get("/api/providers").json()
    assert len(listing["items"]) == 1
    assert "defaults" in listing

    patched = client.patch(f"/api/providers/{provider['id']}", json={"enabled": False}).json()
    assert patched["enabled"] is False

    usage = client.get("/api/providers/usage/summary").json()
    assert usage["requests"] == 0

    assert client.delete(f"/api/providers/{provider['id']}").json()["deleted"] is True
    assert client.get("/api/providers").json()["items"] == []


def test_provider_key_never_persisted_in_plaintext(client: TestClient, data_dir: Path) -> None:
    secret = "sk-plaintext-check-0987654321abcd"
    client.post(
        "/api/providers",
        json={"name": "leaky", "kind": "openai", "api_key": secret, "model": "m"},
    )
    database = data_dir / "openlab.db"
    assert database.exists()
    raw = database.read_bytes()
    assert secret.encode() not in raw
    for wal in data_dir.glob("openlab.db*"):
        assert secret.encode() not in wal.read_bytes()


def test_settings_roundtrip(client: TestClient) -> None:
    current = client.get("/api/settings").json()
    assert current["container_cpu_limit"] > 0
    assert current["security"]["privileged"] is False

    updated = client.patch("/api/settings", json={"container_cpu_limit": 3.5, "max_repair_attempts": 2}).json()
    assert updated["container_cpu_limit"] == 3.5
    assert updated["max_repair_attempts"] == 2

    tokenized = client.patch("/api/settings", json={"github_token": "ghp_abcdefghijklmnopqrstuvwxyz"}).json()
    assert tokenized["has_github_token"] is True
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in json.dumps(tokenized)
    from openlab.db import session_scope
    from openlab.models import AppSetting

    with session_scope() as session:
        stored = session.query(AppSetting).filter(AppSetting.key == "github_token").one()
        encrypted_token = stored.value["enc"]
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in encrypted_token
    client.app.state.secret_store.pop("github_token", None)
    from openlab.app import _load_secret_store

    client.app.state.secret_store.update(_load_secret_store(client.app.state.box))
    assert client.app.state.secret_store["github_token"] == "ghp_abcdefghijklmnopqrstuvwxyz"

    cleared = client.patch("/api/settings", json={"clear_github_token": True}).json()
    assert cleared["has_github_token"] is False
    with session_scope() as session:
        assert session.query(AppSetting).filter(AppSetting.key == "github_token").one_or_none() is None


def test_deploy_without_docker_waits_for_user(client: TestClient, fixtures_dir: Path) -> None:
    """Deployment must never fake success: with no Docker it ends WAITING_FOR_USER."""
    status = client.get("/api/system/docker").json()
    if status["available"] and status["daemon_running"]:
        pytest.skip("Docker is available on this host; covered by the e2e suite instead")

    project_id = client.post(
        "/api/projects/import/local", json={"path": str(fixtures_dir / "node-web")}
    ).json()["project_id"]

    queued = client.post(f"/api/projects/{project_id}/deploy", json={"rebuild": True})
    assert queued.status_code == 200
    task_id = queued.json()["task_id"]

    import time

    deadline = time.time() + 90
    status_value = ""
    task = {}
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        status_value = task["status"]
        if status_value in {"succeeded", "failed"}:
            break
        time.sleep(0.4)

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["deploy_status"] == "WAITING_FOR_USER"
    assert "Docker" in (detail["deploy_status_detail"] or "")
    result = client.get(f"/api/tasks/{task_id}").json()
    assert result["result"]["success"] is False
    assert result["result"]["blocked"] is True
    assert result["result"]["error"]


def test_report_generation_from_recorded_state(client: TestClient, fixtures_dir: Path) -> None:
    project_id = client.post(
        "/api/projects/import/local", json={"path": str(fixtures_dir / "python-web")}
    ).json()["project_id"]

    created = client.post(f"/api/projects/{project_id}/reports")
    assert created.status_code == 200, created.text
    report = created.json()
    assert report["version"] == 1
    assert report["status"] in {"FAILED", "BLOCKED", "PARTIALLY_VERIFIED", "UNVERIFIED"}
    assert Path(report["markdown_path"]).exists()
    assert Path(report["html_path"]).exists()
    assert Path(report["json_path"]).exists()

    markdown = client.get(f"/api/reports/{report['id']}/markdown")
    assert markdown.status_code == 200
    assert "OPENLAB experience report" in markdown.text
    assert "README claims were not treated as evidence" in markdown.text

    listing = client.get("/api/reports").json()["items"]
    assert len(listing) == 1

    again = client.post(f"/api/projects/{project_id}/reports").json()
    assert again["version"] == 2


def test_patch_and_snapshot_endpoints(client: TestClient, fixtures_dir: Path) -> None:
    project_id = client.post(
        "/api/projects/import/local", json={"path": str(fixtures_dir / "compose-stack")}
    ).json()["project_id"]

    snapshot = client.post(f"/api/projects/{project_id}/snapshots", json={"label": "before run"})
    assert snapshot.status_code == 200
    snapshots = client.get(f"/api/projects/{project_id}/snapshots").json()["items"]
    assert snapshots[0]["label"] == "before run"

    patches = client.get(f"/api/projects/{project_id}/patches").json()["items"]
    assert patches == []

    reset = client.post(f"/api/projects/{project_id}/reset-workspace").json()
    assert reset["restored_files"] > 0


def test_analysis_endpoint_updates_plan(client: TestClient, fixtures_dir: Path) -> None:
    project_id = client.post(
        "/api/projects/import/local", json={"path": str(fixtures_dir / "compose-stack")}
    ).json()["project_id"]
    payload = client.post(f"/api/projects/{project_id}/analyze").json()
    assert payload["analysis"]["plan"]["strategy"] == "compose"
    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["environment"]["strategy"] == "compose"


def test_delete_project_removes_data(client: TestClient, fixtures_dir: Path) -> None:
    project_id = client.post(
        "/api/projects/import/local", json={"path": str(fixtures_dir / "node-web")}
    ).json()["project_id"]
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    assert client.delete(f"/api/projects/{project_id}").json()["deleted"] is True
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert client.get("/api/projects").json()["items"] == []


def test_tasks_listing_and_unknown_task(client: TestClient) -> None:
    assert client.get("/api/tasks").json()["items"] == []
    assert client.get("/api/tasks/does-not-exist").status_code == 404
    assert client.post("/api/tasks/does-not-exist/action", json={"action": "cancel"}).status_code == 409


def test_assets_endpoints_empty(client: TestClient) -> None:
    payload = client.get("/api/assets").json()
    assert payload["items"] == []
    assert payload["stats"]["total"] == 0
    assert client.get("/api/assets/unknown").status_code == 404


def test_explore_requires_running_project(client: TestClient, fixtures_dir: Path) -> None:
    project_id = client.post(
        "/api/projects/import/local", json={"path": str(fixtures_dir / "node-web")}
    ).json()["project_id"]
    response = client.post(f"/api/projects/{project_id}/explore", json={"mode": "quick"})
    assert response.status_code == 409


def test_security_headers_and_spa_fallback(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "Content-Security-Policy" in response.headers


def test_spa_is_served_when_dist_exists(data_dir: Path, tmp_path: Path) -> None:
    from openlab.app import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>OPENLAB</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = create_app(_settings(data_dir, dist))
    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "OPENLAB" in root.text
        deep = client.get("/projects/abc")
        assert deep.status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/api/does-not-exist").status_code == 404
