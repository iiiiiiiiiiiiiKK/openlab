"""Unit tests for project lifecycle helpers (preservation, restore, listing)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.core.paths import DataLayout
from openlab.services.lifecycle import (
    LifecycleError,
    delete_project_data,
    list_preserved,
    preserve_outputs,
    restore_snapshot,
)


@pytest.fixture()
def layout(tmp_path: Path) -> DataLayout:
    return DataLayout.build(tmp_path / "OPENLAB_DATA")


def _seed_project(layout: DataLayout, project_id: str = "proj1") -> Path:
    dirs = layout.ensure_project(project_id)
    (dirs["screenshots"] / "home.png").write_bytes(b"\x89PNG\r\n\x1a\npayload")
    (dirs["videos"] / "walk.webm").write_bytes(b"webm-payload")
    (dirs["gifs"] / "walk.gif").write_bytes(b"GIF89a-payload")
    (dirs["reports"] / "report-v1.md").write_text("# report", encoding="utf-8")
    (dirs["docker"] / "compose.openlab.yml").write_text("services: {}\n", encoding="utf-8")
    return dirs["source"]


def test_preserve_outputs_copies_the_requested_folders(layout: DataLayout) -> None:
    dirs = layout.ensure_project("proj1")
    (dirs["screenshots"] / "a.png").write_bytes(b"1")
    (dirs["videos"] / "b.webm").write_bytes(b"2")
    (dirs["reports"] / "c.md").write_text("3", encoding="utf-8")

    result = preserve_outputs(layout, "proj1", "Demo Project", keep_assets=True, keep_reports=False)
    assert result["files"] == 2  # screenshots + videos, no reports
    root = Path(result["path"])
    assert (root / "screenshots" / "a.png").exists()
    assert (root / "videos" / "b.webm").exists()
    assert not (root / "reports").exists()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["folders"] == ["screenshots", "videos"]
    assert manifest["kept_reports"] is False


def test_preserve_outputs_nothing_requested(layout: DataLayout) -> None:
    layout.ensure_project("proj1")
    result = preserve_outputs(layout, "proj1", "Demo", keep_assets=False, keep_reports=False)
    assert result["path"] is None
    assert result["files"] == 0
    assert not (layout.root / "preserved").exists()


def test_preserve_outputs_reports_only(layout: DataLayout) -> None:
    dirs = layout.ensure_project("proj1")
    (dirs["reports"] / "r.md").write_text("x", encoding="utf-8")
    (dirs["screenshots"] / "s.png").write_bytes(b"y")
    result = preserve_outputs(layout, "proj1", "Demo", keep_assets=False, keep_reports=True)
    root = Path(result["path"])
    assert (root / "reports" / "r.md").exists()
    assert not (root / "screenshots").exists()


def test_list_preserved_reads_manifests(layout: DataLayout) -> None:
    dirs = layout.ensure_project("proj1")
    (dirs["screenshots"] / "a.png").write_bytes(b"1")
    preserve_outputs(layout, "proj1", "Alpha", keep_assets=True, keep_reports=True)
    items = list_preserved(layout)
    assert len(items) == 1
    assert items[0]["project_name"] == "Alpha"


def test_list_preserved_empty(layout: DataLayout) -> None:
    assert list_preserved(layout) == []


def test_restore_snapshot_requires_existing_rows(db, layout: DataLayout) -> None:
    with pytest.raises(LifecycleError):
        restore_snapshot(layout, "missing", "missing")


def test_delete_project_data_signature_is_safe_for_unknown_id(db, layout: DataLayout) -> None:
    with pytest.raises(LifecycleError):
        delete_project_data(layout, "nope")
