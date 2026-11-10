"""Thin, real wrapper around the Docker CLI.

All container operations go through ``docker`` / ``docker compose``. When Docker is
absent, callers receive :class:`DockerUnavailable` instead of a fake success.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from engine.core.process import CommandResult, run_command
from engine.docker_manager.docker_cli import find_docker_binary


class DockerUnavailable(RuntimeError):
    """Docker CLI or daemon is not usable on this host."""


class DockerCommandError(RuntimeError):
    def __init__(self, message: str, result: CommandResult) -> None:
        super().__init__(message)
        self.result = result


@dataclass
class ContainerInfo:
    id: str
    name: str
    service: str | None
    image: str
    status: str
    state: str
    ports: str
    health: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class DockerRunner:
    binary: str | None = None
    timeout: float = 1800

    def __post_init__(self) -> None:
        self.binary = self.binary or find_docker_binary()

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def _require(self) -> str:
        if not self.binary:
            raise DockerUnavailable(
                "docker CLI not found; install Docker Desktop and make sure `docker` is on PATH"
            )
        return self.binary

    async def version(self) -> CommandResult:
        return await run_command([self._require(), "--version"], timeout=30)

    async def info(self) -> CommandResult:
        return await run_command([self._require(), "info", "--format", "{{json .}}"], timeout=60)

    async def daemon_ok(self) -> bool:
        if not self.available:
            return False
        result = await run_command([self._require(), "info", "--format", "{{.ServerVersion}}"], timeout=30)
        return result.ok and bool(result.stdout.strip())

    async def compose_up(
        self,
        compose_path: Path,
        project_name: str,
        env: dict[str, str] | None = None,
        on_line=None,
        transcript: Path | None = None,
        build: bool = True,
    ) -> CommandResult:
        args = [
            self._require(),
            "compose",
            "-f",
            str(compose_path),
            "-p",
            _compose_project(project_name),
            "up",
            "-d",
        ]
        if build:
            args.append("--build")
        args.append("--remove-orphans")
        return await run_command(
            args,
            cwd=Path(compose_path).parent,
            env=env,
            timeout=self.timeout,
            on_line=on_line,
            transcript=transcript,
        )

    async def compose_down(
        self,
        compose_path: Path,
        project_name: str,
        on_line=None,
        remove_volumes: bool = True,
    ) -> CommandResult:
        args = [
            self._require(),
            "compose",
            "-f",
            str(compose_path),
            "-p",
            _compose_project(project_name),
            "down",
            "--remove-orphans",
        ]
        if remove_volumes:
            args.append("-v")
        return await run_command(args, timeout=300, on_line=on_line)

    async def compose_ps(self, compose_path: Path, project_name: str) -> list[ContainerInfo]:
        args = [
            self._require(),
            "compose",
            "-f",
            str(compose_path),
            "-p",
            _compose_project(project_name),
            "ps",
            "--format",
            "json",
        ]
        result = await run_command(args, timeout=120)
        if not result.ok:
            return []
        entries: list[ContainerInfo] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(
                ContainerInfo(
                    id=data.get("ID") or data.get("Id") or "",
                    name=data.get("Name") or "",
                    service=data.get("Service") or None,
                    image=data.get("Image") or "",
                    status=data.get("Status") or "",
                    state=data.get("State") or "",
                    ports=data.get("Ports") or "",
                    health=data.get("Health") or None,
                    raw=data,
                )
            )
        return entries

    async def compose_logs(
        self, compose_path: Path, project_name: str, tail: int = 400
    ) -> CommandResult:
        args = [
            self._require(),
            "compose",
            "-f",
            str(compose_path),
            "-p",
            _compose_project(project_name),
            "logs",
            "--no-color",
            "--tail",
            str(tail),
        ]
        return await run_command(args, timeout=120)

    async def exec_in(
        self, container: str, command: str, on_line=None, timeout: float = 300
    ) -> CommandResult:
        args = [self._require(), "exec", "-i", container, "/bin/sh", "-lc", command]
        return await run_command(args, timeout=timeout, on_line=on_line)

    async def remove_container(self, container_id: str, force: bool = True) -> CommandResult:
        args = [self._require(), "rm"]
        if force:
            args.append("-f")
        args.append(container_id)
        return await run_command(args, timeout=120)

    async def remove_image(self, image: str) -> CommandResult:
        return await run_command([self._require(), "rmi", "-f", image], timeout=180)

    async def image_exists(self, image: str) -> bool:
        result = await run_command([self._require(), "image", "inspect", image], timeout=60)
        return result.ok

    async def network_remove(self, network: str) -> CommandResult:
        return await run_command([self._require(), "network", "rm", network], timeout=60)

    async def container_ip(self, container: str) -> str | None:
        result = await run_command(
            [
                self._require(),
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
                container,
            ],
            timeout=60,
        )
        if not result.ok:
            return None
        return (result.stdout.strip().split() or [None])[0]

    async def container_stats(self, container: str) -> dict:
        result = await run_command(
            [self._require(), "stats", "--no-stream", "--format", "{{json .}}", container], timeout=60
        )
        if not result.ok:
            return {}
        try:
            return json.loads(result.stdout.strip() or "{}")
        except json.JSONDecodeError:
            return {}

    async def prune_project(self, project_name: str) -> None:
        prefix = f"openlab-{_compose_project(project_name)}"
        listing = await run_command(
            [
                self._require(),
                "ps",
                "-a",
                "--filter",
                f"name={prefix}",
                "--format",
                "{{.ID}}",
            ],
            timeout=60,
        )
        for cid in (listing.stdout or "").split():
            await self.remove_container(cid)


def _compose_project(project_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", project_name.lower())[:24]
    return slug or "openlab"


PORT_MAP_RE = re.compile(r"(?:(?P<host>[0-9.]+|\[::\]):)?(?P<hport>\d+)->(?P<cport>\d+)/(?P<proto>tcp|udp)")


def parse_port_mappings(ports_field: str) -> list[dict]:
    """Parse compose/docker ``Ports`` strings like ``127.0.0.1:41000->3000/tcp``."""
    out: list[dict] = []
    for match in PORT_MAP_RE.finditer(ports_field or ""):
        out.append(
            {
                "host_ip": match.group("host"),
                "host_port": int(match.group("hport")),
                "container_port": int(match.group("cport")),
                "protocol": match.group("proto"),
            }
        )
    return out
