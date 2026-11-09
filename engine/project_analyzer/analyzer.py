"""Project analysis: turns a source tree into a structured, verifiable profile.

``analyze_project`` is the entry point used by the deploy task. It performs all
deterministic detection, then optionally asks the model to refine the plan when
confidence is low (never to invent facts that contradict the manifests).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from engine.project_analyzer.detect import TreeScan, scan_tree
from engine.project_analyzer.planner import RunPlan, build_plan


@dataclass
class AnalysisResult:
    scan: TreeScan
    plan: RunPlan
    readme_excerpt: str = ""
    tech_stack: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "tree": self.scan.as_dict(),
            "plan": self.plan.as_dict(),
            "tech_stack": self.tech_stack,
            "readme_excerpt": self.readme_excerpt[:4000],
            "warnings": self.warnings,
        }


def analyze_project(source_dir: str | Path, project_name: str = "project") -> AnalysisResult:
    scan = scan_tree(source_dir)
    plan = build_plan(project_name, source_dir, scan=scan)
    tech_stack = _tech_stack(scan, plan)
    warnings = list(plan.warnings)
    if scan.total_files == 0:
        warnings.append("Source tree is empty")
    if not plan.evidence.get("readme"):
        pass
    return AnalysisResult(
        scan=scan,
        plan=plan,
        readme_excerpt=(scan.facts.readme or "")[:6000],
        tech_stack=tech_stack,
        warnings=warnings,
    )


def _tech_stack(scan: TreeScan, plan: RunPlan) -> dict:
    facts = scan.facts
    deps: list[str] = []
    dev_deps: list[str] = []
    pkg = facts.package_json or {}
    deps.extend(sorted((pkg.get("dependencies") or {}).keys())[:40])
    dev_deps.extend(sorted((pkg.get("devDependencies") or {}).keys())[:30])
    deps.extend(facts.requirements[:40])
    database = [s.name for s in plan.services if s.name in {"postgres", "mysql", "sqlite", "mongodb"}]
    cache = [s.name for s in plan.services if s.name in {"redis", "memcached"}]
    return {
        "primary_language": scan.primary_language,
        "languages": scan.languages,
        "framework": plan.framework,
        "package_manager": plan.package_manager,
        "runtime_version": plan.runtime_version,
        "project_type": plan.project_type,
        "dependencies": deps,
        "dev_dependencies": dev_deps,
        "databases": database,
        "cache_services": cache,
        "services": [s.name for s in plan.services],
        "deployment": {
            "strategy": plan.strategy,
            "base_image": plan.base_image,
            "port": plan.port,
        },
        "gpu_required": plan.gpu_required,
        "external_apis": plan.external_apis,
        "has_dockerfile": bool(facts.dockerfile.strip()),
        "has_compose": bool(facts.compose.strip()),
        "has_tests": any(
            (scan.root / candidate).exists()
            for candidate in ("tests", "test", "spec", "__tests__", "pytest.ini", "vitest.config.ts")
        ),
        "has_ci": (scan.root / ".github" / "workflows").is_dir(),
        "license_file": any((scan.root / n).exists() for n in ("LICENSE", "LICENSE.md", "COPYING")),
        "entrypoints": scan.entrypoints,
        "subprojects": scan.subprojects,
    }


PLAN_SYSTEM_PROMPT = """You are the OPENLAB environment planner.
You receive facts extracted from a real repository (file tree summary, manifest
contents, detected scripts). Produce a run plan that a Docker container can
execute unattended on Linux. Rules:
- Only use commands supported by the evidence you were given.
- Never invent a build step that the repository does not define.
- Prefer the project's own Dockerfile/compose when present.
- The app must bind to 0.0.0.0 on the given port.
- Answer with JSON only, matching the requested schema.
"""


def plan_prompt(analysis: AnalysisResult) -> str:
    facts = analysis.scan.facts
    payload = {
        "project_name": analysis.plan.project_name,
        "detected_plan": analysis.plan.as_dict(),
        "tree": analysis.scan.as_dict(),
        "package_json": facts.package_json,
        "requirements": facts.requirements[:60],
        "pyproject_excerpt": facts.pyproject[:4000],
        "compose_excerpt": facts.compose[:4000],
        "dockerfile_excerpt": facts.dockerfile[:4000],
        "readme_excerpt": (facts.readme or "")[:4000],
        "env_example": facts.env_example[:2000],
    }
    schema = {
        "project_type": "node|python|go|rust|static|unknown",
        "strategy": "compose|dockerfile|generated",
        "install_command": "string|null",
        "build_command": "string|null",
        "start_command": "string",
        "port": "integer",
        "health_path": "string",
        "base_image": "string",
        "services": [{"name": "string", "port": "integer|null"}],
        "env_vars": [{"name": "string", "required": "boolean", "description": "string|null"}],
        "gpu_required": "boolean",
        "confidence": "number 0..1",
        "notes": "string",
    }
    return (
        "Repository facts:\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)[:24000]
        + "\n\nReturn JSON with this shape:\n"
        + json.dumps(schema, indent=2)
    )
