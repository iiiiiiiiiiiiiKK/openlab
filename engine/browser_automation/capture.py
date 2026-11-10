"""Screenshot capture with viewport presets, aspect ratios and full-page support."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from engine.browser_automation.launcher import VIEWPORTS, ViewportPreset, get_viewport

ASPECT_RATIOS = {
    "16:9": (16, 9),
    "4:3": (4, 3),
    "1:1": (1, 1),
    "3:4": (3, 4),
    "9:16": (9, 16),
    "2:1": (2, 1),
}


@dataclass
class ImageSize:
    width: int
    height: int


def parse_ratio(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    if value in ASPECT_RATIOS:
        return ASPECT_RATIOS[value]
    if "x" in value.lower():
        left, _, right = value.lower().partition("x")
        try:
            return int(left), int(right)
        except ValueError:
            return None
    if ":" in value:
        left, _, right = value.partition(":")
        try:
            return int(left), int(right)
        except ValueError:
            return None
    return None


def crop_to_ratio(size: ImageSize, ratio: tuple[int, int]) -> ImageSize:
    """Largest rectangle of the requested ratio that fits inside ``size``."""
    target = ratio[0] / ratio[1]
    current = size.width / size.height
    if current > target:
        width = int(size.height * target)
        return ImageSize(max(1, width), size.height)
    height = int(size.width / target)
    return ImageSize(size.width, max(1, height))


@dataclass
class CaptureSpec:
    viewport: str = "desktop"
    full_page: bool = True
    selector: str | None = None
    aspect_ratio: str | None = None
    custom_width: int | None = None
    custom_height: int | None = None
    filename: str = "screenshot.png"

    def viewport_preset(self) -> ViewportPreset:
        preset = get_viewport(self.viewport)
        if self.custom_width and self.custom_height:
            return ViewportPreset(
                name=f"custom-{self.custom_width}x{self.custom_height}",
                width=self.custom_width,
                height=self.custom_height,
            )
        return preset


@dataclass
class CaptureResult:
    path: Path
    width: int
    height: int
    size_bytes: int
    viewport: str
    aspect_ratio: str | None = None
    kind: str = "screenshot"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "viewport": self.viewport,
            "aspect_ratio": self.aspect_ratio,
            "kind": self.kind,
        }


async def capture(page, spec: CaptureSpec, target_dir: Path) -> CaptureResult:
    """Take a real screenshot and report the dimensions actually produced."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    preset = spec.viewport_preset()
    await page.set_viewport_size({"width": preset.width, "height": preset.height})
    await page.wait_for_timeout(250)

    path = target_dir / spec.filename
    raw: bytes
    # Screenshots wait for fonts and layout and can exceed the context default when the
    # machine is busy (a full test run opens several browsers). Give them room and retry
    # once instead of failing an otherwise good capture.
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            if spec.selector:
                locator = page.locator(spec.selector).first
                await locator.wait_for(state="visible", timeout=15_000)
                raw = await locator.screenshot(path=str(path), type="png", timeout=60_000)
            else:
                raw = await page.screenshot(
                    path=str(path), full_page=spec.full_page, type="png", timeout=60_000
                )
            break
        except Exception as exc:  # noqa: BLE001 - retried once, then surfaced
            last_error = exc
            if attempt == 1:
                raise
            await page.wait_for_timeout(500)
    else:  # pragma: no cover - defensive
        raise RuntimeError(f"screenshot failed: {last_error}")

    width, height = png_dimensions(raw)
    ratio = parse_ratio(spec.aspect_ratio)
    aspect_label = spec.aspect_ratio
    if ratio:
        box = crop_to_ratio(ImageSize(width, height), ratio)
        if box.width != width or box.height != height:
            cropped = crop_png(raw, box.width, box.height)
            if cropped is not None:
                path.write_bytes(cropped)
                raw = cropped
                width, height = png_dimensions(raw)
        aspect_label = spec.aspect_ratio
    return CaptureResult(
        path=path,
        width=width,
        height=height,
        size_bytes=len(raw),
        viewport=preset.name,
        aspect_ratio=aspect_label,
    )


def png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def crop_png(data: bytes, width: int, height: int) -> bytes | None:
    """Crop a PNG using Pillow when available (bundled with OPENLAB)."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow ships with the desktop bundle
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            cropped = image.crop((0, 0, min(width, image.width), min(height, image.height)))
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 - cropping must never break a capture
        return None


def available_viewports() -> list[dict]:
    return [
        {"name": v.name, "width": v.width, "height": v.height, "mobile": v.is_mobile}
        for v in VIEWPORTS.values()
    ]
