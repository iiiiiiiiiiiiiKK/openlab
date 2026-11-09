"""Shared pytest fixtures for OPENLAB.

Tests run against a throw-away copy of the data directory so a developer's real
OPENLAB_DATA is never touched.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "backend")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """An isolated OPENLAB_DATA directory for one test."""
    target = tmp_path / "OPENLAB_DATA"
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture()
def layout(data_dir: Path):
    from engine.core.paths import DataLayout

    return DataLayout.build(data_dir)


@pytest.fixture()
def box(data_dir: Path):
    from engine.core.security import SecretBox

    return SecretBox(data_dir / "secrets")


@pytest.fixture()
def db(monkeypatch, data_dir: Path):
    """Initialise a fresh database bound to this test's data dir."""
    monkeypatch.setenv("OPENLAB_DATA_DIR", str(data_dir))
    from openlab import db as db_module

    engine = db_module.init_database(data_dir / "openlab.db")
    yield engine
    engine.dispose()


@pytest.fixture()
def tmp_browsers(repo_root: Path) -> Iterator[Path | None]:
    """Expose the workspace browser bundle when Playwright is available."""
    candidate = repo_root / ".browsers"
    if candidate.exists():
        previous = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)
        yield candidate
        if previous is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = previous
    else:
        yield None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_http(url: str, timeout: float = 30.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.25)
    return False


@pytest.fixture()
def playwright_ready(tmp_browsers) -> bool:
    from engine.browser_automation.launcher import browser_inventory

    return bool(browser_inventory().get("chromium_available"))


@pytest.fixture()
def node_available() -> bool:
    return shutil.which("node") is not None


@pytest.fixture()
def docker_available() -> bool:
    import asyncio

    from engine.docker_manager.docker_cli import docker_status

    status = asyncio.run(docker_status(timeout=10))
    return bool(status.available and status.daemon_running)
