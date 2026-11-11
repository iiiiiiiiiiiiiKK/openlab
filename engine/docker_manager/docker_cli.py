"""Docker availability probing and host capability reporting.

Every probe is real: the version strings come from the actual CLI, and a missing
binary is reported as ``available: false`` rather than silently assumed.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from engine.core.process import probe, which

DOCKER_CANDIDATES = (
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    r"C:\Program Files\Docker\Docker\resources\bin\docker-compose.exe",
)


@dataclass
class DockerStatus:
    available: bool
    client_version: str | None = None
    server_version: str | None = None
    compose_version: str | None = None
    context: str | None = None
    daemon_running: bool = False
    error: str | None = None
    install_hint: str | None = None
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "client_version": self.client_version,
            "server_version": self.server_version,
            "compose_version": self.compose_version,
            "context": self.context,
            "daemon_running": self.daemon_running,
            "error": self.error,
            "install_hint": self.install_hint,
            "details": self.details,
        }


def find_docker_binary() -> str | None:
    found = which("docker")
    if found:
        return found
    for candidate in DOCKER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def find_compose_binary() -> str | None:
    found = which("docker-compose")
    return found


async def docker_status(timeout: float = 20) -> DockerStatus:
    binary = find_docker_binary()
    if not binary:
        return DockerStatus(
            available=False,
            error="docker CLI not found on PATH",
            install_hint=_install_hint(),
        )
    client = await probe([binary, "--version"], timeout=timeout)
    version_text = (client.stdout or client.stderr).strip()
    details = {"binary": binary, "client_probe_ok": client.ok}
    if not client.ok:
        return DockerStatus(
            available=False,
            error=version_text or "docker --version failed",
            install_hint=_install_hint(),
            details=details,
        )
    client_version = _version_after(version_text, "version")

    info = await probe([binary, "info", "--format", "{{.ServerVersion}}|{{.OperatingSystem}}|{{.Driver}}"], timeout=timeout)
    server_version = None
    daemon_running = False
    if info.ok and info.stdout.strip():
        parts = info.stdout.strip().split("|")
        server_version = parts[0] or None
        daemon_running = bool(server_version)
        details["os"] = parts[1] if len(parts) > 1 else None
        details["storage_driver"] = parts[2] if len(parts) > 2 else None
    else:
        details["daemon_error"] = (info.stderr or info.stdout).strip()[:500]

    ctx = await probe([binary, "context", "show"], timeout=timeout)
    compose_cmd = [binary, "compose", "version", "--short"]
    compose = await probe(compose_cmd, timeout=timeout)
    compose_version = compose.stdout.strip() if compose.ok else None
    if not compose_version:
        legacy = find_compose_binary()
        if legacy:
            legacy_probe = await probe([legacy, "--version"], timeout=timeout)
            if legacy_probe.ok:
                compose_version = legacy_probe.stdout.strip()

    return DockerStatus(
        available=True,
        client_version=client_version,
        server_version=server_version,
        compose_version=compose_version,
        context=ctx.stdout.strip() if ctx.ok else None,
        daemon_running=daemon_running,
        error=None if daemon_running else "Docker daemon is not running",
        install_hint=None if daemon_running else "Start Docker Desktop (or the docker service) and retry.",
        details=details,
    )


def _version_after(text: str, word: str) -> str | None:
    """Extract the version token that follows ``word``.

    ``Docker version 29.9.3, build 5457199`` leaves a trailing comma on the token, which
    then surfaced in the UI as "29.9.3,". Strip surrounding punctuation, not just a
    leading "v".
    """
    parts = text.split()
    candidate: str | None = None
    for index, part in enumerate(parts):
        if part.lower() == word and index + 1 < len(parts):
            candidate = parts[index + 1]
            break
    if candidate is None and parts:
        candidate = parts[-1]
    if not candidate:
        return None
    cleaned = candidate.strip().rstrip(",.;")
    return cleaned.lstrip("v") or None


def _install_hint() -> str:
    system = platform.system()
    if system == "Windows":
        return (
            "Install Docker Desktop for Windows with the WSL2 backend "
            "(https://docs.docker.com/desktop/install/windows-install/)."
        )
    if system == "Darwin":
        return (
            "Install Docker Desktop for macOS (Apple Silicon or Intel build) "
            "(https://docs.docker.com/desktop/install/mac-install/)."
        )
    return "Install Docker Engine (https://docs.docker.com/engine/install/)."


@dataclass
class HostReport:
    os: str
    os_version: str
    arch: str
    python: str
    node: str | None
    cpu_count: int
    memory_total_mb: int
    disk_free_mb: int
    docker: DockerStatus
    ffmpeg: str | None
    browsers: dict = field(default_factory=dict)
    wsl: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "os": self.os,
            "os_version": self.os_version,
            "arch": self.arch,
            "python": self.python,
            "node": self.node,
            "cpu_count": self.cpu_count,
            "memory_total_mb": self.memory_total_mb,
            "disk_free_mb": self.disk_free_mb,
            "docker": self.docker.as_dict(),
            "ffmpeg": self.ffmpeg,
            "browsers": self.browsers,
            "wsl": self.wsl,
            "warnings": self.warnings,
        }


async def host_report() -> HostReport:
    docker = await docker_status()
    warnings: list[str] = []
    if not docker.available:
        warnings.append("Docker is not installed: container deployment is unavailable.")
    elif not docker.daemon_running:
        warnings.append("Docker daemon is stopped: container deployment will fail until it starts.")
    node_path = which("node")
    node_version = None
    if node_path:
        node_probe = await probe([node_path, "--version"])
        node_version = node_probe.stdout.strip() or None
    from engine.media_processor.ffmpeg import MediaProcessor

    media = MediaProcessor()
    ffmpeg_path = media.binary
    from engine.browser_automation.launcher import browser_inventory

    browsers = browser_inventory()
    if not browsers.get("chromium_available"):
        warnings.append(
            "Playwright Chromium is not installed: run `python -m playwright install chromium`."
        )
    if not ffmpeg_path:
        warnings.append("FFmpeg not found: MP4/GIF export is unavailable until it is installed.")

    wsl = _wsl_info() if platform.system() == "Windows" else {}
    free_mb = _disk_free_mb()
    total_mb = _memory_total_mb()
    if free_mb < 5000:
        warnings.append("Less than 5GB free disk space: container layers may not fit.")

    return HostReport(
        os=platform.system(),
        os_version=platform.version(),
        arch=platform.machine(),
        python=platform.python_version(),
        node=node_version,
        cpu_count=os.cpu_count() or 1,
        memory_total_mb=total_mb,
        disk_free_mb=free_mb,
        docker=docker,
        ffmpeg=ffmpeg_path,
        browsers=browsers,
        wsl=wsl,
        warnings=warnings,
    )


def _disk_free_mb() -> int:
    target = Path(os.environ.get("OPENLAB_DATA_DIR", ".")).resolve()
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
        return int(usage.free / (1024 * 1024))
    except OSError:
        return 0


def _memory_total_mb() -> int:
    try:
        import psutil  # optional dependency

        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:
        pass
    if platform.system() == "Windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys / (1024 * 1024))
        except Exception:
            return 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return 0


def _wsl_info() -> dict:  # pragma: no cover - Windows only
    import subprocess

    result: dict = {"available": False}
    if not shutil.which("wsl"):
        return result
    try:
        proc = subprocess.run(
            ["wsl", "-l", "-v"],
            capture_output=True,
            timeout=15,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        result["available"] = proc.returncode == 0
        result["raw"] = (proc.stdout or proc.stderr).replace("\x00", "")[:1000]
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    return result
