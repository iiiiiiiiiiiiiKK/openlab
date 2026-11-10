"""Unified diff generation and safe patch application.

Because OPENLAB must work even when ``git`` is not installed, this module provides
its own unified-diff writer/applier. Patches are always produced against a copy of
the pristine ``source`` tree, applied to ``workspace``, and every file is backed up
before it is touched so a rollback is exact.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

MAX_PATCH_FILE_BYTES = 2 * 1024 * 1024

_TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".toml", ".txt", ".md",
    ".yml", ".yaml", ".ini", ".cfg", ".conf", ".env", ".sh", ".dockerfile", ".html", ".css",
    ".go", ".rs", ".rb", ".php", ".sql", ".xml", ".lock", ".example",
}


class PatchError(RuntimeError):
    """Raised when a patch cannot be applied cleanly."""


@dataclass
class FileChange:
    path: str  # repository-relative, POSIX separators
    old_content: str
    new_content: str
    reason: str = ""

    def as_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason, "bytes": len(self.new_content)}


@dataclass
class PatchBundle:
    changes: list[FileChange] = field(default_factory=list)
    diagnosis: str = ""
    model: str | None = None

    @property
    def files_touched(self) -> list[str]:
        return [c.path for c in self.changes]

    def unified_diff(self) -> str:
        chunks: list[str] = []
        for change in self.changes:
            old_lines = change.old_content.splitlines(keepends=True)
            new_lines = change.new_content.splitlines(keepends=True)
            diff = difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{change.path}",
                tofile=f"b/{change.path}",
                n=3,
                lineterm="\n",
            )
            header = f"# reason: {change.reason}\n" if change.reason else ""
            body = "".join(diff)
            if body and not body.endswith("\n"):
                body += "\n"
            chunks.append(header + body)
        return "".join(chunks)

    def as_dict(self) -> dict:
        return {
            "diagnosis": self.diagnosis,
            "model": self.model,
            "files": [c.as_dict() for c in self.changes],
            "diff": self.unified_diff(),
        }


def _is_text(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES or path.name.lower().startswith("dockerfile"):
        return True
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\x00" not in chunk


def safe_relpath(value: str) -> str:
    """Normalise a model-provided path and refuse anything that escapes the repo."""
    cleaned = (value or "").replace("\\", "/").strip()
    cleaned = re.sub(r"^[a-zA-Z]:", "", cleaned)
    cleaned = cleaned.lstrip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise PatchError(f"unsafe patch path: {value!r}")
    if cleaned.startswith("a/") or cleaned.startswith("b/"):
        parts = parts[1:]
    return "/".join(parts)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_changes(
    source_dir: Path, workspace_dir: Path, edits: list[dict]
) -> PatchBundle:
    """Convert ``{"path": ..., "content": ...}`` edits into a validated bundle."""
    source_dir = Path(source_dir)
    workspace_dir = Path(workspace_dir)
    changes: list[FileChange] = []
    for edit in edits or []:
        rel = safe_relpath(str(edit.get("path") or edit.get("file") or ""))
        if "content" not in edit:
            raise PatchError(f"edit for {rel} has no content")
        new_content = str(edit["content"])
        if len(new_content.encode("utf-8")) > MAX_PATCH_FILE_BYTES:
            raise PatchError(f"refusing to write {rel}: file exceeds the 2MB patch limit")
        source_path = source_dir / rel
        if source_path.exists():
            if not _is_text(source_path) and source_path.stat().st_size > 0:
                raise PatchError(f"refusing to patch binary file: {rel}")
            old_content = read_text(source_path)
        else:
            old_content = ""
        changes.append(
            FileChange(
                path=rel,
                old_content=old_content,
                new_content=new_content,
                reason=str(edit.get("reason") or ""),
            )
        )
    return PatchBundle(changes=changes)


def apply_patch(bundle: PatchBundle, workspace_dir: Path, backup_root: Path) -> list[str]:
    """Write every change into the workspace, backing up prior contents first.

    The write is all-or-nothing: if any file fails, every file already written in this
    call is restored from its backup before the error is raised. Without that, a
    half-applied bundle left the workspace in a state that no later attempt could
    reason about.
    """
    workspace_dir = Path(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    try:
        for change in bundle.changes:
            target = (workspace_dir / change.path).resolve()
            if workspace_dir.resolve() not in target.parents and target != workspace_dir.resolve():
                raise PatchError(f"{change.path} escapes the workspace")
            if target.exists():
                backup = backup_root / change.path
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            else:
                marker = backup_root / (change.path + ".absent")
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("", encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.new_content, encoding="utf-8")
            written.append(change.path)
    except Exception:
        rollback_patch(bundle, workspace_dir, backup_root)
        raise
    return written


def rollback_patch(bundle: PatchBundle, workspace_dir: Path, backup_root: Path) -> list[str]:
    """Restore the workspace to pre-patch state for every touched file."""
    workspace_dir = Path(workspace_dir)
    backup_root = Path(backup_root)
    restored: list[str] = []
    for change in bundle.changes:
        target = workspace_dir / change.path
        backup = backup_root / change.path
        absent = backup_root / (change.path + ".absent")
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
            restored.append(change.path)
        elif absent.exists():
            if target.exists():
                target.unlink()
            restored.append(change.path)
    return restored


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def apply_unified_diff(diff_text: str, root: Path) -> list[str]:
    """Apply a standard unified diff to ``root`` (used for user-supplied patches)."""
    root = Path(root)
    lines = diff_text.splitlines()
    index = 0
    current_file: str | None = None
    targets: dict[str, list[str]] = {}
    while index < len(lines):
        line = lines[index]
        if line.startswith("+++ "):
            raw = line[4:].strip()
            raw = raw.split("\t")[0]
            current_file = safe_relpath(raw[2:] if raw.startswith("b/") else raw)
            targets.setdefault(current_file, [])
        elif line.startswith("--- ") or line.startswith("#") or not line.strip():
            pass
        elif current_file and line.startswith("@"):
            match = _HUNK_RE.match(line)
            if not match:
                raise PatchError(f"malformed hunk header: {line}")
            old_start = int(match.group(1))
            index = _apply_hunk(lines, index + 1, root, current_file, old_start, targets[current_file])
            continue
        index += 1
    for rel, content in targets.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(content) + ("\n" if content else ""), encoding="utf-8")
    return sorted(targets)


def _apply_hunk(
    lines: list[str], start: int, root: Path, rel: str, old_start: int, existing: list[str]
) -> int:
    path = root / rel
    base = existing if existing else (read_text(path).splitlines() if path.exists() else [])
    position = max(0, old_start - 1)
    index = start
    out = list(base)
    cursor = position
    while index < len(lines):
        line = lines[index]
        if line.startswith("@@"):
            break
        if line.startswith("+++") or line.startswith("---"):
            break
        if not line:
            index += 1
            continue
        tag, body = line[0], line[1:]
        if tag == " " or tag == "":
            # A context line must match the file. The old code silently overwrote
            # whatever happened to be at that offset, so a hunk generated from a
            # different revision corrupted the file while the patch reported success.
            if cursor >= len(out):
                raise PatchError(f"patch context runs past the end of {rel} at line {cursor + 1}")
            if out[cursor] != body:
                raise PatchError(f"patch context mismatch at line {cursor + 1} of {rel}")
            cursor += 1
        elif tag == "-":
            if cursor < len(out) and out[cursor] == body:
                del out[cursor]
            else:
                raise PatchError(f"patch context mismatch at line {cursor + 1} of {rel}")
        elif tag == "+":
            out.insert(cursor, body)
            cursor += 1
        elif tag == "\\":
            pass
        else:
            break
        index += 1
    existing[:] = out
    return index


def summarize_changes(bundle: PatchBundle) -> str:
    rows = [f"{c.path} ({len(c.new_content)} bytes)" for c in bundle.changes]
    return json.dumps({"files": rows, "diagnosis": bundle.diagnosis}, ensure_ascii=False)
