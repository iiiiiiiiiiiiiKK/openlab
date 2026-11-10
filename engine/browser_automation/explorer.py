"""Exploration planning: quick, deep and custom modes.

Plans are generated from the pages actually discovered in the running app. A model
is only consulted for the planning step of deep/default exploration and is always
validated: every step must reference a real URL or a selector observed on the page.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from engine.browser_automation.inspector import PageSnapshot

SAFE_ACTIONS = {"goto", "click", "fill", "press", "scroll", "screenshot", "wait", "hover", "check", "select"}

# Anything matching these is never clicked automatically.
# Latin terms use word boundaries; CJK terms are matched literally because \b is
# meaningless between CJK characters.
RISKY_LATIN = re.compile(
    r"(?i)\b("
    r"delete|remove|destroy|drop|purge|reset|terminate|cancel subscription|unsubscribe|"
    r"pay|purchase|buy|checkout|subscribe now|upgrade|billing|withdraw|transfer|"
    r"publish|deploy|send|submit order|confirm order|log ?out|sign ?out|revoke|"
    r"wipe|erase|format disk|shutdown|reboot"
    r")\b"
)
RISKY_CJK = re.compile(
    "解除|删除|購買|购买|支付|转账|轉帳|下单|下單|登出|注销|註銷|发布|發佈|发布内容|发送|發送|清空|重置"
)
RISKY_TEXT = RISKY_LATIN


class PlanRejected(ValueError):
    """Raised when a proposed plan contains unsafe or unverifiable steps."""


@dataclass
class Step:
    index: int
    action: str
    target: str | None = None
    value: str | None = None
    description: str = ""
    capture: bool = False
    wait_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "action": self.action,
            "target": self.target,
            "value": self.value,
            "description": self.description,
            "capture": self.capture,
            "wait_ms": self.wait_ms,
        }


@dataclass
class ExplorationPlanSpec:
    mode: str
    goal: str | None
    origin_url: str
    steps: list[Step] = field(default_factory=list)
    discovered_pages: list[dict] = field(default_factory=list)
    summary: str = ""
    generated_by: str = "heuristic"

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "goal": self.goal,
            "origin_url": self.origin_url,
            "summary": self.summary,
            "generated_by": self.generated_by,
            "steps": [s.as_dict() for s in self.steps],
            "discovered_pages": self.discovered_pages,
        }


def is_risky(label: str) -> bool:
    text = label or ""
    return bool(RISKY_LATIN.search(text) or RISKY_CJK.search(text))


def internal_pages(snapshot: PageSnapshot, origin: str) -> list[dict]:
    """Keep same-origin navigable links, deduplicated by path."""
    from urllib.parse import urljoin, urlparse

    base = urlparse(origin)
    seen: dict[str, dict] = {}
    for item in snapshot.nav:
        href = item.get("href") or ""
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(origin, href)
        parsed = urlparse(absolute)
        if parsed.netloc and parsed.netloc != base.netloc:
            continue
        key = parsed.path or "/"
        if key in seen:
            continue
        seen[key] = {"url": absolute, "path": key, "text": item.get("text") or key}
    return list(seen.values())[:20]


def quick_plan(snapshot: PageSnapshot, origin: str) -> ExplorationPlanSpec:
    """Home page + up to three main internal pages, one screenshot each."""
    steps: list[Step] = [
        Step(
            index=0,
            action="goto",
            target=origin,
            description=f"Open the landing page ({snapshot.title or 'home'})",
            capture=True,
        )
    ]
    pages = internal_pages(snapshot, origin)
    for page in pages[:3]:
        steps.append(
            Step(
                index=len(steps),
                action="goto",
                target=page["url"],
                description=f"Visit {page['text']}",
                capture=True,
            )
        )
    steps.append(
        Step(
            index=len(steps),
            action="goto",
            target=origin,
            description="Return to the landing page",
            capture=False,
        )
    )
    return ExplorationPlanSpec(
        mode="quick",
        goal="Identify the main screens and capture them",
        origin_url=origin,
        steps=steps,
        discovered_pages=pages,
        summary=(
            f"Quick exploration of {snapshot.title or origin}: landing page plus "
            f"{min(3, len(pages))} linked page(s)."
        ),
    )


def deep_plan(snapshot: PageSnapshot, origin: str) -> ExplorationPlanSpec:
    """Every discovered page plus the two safest buttons per page."""
    steps: list[Step] = [
        Step(index=0, action="goto", target=origin, description="Open the landing page", capture=True)
    ]
    for button in snapshot.buttons:
        if len([s for s in steps if s.action == "click"]) >= 2:
            break
        if is_risky(button.get("text", "")):
            continue
        steps.append(
            Step(
                index=len(steps),
                action="click",
                target=button.get("selector"),
                description=f"Trigger “{button.get('text')}” on the landing page",
                capture=True,
                wait_ms=800,
            )
        )
    pages = internal_pages(snapshot, origin)
    for page in pages[:8]:
        steps.append(
            Step(
                index=len(steps),
                action="goto",
                target=page["url"],
                description=f"Explore {page['text']}",
                capture=True,
            )
        )
    return ExplorationPlanSpec(
        mode="deep",
        goal="Walk every discovered page and exercise safe primary actions",
        origin_url=origin,
        steps=steps,
        discovered_pages=pages,
        summary=f"Deep exploration covering {len(pages)} linked page(s) and safe primary actions.",
    )


def custom_plan(snapshot: PageSnapshot, origin: str, goal: str) -> ExplorationPlanSpec:
    """Keyword-driven plan for a natural-language goal (no model required)."""
    keywords = [w for w in re.split(r"[\s,，、。;；]+", goal or "") if len(w) >= 2]
    steps: list[Step] = [
        Step(index=0, action="goto", target=origin, description=f"Open {origin} to start: {goal}", capture=True)
    ]
    candidates: list[tuple[int, dict]] = []
    for element in snapshot.nav + snapshot.buttons:
        text = (element.get("text") or "").lower()
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score and not is_risky(text):
            candidates.append((score, element))
    for _, element in sorted(candidates, key=lambda item: -item[0])[:6]:
        action = "click" if element.get("selector") and "href" not in element else "goto"
        target = element.get("selector") if action == "click" else element.get("href")
        if not target:
            continue
        steps.append(
            Step(
                index=len(steps),
                action=action,
                target=target,
                description=f"Follow “{element.get('text')}” (goal match)",
                capture=True,
                wait_ms=600,
            )
        )
    fill_candidates = [i for i in snapshot.inputs if i.get("type") in ("text", "textarea", "email", "search")]
    for input_el in fill_candidates[:2]:
        steps.append(
            Step(
                index=len(steps),
                action="fill",
                target=input_el.get("selector"),
                value="OPENLAB test input",
                description=f"Type sample text into {input_el.get('label') or input_el.get('name') or 'input'}",
                capture=False,
            )
        )
    return ExplorationPlanSpec(
        mode="custom",
        goal=goal,
        origin_url=origin,
        steps=steps,
        discovered_pages=[{"url": origin, "path": "/", "text": "home"}],
        summary=f"Custom exploration for goal: {goal}",
    )


PLAN_PROMPT_SYSTEM = """You are the OPENLAB exploration planner.
You are given the real DOM inventory of a running web application. Produce a
short, safe exploration plan that demonstrates the requested goal.

Rules:
- Only use actions: goto, click, fill, press, scroll, screenshot, wait, hover.
- Only use URLs or CSS selectors that appear in the inventory.
- Never plan to click anything that deletes data, pays money, sends email,
  publishes content, signs out or otherwise mutates real state destructively.
- Keep the plan under 14 steps and mark capture=true on steps worth screenshotting.
- Return JSON only: {"summary": string, "steps": [{"action": ..., "target": ..., "value": ..., "description": ..., "capture": bool}]}
"""


def plan_prompt(snapshot: PageSnapshot, mode: str, goal: str | None) -> str:
    payload = {
        "mode": mode,
        "goal": goal,
        "page": snapshot.as_dict(),
        "body_excerpt": snapshot.body_excerpt[:2000],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)[:20000]


def validate_model_plan(data: dict, snapshot: PageSnapshot, origin: str, mode: str, goal: str | None) -> ExplorationPlanSpec:
    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanRejected("model returned no steps")
    allowed_selectors = {
        item.get("selector") for item in (snapshot.nav + snapshot.buttons + snapshot.inputs) if item.get("selector")
    }
    steps: list[Step] = []
    for index, raw in enumerate(raw_steps[:20]):
        action = str(raw.get("action") or "").lower().strip()
        if action not in SAFE_ACTIONS:
            raise PlanRejected(f"unsupported action: {action}")
        target = raw.get("target")
        description = str(raw.get("description") or "")[:300]
        if is_risky(description) or (action == "click" and is_risky(str(target))):
            raise PlanRejected("plan contains a risky interaction")
        if action == "goto" and target:
            from urllib.parse import urlparse

            if urlparse(str(target)).netloc not in ("", urlparse(origin).netloc):
                raise PlanRejected("plan navigates outside the project origin")
        if action in ("click", "fill") and target and str(target) not in allowed_selectors:
            # Allow it only when the selector clearly still exists on the page.
            raise PlanRejected(f"selector not present on the page: {target}")
        steps.append(
            Step(
                index=index,
                action=action,
                target=str(target) if target else None,
                value=str(raw.get("value")) if raw.get("value") is not None else None,
                description=description,
                capture=bool(raw.get("capture")),
                wait_ms=int(raw.get("wait_ms") or 0),
            )
        )
    if not steps or steps[0].action != "goto":
        steps.insert(0, Step(index=0, action="goto", target=origin, description="Open the landing page", capture=True))
        for position, step in enumerate(steps):
            step.index = position
    return ExplorationPlanSpec(
        mode=mode,
        goal=goal,
        origin_url=origin,
        steps=steps,
        discovered_pages=internal_pages(snapshot, origin),
        summary=str(data.get("summary") or "")[:500],
        generated_by="model",
    )


def heuristic_plan(snapshot: PageSnapshot, origin: str, mode: str, goal: str | None) -> ExplorationPlanSpec:
    if mode == "deep":
        return deep_plan(snapshot, origin)
    if mode == "custom" and goal:
        return custom_plan(snapshot, origin, goal)
    return quick_plan(snapshot, origin)
