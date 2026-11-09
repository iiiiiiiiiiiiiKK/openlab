"""Unit tests for deterministic project analysis and run-plan building."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.project_analyzer.analyzer import analyze_project
from engine.project_analyzer.detect import find_ports, parse_env_example, scan_tree
from engine.project_analyzer.planner import build_plan


def _node_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "scripts": {"dev": "next dev", "build": "next build", "start": "next start -p 3000"},
                "dependencies": {"next": "14.0.0", "react": "18.2.0", "pg": "^8.11.0"},
                "engines": {"node": ">=18"},
            }
        ),
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text("{}", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n\nRun `npm start`. Visit http://localhost:3000\n", encoding="utf-8")
    (root / ".env.example").write_text(
        "# Postgres connection string\nDATABASE_URL=postgres://localhost/demo\n\n# Optional OpenAI key\nOPENAI_API_KEY=\n",
        encoding="utf-8",
    )
    (root / "pages").mkdir(exist_ok=True)
    (root / "pages" / "index.tsx").write_text("export default function Home(){return null}\n", encoding="utf-8")
    return root


def test_scan_tree_collects_manifests(tmp_path: Path) -> None:
    root = _node_project(tmp_path / "demo")
    scan = scan_tree(root)
    assert scan.total_files >= 5
    assert "package.json" in scan.manifests
    assert scan.facts.package_json["name"] == "demo"
    assert scan.primary_language in {"TypeScript", "JavaScript", None}
    assert scan.as_dict()["manifests"]["package.json"] == "package.json"


def test_scan_tree_ignores_node_modules(tmp_path: Path) -> None:
    root = _node_project(tmp_path / "demo")
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "index.js").write_text("module.exports=1", encoding="utf-8")
    scan = scan_tree(root)
    assert not any("node_modules" in p for p in scan.as_dict()["manifests"].values())
    assert all("node_modules" not in str(path) for path in scan.manifests.values())


def test_plan_detects_node_next_stack(tmp_path: Path) -> None:
    root = _node_project(tmp_path / "demo")
    plan = build_plan("demo", root)
    assert plan.project_type == "node"
    assert plan.framework == "Next.js"
    assert plan.package_manager == "npm"
    assert plan.install_command == "npm ci"
    assert plan.build_command == "npm run build"
    assert plan.start_command == "npm run start"
    assert plan.port == 3000
    assert plan.strategy == "generated"
    assert plan.confidence > 0.6
    assert plan.as_dict()["evidence"]["package_manager"]["source"] == "package-lock.json"


def test_plan_detects_compose_strategy(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    (root / "docker-compose.yml").write_text(
        "services:\n  web:\n    build: .\n    ports:\n      - \"8080:3000\"\n  db:\n    image: postgres:16\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(json.dumps({"scripts": {"start": "node ."}}), encoding="utf-8")
    plan = build_plan("stack", root)
    assert plan.strategy == "compose"
    assert plan.port == 8080
    assert any(service.name == "postgres" for service in plan.services)


def test_plan_detects_python_fastapi(tmp_path: Path) -> None:
    root = tmp_path / "api"
    root.mkdir()
    (root / "requirements.txt").write_text("fastapi==0.115.0\nuvicorn==0.30.0\n", encoding="utf-8")
    (root / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    plan = build_plan("api", root)
    assert plan.project_type == "python"
    assert plan.package_manager == "pip"
    assert plan.install_command == "pip install --no-cache-dir -r requirements.txt"
    assert "uvicorn" in (plan.start_command or "")
    assert plan.base_image.startswith("python:3.12")


def test_plan_flags_gpu_and_external_apis(tmp_path: Path) -> None:
    root = tmp_path / "ml"
    root.mkdir()
    (root / "requirements.txt").write_text("torch\ntransformers\n", encoding="utf-8")
    (root / "README.md").write_text("This project requires an NVIDIA GPU with CUDA 12.\n", encoding="utf-8")
    (root / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    plan = build_plan("ml", root)
    assert plan.gpu_required is True
    assert any("OpenAI" in api for api in plan.external_apis)


def test_plan_unknown_project_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    (root / "notes.txt").write_text("nothing here", encoding="utf-8")
    plan = build_plan("empty", root)
    assert plan.project_type == "unknown"
    assert plan.blocking_requirements
    assert plan.confidence < 0.35


def test_env_example_parsing(tmp_path: Path) -> None:
    text = (
        "# Secret key for the API\n"
        "API_KEY=\n"
        "# Optional override\n"
        "LOG_LEVEL=info\n"
        "export DB_URL=postgres://x\n"
        "lowercase=ignored\n"
    )
    variables = parse_env_example(text)
    names = {v["name"]: v for v in variables}
    assert "API_KEY" in names
    assert names["API_KEY"]["required"] is True
    assert names["API_KEY"]["sensitive"] is True
    assert names["LOG_LEVEL"]["required"] is False
    assert names["DB_URL"]["example"].startswith("postgres://")
    assert "lowercase" not in names


def test_find_ports() -> None:
    text = "app.listen(4000) and http://localhost:5173 plus PORT=9000"
    ports = find_ports(text)
    assert 4000 in ports and 5173 in ports and 9000 in ports
    assert all(1 <= port <= 65535 for port in ports)


def test_analyze_project_bundle(tmp_path: Path) -> None:
    root = _node_project(tmp_path / "demo")
    analysis = analyze_project(root, project_name="demo")
    payload = analysis.as_dict()
    assert payload["plan"]["project_type"] == "node"
    assert payload["tech_stack"]["framework"] == "Next.js"
    assert payload["tree"]["total_files"] >= 5
    assert "Demo" in payload["readme_excerpt"]


def test_analysis_handles_invalid_package_json(tmp_path: Path) -> None:
    root = tmp_path / "broken"
    root.mkdir()
    (root / "package.json").write_text("{ this is not json", encoding="utf-8")
    scan = scan_tree(root)
    assert scan.facts.package_json.get("__parse_error__") is True
