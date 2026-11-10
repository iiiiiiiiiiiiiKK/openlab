"""Explicitly opt-in host runtime (no isolation).

Docker is OPENLAB's default and only *sandboxed* execution path. When Docker is not
installed the user may enable ``allow_host_runtime`` to try a project directly on
the machine so the workbench stays useful. This is never silent:

* the setting defaults to ``False``;
* every project started this way is labelled ``host`` in the database and the UI;
* the deployment detail text says isolation is not available and lists the risk;
* no system configuration is modified and nothing runs as administrator.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from engine.core.process import run_shell, which
from engine.docker_manager.health import allocate_port, is_port_free


def _inject_port_flag(command: str, port: int) -> str:
    """Add an explicit port flag when the command does not already pin one.

    ``npm run start`` with a script like ``next start`` only honours ``PORT`` if the
    app reads it. Passing ``-- -p <port>`` forwards the flag through npm to the
    underlying binary, which is what Next, Vite, Astro and similar tools expect.
    """
    if "--port" in command or " -p " in command:
        return command
    lowered = command.lower()
    npm_like = lowered.startswith(("npm ", "pnpm ", "yarn ", "bun "))
    if not npm_like:
        return command
    if lowered.startswith("npm "):
        return f"{command} -- --port {port}"
    return f"{command} --port {port}"


def listening_ports(pid: int) -> list[int]:
    """Real listening TCP ports owned by ``pid`` (best effort, cross-platform).

    Used as a fallback when a framework ignores both ``PORT`` and ``--port``: rather
    than reporting a false failure, OPENLAB finds the port the process actually
    opened and health-checks that.
    """
    if pid <= 0:
        return []
    ports: set[int] = set()
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                timeout=25,
                errors="replace",
            )
            for line in (completed.stdout or "").splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[0].upper() != "TCP":
                    continue
                if parts[3].upper() != "LISTENING":
                    continue
                if parts[4] != str(pid):
                    continue
                address = parts[1]
                _, _, port_text = address.rpartition(":")
                if port_text.isdigit():
                    ports.add(int(port_text))
        else:
            completed = subprocess.run(
                ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=25,
                errors="replace",
            )
            if completed.returncode != 0:
                completed = subprocess.run(
                    ["ss", "-ltnp"],
                    capture_output=True,
                    text=True,
                    timeout=25,
                    errors="replace",
                )
            for line in (completed.stdout or "").splitlines():
                if str(pid) not in line and completed.args[0] == "ss":
                    continue
                for token in line.replace(",", " ").split():
                    candidate = token.rpartition(":")[2]
                    if candidate.isdigit() and 1 <= int(candidate) <= 65535:
                        ports.add(int(candidate))
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(p for p in ports if p > 0)


def find_service_port(pid: int, preferred: int | None = None) -> int | None:
    """Pick the port to health-check: the preferred one if it is listening."""
    open_ports = listening_ports(pid)
    if not open_ports:
        return None
    if preferred and preferred in open_ports:
        return preferred
    # Prefer ephemeral high ports (dev servers) over privileged ones.
    for port in open_ports:
        if port >= 1024:
            return port
    return open_ports[0]


def _pid_alive(pid: int) -> bool:
    """Cross-platform liveness probe for a process we did not spawn ourselves."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return str(pid) in (completed.stdout or "")
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class HostRuntimeError(RuntimeError):
    """Raised when a host process cannot be started or fails instantly."""


@dataclass
class HostProcess:
    project_id: str
    command: str
    cwd: Path
    port: int
    pid: int | None = None
    started_at: float = 0.0
    log_path: Path | None = None
    process: subprocess.Popen | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "command": self.command,
            "cwd": str(self.cwd),
            "port": self.port,
            "pid": self.pid,
            "url": self.url,
            "log_path": str(self.log_path) if self.log_path else None,
            "started_at": self.started_at,
        }


class HostRuntime:
    """Supervises one local process per project (opt-in, unsandboxed).

    State is mirrored to ``processes_dir/<project-id>.json`` so a different process
    (a new request, or the backend after a restart) can still stop a project that
    another instance started.
    """

    def __init__(self, processes_dir: Path) -> None:
        self.processes_dir = Path(processes_dir)
        self.processes_dir.mkdir(parents=True, exist_ok=True)
        self._running: dict[str, HostProcess] = {}

    def _state_path(self, project_id: str) -> Path:
        return self.processes_dir / f"{project_id}.json"

    def _write_state(self, proc: HostProcess) -> None:
        try:
            self._state_path(proc.project_id).write_text(
                json.dumps(proc.as_dict(), indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def get(self, project_id: str) -> HostProcess | None:
        proc = self._running.get(project_id)
        if proc and proc.process is not None and proc.process.poll() is not None:
            self._running.pop(project_id, None)
            self._state_path(project_id).unlink(missing_ok=True)
            return None
        if proc is None:
            state = self._state_path(project_id)
            if state.exists():
                try:
                    data = json.loads(state.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return None
                migrated = HostProcess(
                    project_id=project_id,
                    command=str(data.get("command") or ""),
                    cwd=Path(str(data.get("cwd") or ".")),
                    port=int(data.get("port") or 0),
                    pid=int(data["pid"]) if data.get("pid") else None,
                    started_at=float(data.get("started_at") or 0.0),
                    log_path=Path(str(data["log_path"])) if data.get("log_path") else None,
                )
                if migrated.pid and _pid_alive(migrated.pid):
                    self._running[project_id] = migrated
                    return migrated
        return proc

    async def prepare(self, workspace: Path, install_command: str | None, log_path: Path) -> dict:
        """Run the detected install command once (real npm/pip invocation)."""
        if not install_command:
            return {"ok": True, "skipped": True}
        log_path.parent.mkdir(parents=True, exist_ok=True)
        result = await run_shell(
            install_command,
            cwd=workspace,
            timeout=1800,
            transcript=log_path,
        )
        return {"ok": result.ok, "code": result.returncode, "tail": result.tail(60)}

    def start(
        self,
        project_id: str,
        workspace: Path,
        command: str,
        env: dict[str, str] | None = None,
        port: int | None = None,
        log_path: Path | None = None,
        port_range: tuple[int, int] = (41000, 41999),
    ) -> HostProcess:
        existing = self.get(project_id)
        if existing is not None:
            self.stop(project_id)
        # Always take a free port from the reserved range. Reusing the port the
        # planner guessed (often 3000) collides with whatever else the machine is
        # running, and the child would die with EADDRINUSE. A preferred port from the
        # planner is only honoured when it is inside the reserved range and free.
        low, high = port_range
        chosen = allocate_port(low, high)
        if port and low <= port <= high and is_port_free(port):
            chosen = port
        log_path = log_path or (self.processes_dir / f"{project_id}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        merged = os.environ.copy()
        merged.update(
            {
                "PORT": str(chosen),
                "HOST": "127.0.0.1",
                "BROWSER": "none",
                "CI": "1",
                "NODE_ENV": merged.get("NODE_ENV", "production"),
            }
        )
        if env:
            merged.update(env)

        log_handle = log_path.open("a", encoding="utf-8", errors="replace")
        log_handle.write(f"\n$ {command}\n")
        log_handle.flush()
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        shell_cmd = command.replace("--port 3000", f"--port {chosen}").replace(
            "--port 8000", f"--port {chosen}"
        )
        # Many Node/Next/Vite servers ignore PORT unless a flag is given, so make the
        # port explicit on the command line whenever the framework supports it.
        shell_cmd = _inject_port_flag(shell_cmd, chosen)
        if os.name == "nt":
            argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", shell_cmd]
        else:
            argv = ["/bin/sh", "-lc", shell_cmd]
        process = subprocess.Popen(  # noqa: S603 - command derived from the run plan
            argv,
            cwd=str(workspace),
            env=merged,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        proc = HostProcess(
            project_id=project_id,
            command=shell_cmd,
            cwd=Path(workspace),
            port=chosen,
            pid=process.pid,
            started_at=time.time(),
            log_path=log_path,
            process=process,
        )
        self._running[project_id] = proc
        self._write_state(proc)
        return proc

    def stop(self, project_id: str) -> bool:
        proc = self._running.pop(project_id, None)
        if proc is None:
            # A previous process (or the backend before a restart) may own it.
            proc = self.get(project_id)
            if proc is not None:
                self._running.pop(project_id, None)
        if proc is None:
            self._state_path(project_id).unlink(missing_ok=True)
            return False
        pid = proc.process.pid if proc.process is not None else proc.pid
        if pid is None:
            self._state_path(project_id).unlink(missing_ok=True)
            return False
        killed = False
        try:
            if os.name == "nt":
                completed = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=25,
                )
                killed = completed.returncode == 0
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                killed = True
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            killed = False
        if proc.process is not None:
            try:
                proc.process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                proc.process.kill()
        elif not _pid_alive(pid):
            killed = True
        self._state_path(project_id).unlink(missing_ok=True)
        return bool(killed or proc.process is not None)

    def status(self) -> list[dict]:
        alive: list[dict] = []
        for project_id in list(self._running):
            proc = self.get(project_id)
            if proc is not None:
                alive.append(proc.as_dict())
        return alive


def environment_warning() -> str:
    return (
        "Host runtime is enabled: this project runs directly on your machine WITHOUT container "
        "isolation. Third-party code can read your files and use your network. Only use this for "
        "repositories you trust, or install Docker for isolated execution."
    )


def runtime_available(command: str) -> tuple[bool, str | None]:
    """Check the binary a command needs actually exists."""
    first = (command or "").strip().split()
    if not first:
        return False, "empty start command"
    binary = first[0]
    if binary in ("npm", "npx", "node"):
        found = which(binary)
        return (bool(found), None if found else f"{binary} not found on PATH")
    if binary in ("python", "python3", "uvicorn", "flask"):
        return True, None
    found = which(binary)
    return (bool(found), None if found else f"{binary} not found on PATH")


def python_executable() -> str:
    return sys.executable
