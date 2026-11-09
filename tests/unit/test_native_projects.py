"""Unit tests for compiled/desktop project detection.

OPENLAB verifies projects by running them behind HTTP and driving a browser. Windows
shell extensions, desktop applications and firmware cannot be verified that way, so
they must be reported as non-runnable instead of being planned as a web app.
"""

from __future__ import annotations

from pathlib import Path

from engine.project_analyzer.analyzer import analyze_project
from engine.project_analyzer.detect import scan_tree
from engine.project_analyzer.planner import build_plan


def _cpp_desktop_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "App.sln").write_text("Microsoft Visual Studio Solution File", encoding="utf-8")
    (root / "App.vcxproj").write_text("<Project></Project>", encoding="utf-8")
    (root / "README.md").write_text(
        "# Blur tool\n\nA Windows DWM shell extension distributed as a DLL.\n",
        encoding="utf-8",
    )
    src = root / "src"
    src.mkdir()
    (src / "main.cpp").write_text('#include <windows.h>\nint main(){return 0;}\n', encoding="utf-8")
    (src / "hook.hpp").write_text("void hook();", encoding="utf-8")
    return root


def test_scan_flags_a_cpp_solution(tmp_path: Path) -> None:
    root = _cpp_desktop_project(tmp_path / "desktop")
    scan = scan_tree(root)
    native = scan.native_project
    assert native is not None
    assert native["kind"] == "windows-desktop"
    assert any("Visual Studio" in item for item in native["indicators"])
    assert native["primary_language"] in {"C++", "C/C++ header"}


def test_plan_marks_native_project_as_non_runnable(tmp_path: Path) -> None:
    root = _cpp_desktop_project(tmp_path / "desktop")
    plan = build_plan("desktop", root)
    assert plan.project_type == "native"
    assert plan.start_command is None
    assert plan.confidence == 0.0
    joined = " ".join(plan.blocking_requirements)
    assert "compiled/desktop project" in joined
    assert "will not claim a browser experience" in joined
    assert any("do not apply" in warning for warning in plan.warnings)


def test_analysis_bundle_reports_native_honestly(tmp_path: Path) -> None:
    root = _cpp_desktop_project(tmp_path / "desktop")
    analysis = analyze_project(root, project_name="desktop")
    payload = analysis.as_dict()
    assert payload["plan"]["project_type"] == "native"
    assert payload["tree"]["languages"][0]["language"] in {"C++", "C/C++ header"}


def test_c_header_heavy_repo_is_still_native(tmp_path: Path) -> None:
    root = tmp_path / "headers-only"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(x CXX)", encoding="utf-8")
    for index in range(5):
        (root / f"m{index}.hpp").write_text("#pragma once\n", encoding="utf-8")
    scan = scan_tree(root)
    assert scan.native_project is not None
    assert scan.native_project["kind"] == "native"


def test_web_project_is_not_mistaken_for_native(tmp_path: Path) -> None:
    root = tmp_path / "web"
    root.mkdir()
    (root / "package.json").write_text('{"name":"web","scripts":{"start":"node server.js"}}', encoding="utf-8")
    (root / "server.js").write_text("console.log('hi')", encoding="utf-8")
    scan = scan_tree(root)
    assert scan.native_project is None
    plan = build_plan("web", root)
    assert plan.project_type == "node"
    assert plan.start_command == "npm run start"


def test_python_project_is_not_mistaken_for_native(tmp_path: Path) -> None:
    root = tmp_path / "api"
    root.mkdir()
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (root / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    assert scan_tree(root).native_project is None
    assert build_plan("api", root).project_type == "python"


def test_go_and_rust_are_not_native(tmp_path: Path) -> None:
    go_root = tmp_path / "goapp"
    go_root.mkdir()
    (go_root / "go.mod").write_text("module demo\n", encoding="utf-8")
    (go_root / "main.go").write_text("package main\nfunc main(){}", encoding="utf-8")
    assert scan_tree(go_root).native_project is None

    rust_root = tmp_path / "rustapp"
    rust_root.mkdir()
    (rust_root / "Cargo.toml").write_text('[package]\nname="demo"\n', encoding="utf-8")
    (rust_root / "src").mkdir()
    (rust_root / "src" / "main.rs").write_text("fn main(){}", encoding="utf-8")
    assert scan_tree(rust_root).native_project is None


def test_cpp_web_server_without_readme_hint_is_still_flagged(tmp_path: Path) -> None:
    """A CMake project is not a web service even when the README says nothing."""
    root = tmp_path / "cmake"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
    (root / "main.cpp").write_text("int main(){}", encoding="utf-8")
    scan = scan_tree(root)
    assert scan.native_project is not None
    assert scan.native_project["kind"] == "native"
