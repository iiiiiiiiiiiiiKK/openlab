"""Port allocation and HTTP health checking against the real running service."""

from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class PortLease:
    port: int
    host: str = "127.0.0.1"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Return True only when nothing is listening and the port can be bound.

    ``SO_REUSEADDR`` is deliberately **not** set: on Windows it allows binding to a
    port another process is already listening on, which would make this check
    silently wrong and let OPENLAB report a port as free while a real service owns
    it.
    """
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0") else host
    # 1. Someone is already listening (connect probe works cross-platform).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.35)
        if probe.connect_ex((probe_host, port)) == 0:
            return False
    # 2. The port can actually be bound (catches TIME_WAIT and reserved ranges).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((probe_host, port))
        except OSError:
            return False
    return True


def allocate_port(start: int = 41000, end: int = 41999, host: str = "127.0.0.1") -> int:
    for port in range(start, end + 1):
        if is_port_free(port, host):
            return port
    raise RuntimeError(f"no free port in range {start}-{end}")


def allocate_ports(count: int, start: int = 41000, end: int = 41999) -> list[int]:
    ports: list[int] = []
    for port in range(start, end + 1):
        if len(ports) == count:
            break
        if is_port_free(port):
            ports.append(port)
    if len(ports) < count:
        raise RuntimeError(f"could not allocate {count} free ports in {start}-{end}")
    return ports


@dataclass
class HealthResult:
    ok: bool
    status_code: int | None = None
    url: str = ""
    elapsed_ms: int = 0
    attempts: int = 0
    body_excerpt: str = ""
    title: str = ""
    error: str | None = None
    history: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "url": self.url,
            "elapsed_ms": self.elapsed_ms,
            "attempts": self.attempts,
            "title": self.title,
            "error": self.error,
            "history": self.history,
        }


async def http_ready(
    url: str,
    timeout_s: float = 120,
    interval_s: float = 2.0,
    expected_status: tuple[int, ...] = (200, 201, 204, 301, 302, 304, 401, 403),
    on_attempt=None,
) -> HealthResult:
    """Poll ``url`` until it answers or the deadline passes."""
    started = time.monotonic()
    attempts = 0
    history: list[dict] = []
    last_error: str | None = None
    last_status: int | None = None
    body = ""
    while time.monotonic() - started < timeout_s:
        attempts += 1
        attempt_start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(url)
            last_status = resp.status_code
            body = resp.text[:4000]
            elapsed = int((time.monotonic() - attempt_start) * 1000)
            history.append({"attempt": attempts, "status": last_status, "elapsed_ms": elapsed})
            if on_attempt is not None:
                await on_attempt(attempts, last_status)
            if resp.status_code in expected_status:
                return HealthResult(
                    ok=True,
                    status_code=resp.status_code,
                    url=url,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    attempts=attempts,
                    body_excerpt=body[:1500],
                    title=_extract_title(body),
                    history=history,
                )
            last_error = f"unexpected HTTP status {resp.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            history.append({"attempt": attempts, "error": last_error[:200]})
            if on_attempt is not None:
                await on_attempt(attempts, None)
        await asyncio.sleep(interval_s)
    return HealthResult(
        ok=False,
        status_code=last_status,
        url=url,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        attempts=attempts,
        error=last_error or "service did not become ready before the timeout",
        history=history,
    )


def _extract_title(html: str) -> str:
    import re

    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:200]
