"""Filesystem layout for OPENLAB runtime data.

All project artefacts live under ``OPENLAB_DATA/projects/<project-id>/`` so that
source code, AI-modified workspace, logs and generated media never mix.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_SUBDIRS = (
    "source",
    "workspace",
    "docker",
    "logs",
    "screenshots",
    "videos",
    "gifs",
    "reports",
    "patches",
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class UnsafePath(ValueError):
    """Raised when a caller tries to escape the managed data directory."""


def default_data_dir() -> Path:
    env = os.environ.get("OPENLAB_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "OPENLAB_DATA").resolve()


@dataclass(frozen=True)
class DataLayout:
    """Resolved, validated view of the data directory."""

    root: Path

    @classmethod
    def build(cls, root: str | Path | None = None) -> "DataLayout":
        base = Path(root) if root else default_data_dir()
        base = base.expanduser()
        base.mkdir(parents=True, exist_ok=True)
        return cls(root=base.resolve())

    # -- generic helpers ---------------------------------------------------
    @property
    def projects_dir(self) -> Path:
        p = self.root / "projects"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.root / "openlab.db"

    @property
    def browsers_dir(self) -> Path:
        p = self.root / "browsers"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def tmp_dir(self) -> Path:
        p = self.root / "tmp"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def secrets_dir(self) -> Path:
        p = self.root / "secrets"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def ensure_inside(self, path: Path, parent: Path) -> Path:
        path = path.expanduser().resolve()
        parent = parent.expanduser().resolve()
        if parent != path and parent not in path.parents:
            raise UnsafePath(f"{path} escapes {parent}")
        return path

    # -- project helpers ---------------------------------------------------
    def project_dir(self, project_id: str) -> Path:
        if not _SAFE_ID.match(project_id):
            raise UnsafePath(f"invalid project id: {project_id!r}")
        p = self.projects_dir / project_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def project_subdir(self, project_id: str, name: str) -> Path:
        if name not in PROJECT_SUBDIRS:
            raise UnsafePath(f"unknown project subdir: {name!r}")
        p = self.project_dir(project_id) / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def ensure_project(self, project_id: str) -> dict[str, Path]:
        return {name: self.project_subdir(project_id, name) for name in PROJECT_SUBDIRS}

    def metadata_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "metadata.json"

    def write_metadata(self, project_id: str, metadata: dict) -> Path:
        path = self.metadata_path(project_id)
        path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def read_metadata(self, project_id: str) -> dict:
        path = self.metadata_path(project_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def logs_dir(self, project_id: str) -> Path:
        return self.project_subdir(project_id, "logs")
