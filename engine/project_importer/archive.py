"""Safe archive extraction with traversal and bomb protection."""

from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from engine.core.paths import UnsafePath


class ArchiveRejected(ValueError):
    """Raised when an archive violates the import safety rules."""


@dataclass
class ExtractStats:
    files: int = 0
    bytes: int = 0
    top_level: str | None = None


BLOCKED_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".scr", ".msi", ".bat", ".cmd", ".pif"}


def _safe_member_path(name: str, target: Path) -> Path:
    if name.startswith("/") or name.startswith("\\") or ":" in name.split("/")[0]:
        raise ArchiveRejected(f"absolute path in archive: {name}")
    pure = PurePosixPath(name.replace("\\", "/"))
    if any(part == ".." for part in pure.parts):
        raise ArchiveRejected(f"path traversal in archive: {name}")
    dest = (target / pure).resolve()
    if target.resolve() != dest and target.resolve() not in dest.parents:
        raise ArchiveRejected(f"archive entry escapes target: {name}")
    return dest


def _check_quota(files: int, total: int, max_files: int, max_bytes: int) -> None:
    if files > max_files:
        raise ArchiveRejected(f"archive exceeds file limit ({files} > {max_files})")
    if total > max_bytes:
        raise ArchiveRejected(
            f"archive exceeds unpacked size limit ({total // (1024 * 1024)}MB > {max_bytes // (1024 * 1024)}MB)"
        )


def extract_zip(
    archive: Path,
    target: Path,
    max_files: int = 60000,
    max_unpacked_mb: int = 2048,
    strip_single_root: bool = True,
) -> ExtractStats:
    target.mkdir(parents=True, exist_ok=True)
    max_bytes = max_unpacked_mb * 1024 * 1024
    stats = ExtractStats()
    roots: set[str] = set()
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        _check_quota(len(infos), sum(i.file_size for i in infos), max_files, max_bytes)
        for info in infos:
            if info.is_dir():
                continue
            dest = _safe_member_path(info.filename, target)
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if parts:
                roots.add(parts[0])
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 256)
            stats.files += 1
            stats.bytes += info.file_size
    if strip_single_root and len(roots) == 1:
        only = target / next(iter(roots))
        if only.is_dir():
            stats.top_level = only.name
            _flatten(only, target)
    return stats


def extract_tar(
    archive: Path,
    target: Path,
    max_files: int = 60000,
    max_unpacked_mb: int = 2048,
) -> ExtractStats:
    target.mkdir(parents=True, exist_ok=True)
    max_bytes = max_unpacked_mb * 1024 * 1024
    stats = ExtractStats()
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        _check_quota(len(members), sum(m.size for m in members), max_files, max_bytes)
        for member in members:
            if member.issym() or member.islnk():
                raise ArchiveRejected(f"links are not allowed in archives: {member.name}")
            if not member.isfile():
                continue
            dest = _safe_member_path(member.name, target)
            dest.parent.mkdir(parents=True, exist_ok=True)
            handle = tf.extractfile(member)
            if handle is None:
                continue
            with handle, dest.open("wb") as out:
                shutil.copyfileobj(handle, out, length=1024 * 256)
            stats.files += 1
            stats.bytes += member.size
    return stats


def _flatten(root: Path, target: Path) -> None:
    """Move the single top-level directory's contents up one level."""
    for child in list(root.iterdir()):
        dest = target / child.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            else:
                dest.unlink()
        try:
            os.replace(child, dest)
        except OSError:
            shutil.move(str(child), str(dest))
    try:
        root.rmdir()
    except OSError:
        pass


def copy_local_tree(source: Path, target: Path, max_files: int = 60000, max_unpacked_mb: int = 2048) -> ExtractStats:
    """Copy a user-selected folder, skipping VCS metadata and dependency caches."""
    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise ArchiveRejected(f"not a directory: {source}")
    target = Path(target).resolve()
    if target == source or source in target.parents:
        raise UnsafePath("target folder must be outside the source folder")
    skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build", ".turbo"}
    stats = ExtractStats()
    max_bytes = max_unpacked_mb * 1024 * 1024
    for current, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            src = Path(current) / name
            if src.is_symlink():
                continue
            rel = src.relative_to(source)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dest)
            except OSError:
                continue
            stats.files += 1
            try:
                stats.bytes += dest.stat().st_size
            except OSError:
                pass
            _check_quota(stats.files, stats.bytes, max_files, max_bytes)
    return stats
