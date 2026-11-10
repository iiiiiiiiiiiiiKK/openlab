"""Playwright launcher: real Chromium sessions with honest availability checks."""

from __future__ import annotations

import json
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


def expected_video_ffmpeg() -> Path | None:
    """Where Playwright expects the ffmpeg build it records video with.

    The revision comes from the installed driver's browsers.json, so this follows the
    package rather than a hard-coded version.
    """
    try:
        import playwright
    except ImportError:
        return None
    driver_root = Path(playwright.__file__).resolve().parent / "driver" / "package"
    manifest = driver_root / "browsers.json"
    revision = None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for browser in data.get("browsers") or []:
            if str(browser.get("name")) == "ffmpeg":
                revision = str(browser.get("revision") or "")
                break
    except (OSError, ValueError):
        revision = None
    if not revision:
        return None
    root = browsers_root()
    candidates = [
        root / f"ffmpeg-{revision}" / "ffmpeg-win64.exe",
        root / f"ffmpeg-{revision}" / "ffmpeg-linux",
        root / f"ffmpeg-{revision}" / "ffmpeg-mac",
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def video_recording_available() -> tuple[bool, str | None]:
    """Playwright validates its ffmpeg build when a recorded page is created.

    When the recorded revision does not match the installed package it raises
    "Executable doesn't exist at .../ffmpeg-XXXX/ffmpeg-win64.exe" *and* the driver can
    drop the connection, which took the whole browsing session with it. Detecting it up
    front lets OPENLAB skip the recording and say why instead of failing.
    """
    expected = expected_video_ffmpeg()
    if expected is not None and expected.is_file():
        return True, None
    if expected is not None:
        return False, (
            f"Playwright video recording is unavailable: it expects {expected}, which is "
            "not installed. Run `python -m playwright install ffmpeg`."
        )
    return True, None


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


def _bundled_chromium() -> str | None:
    """Locate the Chromium binary Playwright would use for this environment."""
    root = browsers_root()
    candidates: list[Path] = []
    for folder in reversed(_chromium_dirs(root)):
        candidates.extend(
            [
                folder / "chrome-win64" / "chrome.exe",
                folder / "chrome-win" / "chrome.exe",
                folder / "chrome-linux64" / "chrome",
                folder / "chrome-linux" / "chrome",
                folder / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
            ]
        )
    for folder in sorted(root.glob("chromium_headless_shell-*"), reverse=True):
        candidates.extend(
            [
                folder / "chrome-headless-shell-win64" / "chrome-headless-shell.exe",
                folder / "chrome-win" / "headless_shell.exe",
                folder / "chrome-linux" / "headless_shell",
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _pick_executable(options: LaunchOptions) -> str | None:
    if options.executable_path:
        return options.executable_path
    system = _system_browsers()
    if options.channel and options.channel in system:
        return system[options.channel]
    # Any installed build is better than letting Playwright fail on a version it
    # expects but the machine does not have.
    return _bundled_chromium() or (next(iter(system.values())) if system else None)


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
        self.storage_state_missing: str | None = None
        self.video_unavailable_reason: str | None = None

    async def __aenter__(self) -> "BrowserSession":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise BrowserUnavailable(PLAYWRIGHT_HINT) from exc
        ensure_artifact_temp(self.options.artifact_temp)
        if self.options.record_video_dir:
            available, reason = video_recording_available()
            if not available:
                self.video_unavailable_reason = reason
                self.options.record_video_dir = None
                self.options.record_video_size = None
        self._playwright = await async_playwright().start()
        context_kwargs: dict = {**self.options.viewport.as_playwright}
        if self.options.storage_state:
            state_path = Path(self.options.storage_state)
            if state_path.exists():
                context_kwargs["storage_state"] = str(state_path)
            else:
                # Silently ignoring a missing session file made an authenticated run
                # execute logged out while still being reported as verified.
                self.storage_state_missing = str(state_path)
        if self.options.record_video_dir:
            Path(self.options.record_video_dir).mkdir(parents=True, exist_ok=True)
            context_kwargs["record_video_dir"] = str(self.options.record_video_dir)
            if self.options.record_video_size:
                context_kwargs["record_video_size"] = {
                    "width": self.options.record_video_size[0],
                    "height": self.options.record_video_size[1],
                }
        last_error: Exception | None = None
        try:
            for attempt_kwargs in self._attempt_kwargs():
                try:
                    self._browser = await self._playwright.chromium.launch(**attempt_kwargs)
                    break
                except Exception as exc:  # noqa: BLE001 - normalize playwright errors
                    last_error = exc
                    self._browser = None
            if self._browser is None:
                raise BrowserUnavailable(
                    f"could not launch Chromium: {last_error}\n{PLAYWRIGHT_HINT}"
                )
            self.context = await self._browser.new_context(**context_kwargs)
            self.context.set_default_timeout(20_000)
            try:
                self.page = await self.context.new_page()
            except Exception as exc:  # noqa: BLE001 - recording is optional evidence
                # Playwright validates the video pipeline when the first page is
                # created, and its recorded-ffmpeg build frequently does not match the
                # installed package version ("Executable doesn't exist at
                # .../ffmpeg-XXXX/ffmpeg-win64.exe"). Losing the recording is far better
                # than losing the whole exploration, so retry once without it.
                if "record_video_dir" not in context_kwargs:
                    raise
                self.video_unavailable_reason = str(exc).splitlines()[0][:300]
                try:
                    await self.context.close()
                except Exception:  # noqa: BLE001
                    pass
                context_kwargs.pop("record_video_dir", None)
                context_kwargs.pop("record_video_size", None)
                self.context = await self._browser.new_context(**context_kwargs)
                self.context.set_default_timeout(20_000)
                self.page = await self.context.new_page()
        except Exception:
            # __aexit__ is not called when __aenter__ raises, so every failed launch
            # (or a browser that started but could not open a context) leaked a
            # Chromium process and a Playwright driver for the rest of the session.
            await self.__aexit__(None, None, None)
            raise
        return self

    def _attempt_kwargs(self) -> list[dict]:
        """Launch attempts, most specific first, de-duplicated by executable.

        Every fallback must be a real binary: retrying with only a version change
        (no executable_path) just repeats the same "Executable doesn't exist" error.
        """
        primary = launch_kwargs(self.options)
        attempts: list[dict] = [primary]
        seen = {primary.get("executable_path") or ""}
        for path in _system_browsers().values():
            if path in seen:
                continue
            seen.add(path)
            fallback = dict(primary)
            fallback.pop("channel", None)
            fallback["executable_path"] = path
            attempts.append(fallback)
        if primary.get("headless") is False:
            headless_fallback = dict(primary)
            headless_fallback["headless"] = True
            attempts.append(headless_fallback)
        return attempts

    async def save_state(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.context is not None:
            await self.context.storage_state(path=str(path))
        return path

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Idempotent: __aenter__ calls this on failure and the caller calls it again
        # on the normal path, so every attribute is cleared as soon as it is closed.
        try:
            if self.context is not None:
                await self.context.close()
        except Exception:  # noqa: BLE001 - teardown must not mask the real error
            pass
        finally:
            self.context = None
            try:
                if self._browser is not None:
                    await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._browser = None
                try:
                    if self._playwright is not None:
                        await self._playwright.stop()
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    self._playwright = None
