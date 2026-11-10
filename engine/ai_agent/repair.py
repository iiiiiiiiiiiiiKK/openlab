"""AI repair loop with a hard attempt cap.

The loop is strictly evidence driven: real container/install logs are collected,
the responsible files are located, a patch is requested, applied to the isolated
workspace, and only the failed step is retried. After ``max_attempts`` the task
stops and asks the user -- OPENLAB never retries forever and never touches host
configuration.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from engine.ai_agent.adapters import ChatMessage, ProviderError
from engine.ai_agent.client import AIClient, Budget, BudgetExceeded
from engine.ai_agent.patch import PatchBundle, apply_patch, build_changes, rollback_patch
from engine.core.security import redact

REPAIR_SYSTEM_PROMPT = """You are the OPENLAB deployment repair engineer.
You receive: (1) the run plan, (2) the exact failing command, (3) the tail of the
REAL error output, and (4) an excerpt of the source files that are most likely
responsible. Diagnose the failure and return minimal edits.

Rules:
- Return JSON only: {"diagnosis": string, "edits": [{"path": string, "reason": string, "content": string}]}
- "content" must be the COMPLETE new file content, not a diff fragment.
- Never delete the original feature; prefer configuration or environment fixes.
- Never add privileged Docker settings, host mounts or docker socket access.
- Never hardcode credentials. Use environment variables instead.
- Keep edits to the fewest files that can fix the error.
"""

# A file offered for editing is sent whole, because the repair contract asks for the
# complete new content. Anything larger than this is skipped rather than truncated.
MAX_EDITABLE_FILE_BYTES = 60_000

STAGE_HINT = {
    "INSTALLING": "dependency installation failed",
    "BUILDING": "build step failed",
    "STARTING": "the process exited during startup",
    "HEALTH_CHECK": "the container runs but the HTTP endpoint never became ready",
}


@dataclass
class RepairAttempt:
    attempt: int
    stage: str
    diagnosis: str
    diff: str
    files: list[str]
    applied: bool
    rolled_back: bool = False
    error: str | None = None
    model: str | None = None

    def as_dict(self) -> dict:
        return {
            "attempt": self.attempt,
            "stage": self.stage,
            "diagnosis": self.diagnosis,
            "diff": self.diff,
            "files": self.files,
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "error": self.error,
            "model": self.model,
        }


@dataclass
class RepairContext:
    project_id: str
    project_name: str
    stage: str
    command: str
    logs: str
    plan: dict
    workspace_dir: Path
    source_dir: Path
    patch_dir: Path
    max_attempts: int = 3
    history: list[RepairAttempt] = field(default_factory=list)


_ERROR_PATTERNS = (
    re.compile(r"(?i)(ModuleNotFoundError|ImportError): No module named '?([\w.\-]+)'?"),
    re.compile(r"(?i)cannot find module '([^']+)'"),
    re.compile(r"(?i)ERR_PNPM_[\w_]+"),
    re.compile(r"(?i)npm ERR! .*"),
    re.compile(r"(?i)EACCES.*"),
    re.compile(r"(?i)command not found:?\s*(\S+)"),
    re.compile(r"(?i)missing script:\s*(\S+)"),
    re.compile(r"(?i)python: can't open file '([^']+)'"),
    re.compile(r"(?i)error while loading shared libraries: ([^:]+)"),
    re.compile(r"(?i)Connection refused"),
    re.compile(r"(?i)address already in use"),
)


def extract_error_lines(logs: str, limit: int = 60) -> list[str]:
    """Pull the most informative lines out of a long build log."""
    lines = [ln.rstrip() for ln in (logs or "").splitlines() if ln.strip()]
    scored: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("!"):
            scored.append((100, line))
            continue
        for pattern in _ERROR_PATTERNS:
            if pattern.search(line):
                scored.append((90, line))
                break
        else:
            if re.search(r"(?i)\b(error|failed|exception|traceback)\b", line):
                scored.append((70, line))
    seen: set[str] = set()
    picked: list[str] = []
    for _, line in sorted(scored, key=lambda item: -item[0]):
        if line in seen:
            continue
        seen.add(line)
        picked.append(line[:400])
        if len(picked) >= limit:
            break
    if not picked:
        picked = lines[-limit:]
    return picked


def locate_relevant_files(workspace_dir: Path, logs: str, plan: dict, limit: int = 6) -> dict[str, str]:
    """Find the files a model most likely needs to see, with their content."""
    workspace_dir = Path(workspace_dir)
    needles: set[str] = set()
    for pattern in _ERROR_PATTERNS:
        for match in pattern.finditer(logs or ""):
            if match.groups():
                needles.add(match.group(match.lastindex or 0).strip())
    candidates: list[Path] = []

    for name in (
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "Dockerfile",
        "docker-compose.yml",
        "compose.yaml",
        ".env.example",
        ".nvmrc",
        "vite.config.ts",
        "next.config.js",
        "manage.py",
        "Procfile",
    ):
        candidate = workspace_dir / name
        if candidate.exists():
            candidates.append(candidate)

    for needle in needles:
        token = needle.replace(".", "/")
        for pattern in (f"**/{needle}", f"**/{token}.py", f"**/{token}.ts", f"**/{token}.js"):
            try:
                candidates.extend(list(workspace_dir.glob(pattern))[:2])
            except (ValueError, OSError):
                continue

    for hint in (plan.get("start_command") or "", plan.get("install_command") or ""):
        for word in re.findall(r"[\w.\-/]+\.(?:py|js|ts|mjs|sh)", hint or ""):
            candidate = workspace_dir / word
            if candidate.exists():
                candidates.append(candidate)

    selected: dict[str, str] = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(workspace_dir).as_posix()
        except ValueError:
            continue
        if rel in selected:
            continue
        try:
            if path.stat().st_size > MAX_EDITABLE_FILE_BYTES:
                # The model is asked for the COMPLETE new file content, so a file it
                # cannot see in full must never be offered as an edit candidate:
                # applying a 12k excerpt used to truncate real source files.
                continue
            selected[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(selected) >= limit:
            break
    return selected


def build_repair_prompt(ctx: RepairContext, relevant: dict[str, str]) -> list[ChatMessage]:
    payload = {
        "project": ctx.project_name,
        "failing_stage": ctx.stage,
        "stage_hint": STAGE_HINT.get(ctx.stage, "deployment step failed"),
        "failing_command": ctx.command,
        "run_plan": ctx.plan,
        "error_lines": extract_error_lines(ctx.logs),
        "log_tail": redact(ctx.logs[-6000:]),
        "previous_attempts": [
            {"attempt": a.attempt, "diagnosis": a.diagnosis, "files": a.files, "error": a.error}
            for a in ctx.history
        ],
        "files": relevant,
    }
    return [
        ChatMessage(role="system", content=REPAIR_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content="Deployment failure evidence:\n"
            + json.dumps(payload, indent=2, ensure_ascii=False)[:60000]
            + '\n\nReturn JSON: {"diagnosis": "...", "edits": [{"path": "...", "reason": "...", "content": "..."}]}',
        ),
    ]


async def propose_repair(client: AIClient, ctx: RepairContext, budget: Budget) -> PatchBundle:
    relevant = locate_relevant_files(ctx.workspace_dir, ctx.logs, ctx.plan)
    messages = build_repair_prompt(ctx, relevant)
    budget.check()
    data = await client.complete_json(
        messages,
        purpose="repair",
        project_id=ctx.project_id,
        task_id=None,
        prefer_cheap=False,
        budget=budget,
    )
    edits = data.get("edits") or []
    if not isinstance(edits, list) or not edits:
        raise ProviderError("model returned no edits", body=json.dumps(data)[:800])
    bundle = build_changes(ctx.source_dir, ctx.workspace_dir, edits)
    bundle.diagnosis = str(data.get("diagnosis") or "")[:4000]
    return bundle


@dataclass
class RepairOutcome:
    resolved: bool
    attempts: list[RepairAttempt]
    awaiting_user: bool
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "resolved": self.resolved,
            "awaiting_user": self.awaiting_user,
            "reason": self.reason,
            "attempts": [a.as_dict() for a in self.attempts],
        }


async def run_repair_loop(
    client: AIClient,
    ctx: RepairContext,
    retry_step,
    budget: Budget | None = None,
    on_event=None,
) -> RepairOutcome:
    """Repeatedly diagnosis -> patch -> retry until success or the cap is hit."""
    budget = budget or Budget()
    attempts: list[RepairAttempt] = []
    for index in range(1, ctx.max_attempts + 1):
        try:
            budget.check()
        except BudgetExceeded as exc:
            return RepairOutcome(False, attempts, True, f"repair stopped: {exc}")
        if on_event is not None:
            await on_event("repair.planning", {"attempt": index, "stage": ctx.stage})
        try:
            bundle = await propose_repair(client, ctx, budget)
        except (ProviderError, BudgetExceeded) as exc:
            attempts.append(
                RepairAttempt(index, ctx.stage, "model call failed", "", [], False, error=str(exc))
            )
            return RepairOutcome(False, attempts, True, f"model call failed: {exc}")

        backup_root = ctx.patch_dir / f"backup-attempt-{index}"
        applied_files: list[str] = []
        try:
            # apply_patch is all-or-nothing: on failure the workspace is restored,
            # so the next attempt never stacks a patch on a half-written tree.
            applied_files = apply_patch(bundle, ctx.workspace_dir, backup_root)
        except Exception as exc:  # noqa: BLE001 - patch errors are user-facing
            attempts.append(
                RepairAttempt(
                    index, ctx.stage, bundle.diagnosis, bundle.unified_diff(), bundle.files_touched, False, error=str(exc)
                )
            )
            continue

        diff_text = bundle.unified_diff()
        (ctx.patch_dir / f"attempt-{index}.diff").write_text(diff_text, encoding="utf-8")
        if on_event is not None:
            await on_event(
                "repair.applied",
                {"attempt": index, "files": applied_files, "diagnosis": bundle.diagnosis},
            )

        retry = await retry_step()
        attempts.append(
            RepairAttempt(
                index,
                ctx.stage,
                bundle.diagnosis,
                diff_text,
                applied_files,
                True,
                error=None if retry.get("ok") else (retry.get("error") or "retry failed"),
                model=bundle.model,
            )
        )
        ctx.history = attempts
        if retry.get("ok"):
            return RepairOutcome(True, attempts, False, None)
        ctx.logs = retry.get("logs") or ctx.logs
        ctx.command = retry.get("command") or ctx.command
        if on_event is not None:
            await on_event("repair.failed", {"attempt": index, "error": retry.get("error")})
    return RepairOutcome(
        False,
        attempts,
        True,
        f"automatic repair gave up after {ctx.max_attempts} attempts; manual review required",
    )


def rollback_last_attempt(ctx: RepairContext, attempts: list[RepairAttempt]) -> bool:
    """Restore the workspace to the state before the most recent applied patch."""
    for attempt in reversed(attempts):
        if attempt.applied and not attempt.rolled_back:
            backup_root = ctx.patch_dir / f"backup-attempt-{attempt.attempt}"
            synthetic = PatchBundle(changes=[])
            from engine.ai_agent.patch import FileChange

            for rel in attempt.files:
                source_path = ctx.source_dir / rel
                old = source_path.read_text(encoding="utf-8", errors="replace") if source_path.exists() else ""
                synthetic.changes.append(FileChange(path=rel, old_content=old, new_content=old))
            rollback_patch(synthetic, ctx.workspace_dir, backup_root)
            attempt.rolled_back = True
            return True
    return False
