"""Playwright launcher: real Chromium sessions with honest availability checks."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PLAYWRIGHT_HINT = (
    "Playwright Chromium is required for preview, screenshots and exploration. "
    "Install it with: python -m playwright install chromium"
)


class BrowserUnavailable(RuntimeError):
    """Raised when no usable browser binary can be started."""


@dataclass
class ViewportPreset:
    name: str
    width: int
    height: int
    device_scale_factor: float = 1.0
    is_mobile: bool = False

    @property
    def as_playwright(self) -> dict:
        return {
            "viewport": {"width": self.width, "height": self.height},
            "device_scale_factor": self.device_scale_factor,
            "is_mobile": self.is_mobile,
            "has_touch": self.is_mobile,
        }


VIEWPORTS: dict[str, ViewportPreset] = {
    "desktop": ViewportPreset("desktop", 1440, 900),
    "desktop-1080": ViewportPreset("desktop-1080", 1920, 1080),
    "laptop": ViewportPreset("laptop", 1280, 800),
    "tablet": ViewportPreset("tablet", 834, 1112, 2.0, True),
    "mobile": ViewportPreset("mobile", 390, 844, 3.0, True),
    "square": ViewportPreset("square", 1080, 1080),
}


def get_viewport(name: str | None) -> ViewportPreset:
    return VIEWPORTS.get(name or "desktop", VIEWPORTS["desktop"])


def ensure_artifact_temp(preferred: Path | None = None) -> Path:
    """Make sure Playwright can create its artifact directory.

    Some sandboxed or locked-down Windows accounts cannot write to ``%TEMP%``.
    OPENLAB therefore prefers a temp directory inside its own data folder and only
    falls back to the system temp when that folder is writable.
    """
    import tempfile

    candidates: list[Path] = []
    if preferred is not None:
        candidates.append(Path(preferred))
    env_root = os.environ.get("OPENLAB_DATA_DIR")
    if env_root:
        candidates.append(Path(env_root) / "tmp")
    candidates.append(Path(tempfile.gettempdir()))

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".openlab-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        os.environ["TEMP"] = str(candidate)
        os.environ["TMP"] = str(candidate)
        os.environ["TMPDIR"] = str(candidate)
        return candidate
    return Path(tempfile.gettempdir())


def browsers_root() -> Path:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        return Path(env)
    return Path.home() / "AppData" / "Local" / "ms-playwright"


def _chromium_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir() and p.name.startswith("chromium-")])


def _system_browsers() -> dict[str, str]:
    candidates = {
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/usr/bin/microsoft-edge",
        ],
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
        ],
    }
    found: dict[str, str] = {}
    for name, paths in candidates.items():
        for path in paths:
            if Path(path).exists():
                found[name] = path
                break
    return found


def browser_inventory() -> dict:
    """Report which browsers can actually be launched, without launching them."""
    root = browsers_root()
    chromium_dirs = _chromium_dirs(root)
    system = _system_browsers()
    try:
        import playwright  # noqa: F401

        playwright_installed = True
    except ImportError:
        playwright_installed = False
    return {
        "playwright_package": playwright_installed,
        "browsers_path": str(root),
        "chromium_bundled": [p.name for p in chromium_dirs],
        "chromium_available": bool(chromium_dirs) or bool(system),
        "system_browsers": system,
        "hint": None if (chromium_dirs or system) else PLAYWRIGHT_HINT,
    }


@dataclass
class LaunchOptions:
    headless: bool = True
    viewport: ViewportPreset = field(default_factory=lambda: VIEWPORTS["desktop"])
    storage_state: Path | None = None
    channel: str | None = None
    slow_mo_ms: int = 0
    record_video_dir: Path | None = None
    record_video_size: tuple[int, int] | None = None
    executable_path: str | None = None
    extra_args: list[str] = field(default_factory=list)
    artifact_temp: Path | None = None


def _pick_executable(options: LaunchOptions) -> str | None:
    if options.executable_path:
        return options.executable_path
    system = _system_browsers()
    if options.channel and options.channel in system:
        return system[options.channel]
    return None


def launch_kwargs(options: LaunchOptions) -> dict:
    executable = _pick_executable(options)
    kwargs: dict = {
        "headless": options.headless,
        "args": [
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,BackForwardCache",
            *options.extra_args,
        ],
    }
    if executable:
        kwargs["executable_path"] = executable
    elif options.channel:
        kwargs["channel"] = options.channel
    if options.slow_mo_ms:
        kwargs["slow_mo"] = options.slow_mo_ms
    return kwargs


class BrowserSession:
    """Small async context manager around ``chromium.launch`` + context + page."""

    def __init__(self, options: LaunchOptions | None = None) -> None:
        self.options = options or LaunchOptions()
        self._playwright = None
        self._browser = None
        self.context = None
        self.page = None

    async def __aenter__(self) -> "BrowserSession":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise BrowserUnavailable(PLAYWRIGHT_HINT) from exc
        ensure_artifact_temp(self.options.artifact_temp)
        self._playwright = await async_playwright().start()
        context_kwargs: dict = {**self.options.viewport.as_playwright}
        if self.options.storage_state and Path(self.options.storage_state).exists():
            context_kwargs["storage_state"] = str(self.options.storage_state)
        if self.options.record_video_dir:
            Path(self.options.record_video_dir).mkdir(parents=True, exist_ok=True)
            context_kwargs["record_video_dir"] = str(self.options.record_video_dir)
            if self.options.record_video_size:
                context_kwargs["record_video_size"] = {
                    "width": self.options.record_video_size[0],
                    "height": self.options.record_video_size[1],
                }
        last_error: Exception | None = None
        for attempt_kwargs in self._attempt_kwargs():
            try:
                self._browser = await self._playwright.chromium.launch(**attempt_kwargs)
                break
            except Exception as exc:  # noqa: BLE001 - normalize playwright errors
                last_error = exc
                self._browser = None
        if self._browser is None:
            await self._playwright.stop()
            self._playwright = None
            raise BrowserUnavailable(
                f"could not launch Chromium: {last_error}\n{PLAYWRIGHT_HINT}"
            )
        self.context = await self._browser.new_context(**context_kwargs)
        self.context.set_default_timeout(20_000)
        self.page = await self.context.new_page()
        return self

    def _attempt_kwargs(self) -> list[dict]:
        primary = launch_kwargs(self.options)
        attempts = [primary]
        system = _system_browsers()
        if not _pick_executable(self.options):
            for path in system.values():
                fallback = dict(primary)
                fallback.pop("channel", None)
                fallback["executable_path"] = path
                attempts.append(fallback)
        headless_fallback = dict(primary)
        headless_fallback["headless"] = True
        if primary.get("headless") is False:
            attempts.append(headless_fallback)
        return attempts

    async def save_state(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.context is not None:
            await self.context.storage_state(path=str(path))
        return path

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self.context is not None:
                await self.context.close()
        finally:
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
