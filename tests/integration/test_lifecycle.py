"""Integration tests for project lifecycle: deletion with preservation and snapshot restore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openlab.config import Settings


def _settings(data_dir: Path) -> Settings:
    settings = Settings(data_dir=data_dir)
    settings.data_dir = data_dir
    settings.web_dist = data_dir.parent / "no-web-dist"
    settings.max_parallel_deployments = 1
    return settings


@pytest.fixture()
def client(data_dir: Path):
    from openlab.app import create_app

    app = create_app(_settings(data_dir))
    with TestClient(app) as test_client:
        yield test_client


def _import(client: TestClient, fixtures_dir: Path) -> str:
    response = client.post(
        "/api/projects/import/local",
        json={"path": str(fixtures_dir / "node-web"), "display_name": "Lifecycle Fixture"},
    )
    assert response.status_code == 200, response.text
    return response.json()["project_id"]


def test_delete_preserves_assets_and_reports(client: TestClient, fixtures_dir: Path, data_dir: Path) -> None:
    project_id = _import(client, fixtures_dir)
    project_dir = data_dir / "projects" / project_id

    # Put real files in the folders the user asked to keep.
    screenshots = project_dir / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    (screenshots / "home.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-but-real-file")
    reports = project_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "report-v1.md").write_text("# kept report", encoding="utf-8")

    response = client.delete(f"/api/projects/{project_id}?keep_assets=true&keep_reports=true")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] is True
    assert body["kept_assets"] is True and body["kept_reports"] is True
    assert body["preserved_files"] == 2
    preserved = Path(body["preserved_path"])
    assert preserved.is_dir()
    assert (preserved / "screenshots" / "home.png").exists()
    assert (preserved / "reports" / "report-v1.md").read_text(encoding="utf-8") == "# kept report"
    manifest = json.loads((preserved / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_id"] == project_id
    assert manifest["files"] == 2

    # The project itself is gone.
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert not project_dir.exists()


def test_delete_without_preservation_leaves_nothing(client: TestClient, fixtures_dir: Path, data_dir: Path) -> None:
    project_id = _import(client, fixtures_dir)
    project_dir = data_dir / "projects" / project_id
    (project_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    (project_dir / "screenshots" / "x.png").write_bytes(b"data")

    body = client.delete(f"/api/projects/{project_id}?keep_assets=false&keep_reports=false").json()
    assert body["preserved_path"] is None
    assert body["preserved_files"] == 0
    assert not (data_dir / "preserved").exists()
    assert not project_dir.exists()


def test_delete_unknown_project_returns_404(client: TestClient) -> None:
    response = client.delete("/api/projects/does-not-exist")
    assert response.status_code == 404


def test_snapshot_restore_writes_compose_and_environment(
    client: TestClient, fixtures_dir: Path, data_dir: Path
) -> None:
    project_id = _import(client, fixtures_dir)

    created = client.post(f"/api/projects/{project_id}/snapshots", json={"label": "before tune"})
    assert created.status_code == 200, created.text
    snapshot_id = created.json()["id"]

    listed = client.get(f"/api/projects/{project_id}/snapshots").json()["items"]
    assert listed and listed[0]["id"] == snapshot_id

    # Simulate drift: remove the generated compose file, then restore the snapshot.
    compose = data_dir / "projects" / project_id / "docker" / "compose.openlab.yml"
    client.post(f"/api/projects/{project_id}/analyze")
    if compose.exists():
        compose.unlink()

    restored = client.post(f"/api/projects/{project_id}/snapshots/{snapshot_id}/restore")
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["restored"] is True
    assert body["snapshot_id"] == snapshot_id
    assert "deploy" in body["next_step"].lower()

    detail = client.get(f"/api/projects/{project_id}").json()
    assert "restored from snapshot" in (detail["deploy_status_detail"] or "")


def test_restore_unknown_snapshot_returns_404(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _import(client, fixtures_dir)
    response = client.post(f"/api/projects/{project_id}/snapshots/missing/restore")
    assert response.status_code == 404


def test_restore_rejects_snapshot_of_another_project(client: TestClient, fixtures_dir: Path) -> None:
    first = _import(client, fixtures_dir)
    second = _import(client, fixtures_dir)
    snapshot_id = client.post(f"/api/projects/{first}/snapshots", json={"label": "one"}).json()["id"]
    response = client.post(f"/api/projects/{second}/snapshots/{snapshot_id}/restore")
    assert response.status_code == 404
