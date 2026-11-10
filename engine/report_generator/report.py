"""Experience report generation (Markdown, HTML, JSON).

Every claim in the report is traceable to an observation recorded during this run:
the deploy log, the HTTP health check, the Playwright steps and the captured
assets. Anything untested is marked UNVERIFIED and README marketing claims are
never promoted to verification results.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from engine.core.security import redact

STATUS_LABELS = {
    "VERIFIED": "VERIFIED",
    "PARTIALLY_VERIFIED": "PARTIALLY VERIFIED",
    "UNVERIFIED": "UNVERIFIED",
    "FAILED": "FAILED",
    "BLOCKED": "BLOCKED",
}


@dataclass
class ReportSection:
    heading: str
    body: str
    level: int = 2

    def to_markdown(self) -> str:
        return f"{'#' * self.level} {self.heading}\n\n{self.body}\n"


@dataclass
class ReportInput:
    project: dict
    repository: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    deploy: dict = field(default_factory=dict)
    features: list[dict] = field(default_factory=list)
    technical: dict = field(default_factory=dict)
    assets: list[dict] = field(default_factory=list)
    patches: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    exploration: dict = field(default_factory=dict)
    screenshot_refs: list[str] = field(default_factory=list)
    generated_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def overall_status(data: ReportInput) -> str:
    """Aggregate the verification status from concrete evidence only."""
    deploy_ok = bool(data.deploy.get("success"))
    feature_statuses = [f.get("status", "UNVERIFIED") for f in data.features]
    if not deploy_ok and not feature_statuses:
        blocked = data.deploy.get("blocked")
        return "BLOCKED" if blocked else "FAILED"
    if not deploy_ok:
        return "FAILED"
    if feature_statuses:
        if all(s == "VERIFIED" for s in feature_statuses):
            return "VERIFIED"
        if any(s in ("VERIFIED", "PARTIALLY_VERIFIED") for s in feature_statuses):
            return "PARTIALLY_VERIFIED"
        return "FAILED"
    return "PARTIALLY_VERIFIED"


def build_payload(data: ReportInput) -> dict:
    project = data.project or {}
    return {
        "generated_at": data.generated_at or _now(),
        "status": overall_status(data),
        "status_label": STATUS_LABELS.get(overall_status(data), "UNVERIFIED"),
        "project": {
            "name": project.get("name"),
            "display_name": project.get("display_name"),
            "description": project.get("description"),
            "source_url": project.get("source_url"),
            "stars": project.get("stars"),
            "license": project.get("license"),
            "language": project.get("language"),
            "author": project.get("author"),
        },
        "repository": data.repository,
        "deployment": data.deploy,
        "environment": data.environment,
        "features": data.features,
        "technical": data.technical,
        "assets": data.assets,
        "patches": data.patches,
        "usage": data.usage,
        "exploration": data.exploration,
        "verdict": build_verdict(data),
    }


def build_verdict(data: ReportInput) -> str:
    """A short, evidence-based assessment (never README copy)."""
    lines: list[str] = []
    deploy = data.deploy or {}
    if deploy.get("success"):
        port = deploy.get("port")
        status_code = deploy.get("http_status")
        if status_code is None:
            lines.append(
                f"- The project was started on `http://127.0.0.1:{port}`, but it did not "
                "answer an HTTP probe at report time, so no status code can be claimed."
            )
        else:
            lines.append(
                f"- The project really started: it answered HTTP health checks on "
                f"`http://127.0.0.1:{port}` (status {status_code})."
            )
        if (data.environment or {}).get("strategy") == "host":
            lines.append(
                "- It ran **without container isolation** on the host machine (unsandboxed opt-in mode)."
            )
        lines.append(f"- Real experience was recorded: {len(data.features)} interaction step(s) were executed.")
    elif deploy.get("blocked"):
        lines.append(f"- Not verified: {deploy.get('error') or 'blocked by environment requirements'}.")
    else:
        lines.append(
            f"- Deployment failed at stage `{deploy.get('stage')}`: "
            f"{(deploy.get('error') or 'unknown error')[:300]}."
        )
    verified = [f for f in data.features if f.get("status") == "VERIFIED"]
    failed = [f for f in data.features if f.get("status") == "FAILED"]
    if verified:
        names = ", ".join(f.get("name", "?") for f in verified[:8])
        lines.append(f"- Verified capabilities: {names}.")
    if failed:
        names = ", ".join(f.get("name", "?") for f in failed[:8])
        lines.append(f"- Capabilities that failed in practice: {names}.")
    assets = data.assets or []
    if assets:
        lines.append(
            f"- Evidence collected: {len([a for a in assets if a.get('kind') == 'screenshot'])} screenshot(s), "
            f"{len([a for a in assets if a.get('kind') == 'video'])} video(s), "
            f"{len([a for a in assets if a.get('kind') == 'gif'])} GIF(s)."
        )
    if data.patches:
        lines.append(f"- {len(data.patches)} AI patch(es) were applied; see the patch section for diffs.")
    lines.append(
        f"- README claims were not treated as evidence: only the observations above count toward the "
        f"`{STATUS_LABELS.get(overall_status(data), 'UNVERIFIED')}` verdict."
    )
    return "\n".join(lines)


def render_markdown(data: ReportInput, payload: dict | None = None) -> str:
    payload = payload or build_payload(data)
    p = payload["project"]
    out: list[str] = []
    out.append(f"# {p.get('display_name') or p.get('name') or 'Project'} — OPENLAB experience report\n")
    out.append(f"**Verification status:** `{payload['status_label']}`  ")
    out.append(f"**Generated:** {payload['generated_at']}  ")
    if p.get("source_url"):
        out.append(f"**Repository:** {p['source_url']}  ")
    if p.get("license"):
        out.append(f"**License:** {p['license']}  ")
    if p.get("stars") is not None:
        out.append(f"**Stars:** {p['stars']}  ")
    out.append("")

    out.append("## 1. Project basics\n")
    out.append(f"- **Name:** {p.get('name')}")
    if p.get("description"):
        out.append(f"- **Description (from the repository):** {_one_line(p['description'])}")
    out.append(f"- **Author:** {p.get('author') or 'unknown'}")
    out.append(f"- **Primary language:** {p.get('language') or 'unknown'}")
    stack = data.technical or {}
    if stack.get("framework"):
        out.append(f"- **Framework:** {stack['framework']}")
    out.append("")

    env = data.environment or {}
    deploy = data.deploy or {}
    out.append("## 2. Deployment\n")
    out.append(f"- **Result:** {'success' if deploy.get('success') else 'not successful'}")
    strategy = env.get("strategy") or deploy.get("strategy") or "unknown"
    out.append(f"- **Strategy:** {strategy}")
    if strategy == "host":
        out.append(
            "- **Isolation:** NONE — this project ran directly on the host machine because the "
            "unsandboxed host runtime was enabled and Docker was unavailable. Treat the result as "
            "an experience report about untrusted code that was allowed to run locally."
        )
    if env.get("base_image"):
        out.append(f"- **Base image:** {env['base_image']}")
    if env.get("package_manager"):
        out.append(f"- **Package manager:** {env['package_manager']}")
    if env.get("install_command"):
        out.append(f"- **Install command:** `{env['install_command']}`")
    if env.get("build_command"):
        out.append(f"- **Build command:** `{env['build_command']}`")
    if env.get("start_command"):
        out.append(f"- **Start command:** `{env['start_command']}`")
    if deploy.get("port"):
        out.append(f"- **Local URL:** http://127.0.0.1:{deploy['port']}")
    if deploy.get("health_url"):
        out.append(f"- **Health check URL:** {deploy['health_url']}")
    if deploy.get("duration_s") is not None:
        out.append(f"- **Deploy duration:** {deploy['duration_s']}s")
    env_vars = env.get("env_vars") or []
    if env_vars:
        out.append("\n### Environment variables\n")
        out.append("| Variable | Required | Configured | Purpose |")
        out.append("| --- | --- | --- | --- |")
        for var in env_vars:
            out.append(
                f"| `{var.get('name')}` | {'yes' if var.get('required') else 'no'} | "
                f"{'yes' if var.get('configured') else 'no'} | {_one_line(var.get('description') or '')} |"
            )
    if data.patches:
        out.append("\n### Applied patches\n")
        for patch in data.patches:
            out.append(
                f"- Attempt {patch.get('attempt')}: {_one_line(patch.get('diagnosis') or 'no diagnosis')} "
                f"(files: {', '.join(patch.get('files') or []) or 'n/a'})"
            )
    out.append("")

    out.append("## 3. Feature verification\n")
    if not data.features:
        out.append("_No feature was executed, so nothing is marked as verified._\n")
    for feature in data.features:
        out.append(f"### {feature.get('name') or 'Feature'} — {feature.get('status')}\n")
        if feature.get("description"):
            out.append(f"{feature['description']}\n")
        steps = feature.get("steps") or []
        if steps:
            out.append("| # | Action | Target | Result | URL |")
            out.append("| --- | --- | --- | --- | --- |")
            for step in steps:
                out.append(
                    f"| {step.get('index')} | {step.get('action')} | "
                    f"`{_one_line(str(step.get('target') or ''))}` | {step.get('status')} | "
                    f"{step.get('url') or ''} |"
                )
        shots = feature.get("screenshots") or []
        for shot in shots:
            out.append(f"\n![{feature.get('name')}]({shot})")
        if feature.get("video"):
            out.append(f"\n[video]({feature['video']})")
        if feature.get("failure_reason"):
            out.append(f"\n**Failure reason:** {feature['failure_reason']}\n")
        out.append("")

    out.append("## 4. Technical analysis\n")
    out.append(f"- **Languages:** {_stack_languages(stack)}")
    out.append(f"- **Services:** {', '.join(stack.get('services') or []) or 'none detected'}")
    out.append(f"- **Databases:** {', '.join(stack.get('databases') or []) or 'none detected'}")
    out.append(f"- **External APIs:** {', '.join(stack.get('external_apis') or []) or 'none detected'}")
    out.append(f"- **GPU required:** {'yes' if stack.get('gpu_required') else 'no'}")
    out.append(f"- **Has Dockerfile:** {'yes' if stack.get('has_dockerfile') else 'no'}")
    out.append(f"- **Has compose file:** {'yes' if stack.get('has_compose') else 'no'}")
    if stack.get("dependencies"):
        out.append(f"- **Key dependencies:** {', '.join(stack['dependencies'][:25])}")
    out.append("")

    out.append("## 5. Verdict\n")
    out.append(payload["verdict"] + "\n")

    out.append("## 6. Evidence\n")
    if not data.assets:
        out.append("_No screenshots or videos were captured._\n")
    for asset in data.assets:
        out.append(
            f"- `{asset.get('kind')}` — {asset.get('filename')} "
            f"({asset.get('width') or '?'}x{asset.get('height') or '?'}, {_human_bytes(asset.get('size_bytes') or 0)})"
        )
    out.append("")
    if data.usage:
        out.append("## 7. AI usage\n")
        out.append(f"- **Requests:** {data.usage.get('requests', 0)}")
        out.append(f"- **Tokens:** {data.usage.get('tokens', 0)}")
        out.append(
            f"- **Estimated cost:** ${data.usage.get('cost_usd', 0):.4f} "
            "(estimate — provider-reported usage only)"
        )
        if data.usage.get("records_with_unknown_usage"):
            out.append(
                f"- **Records without token usage:** {data.usage['records_with_unknown_usage']} (treated as unknown)"
            )
        out.append("")
    out.append("---\n")
    out.append(
        "_Generated by OPENLAB from real deployment logs, HTTP checks and Playwright observations. "
        "Repository marketing text is never used as a verification result._\n"
    )
    return redact("\n".join(out))


def render_html(data: ReportInput, payload: dict | None = None) -> str:
    payload = payload or build_payload(data)
    markdown = render_markdown(data, payload=payload)
    body = _markdown_to_html(markdown)
    assets_html = ""
    for asset in data.assets:
        if asset.get("kind") == "screenshot" and asset.get("relative_path"):
            assets_html += (
                f'<figure><img src="{html.escape(asset["relative_path"])}" '
                f'alt="{html.escape(asset.get("label") or asset.get("filename") or "")}" />'
                f'<figcaption>{html.escape(asset.get("filename") or "")}</figcaption></figure>'
            )
    status = payload["status"]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(payload['project'].get('name') or 'OPENLAB report')} — OPENLAB report</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0b0c0e; color: #e6e8eb; font: 15px/1.65 -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ max-width: 900px; margin: 0 auto; padding: 48px 24px 96px; }}
  h1 {{ font-size: 30px; letter-spacing: -0.02em; margin: 0 0 8px; }}
  h2 {{ font-size: 20px; margin: 40px 0 12px; border-bottom: 1px solid #1f2226; padding-bottom: 8px; }}
  h3 {{ font-size: 16px; margin: 24px 0 8px; }}
  code, pre {{ font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 13px; }}
  pre {{ background: #121418; border: 1px solid #1f2226; border-radius: 10px; padding: 14px; overflow: auto; }}
  code {{ background: #14161a; padding: 1px 5px; border-radius: 5px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13.5px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #1c1f24; vertical-align: top; }}
  th {{ color: #9aa4b2; font-weight: 600; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px;
           letter-spacing: .04em; text-transform: uppercase; font-weight: 600;
           border: 1px solid #2a2e34; background: #14161a; }}
  .badge.VERIFIED {{ color: #4ade80; border-color: #14532d; background: #0b1f13; }}
  .badge.PARTIALLY_VERIFIED {{ color: #fbbf24; border-color: #4a2f07; background: #1d1505; }}
  .badge.FAILED {{ color: #f87171; border-color: #4c1013; background: #1d0a0c; }}
  .badge.BLOCKED {{ color: #93c5fd; border-color: #16304f; background: #0a1622; }}
  .badge.UNVERIFIED {{ color: #9aa4b2; }}
  figure {{ margin: 18px 0; }}
  img {{ max-width: 100%; border-radius: 10px; border: 1px solid #1f2226; display: block; }}
  figcaption {{ color: #7d8794; font-size: 12.5px; margin-top: 6px; }}
  a {{ color: #7dd3fc; }}
  blockquote {{ border-left: 3px solid #2a2e34; margin: 12px 0; padding: 2px 16px; color: #a6b0bd; }}
  hr {{ border: 0; border-top: 1px solid #1f2226; margin: 32px 0; }}
</style>
</head>
<body>
<main>
<div class="badge {status}">{html.escape(payload['status_label'])}</div>
{body}
<h2>Captured screenshots</h2>
{assets_html or '<p><em>No screenshots captured.</em></p>'}
</main>
</body>
</html>
"""


def _markdown_to_html(markdown: str) -> str:
    """A deliberately small Markdown subset renderer (no external dependency)."""
    lines = markdown.splitlines()
    out: list[str] = []
    in_table = False
    in_list = False
    in_code = False
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                out.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        if not line.strip():
            if in_table:
                out.append("</table>")
                in_table = False
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue
            if not in_table:
                out.append("<table>")
                out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{min(level, 6)}>{_inline(line[level:].strip())}</h{min(level, 6)}>")
            continue
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("> "):
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
            continue
        if line.strip() in {"---", "***"}:
            out.append("<hr />")
            continue
        out.append(f"<p>{_inline(line)}</p>")
    if in_table:
        out.append("</table>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def _inline(text: str) -> str:
    import re

    escaped = html.escape(text)
    escaped = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img alt="\1" src="\2" />', escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _one_line(text: str, limit: int = 300) -> str:
    return " ".join(str(text or "").split())[:limit]


def _human_bytes(value: int) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}GB"


def _stack_languages(stack: dict) -> str:
    languages = stack.get("languages") or []
    if not languages:
        return stack.get("primary_language") or "unknown"
    return ", ".join(f"{item.get('language')} ({item.get('share')}%)" for item in languages[:5])


@dataclass
class WrittenReport:
    markdown: Path
    html: Path
    json_path: Path
    status: str
    title: str

    def as_dict(self) -> dict:
        return {
            "markdown_path": str(self.markdown),
            "html_path": str(self.html),
            "json_path": str(self.json_path),
            "status": self.status,
            "title": self.title,
        }


def write_reports(data: ReportInput, reports_dir: Path, basename: str = "report") -> WrittenReport:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    # One payload for all three artefacts. Recomputing it per format produced a
    # different generated_at / verdict in the .md, .html and .json of the same report.
    payload = build_payload(data)
    markdown_path = reports_dir / f"{basename}.md"
    html_path = reports_dir / f"{basename}.html"
    json_path = reports_dir / f"{basename}.json"
    markdown_path.write_text(render_markdown(data, payload=payload), encoding="utf-8")
    html_path.write_text(render_html(data, payload=payload), encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    title = f"{(data.project or {}).get('name') or 'project'} — OPENLAB report"
    return WrittenReport(
        markdown=markdown_path,
        html=html_path,
        json_path=json_path,
        status=payload["status"],
        title=title,
    )
