"""Unit tests for archive extraction safety and path handling."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from engine.core.paths import DataLayout, UnsafePath
from engine.project_importer.archive import ArchiveRejected, extract_zip


def _write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


def test_extracts_and_strips_single_root(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "repo.zip",
        {"repo-main/package.json": b'{"name":"x"}', "repo-main/src/index.js": b"console.log(1)"},
    )
    target = tmp_path / "out"
    stats = extract_zip(archive, target)
    assert stats.files == 2
    assert (target / "package.json").exists()
    assert (target / "src" / "index.js").exists()
    assert not (target / "repo-main").exists()


def test_rejects_path_traversal(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "evil.zip", {"../../escape.txt": b"pwned"})
    with pytest.raises(ArchiveRejected):
        extract_zip(archive, tmp_path / "out")


def test_rejects_absolute_paths(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "abs.zip", {"/etc/passwd": b"x"})
    with pytest.raises(ArchiveRejected):
        extract_zip(archive, tmp_path / "out")


def test_rejects_too_many_files(tmp_path: Path) -> None:
    entries = {f"f{i}.txt": b"x" for i in range(20)}
    archive = _write_zip(tmp_path / "many.zip", entries)
    with pytest.raises(ArchiveRejected):
        extract_zip(archive, tmp_path / "out", max_files=10)


def test_rejects_oversized_unpacked_content(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "big.zip", {"big.bin": b"0" * (3 * 1024 * 1024)})
    with pytest.raises(ArchiveRejected):
        extract_zip(archive, tmp_path / "out", max_unpacked_mb=1)


def test_zip_of_bytes_helper(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("a.txt", "hello")
    path = tmp_path / "mem.zip"
    path.write_bytes(buffer.getvalue())
    stats = extract_zip(path, tmp_path / "out")
    assert stats.files == 1


def test_layout_rejects_unsafe_project_id(tmp_path: Path) -> None:
    layout = DataLayout.build(tmp_path)
    with pytest.raises(UnsafePath):
        layout.project_dir("../../etc")
    with pytest.raises(UnsafePath):
        layout.project_subdir("abc", "../escape")


def test_layout_creates_project_subdirs(tmp_path: Path) -> None:
    layout = DataLayout.build(tmp_path)
    dirs = layout.ensure_project("proj1")
    for name in ("source", "workspace", "logs", "screenshots", "videos", "gifs", "reports", "patches", "docker"):
        assert name in dirs
        assert dirs[name].is_dir()


def test_layout_metadata_roundtrip(tmp_path: Path) -> None:
    layout = DataLayout.build(tmp_path)
    layout.ensure_project("proj1")
    layout.write_metadata("proj1", {"project": {"name": "demo"}})
    assert layout.read_metadata("proj1")["project"]["name"] == "demo"
    assert layout.read_metadata("missing") == {}


def test_ensure_inside_blocks_escape(tmp_path: Path) -> None:
    layout = DataLayout.build(tmp_path)
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(UnsafePath):
        layout.ensure_inside(tmp_path / "other" / "file.txt", base)
    assert layout.ensure_inside(base / "ok.txt", base) == (base / "ok.txt").resolve()
