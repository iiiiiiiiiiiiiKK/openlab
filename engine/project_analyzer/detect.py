"""Deterministic source-tree inspection.

This module never calls a model. It walks the imported tree, records the manifest
files that actually exist, and extracts concrete facts (scripts, dependencies,
declared ports, runtime hints). The run plan is built from these facts first; the
LLM is only asked to resolve ambiguity or produce repairs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAMES = (
    "README.md",
    "readme.md",
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "bun.lockb",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "poetry.lock",
    "setup.py",
    "setup.cfg",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Makefile",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
    "Gemfile.lock",
    "composer.lock",
    "CMakeLists.txt",
    "meson.build",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "pom.xml",
    "BUILD",
    "WORKSPACE",
    "global.json",
    "Directory.Build.props",
    ".env.example",
    ".env.sample",
    ".env.template",
    ".nvmrc",
    ".python-version",
    ".tool-versions",
    "rust-toolchain.toml",
    "requirements.lock",
    "uv.lock",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "vite.config.ts",
    "vite.config.js",
    "nuxt.config.ts",
    "angular.json",
    "manage.py",
    "Procfile",
    "deno.json",
)

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    "dist",
    "build",
    "target",
    "vendor",
    ".turbo",
    ".idea",
    ".vscode",
    "coverage",
}

LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".h": "C/C++ header",
    ".hh": "C/C++ header",
    ".hpp": "C/C++ header",
    ".hxx": "C/C++ header",
    ".ino": "Arduino",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".dart": "Dart",
    ".scala": "Scala",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".lua": "Lua",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
    ".asm": "Assembly",
    ".s": "Assembly",
    ".glsl": "GLSL",
    ".hlsl": "HLSL",
    ".sol": "Solidity",
    ".zig": "Zig",
    ".nim": "Nim",
}

PORT_PATTERNS = (
    re.compile(r"--port[= ](\d{2,5})"),
    re.compile(r"\bPORT\s*[:=]\s*['\"]?(\d{2,5})"),
    re.compile(r"listen\(\s*(\d{2,5})"),
    re.compile(r"127\.0\.0\.1:(\d{2,5})"),
    re.compile(r"localhost:(\d{2,5})"),
    re.compile(r"0\.0\.0\.0:(\d{2,5})"),
    re.compile(r"port\s*[:=]\s*(\d{2,5})", re.I),
)

ENV_HINT_KEYS = re.compile(
    r"^[A-Z][A-Z0-9_]{1,60}$",
)


@dataclass
class ManifestFacts:
    package_json: dict = field(default_factory=dict)
    pyproject: str = ""
    requirements: list[str] = field(default_factory=list)
    dockerfile: str = ""
    compose: str = ""
    makefile: str = ""
    cargo: str = ""
    gomod: str = ""
    env_example: str = ""
    readme: str = ""
    nvmrc: str = ""
    python_version: str = ""
    procfile: str = ""
    lock_files: list[str] = field(default_factory=list)


@dataclass
class TreeScan:
    root: Path
    total_files: int = 0
    total_bytes: int = 0
    language_bytes: dict[str, int] = field(default_factory=dict)
    manifests: dict[str, Path] = field(default_factory=dict)
    facts: ManifestFacts = field(default_factory=ManifestFacts)
    entrypoints: list[str] = field(default_factory=list)
    subprojects: list[str] = field(default_factory=list)

    @property
    def primary_language(self) -> str | None:
        if not self.language_bytes:
            return None
        return max(self.language_bytes.items(), key=lambda kv: kv[1])[0]

    @property
    def native_project(self) -> dict | None:
        """Detect a compiled/native project that cannot run as a local web service.

        OPENLAB's job is to *run* a project and explore it in a browser. Libraries,
        Windows shell extensions, desktop apps and firmware cannot be verified that
        way, so they are reported honestly as non-runnable instead of being planned
        as if they were a web app.
        """
        facts = self.facts
        indicators: list[str] = []
        kind: str | None = None

        if any(p.name.endswith((".sln", ".vcxproj", ".csproj", ".vcxproj.filters")) for p in self.root.iterdir() if p.is_file()):
            kind = "windows-desktop"
            indicators.append("Visual Studio solution/project file")
        if "CMakeLists.txt" in self.manifests:
            kind = kind or "native"
            indicators.append("CMakeLists.txt")
        if "meson.build" in self.manifests:
            kind = kind or "native"
            indicators.append("meson.build")
        if facts.gomod or facts.cargo:
            return None  # Go/Rust are handled by the normal planner
        if facts.package_json:
            return None  # a JS project may still be a headless tool; handled separately
        if facts.pyproject or facts.requirements:
            return None
        if "build.gradle" in self.manifests or "pom.xml" in self.manifests:
            kind = kind or "jvm"
            indicators.append("Gradle/Maven build file")
        if "BUILD" in self.manifests or "WORKSPACE" in self.manifests:
            kind = kind or "native"
            indicators.append("Bazel build file")
        if "global.json" in self.manifests or "Directory.Build.props" in self.manifests:
            kind = kind or "dotnet"
            indicators.append(".NET build configuration")

        has_native_sources = any(
            suffix in self.language_bytes
            for suffix in ("C", "C++", "C/C++ header", "C#", "Assembly")
        )
        if kind is None and has_native_sources and self.facts.readme:
            readme = self.facts.readme.lower()
            if any(
                needle in readme
                for needle in (
                    ".dll",
                    "shell extension",
                    "dwm",
                    "win32",
                    "windows service",
                    "driver",
                    "kernel module",
                )
            ):
                kind = "windows-desktop"
                indicators.append("README describes a Windows/native component")

        if kind is None:
            return None
        return {
            "kind": kind,
            "indicators": indicators,
            "primary_language": self.primary_language,
            "languages": self.languages[:5],
        }

    @property
    def languages(self) -> list[dict]:
        total = sum(self.language_bytes.values()) or 1
        ranked = sorted(self.language_bytes.items(), key=lambda kv: kv[1], reverse=True)
        return [
            {"language": lang, "bytes": size, "share": round(size / total * 100, 1)}
            for lang, size in ranked[:8]
        ]

    def as_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "languages": self.languages,
            "primary_language": self.primary_language,
            "manifests": {k: str(v.relative_to(self.root)) for k, v in self.manifests.items()},
            "entrypoints": self.entrypoints,
            "subprojects": self.subprojects,
            "lock_files": self.facts.lock_files,
        }


def scan_tree(root: str | Path, max_files: int = 60000) -> TreeScan:
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"source directory not found: {root}")
    scan = TreeScan(root=root)
    for path in _walk(root, max_files=max_files):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        scan.total_files += 1
        scan.total_bytes += size
        lang = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
        if lang:
            scan.language_bytes[lang] = scan.language_bytes.get(lang, 0) + size
        name = path.name
        rel = path.relative_to(root)
        if name in MANIFEST_NAMES and name not in scan.manifests:
            scan.manifests[name] = path
        if name in {"main.py", "app.py", "server.py", "index.js", "server.js", "main.go", "main.rs"}:
            if len(rel.parts) <= 2:
                scan.entrypoints.append(str(rel))
    _load_facts(scan)
    _detect_subprojects(scan)
    return scan


def _walk(root: Path, max_files: int):
    count = 0
    for current, dirs, files in _os_walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for name in files:
            count += 1
            if count > max_files:
                return
            yield Path(current) / name


def _os_walk(root: Path):
    import os

    return os.walk(root)


def _read_text(path: Path | None, limit: int = 200_000) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _load_facts(scan: TreeScan) -> None:
    m = scan.manifests
    facts = scan.facts
    if "package.json" in m:
        try:
            facts.package_json = json.loads(_read_text(m["package.json"]))
        except json.JSONDecodeError:
            facts.package_json = {"__parse_error__": True}
    facts.pyproject = _read_text(m.get("pyproject.toml"))
    facts.requirements = _parse_requirements(_read_text(m.get("requirements.txt")))
    facts.dockerfile = _read_text(m.get("Dockerfile"))
    for compose_name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        if compose_name in m:
            facts.compose = _read_text(m[compose_name])
            break
    facts.makefile = _read_text(m.get("Makefile"))
    facts.cargo = _read_text(m.get("Cargo.toml"))
    facts.gomod = _read_text(m.get("go.mod"))
    facts.procfile = _read_text(m.get("Procfile"))
    facts.nvmrc = _read_text(m.get(".nvmrc")).strip()
    facts.python_version = _read_text(m.get(".python-version")).strip()
    for key in (".env.example", ".env.sample", ".env.template"):
        if key in m:
            facts.env_example = _read_text(m[key])
            break
    for readme in ("README.md", "readme.md"):
        if readme in m:
            facts.readme = _read_text(m[readme])
            break
    facts.lock_files = [
        name
        for name in (
            "pnpm-lock.yaml",
            "package-lock.json",
            "yarn.lock",
            "bun.lockb",
            "poetry.lock",
            "uv.lock",
            "Pipfile",
            "requirements.lock",
        )
        if name in m
    ]


def _parse_requirements(text: str) -> list[str]:
    deps: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip()
        if name:
            deps.append(name.lower())
    return deps


def _detect_subprojects(scan: TreeScan) -> None:
    for child in sorted(scan.root.iterdir()):
        if not child.is_dir() or child.name in IGNORED_DIRS:
            continue
        for candidate in ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml"):
            if (child / candidate).exists():
                scan.subprojects.append(child.name)
                break


def find_ports(text: str) -> list[int]:
    found: list[int] = []
    for pattern in PORT_PATTERNS:
        for match in pattern.finditer(text or ""):
            try:
                port = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535 and port not in found:
                found.append(port)
    return found


def parse_env_example(text: str) -> list[dict]:
    """Turn ``.env.example`` into structured variable descriptors."""
    variables: list[dict] = []
    seen: set[str] = set()
    pending_comment: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            pending_comment = []
            continue
        if line.startswith("#"):
            pending_comment.append(line.lstrip("# ").strip())
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not ENV_HINT_KEYS.match(key) or key in seen:
            continue
        seen.add(key)
        value = value.strip().strip("\"'")
        description = " ".join(pending_comment)[:240] or None
        pending_comment = []
        required = not value and not re.search(r"(optional|default|if you|leave blank)", (description or ""), re.I)
        variables.append(
            {
                "name": key,
                "example": value[:120],
                "description": description,
                "required": required,
                "sensitive": bool(re.search(r"(key|token|secret|password|passwd|credential)", key, re.I)),
                "configured": False,
            }
        )
    return variables


def detect_services(facts: ManifestFacts) -> list[dict]:
    """Infer backing services from compose files, requirements and package.json."""
    services: list[dict] = []
    compose = (facts.compose or "").lower()
    blob = " ".join(
        [
            compose,
            " ".join(facts.requirements),
            json.dumps(facts.package_json or {}).lower(),
            facts.pyproject.lower(),
        ]
    )
    candidates = {
        "postgres": ("postgres", "postgresql", "psycopg", "asyncpg"),
        "mysql": ("mysql", "mariadb"),
        "redis": ("redis", "ioredis"),
        "mongodb": ("mongo",),
        "sqlite": ("sqlite",),
        "rabbitmq": ("rabbitmq", "amqp", "celery"),
        "minio": ("minio", "s3"),
        "elasticsearch": ("elasticsearch", "opensearch"),
    }
    for name, needles in candidates.items():
        if any(needle in blob for needle in needles):
            services.append(
                {
                    "name": name,
                    "detected_from": "compose" if name in compose else "dependencies",
                    "port": _DEFAULT_SERVICE_PORTS.get(name),
                }
            )
    return services


_DEFAULT_SERVICE_PORTS = {
    "postgres": 5432,
    "mysql": 3306,
    "redis": 6379,
    "mongodb": 27017,
    "rabbitmq": 5672,
    "minio": 9000,
    "elasticsearch": 9200,
}


def detect_external_apis(facts: ManifestFacts, env_vars: list[dict]) -> list[str]:
    apis: list[str] = []
    names = {v["name"].upper() for v in env_vars}
    table = {
        "OPENAI_API_KEY": "OpenAI",
        "ANTHROPIC_API_KEY": "Anthropic",
        "GOOGLE_API_KEY": "Google",
        "GEMINI_API_KEY": "Google Gemini",
        "DEEPSEEK_API_KEY": "DeepSeek",
        "STRIPE_SECRET_KEY": "Stripe",
        "SENDGRID_API_KEY": "SendGrid",
        "AWS_ACCESS_KEY_ID": "AWS",
        "SUPABASE_URL": "Supabase",
        "FIREBASE_API_KEY": "Firebase",
        "GITHUB_TOKEN": "GitHub",
        "HF_TOKEN": "Hugging Face",
        "REPLICATE_API_TOKEN": "Replicate",
    }
    for key, label in table.items():
        if key in names and label not in apis:
            apis.append(label)
    if not apis and re.search(r"openai|anthropic|gemini|huggingface", json.dumps(facts.package_json).lower()):
        apis.append("LLM provider (inferred from dependencies)")
    return apis


def detect_gpu_requirements(facts: ManifestFacts, readme: str = "") -> tuple[bool, list[str]]:
    notes: list[str] = []
    blob = " ".join(
        [
            " ".join(facts.requirements),
            facts.pyproject.lower(),
            (readme or "")[:8000].lower(),
            json.dumps(facts.package_json or {}).lower(),
        ]
    )
    gpu_packages = ("torch", "tensorflow", "jax", "cupy", "onnxruntime-gpu", "vllm", "transformers")
    hits = [pkg for pkg in gpu_packages if re.search(rf"\b{re.escape(pkg)}\b", blob)]
    if hits:
        notes.append(f"GPU-capable dependencies detected: {', '.join(hits)}")
    if re.search(r"(requires?|needs?)\s+(an?\s+)?(nvidia\s+)?gpu", readme or "", re.I):
        notes.append("README states a GPU requirement")
    if re.search(r"cuda", blob):
        notes.append("CUDA mentioned in manifests or README")
    return bool(notes), notes
