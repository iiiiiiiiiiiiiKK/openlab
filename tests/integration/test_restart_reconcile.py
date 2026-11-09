"""Integration tests for restart reconciliation.

After a reboot the database may still claim a project is RUNNING while nothing is
listening on its port. The startup pass must correct that instead of showing a dead
service as live.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openlab.config import Settings


def _settings(data_dir: Path) -> Settings:
    settings = Settings(data_dir=data_dir)
    settings.data_dir = data_dir
    settings.web_dist = data_dir.parent / "no-web-dist"
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
        json={"path": str(fixtures_dir / "node-web"), "display_name": "Reconcile Fixture"},
    )
    assert response.status_code == 200, response.text
    return response.json()["project_id"]


def _mark_running(project_id: str, port: int) -> None:
    from openlab.db import session_scope
    from openlab.models import Project, ProjectContainer

    with session_scope() as session:
        project = session.get(Project, project_id)
        assert project is not None
        project.deploy_status = "RUNNING"
        project.deploy_status_detail = None
        session.add(
            ProjectContainer(
                project_id=project_id,
                service_name="app",
                status="running",
                host_port=port,
            )
        )


def test_stale_running_project_is_demoted_to_stopped(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _import(client, fixtures_dir)
    # Port 1 is never a listening HTTP service, so this is unambiguously dead.
    _mark_running(project_id, 1)

    from openlab.services.lifecycle import reconcile_runtime_state

    result = reconcile_runtime_state(client.app.state.settings.resolve_data())
    assert result["checked"] >= 1
    assert any(item["project_id"] == project_id for item in result["demoted"])

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["deploy_status"] == "STOPPED"
    assert "no longer listening" in (detail["deploy_status_detail"] or "")


def test_live_project_is_left_alone(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _import(client, fixtures_dir)

    # A real listening socket stands in for a running container port.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        _mark_running(project_id, port)
        from openlab.services.lifecycle import reconcile_runtime_state

        result = reconcile_runtime_state(client.app.state.settings.resolve_data())
        assert all(item["project_id"] != project_id for item in result["demoted"])
        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["deploy_status"] == "RUNNING"
    finally:
        server.close()


def test_reconcile_is_idempotent(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _import(client, fixtures_dir)
    _mark_running(project_id, 1)
    from openlab.services.lifecycle import reconcile_runtime_state

    layout = client.app.state.settings.resolve_data()
    first = reconcile_runtime_state(layout)
    second = reconcile_runtime_state(layout)
    assert any(item["project_id"] == project_id for item in first["demoted"])
    # Nothing is RUNNING any more, so the second pass has nothing to fix.
    assert second["demoted"] == []


def test_reconcile_ignores_projects_without_ports(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _import(client, fixtures_dir)
    from openlab.db import session_scope
    from openlab.models import Project

    with session_scope() as session:
        project = session.get(Project, project_id)
        assert project is not None
        project.deploy_status = "RUNNING"

    from openlab.services.lifecycle import reconcile_runtime_state

    result = reconcile_runtime_state(client.app.state.settings.resolve_data())
    # No recorded port means nothing can be verified, so it is treated as dead.
    assert any(item["project_id"] == project_id for item in result["demoted"])
