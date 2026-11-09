"""Run-plan construction from real repository facts.

The planner prefers what the repository itself declares:

1. ``docker-compose`` / ``compose.yaml`` -> compose strategy.
2. ``Dockerfile`` -> single container built from the repo Dockerfile.
3. Otherwise a generated Dockerfile derived from the detected runtime and the
   start command that the manifests actually define.

Everything is traceable: each plan field records where the value came from so the
UI can show why OPENLAB picked a command instead of silently guessing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from engine.project_analyzer.detect import (
    ManifestFacts,
    TreeScan,
    detect_external_apis,
    detect_gpu_requirements,
    detect_services,
    find_ports,
    parse_env_example,
    scan_tree,
)

FRAMEWORK_HINTS: tuple[tuple[str, str], ...] = (
    ("next", "Next.js"),
    ("nuxt", "Nuxt"),
    ("@remix-run/react", "Remix"),
    ("svelte", "Svelte"),
    ("vue", "Vue"),
    ("@angular/core", "Angular"),
    ("react", "React"),
    ("express", "Express"),
    ("fastify", "Fastify"),
    ("koa", "Koa"),
    ("nestjs", "NestJS"),
    ("vite", "Vite"),
    ("astro", "Astro"),
    ("fastapi", "FastAPI"),
    ("django", "Django"),
    ("flask", "Flask"),
    ("starlette", "Starlette"),
    ("streamlit", "Streamlit"),
    ("gradio", "Gradio"),
    ("gin-gonic", "Gin"),
    ("actix-web", "Actix Web"),
    ("axum", "Axum"),
    ("rails", "Rails"),
    ("laravel", "Laravel"),
)

NODE_BASE = {
    "20": "node:20-bookworm-slim",
    "22": "node:22-bookworm-slim",
    "18": "node:18-bookworm-slim",
}
PYTHON_BASE = "python:3.12-slim"


class PlanningError(RuntimeError):
    """Raised when no runnable plan can be derived."""


@dataclass
class PlanField:
    value: str | None
    source: str
    confidence: float = 0.5

    def as_dict(self) -> dict:
        return {"value": self.value, "source": self.source, "confidence": self.confidence}


@dataclass
class ServiceSpec:
    name: str
    port: int | None = None
    image: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    detected_from: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "port": self.port,
            "image": self.image,
            "env": self.env,
            "detected_from": self.detected_from,
        }


@dataclass
class RunPlan:
    project_name: str
    project_type: str  # node|python|go|rust|static|compose|unknown
    language: str | None
    framework: str | None
    package_manager: str | None
    runtime_version: str | None
    strategy: str  # compose|dockerfile|generated
    install_command: str | None
    build_command: str | None
    start_command: str | None
    port: int
    health_path: str
    base_image: str
    services: list[ServiceSpec] = field(default_factory=list)
    env_vars: list[dict] = field(default_factory=list)
    gpu_required: bool = False
    gpu_notes: list[str] = field(default_factory=list)
    external_apis: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: dict[str, PlanField] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    blocking_requirements: list[str] = field(default_factory=list)
    workdir: str = "."

    def as_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "project_type": self.project_type,
            "language": self.language,
            "framework": self.framework,
            "package_manager": self.package_manager,
            "runtime_version": self.runtime_version,
            "strategy": self.strategy,
            "install_command": self.install_command,
            "build_command": self.build_command,
            "start_command": self.start_command,
            "port": self.port,
            "health_path": self.health_path,
            "base_image": self.base_image,
            "services": [s.__dict__ for s in self.services],
            "env_vars": self.env_vars,
            "gpu_required": self.gpu_required,
            "gpu_notes": self.gpu_notes,
            "external_apis": self.external_apis,
            "confidence": self.confidence,
            "evidence": {k: v.as_dict() for k, v in self.evidence.items()},
            "warnings": self.warnings,
            "blocking_requirements": self.blocking_requirements,
            "workdir": self.workdir,
        }


def _detect_framework(pkg: dict, requirements: list[str], pyproject: str) -> str | None:
    deps = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps.update((pkg.get(key) or {}).keys())
    blob = " ".join(sorted(deps))
    for needle, label in FRAMEWORK_HINTS:
        if needle in blob:
            return label
    req_blob = " ".join(requirements) + " " + pyproject.lower()
    for needle, label in FRAMEWORK_HINTS:
        if re.search(rf"\b{re.escape(needle)}\b", req_blob):
            return label
    return None


def _package_manager(facts: ManifestFacts) -> PlanField:
    if "pnpm-lock.yaml" in facts.lock_files:
        return PlanField("pnpm", "pnpm-lock.yaml", 0.98)
    if "yarn.lock" in facts.lock_files:
        return PlanField("yarn", "yarn.lock", 0.98)
    if "bun.lockb" in facts.lock_files:
        return PlanField("bun", "bun.lockb", 0.95)
    if "package-lock.json" in facts.lock_files:
        return PlanField("npm", "package-lock.json", 0.95)
    if "uv.lock" in facts.lock_files:
        return PlanField("uv", "uv.lock", 0.95)
    if "poetry.lock" in facts.lock_files:
        return PlanField("poetry", "poetry.lock", 0.95)
    if facts.package_json:
        declared = str((facts.package_json.get("packageManager") or "")).split("@")[0]
        if declared:
            return PlanField(declared, "packageManager field", 0.9)
        return PlanField("npm", "no lockfile, defaulting", 0.6)
    if facts.requirements or facts.pyproject:
        return PlanField("pip", "requirements/pyproject", 0.8)
    if facts.gomod:
        return PlanField("go", "go.mod", 0.95)
    if facts.cargo:
        return PlanField("cargo", "Cargo.toml", 0.95)
    return PlanField(None, "unknown", 0.1)


def _node_scripts(pkg: dict) -> dict[str, str]:
    scripts = pkg.get("scripts") or {}
    return {str(k): str(v) for k, v in scripts.items()}


def _pick_start_script(scripts: dict[str, str]) -> PlanField:
    for key in ("start", "serve", "dev"):
        if key in scripts:
            return PlanField(f"npm run {key}", f"package.json scripts.{key}", 0.85)
    return PlanField(None, "no start script", 0.2)


def _node_runtime_version(facts: ManifestFacts) -> PlanField:
    if facts.nvmrc:
        return PlanField(facts.nvmrc.splitlines()[0].strip(), ".nvmrc", 0.95)
    engines = (facts.package_json.get("engines") or {}) if facts.package_json else {}
    node = str(engines.get("node") or "")
    if node:
        digits = re.sub(r"[^0-9]", "", node)[:2]
        return PlanField(digits or node, "package.json engines.node", 0.85)
    return PlanField("20", "default LTS", 0.5)


def _python_start(facts: ManifestFacts, scan: TreeScan) -> PlanField:
    if "manage.py" in scan.manifests:
        return PlanField(
            "python manage.py runserver 0.0.0.0:8000", "manage.py present (Django)", 0.9
        )
    proc = (facts.procfile or "").strip()
    if proc:
        first = proc.splitlines()[0]
        if ":" in first:
            cmd = first.split(":", 1)[1].strip()
            return PlanField(f"sh -c '{cmd}'", "Procfile", 0.85)
    req = " ".join(facts.requirements) + " " + (facts.pyproject or "").lower()
    if "fastapi" in req or "uvicorn" in req:
        target = _guess_asgi_module(scan)
        return PlanField(f"uvicorn {target}:app --host 0.0.0.0 --port 8000", "FastAPI + uvicorn", 0.85)
    if "flask" in req:
        return PlanField("flask run --host 0.0.0.0 --port 8000", "Flask app", 0.75)
    if "streamlit" in req:
        return PlanField("streamlit run app.py --server.port 8501 --server.address 0.0.0.0", "Streamlit", 0.75)
    if "gradio" in req:
        return PlanField("python app.py", "Gradio app", 0.6)
    for candidate, command in (
        ("app.py", "python app.py"),
        ("main.py", "python main.py"),
        ("server.py", "python server.py"),
    ):
        if (scan.root / candidate).exists() or any(e.endswith(candidate) for e in scan.entrypoints):
            return PlanField(command, f"{candidate} entrypoint", 0.7)
    return PlanField("python -m http.server 8000 --bind 0.0.0.0", "fallback static server", 0.25)


def _guess_asgi_module(scan: TreeScan) -> str:
    for name in ("main.py", "app.py", "server.py", "api.py"):
        if (scan.root / name).exists():
            return name[:-3]
    for path in scan.root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")[:4000] if path.stat().st_size < 400_000 else ""
        if "FastAPI(" in text:
            return str(path.relative_to(scan.root).with_suffix("")).replace("\\", "/").replace("/", ".")
    return "main"


def _install_command(pm: str | None, facts: ManifestFacts) -> PlanField:
    if pm == "pnpm":
        return PlanField("npm i -g pnpm@9 && pnpm install --frozen-lockfile || pnpm install", "pnpm lockfile", 0.9)
    if pm == "yarn":
        return PlanField("corepack enable && yarn install --frozen-lockfile || yarn install", "yarn lockfile", 0.9)
    if pm == "bun":
        return PlanField("npm i -g bun && bun install", "bun lockfile", 0.85)
    if pm == "npm" and facts.package_json:
        lock = "npm ci" if "package-lock.json" in facts.lock_files else "npm install"
        return PlanField(lock, "package.json", 0.92)
    if pm == "uv":
        return PlanField("pip install uv && uv sync", "uv.lock", 0.9)
    if pm == "poetry":
        return PlanField("pip install poetry && poetry install --no-interaction", "poetry.lock", 0.9)
    if pm == "pip":
        parts = []
        if facts.requirements:
            parts.append("pip install --no-cache-dir -r requirements.txt")
        if facts.pyproject:
            parts.append("pip install --no-cache-dir .")
        return PlanField(" && ".join(parts) or None, "python manifests", 0.85)
    if pm == "go":
        return PlanField("go mod download", "go.mod", 0.95)
    if pm == "cargo":
        return PlanField("cargo fetch", "Cargo.toml", 0.95)
    return PlanField(None, "nothing to install", 0.3)


def _build_command(pm: str | None, facts: ManifestFacts, scripts: dict[str, str]) -> PlanField:
    if "build" in scripts:
        runner = {"pnpm": "pnpm run build", "yarn": "yarn build", "bun": "bun run build"}.get(pm or "", "npm run build")
        return PlanField(runner, "package.json scripts.build", 0.9)
    if pm == "go":
        return PlanField("go build ./...", "go.mod", 0.8)
    if pm == "cargo":
        return PlanField("cargo build --release", "Cargo.toml", 0.8)
    return PlanField(None, "no build step declared", 0.6)


def build_plan(
    project_name: str,
    source_dir: str | Path,
    scan: TreeScan | None = None,
    prefer_port: int | None = None,
) -> RunPlan:
    scan = scan or scan_tree(source_dir)
    facts = scan.facts
    evidence: dict[str, PlanField] = {}
    warnings: list[str] = []
    blocking: list[str] = []

    has_compose = bool(facts.compose.strip())
    has_dockerfile = bool(facts.dockerfile.strip())

    pm_field = _package_manager(facts)
    evidence["package_manager"] = pm_field
    pm = pm_field.value

    framework = _detect_framework(facts.package_json or {}, facts.requirements, facts.pyproject)
    project_type = _project_type(scan, facts, pm)
    language = scan.primary_language or _language_from_type(project_type)

    install_field = _install_command(pm, facts)
    evidence["install_command"] = install_field

    scripts = _node_scripts(facts.package_json or {})
    node_version_field = _node_runtime_version(facts)
    build_field = _build_command(pm, facts, scripts)

    if project_type == "node":
        start_field = _pick_start_script(scripts)
        runtime_version = node_version_field.value
        base_image = NODE_BASE.get((runtime_version or "20")[:2], "node:20-bookworm-slim")
    elif project_type == "python":
        start_field = _python_start(facts, scan)
        runtime_version = facts.python_version or "3.12"
        base_image = PYTHON_BASE
    elif project_type == "go":
        start_field = PlanField("go run .", "go.mod", 0.6)
        runtime_version = "1.22"
        base_image = "golang:1.22-bookworm"
    elif project_type == "rust":
        start_field = PlanField("cargo run --release", "Cargo.toml", 0.55)
        runtime_version = "stable"
        base_image = "rust:1-slim"
    elif project_type == "static":
        start_field = PlanField("python -m http.server 8000 --bind 0.0.0.0", "static assets", 0.6)
        runtime_version = "3.12"
        base_image = PYTHON_BASE
    elif project_type == "native":
        start_field = PlanField(None, "compiled/desktop project — no HTTP service", 0.0)
        runtime_version = None
        base_image = "debian:bookworm-slim"
    else:
        start_field = PlanField(None, "unknown runtime", 0.1)
        runtime_version = None
        base_image = "debian:bookworm-slim"

    if has_compose:
        strategy = "compose"
    elif has_dockerfile:
        strategy = "dockerfile"
    else:
        strategy = "generated"

    ports = _collect_ports(facts, scripts, scan)
    port = prefer_port or (ports[0] if ports else _default_port(project_type))
    evidence["port"] = PlanField(str(port), "manifests" if ports else "default for runtime", 0.8 if ports else 0.4)
    evidence["start_command"] = start_field
    evidence["build_command"] = build_field

    env_vars = parse_env_example(facts.env_example)
    services = [ServiceSpec(**s) for s in detect_services(facts)]
    gpu_required, gpu_notes = detect_gpu_requirements(facts, facts.readme)
    external_apis = detect_external_apis(facts, env_vars)

    if not start_field.value and strategy == "generated" and project_type != "native":
        blocking.append("No start command could be derived from the repository manifests")
    if has_dockerfile and not has_compose:
        warnings.append("Repository ships a Dockerfile; OPENLAB builds it as-is (no generated Dockerfile).")
    if project_type == "unknown":
        blocking.append("Language/runtime could not be identified from manifests")
    native = scan.native_project
    if project_type == "native" or native is not None:
        detail = native or {"kind": "native", "indicators": [], "primary_language": scan.primary_language}
        blocking.append(
            "This is a compiled/desktop project, not a web service: "
            + (", ".join(detail.get("indicators") or []) or "native build files detected")
            + ". OPENLAB can only verifiably run projects that expose an HTTP interface inside a "
            "container, so it will not claim a browser experience for this repository. "
            "The source, analysis and technical report are still available."
        )
        warnings.append(
            "Browser exploration, screenshots and the live preview do not apply to this project type."
        )

    confidence = _score_confidence(
        scan, pm_field, start_field, has_compose, has_dockerfile, blocking, framework, build_field
    )

    return RunPlan(
        project_name=project_name,
        project_type=project_type,
        language=language,
        framework=framework,
        package_manager=pm,
        runtime_version=runtime_version,
        strategy=strategy,
        install_command=install_field.value,
        build_command=build_field.value,
        start_command=start_field.value,
        port=int(port),
        health_path="/",
        base_image=base_image,
        services=services,
        env_vars=env_vars,
        gpu_required=gpu_required,
        gpu_notes=gpu_notes,
        external_apis=external_apis,
        confidence=confidence,
        evidence=evidence,
        warnings=warnings,
        blocking_requirements=blocking,
    )


def _project_type(scan: TreeScan, facts: ManifestFacts, pm: str | None) -> str:
    # A compiled/desktop project cannot be served over HTTP, so classify it as such
    # instead of pretending a generic web server would exercise it.
    if scan.native_project is not None:
        return "native"
    if facts.package_json:
        return "node"
    if facts.requirements or facts.pyproject or "manage.py" in scan.manifests:
        return "python"
    if facts.gomod:
        return "go"
    if facts.cargo:
        return "rust"
    if any(scan.root.glob("*.html")):
        return "static"
    lang = (scan.primary_language or "").lower()
    if lang in {"python"}:
        return "python"
    if lang in {"typescript", "javascript"}:
        return "node"
    if lang == "go":
        return "go"
    if lang == "rust":
        return "rust"
    if lang in {"c", "c++", "c/c++ header", "c#", "assembly", "java", "kotlin"}:
        return "native"
    return "unknown"


def _language_from_type(project_type: str) -> str | None:
    return {
        "node": "JavaScript/TypeScript",
        "python": "Python",
        "go": "Go",
        "rust": "Rust",
        "static": "HTML/CSS",
        "native": "C/C++/C#",
    }.get(project_type)


def _collect_ports(facts: ManifestFacts, scripts: dict[str, str], scan: TreeScan) -> list[int]:
    texts = [facts.compose, facts.dockerfile, facts.readme[:8000], " ".join(scripts.values())]
    ports: list[int] = []
    for text in texts:
        for port in find_ports(text):
            if port not in ports:
                ports.append(port)
    compose_ports = _compose_ports(facts.compose)
    for port in compose_ports:
        if port not in ports:
            ports.insert(0, port)
    return ports


def _compose_ports(compose_text: str) -> list[int]:
    ports: list[int] = []
    for match in re.finditer(r"[\"']?(\d{2,5}):(\d{2,5})[\"']?", compose_text or ""):
        try:
            host = int(match.group(1))
        except ValueError:
            continue
        if 1 <= host <= 65535:
            ports.append(host)
    return ports


def _default_port(project_type: str) -> int:
    return {
        "node": 3000,
        "python": 8000,
        "go": 8080,
        "rust": 8080,
        "static": 8000,
    }.get(project_type, 3000)


def _score_confidence(
    scan: TreeScan,
    pm_field: PlanField,
    start_field: PlanField,
    has_compose: bool,
    has_dockerfile: bool,
    blocking: list[str],
    framework: str | None = None,
    build_field: PlanField | None = None,
) -> float:
    score = 0.2
    if has_compose:
        score += 0.5
    elif has_dockerfile:
        score += 0.4
    if start_field.value:
        score += 0.15 * start_field.confidence
    if pm_field.value:
        score += 0.1
    if build_field is not None and build_field.value:
        score += 0.08
    if framework:
        score += 0.1
    if scan.manifests:
        score += 0.1
    if blocking:
        score -= 0.35
    return round(max(0.0, min(1.0, score)), 2)


def plan_to_json(plan: RunPlan) -> str:
    return json.dumps(plan.as_dict(), indent=2, ensure_ascii=False)
