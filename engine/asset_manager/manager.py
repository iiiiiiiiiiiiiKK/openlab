"""Asset bookkeeping: files on disk plus their database rows.

Assets are always written to the project folder first, hashed, and only then
registered. The database stores a relative path so the whole OPENLAB_DATA folder
stays portable.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.core.paths import DataLayout, UnsafePath
from engine.core.security import redact

from openlab.models import Asset, Project

KIND_DIRS = {"screenshot": "screenshots", "video": "videos", "gif": "gifs"}

MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}


class AssetError(RuntimeError):
    """Raised when an asset cannot be registered or served."""


def safe_filename(value: str, fallback: str = "asset") -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    name = name.strip("-.")[:120]
    return name or fallback


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class AssetRecord:
    kind: str
    filename: str
    relative_path: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    label: str | None = None
    viewport: str | None = None
    aspect_ratio: str | None = None
    step_id: str | None = None
    source_url: str | None = None
    meta: dict | None = None


class AssetManager:
    def __init__(self, session: Session, layout: DataLayout) -> None:
        self.session = session
        self.layout = layout
        # Set when delete_asset() removed the row but could not unlink the file.
        self.last_delete_warning: str | None = None

    # -- registration ----------------------------------------------------
    def register_file(
        self,
        project_id: str,
        path: Path,
        kind: str,
        label: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
        viewport: str | None = None,
        aspect_ratio: str | None = None,
        step_id: str | None = None,
        source_url: str | None = None,
        meta: dict | None = None,
    ) -> Asset:
        path = Path(path).resolve()
        if not path.exists():
            raise AssetError(f"asset file does not exist: {path}")
        project_dir = self.layout.project_dir(project_id).resolve()
        if project_dir not in path.parents:
            raise UnsafePath(f"asset must live inside {project_dir}")
        if kind not in KIND_DIRS:
            raise AssetError(f"unsupported asset kind: {kind}")
        rel = path.relative_to(project_dir).as_posix()
        existing = self.session.scalar(
            select(Asset).where(Asset.project_id == project_id, Asset.relative_path == rel)
        )
        size = path.stat().st_size
        if existing:
            existing.size_bytes = size
            existing.width = width or existing.width
            existing.height = height or existing.height
            existing.duration_ms = duration_ms or existing.duration_ms
            existing.label = label or existing.label
            existing.meta = {**(existing.meta or {}), **(meta or {})}
            self.session.commit()
            return existing
        asset = Asset(
            project_id=project_id,
            kind=kind,
            label=label or path.stem,
            relative_path=rel,
            filename=path.name,
            mime_type=MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream"),
            width=width,
            height=height,
            duration_ms=duration_ms,
            size_bytes=size,
            viewport=viewport,
            aspect_ratio=aspect_ratio,
            step_id=step_id,
            source_url=source_url,
            meta=meta or {},
        )
        self.session.add(asset)
        self.session.commit()
        self._refresh_count(project_id)
        return asset

    def absolute_path(self, asset: Asset) -> Path:
        project_dir = self.layout.project_dir(asset.project_id).resolve()
        path = (project_dir / asset.relative_path).resolve()
        if project_dir not in path.parents and path != project_dir:
            raise UnsafePath("asset path escapes the project directory")
        return path

    def list_assets(
        self, project_id: str | None = None, kind: str | None = None, limit: int = 500
    ) -> list[Asset]:
        query = select(Asset).order_by(Asset.created_at.desc()).limit(limit)
        if project_id:
            query = query.where(Asset.project_id == project_id)
        if kind:
            query = query.where(Asset.kind == kind)
        return list(self.session.scalars(query))

    def delete_asset(self, asset_id: str, remove_file: bool = True) -> bool:
        asset = self.session.get(Asset, asset_id)
        if asset is None:
            return False
        if remove_file:
            try:
                self.absolute_path(asset).unlink(missing_ok=True)
            except (OSError, UnsafePath):
                # The row is still removed, but the caller and the UI are told the file
                # may have survived instead of claiming a clean delete.
                self.last_delete_warning = "the asset file could not be removed from disk"
        project_id = asset.project_id
        self.session.delete(asset)
        self.session.commit()
        self._refresh_count(project_id)
        return True

    def rename_asset(self, asset_id: str, new_label: str) -> Asset | None:
        asset = self.session.get(Asset, asset_id)
        if asset is None:
            return None
        asset.label = redact(new_label)[:200]
        self.session.commit()
        return asset

    def export_asset(self, asset_id: str, target_dir: Path) -> Path:
        asset = self.session.get(Asset, asset_id)
        if asset is None:
            raise AssetError("asset not found")
        source = self.absolute_path(asset)
        if not source.exists():
            raise AssetError(f"asset file missing on disk: {asset.relative_path}")
        target_dir = Path(target_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / safe_filename(asset.filename, "asset")
        shutil.copy2(source, destination)
        return destination

    def bulk_export(self, asset_ids: list[str], target_dir: Path) -> list[Path]:
        """Copy several assets into ``target_dir`` without silently overwriting.

        Two assets can legitimately share a filename (``preview-desktop.png`` exists in
        many projects), so a collision gets a numeric suffix instead of replacing the
        previous file.
        """
        written: list[Path] = []
        for asset_id in asset_ids:
            try:
                destination = self.export_asset(asset_id, target_dir)
            except (AssetError, OSError):
                continue
            counter = 1
            unique = destination
            while unique in written:
                unique = destination.with_name(
                    f"{destination.stem}-{counter}{destination.suffix}"
                )
                counter += 1
            if unique != destination:
                try:
                    shutil.copy2(destination, unique)
                except OSError:
                    continue
            written.append(unique)
        return written

    def storage_stats(self, project_id: str | None = None) -> dict:
        assets = self.list_assets(project_id=project_id, limit=10_000)
        by_kind: dict[str, dict] = {}
        for asset in assets:
            entry = by_kind.setdefault(asset.kind, {"count": 0, "bytes": 0})
            entry["count"] += 1
            entry["bytes"] += asset.size_bytes
        return {
            "total": len(assets),
            "bytes": sum(a.size_bytes for a in assets),
            "by_kind": by_kind,
        }

    def _refresh_count(self, project_id: str) -> None:
        count = len(list(self.session.scalars(select(Asset).where(Asset.project_id == project_id))))
        project = self.session.get(Project, project_id)
        if project is not None:
            project.asset_count = count
            self.session.commit()


def register_capture(
    manager: AssetManager,
    project_id: str,
    capture_result,
    label: str,
    step_id: str | None = None,
    source_url: str | None = None,
) -> Asset:
    """Convenience wrapper turning a CaptureResult into an Asset row."""
    return manager.register_file(
        project_id=project_id,
        path=capture_result.path,
        kind="screenshot" if capture_result.kind == "screenshot" else capture_result.kind,
        label=label,
        width=capture_result.width,
        height=capture_result.height,
        viewport=capture_result.viewport,
        aspect_ratio=capture_result.aspect_ratio,
        step_id=step_id,
        source_url=source_url,
        meta={"size_bytes": capture_result.size_bytes},
    )
