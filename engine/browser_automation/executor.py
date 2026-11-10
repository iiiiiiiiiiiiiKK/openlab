"""Executes exploration plans against the real running application."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from engine.browser_automation.capture import CaptureSpec, CaptureResult, capture
from engine.browser_automation.explorer import ExplorationPlanSpec, Step, is_risky
from engine.browser_automation.inspector import ConsoleCollector, PageSnapshot, inspect_page
from engine.browser_automation.launcher import BrowserSession, LaunchOptions, ViewportPreset, get_viewport


class StepAborted(RuntimeError):
    """Raised when a step cannot be completed safely."""


# Actions that produce evidence on their own: the page really answered, or the
# control really existed and accepted the interaction. Passive actions such as
# wait/scroll/screenshot stay UNVERIFIED because they prove nothing.
ASSERTING_ACTIONS = frozenset({"goto", "click", "fill", "press", "hover", "check", "select"})


@dataclass
class StepOutcome:
    index: int
    action: str
    target: str | None
    description: str
    status: str
    url: str
    title: str
    duration_ms: int
    observation: str = ""
    error: str | None = None
    http_status: int | None = None
    screenshot: CaptureResult | None = None
    console_errors: list[dict] = field(default_factory=list)
    network_errors: list[dict] = field(default_factory=list)
    features: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "action": self.action,
            "target": self.target,
            "description": self.description,
            "status": self.status,
            "url": self.url,
            "title": self.title,
            "duration_ms": self.duration_ms,
            "observation": self.observation,
            "error": self.error,
            "http_status": self.http_status,
            "screenshot": self.screenshot.as_dict() if self.screenshot else None,
            "console_errors": self.console_errors[:10],
            "network_errors": self.network_errors[:10],
            "features": self.features,
        }


@dataclass
class ExplorationReport:
    origin_url: str
    mode: str
    goal: str | None
    started_at: float
    finished_at: float = 0.0
    steps: list[StepOutcome] = field(default_factory=list)
    video_path: Path | None = None
    console_summary: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def ok_steps(self) -> int:
        return sum(1 for s in self.steps if s.status in ("VERIFIED", "PARTIALLY_VERIFIED"))

    @property
    def verified_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "VERIFIED")

    @property
    def failed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "FAILED")

    def status(self) -> str:
        """Aggregate status from real step evidence.

        A run is only VERIFIED when every step was itself VERIFIED against observed
        evidence. A plan made of waits and page visits that never answered cannot be
        promoted to VERIFIED just because nothing crashed.
        """
        if not self.steps:
            return "UNVERIFIED"
        if self.failed_steps == 0 and self.ok_steps == len(self.steps) and self.verified_steps:
            return "VERIFIED"
        if self.ok_steps > 0:
            return "PARTIALLY_VERIFIED"
        if self.failed_steps:
            return "FAILED"
        return "UNVERIFIED"

    def as_dict(self) -> dict:
        return {
            "origin_url": self.origin_url,
            "mode": self.mode,
            "goal": self.goal,
            "duration_ms": int((self.finished_at - self.started_at) * 1000),
            "status": self.status(),
            "steps": [s.as_dict() for s in self.steps],
            "video_path": str(self.video_path) if self.video_path else None,
            "console_summary": self.console_summary,
            "error": self.error,
        }


@dataclass
class ExecutorOptions:
    screenshot_dir: Path
    viewport: str = "desktop"
    record_video: bool = True
    video_dir: Path | None = None
    headless: bool = True
    storage_state: Path | None = None
    step_timeout_ms: int = 20_000
    slow_mo_ms: int = 0
    stop_on_error: bool = False

    def preset(self) -> ViewportPreset:
        return get_viewport(self.viewport)


async def execute_plan(
    plan: ExplorationPlanSpec,
    options: ExecutorOptions,
    origin_url: str | None = None,
    on_step=None,
) -> ExplorationReport:
    """Run every plan step in one browser session, capturing real artefacts."""
    origin = origin_url or plan.origin_url
    started = time.monotonic()
    report = ExplorationReport(origin_url=origin, mode=plan.mode, goal=plan.goal, started_at=started)
    preset = options.preset()
    launch = LaunchOptions(
        headless=options.headless,
        viewport=preset,
        storage_state=options.storage_state,
        slow_mo_ms=options.slow_mo_ms,
        record_video_dir=options.video_dir if options.record_video else None,
        record_video_size=(preset.width, preset.height) if options.record_video else None,
    )
    path = options.video_dir if options.record_video else None
    if path:
        Path(path).mkdir(parents=True, exist_ok=True)

    async with BrowserSession(launch) as session:
        page = session.page
        collector = ConsoleCollector()
        collector.attach(page)
        for step in plan.steps:
            # Console/network errors are attributed per step: remember how many were
            # already recorded so step N does not inherit the errors of step N-1.
            console_baseline = len(collector.messages)
            network_baseline = len(collector.network_errors)
            outcome = await _run_step(
                page=page,
                step=step,
                origin=origin,
                collector=collector,
                screenshot_dir=Path(options.screenshot_dir),
                viewport=options.viewport,
                timeout_ms=options.step_timeout_ms,
                console_baseline=console_baseline,
                network_baseline=network_baseline,
            )
            report.steps.append(outcome)
            if on_step is not None:
                await on_step(outcome)
            if outcome.status == "FAILED" and options.stop_on_error:
                break
        report.console_summary = collector.summary()
        video_file = page.video if path else None
    # The browser/context is now closed, so the recording is complete on disk.
    if video_file is not None:
        try:
            saved = await video_file.path()
            if saved:
                report.video_path = Path(saved)
        except Exception:  # noqa: BLE001 - recording is optional evidence
            pass
    report.finished_at = time.monotonic()
    return report


async def _run_step(
    page,
    step: Step,
    origin: str,
    collector: ConsoleCollector,
    screenshot_dir: Path,
    viewport: str,
    timeout_ms: int,
    console_baseline: int = 0,
    network_baseline: int = 0,
) -> StepOutcome:
    started = time.monotonic()
    url = page.url
    title = ""
    observation = ""
    error: str | None = None
    # A step starts UNVERIFIED. It only earns a positive status after this run has
    # observed evidence for it (an HTTP response in the 2xx/3xx range, a selector that
    # was actually found, or a real assertion). Anything else -- a bare wait, a scroll,
    # a screenshot -- cannot prove the feature works and must not claim that it does.
    status = "UNVERIFIED"
    http_status: int | None = None
    snapshot: PageSnapshot | None = None
    screenshot: CaptureResult | None = None
    try:
        if step.action == "goto":
            target = step.target or origin
            response = await page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
            if response is not None:
                http_status = response.status
                if http_status >= 400:
                    raise StepAborted(f"the page answered HTTP {http_status}")
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
            except Exception:  # noqa: BLE001 - SPA apps often never go idle
                pass
        elif step.action == "click":
            locator = page.locator(step.target or "").first
            await locator.wait_for(state="visible", timeout=timeout_ms)
            label = (await locator.inner_text() or "")[:200]
            if is_risky(label):
                raise StepAborted(f"skipped risky control: {label!r}")
            await locator.click(timeout=timeout_ms)
            await page.wait_for_timeout(step.wait_ms or 300)
        elif step.action == "fill":
            locator = page.locator(step.target or "").first
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.fill(step.value or "", timeout=timeout_ms)
        elif step.action == "press":
            await page.keyboard.press(step.value or "Enter")
        elif step.action == "scroll":
            await page.mouse.wheel(0, int(step.value or 600))
            await page.wait_for_timeout(250)
        elif step.action == "hover":
            await page.locator(step.target or "").first.hover(timeout=timeout_ms)
        elif step.action == "check":
            locator = page.locator(step.target or "").first
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.check(timeout=timeout_ms)
        elif step.action == "select":
            locator = page.locator(step.target or "").first
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.select_option(step.value or "", timeout=timeout_ms)
        elif step.action == "wait":
            await page.wait_for_timeout(step.wait_ms or int(step.value or 1000))
        elif step.action == "screenshot":
            pass
        else:
            raise StepAborted(f"unsupported action {step.action}")

        url = page.url
        title = await page.title()
        snapshot = await inspect_page(page)
        observation = f"{title or '(untitled)'} — {snapshot.text_length} chars of text"
        if http_status is not None:
            observation += f" (HTTP {http_status})"
        # Evidence rules: only actions that actually changed or proved something can
        # be VERIFIED. Passive steps stay UNVERIFIED by design.
        step_console_errors = collector.console_errors_since(console_baseline)
        if step.action in ASSERTING_ACTIONS:
            status = "PARTIALLY_VERIFIED" if step_console_errors else "VERIFIED"
        if step_console_errors:
            status = "PARTIALLY_VERIFIED" if status == "VERIFIED" else status
            observation += f"; {len(step_console_errors)} console error(s)"
        if step.capture:
            screenshot = await capture(
                page,
                CaptureSpec(
                    viewport=viewport,
                    full_page=True,
                    filename=f"step-{step.index:02d}-{_slug(step.action)}.png",
                ),
                screenshot_dir,
            )
    except StepAborted as exc:
        status = "FAILED"
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - browser errors are reported, not raised
        status = "FAILED"
        error = f"{type(exc).__name__}: {exc}"
    duration = int((time.monotonic() - started) * 1000)
    return StepOutcome(
        index=step.index,
        action=step.action,
        target=step.target,
        description=step.description,
        status=status,
        url=url,
        title=title,
        duration_ms=duration,
        observation=observation,
        error=error,
        http_status=http_status,
        screenshot=screenshot,
        console_errors=collector.console_errors_since(console_baseline)[-10:],
        network_errors=collector.network_errors[network_baseline:][-10:],
        features=snapshot.feature_hints() if snapshot else [],
    )


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "step"


async def open_preview(
    url: str,
    options: ExecutorOptions,
    screenshot_name: str = "preview.png",
) -> tuple[PageSnapshot, ConsoleCollector, CaptureResult, dict]:
    """Load ``url`` once and return the page facts plus a landing screenshot."""
    preset = options.preset()
    launch = LaunchOptions(
        headless=options.headless,
        viewport=preset,
        storage_state=options.storage_state,
    )
    async with BrowserSession(launch) as session:
        page = session.page
        collector = ConsoleCollector()
        collector.attach(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=options.step_timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:  # noqa: BLE001
            pass
        snapshot = await inspect_page(page)
        shot = await capture(
            page,
            CaptureSpec(viewport=options.viewport, full_page=True, filename=screenshot_name),
            options.screenshot_dir,
        )
        info = {"url": page.url, "title": await page.title(), "console": collector.summary()}
        return snapshot, collector, shot, info
