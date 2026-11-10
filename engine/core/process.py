"""Subprocess execution helpers used by every engine module.

Deterministic work (installs, builds, container commands, ffmpeg, git) is always
executed by the program itself -- never by the model. Every command is written to
a transcript file so a run can be audited or replayed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

LineSink = Callable[[str], Awaitable[None]] | None


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail(self, count: int = 40) -> str:
        joined = "\n".join(self.lines) if self.lines else (self.stdout + self.stderr)
        rows = joined.splitlines()[-count:]
        return "\n".join(rows)

    def as_dict(self) -> dict:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "tail": self.tail(60),
        }


def which(name: str) -> str | None:
    """Locate an executable, tolerating Windows extensions."""
    found = shutil.which(name)
    if found:
        return found
    for ext in (".exe", ".cmd", ".bat"):
        found = shutil.which(name + ext)
        if found:
            return found
    return None


def shell_argv(command: str) -> list[str]:
    """Wrap a shell-style command string for the current platform."""
    if os.name == "nt":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/d", "/s", "/c", command]
    return ["/bin/sh", "-lc", command]


async def run_command(
    argv: Sequence[str],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = 900,
    on_line: LineSink = None,
    transcript: Path | None = None,
    stdin_data: str | None = None,
) -> CommandResult:
    """Run a command, streaming each output line to ``on_line`` and a transcript."""
    argv = [str(a) for a in argv]
    merged_env = os.environ.copy()
    if env:
        merged_env.update({k: str(v) for k, v in env.items()})
    started = time.monotonic()
    lines: list[str] = []
    out_buf: list[str] = []
    err_buf: list[str] = []

    handle = None
    if transcript is not None:
        transcript.parent.mkdir(parents=True, exist_ok=True)
        handle = transcript.open("a", encoding="utf-8")
        handle.write(f"\n$ {' '.join(argv)}\n")
        handle.flush()

    async def pump(stream: asyncio.StreamReader, bucket: list[str], prefix: str) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            bucket.append(text)
            line = f"{prefix}{text}" if prefix else text
            lines.append(line)
            if handle is not None:
                handle.write(line + "\n")
                # Flushing per line kept the transcript exact but cost a syscall for
                # every line; the handle is closed in the finally block below.
            if on_line is not None:
                # Each sink call commits a TaskLog row. Doing that for every single
                # line of a long `docker compose build` blocked the event loop on the
                # SQLite write lock and froze the whole UI, so lines are coalesced.
                await _emit(line)

    pending: list[str] = []

    async def _emit(line: str) -> None:
        pending.append(line)
        if len(pending) >= 25:
            await _drain()

    async def _drain() -> None:
        if not pending or on_line is None:
            pending.clear()
            return
        batch = "\n".join(pending)
        pending.clear()
        await on_line(batch)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        if handle is not None:
            handle.write(f"! executable not found: {exc}\n")
            handle.close()
        return CommandResult(argv=argv, returncode=127, stderr=str(exc), lines=[str(exc)])

    timed_out = False
    try:
        if stdin_data is not None:
            assert proc.stdin is not None
            proc.stdin.write(stdin_data.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        await asyncio.wait_for(
            asyncio.gather(pump(proc.stdout, out_buf, ""), pump(proc.stderr, err_buf, "")),
            timeout=timeout,
        )
        await _drain()
        await asyncio.wait_for(proc.wait(), timeout=30)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        lines.append("! command timed out")
        with contextlib.suppress(Exception):
            await _drain()
    except asyncio.CancelledError:
        # A cancelled task must not leave its child running: `docker compose up`
        # would keep building and holding the port after the task showed cancelled.
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=15)
        lines.append("! command cancelled")
        raise
    finally:
        if handle is not None:
            handle.write(f"[exit {proc.returncode}]\n")
            handle.close()

    return CommandResult(
        argv=argv,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout="\n".join(out_buf),
        stderr="\n".join(err_buf),
        duration_ms=int((time.monotonic() - started) * 1000),
        timed_out=timed_out,
        lines=lines,
    )


async def run_shell(
    command: str,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = 900,
    on_line: LineSink = None,
    transcript: Path | None = None,
) -> CommandResult:
    return await run_command(
        shell_argv(command),
        cwd=cwd,
        env=env,
        timeout=timeout,
        on_line=on_line,
        transcript=transcript,
    )


def python_argv() -> list[str]:
    return [sys.executable]


async def probe(argv: Sequence[str], timeout: float = 20) -> CommandResult:
    """Best-effort version probe that never raises."""
    try:
        return await run_command(argv, timeout=timeout)
    except Exception as exc:  # pragma: no cover - defensive
        return CommandResult(argv=[str(a) for a in argv], returncode=-1, stderr=str(exc))
