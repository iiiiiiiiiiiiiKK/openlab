"""Unit tests for the reserved-port probe and the opt-in host runtime."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path

import pytest

from engine.docker_manager.health import allocate_port, allocate_ports, is_port_free
from engine.runtime.host import (
    HostRuntime,
    _inject_port_flag,
    environment_warning,
    runtime_available,
)


def test_python_bind_uses_both_env_and_flag() -> None:
    """The fixture reads PORT; npm-style commands get an explicit --port flag."""
    assert _inject_port_flag("npm run start", 41234).endswith("-- --port 41234")
    assert _inject_port_flag("pnpm start", 41234).endswith("--port 41234")
    assert _inject_port_flag("next start -p 3000", 41234) == "next start -p 3000"
    assert _inject_port_flag("python app.py", 41234) == "python app.py"
    # An explicit flag must never be duplicated.
    assert _inject_port_flag("npm start -- --port 5000", 41234).count("--port") == 1


def test_is_port_free_detects_a_listener() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    busy = server.getsockname()[1]
    try:
        assert is_port_free(busy) is False
    finally:
        server.close()
    time.sleep(0.25)
    assert is_port_free(busy) is True


def test_allocate_port_returns_a_free_port() -> None:
    port = allocate_port(41600, 41620)
    assert 41600 <= port <= 41620
    assert is_port_free(port)


def test_allocate_ports_is_unique() -> None:
    ports = allocate_ports(3, 41630, 41650)
    assert len(ports) == len(set(ports)) == 3
    assert all(is_port_free(port) for port in ports)


def test_allocate_ports_fails_when_range_too_small() -> None:
    with pytest.raises(RuntimeError):
        allocate_ports(5, 41660, 41660)


def test_allocate_port_skips_a_busy_port() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    busy = server.getsockname()[1]
    try:
        # Start the range exactly at the busy port: the allocator must move on.
        chosen = allocate_port(busy, busy + 5)
        assert chosen != busy
    finally:
        server.close()


def test_environment_warning_is_explicit() -> None:
    text = environment_warning()
    assert "WITHOUT container isolation" in text
    assert "trust" in text.lower()


def test_runtime_available_rejects_empty_command() -> None:
    ok, reason = runtime_available("")
    assert ok is False
    assert reason


def test_runtime_available_accepts_python() -> None:
    ok, reason = runtime_available("python -m http.server 8000")
    assert ok is True
    assert reason is None


@pytest.mark.skipif(
    __import__("shutil").which("node") is None, reason="node is not installed on this host"
)
def test_host_runtime_starts_and_stops_a_real_process(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "server.js").write_text(
        "const http=require('http');const p=Number(process.env.PORT||0);"
        "http.createServer((q,r)=>{r.end('ok')}).listen(p,'127.0.0.1');",
        encoding="utf-8",
    )
    runtime = HostRuntime(tmp_path / "procs")
    proc = runtime.start("proj-host", workspace, "node server.js", log_path=tmp_path / "out.log")
    try:
        assert proc.pid
        assert (tmp_path / "procs" / "proj-host.json").exists()
        assert runtime.get("proj-host") is not None
        import urllib.request

        from engine.runtime.host import find_service_port

        # The framework must honour PORT; verify the port it really opened.
        deadline = time.time() + 25
        served = False
        while time.time() < deadline:
            port = find_service_port(proc.pid, preferred=proc.port) or proc.port
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    served = response.read() == b"ok"
                    break
            except Exception:  # noqa: BLE001 - not listening yet
                time.sleep(0.4)
        assert served, "the host process never answered"
    finally:
        assert runtime.stop("proj-host") is True
    assert not (tmp_path / "procs" / "proj-host.json").exists()


def test_host_runtime_recovers_state_from_disk(tmp_path: Path) -> None:
    procs = tmp_path / "procs"
    procs.mkdir()
    (procs / "ghost.json").write_text(
        json.dumps({"project_id": "ghost", "command": "true", "cwd": str(tmp_path), "port": 41999, "pid": 0}),
        encoding="utf-8",
    )
    runtime = HostRuntime(procs)
    # A pid of 0 is never alive, so the stale state must be ignored and stop() a no-op.
    assert runtime.get("ghost") is None
    assert runtime.stop("ghost") is False


def test_host_runtime_prepare_skips_when_no_command(tmp_path: Path) -> None:
    runtime = HostRuntime(tmp_path / "procs")
    result = asyncio.run(runtime.prepare(tmp_path, None, tmp_path / "install.log"))
    assert result["ok"] is True
    assert result.get("skipped") is True
