"""Unit tests for compose/Dockerfile generation and Docker command helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.docker_manager.generator import (
    PolicyViolation,
    container_name_for,
    discover_existing_compose,
    generate_compose,
    generate_dockerfile,
    guard_compose_text,
    network_name_for,
    required_service_containers,
)
from engine.docker_manager.health import allocate_port, is_port_free
from engine.docker_manager.runner import DockerRunner, parse_port_mappings
from engine.project_analyzer.planner import RunPlan, ServiceSpec


def _plan(**overrides) -> RunPlan:
    base = dict(
        project_name="demo",
        project_type="node",
        language="TypeScript",
        framework="Next.js",
        package_manager="npm",
        runtime_version="20",
        strategy="generated",
        install_command="npm ci",
        build_command="npm run build",
        start_command="npm run start",
        port=3000,
        health_path="/",
        base_image="node:20-bookworm-slim",
    )
    base.update(overrides)
    return RunPlan(**base)


def test_generated_dockerfile_is_non_root() -> None:
    text = generate_dockerfile(_plan())
    assert "FROM node:20-bookworm-slim" in text
    assert "useradd" in text
    assert "USER openlab" in text
    assert "EXPOSE 3000" in text
    assert "HEALTHCHECK" in text
    assert "privileged" not in text.lower()


def test_generated_dockerfile_requires_start_command() -> None:
    with pytest.raises(PolicyViolation):
        generate_dockerfile(_plan(start_command=None))


def test_generated_compose_has_limits_and_no_host_mounts(tmp_path: Path) -> None:
    text = generate_compose(
        _plan(),
        tmp_path,
        image_tag="openlab/demo:latest",
        host_port=41500,
        cpu_limit=2.0,
        memory_limit_mb=4096,
    )
    assert 'cpus: "2.0"' in text
    assert "memory: 4096M" in text
    assert "pids_limit: 512" in text
    assert "no-new-privileges:true" in text
    assert "- ALL" in text
    assert "127.0.0.1:41500:3000" in text
    assert "docker.sock" not in text
    assert "privileged: true" not in text
    assert "context: .." in text  # build context is the project folder (workspace copy)


def test_compose_includes_extra_services(tmp_path: Path) -> None:
    plan = _plan(services=[ServiceSpec(name="postgres", port=5432), ServiceSpec(name="redis", port=6379)])
    extras = required_service_containers(plan)
    assert {item["name"] for item in extras} == {"postgres", "redis"}
    text = generate_compose(plan, tmp_path, image_tag="x:1", host_port=41001, extra_services=extras)
    assert "postgres:16-alpine" in text
    assert "redis:7-alpine" in text


def test_compose_renders_env_vars_as_compose_interpolations(tmp_path: Path) -> None:
    plan = _plan(
        env_vars=[
            {"name": "DATABASE_URL", "required": True, "description": "db"},
            {"name": "OPTIONAL_FLAG", "required": False},
        ]
    )
    text = generate_compose(plan, tmp_path, image_tag="x:1", host_port=41002)
    assert 'DATABASE_URL: "${DATABASE_URL:?OPENLAB requires DATABASE_URL}"' in text
    assert 'OPTIONAL_FLAG: "${OPTIONAL_FLAG:-}"' in text


def test_compose_rejects_invalid_env_variable_names(tmp_path: Path) -> None:
    plan = _plan(env_vars=[{"name": "DATABASE_URL:-oops", "required": True}])
    with pytest.raises(PolicyViolation, match="invalid environment variable name"):
        generate_compose(plan, tmp_path, image_tag="x:1", host_port=41002)


@pytest.mark.parametrize(
    "text,expected_problem",
    [
        ("services:\n  app:\n    privileged: true\n", "privileged"),
        ("services:\n  app:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n", "Docker socket"),
        ("services:\n  app:\n    network_mode: host\n", "network_mode"),
        ("services:\n  app:\n    pid: host\n", "PID/IPC"),
    ],
)
def test_guard_compose_detects_violations(text: str, expected_problem: str) -> None:
    problems = guard_compose_text(text)
    assert problems
    assert any(expected_problem in problem for problem in problems)


def test_guard_compose_accepts_clean_file(tmp_path: Path) -> None:
    text = """services:
  app:
    image: example:latest
    ports:
      - "127.0.0.1:41003:3000"
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
"""
    assert guard_compose_text(text) == []


def test_guard_compose_accepts_declared_named_volume_and_tmpfs() -> None:
    text = """services:
  app:
    image: example:latest
    volumes:
      - app-data:/data
      - type: tmpfs
        target: /tmp/cache
volumes:
  app-data: {}
"""
    assert guard_compose_text(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "services:\n  app:\n    volumes:\n      - /etc:/host-etc:ro\n",
        'services:\n  app:\n    volumes:\n      - "./host:/data"\n',
        "services:\n  app:\n    volumes:\n      - ../host:/data\n",
        'services:\n  app:\n    volumes:\n      - "C:\\\\Users:/host:ro"\n',
        'services:\n  app:\n    volumes:\n      - C:\\\\Users:/host:ro\n',
        "services:\n  app:\n    volumes:\n      - type: bind\n        source: /home\n        target: /host-home\n",
        'services:\n  app:\n    volumes:\n      - "${HOST_PATH}:/data"\n',
        "services:\n  app:\n    volumes:\n      - data:/data\nvolumes:\n  data:\n    driver_opts:\n      type: none\n      device: /etc\n      o: bind\n",
        "services:\n  app:\n    volumes:\n      - data:/data\nvolumes:\n  data:\n    external: true\n",
    ],
)
def test_guard_compose_rejects_bind_and_host_backed_mounts(text: str) -> None:
    problems = guard_compose_text(text)
    assert problems


def test_guard_compose_fails_closed_on_invalid_or_ambiguous_yaml() -> None:
    assert guard_compose_text("services: [not-a-mapping")
    assert guard_compose_text("services: {}\n---\nservices: {}")
    assert guard_compose_text("services:\n  app: {}\n  app: {privileged: true}")


def test_guard_compose_checks_every_service_and_escalation_flags() -> None:
    text = """services:
  app:
    image: example:latest
  helper:
    image: example:latest
    privileged: "true"
    pid: host
"""
    problems = guard_compose_text(text)
    assert any("privileged" in problem for problem in problems)
    assert any("PID/IPC" in problem for problem in problems)


def test_guard_compose_rejects_compose_file_includes() -> None:
    text = "include:\n  - ../other/compose.yml\nservices:\n  app: {}\n"
    assert any("include/extends" in problem for problem in guard_compose_text(text))


def test_compose_build_contexts_are_confined_and_rebased(tmp_path: Path) -> None:
    import yaml

    from engine.docker_manager.generator import normalize_compose_build_contexts

    workspace = tmp_path / "workspace"
    project = workspace / "web"
    project.mkdir(parents=True)
    (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    raw = "services:\n  web:\n    build: .\n"
    output_compose = workspace / "docker"
    output_compose.mkdir()
    normalized = normalize_compose_build_contexts(raw, project, workspace, output_compose_dir=output_compose)
    parsed = yaml.safe_load(normalized)
    assert parsed["services"]["web"]["build"]["context"] == "../web"
    with pytest.raises(PolicyViolation, match="escapes"):
        normalize_compose_build_contexts("services:\n  web:\n    build: ../../\n", project, workspace)


def test_container_and_network_names() -> None:
    assert container_name_for("My Project") == "openlab-my-project"
    assert container_name_for("demo", "db") == "openlab-demo-db"
    assert network_name_for("My Project!") == "openlab-net-my-project"


def test_discover_existing_compose(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}", encoding="utf-8")
    found = discover_existing_compose(tmp_path)
    assert found is not None and found.name == "compose.yaml"
    assert discover_existing_compose(tmp_path / "nope") is None


def test_parse_port_mappings() -> None:
    mappings = parse_port_mappings("127.0.0.1:41000->3000/tcp, 0.0.0.0:5432->5432/tcp")
    assert len(mappings) == 2
    assert mappings[0]["host_port"] == 41000
    assert mappings[0]["container_port"] == 3000
    assert mappings[1]["host_ip"] == "0.0.0.0"


def test_port_allocation_and_free_check() -> None:
    port = allocate_port(41200, 41250)
    assert 41200 <= port <= 41250
    assert is_port_free(port)


def test_crop_to_ratio() -> None:
    from engine.browser_automation.capture import ImageSize, crop_to_ratio

    size = crop_to_ratio(ImageSize(1600, 900), (16, 9))
    assert size.width == 1600
    assert size.height == 900
    square = crop_to_ratio(ImageSize(1600, 900), (1, 1))
    assert square.width == square.height == 900
    tall = crop_to_ratio(ImageSize(400, 800), (3, 4))
    assert tall.width == 400 and tall.height == 533


def test_docker_runner_reports_unavailable_without_binary() -> None:
    runner = DockerRunner(binary=None)
    runner.binary = None
    assert runner.available is False
    with pytest.raises(Exception):
        import asyncio

        asyncio.run(runner.version())
