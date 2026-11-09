"""FFmpeg discovery, capability detection and media transformations.

OPENLAB prefers a system ``ffmpeg`` and falls back to the binary bundled with
Playwright. That bundled build is deliberately minimal (it is only meant for
screen-recording), so this module **probes the real capabilities** of whichever
binary it found and picks an encoding strategy that binary can actually perform:

* GIF — native ``palettegen``/``paletteuse`` path when available, otherwise frame
  extraction (PNG) plus Pillow assembly. Never a silent failure.
* MP4 — requires an H.264 encoder and the ``mp4`` muxer. If they are missing the
  caller gets an explicit :class:`MediaCapabilityError` naming what to install
  instead of an empty file.
* WebM — VP8/VP9 plus the ``webm`` muxer, which the bundled build does have.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from engine.core.process import CommandResult, run_command, which


class FFmpegUnavailable(RuntimeError):
    """Raised when no ffmpeg binary can be located."""


class MediaCapabilityError(RuntimeError):
    """Raised when the available ffmpeg cannot perform the requested conversion."""


def find_ffmpeg() -> str | None:
    found = which("ffmpeg")
    if found:
        return found
    candidate_dirs = []
    env = __import__("os").environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        candidate_dirs.append(Path(env))
    candidate_dirs.append(Path.home() / "AppData" / "Local" / "ms-playwright")
    candidate_dirs.append(Path.home() / ".cache" / "ms-playwright")
    for root in candidate_dirs:
        if not root.exists():
            continue
        for binary in root.glob("ffmpeg-*/ffmpeg*"):
            if binary.is_file() and binary.suffix.lower() in {".exe", ""}:
                return str(binary)
    return None


@dataclass
class MediaInfo:
    path: Path
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    codec: str | None = None
    size_bytes: int = 0
    has_audio: bool = False
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "codec": self.codec,
            "size_bytes": self.size_bytes,
            "has_audio": self.has_audio,
        }


@dataclass
class MediaResult:
    """Outcome of a media operation, whether ffmpeg or a fallback performed it."""

    ok: bool
    tool: str = "ffmpeg"
    code: int = 0
    output: str = ""
    detail: str = ""

    @classmethod
    def from_command(cls, result: CommandResult, tool: str = "ffmpeg") -> "MediaResult":
        return cls(
            ok=result.ok,
            tool=tool,
            code=result.returncode,
            output=result.stdout + result.stderr,
            detail=result.tail(30),
        )

    def tail(self, lines: int = 20) -> str:
        text = self.detail or self.output
        return "\n".join(text.splitlines()[-lines:])


@dataclass
class FFmpegCapabilities:
    encoders: set[str] = field(default_factory=set)
    muxers: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)
    version_line: str = ""

    # -- feature checks --------------------------------------------------
    @property
    def gif_encoder(self) -> bool:
        return "gif" in self.encoders

    @property
    def palette_filters(self) -> bool:
        return {"palettegen", "paletteuse"} <= self.filters

    @property
    def native_gif(self) -> bool:
        return self.gif_encoder and self.palette_filters

    @property
    def png_frames(self) -> bool:
        return "png" in self.encoders and "image2" in self.muxers

    @property
    def h264(self) -> bool:
        return bool({"libx264", "h264", "libopenh264"} & self.encoders)

    @property
    def mp4_muxer(self) -> bool:
        return "mp4" in self.muxers

    @property
    def mp4(self) -> bool:
        return self.h264 and self.mp4_muxer

    @property
    def vp8(self) -> bool:
        return bool({"libvpx", "libvpx-vp9", "vp8", "vp9"} & self.encoders)

    @property
    def webm(self) -> bool:
        return self.vp8 and "webm" in self.muxers

    @property
    def scale_filter(self) -> bool:
        return "scale" in self.filters

    def missing_for(self, target: str) -> list[str]:
        if target == "gif":
            if self.native_gif:
                return []
            missing = []
            if not self.png_frames:
                missing.append("png encoder + image2 muxer (frame extraction fallback)")
            return missing
        if target == "mp4":
            missing = []
            if not self.h264:
                missing.append("an H.264 encoder (libx264)")
            if not self.mp4_muxer:
                missing.append("the mp4 muxer")
            return missing
        if target == "webm":
            missing = []
            if not self.vp8:
                missing.append("a VP8/VP9 encoder (libvpx)")
            if not self.webm:
                missing.append("the webm muxer")
            return missing
        return []

    def as_dict(self) -> dict:
        return {
            "version": self.version_line,
            "native_gif": self.native_gif,
            "png_frames": self.png_frames,
            "mp4": self.mp4,
            "webm": self.webm,
            "can_convert_mp4": self.mp4,
            "gif_strategy": "ffmpeg-palette"
            if self.native_gif
            else ("ffmpeg-frames+pillow" if self.png_frames else "unsupported"),
            "encoders": sorted(self.encoders),
            "filters": sorted(self.filters),
        }


def _parse_listing(text: str, kind: str = "encoders") -> set[str]:
    """Parse ``ffmpeg -encoders/-muxers/-filters`` output.

    The three listings use different column layouts:

    * ``encoders`` — one leading space, a 6-character flag column, then the name
      (``" VF...D png   PNG image"``),
    * ``muxers``   — a short flag column followed by two or more spaces (``"  E  image2"``),
    * ``filters``  — a 3-character flag column (``" ..C crop"``).

    Legend lines (``" V..... = Video"``) and separators are skipped explicitly, and a
    candidate that is not a bare identifier is ignored so no stray ``"="`` ever ends up
    in the capability set.
    """
    if kind == "encoders":
        pattern = re.compile(r"^\s*([A-Z.]{6})\s+(\S+)")
    elif kind == "muxers":
        pattern = re.compile(r"^\s*([A-Z.]{1,3})\s{2,}(\S+)")
    else:
        pattern = re.compile(r"^\s*([A-Z.]{3})\s+(\S+)")

    names: set[str] = set()
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        candidate = match.group(2)
        if candidate in {"=", "---", "------"}:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", candidate):
            continue
        names.add(candidate)
    return names


class MediaProcessor:
    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or find_ffmpeg()
        self._caps: FFmpegCapabilities | None = None

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def _require(self) -> str:
        if not self.binary:
            raise FFmpegUnavailable(
                "ffmpeg was not found. Install FFmpeg and add it to PATH, or install Playwright "
                "(`python -m playwright install`) which bundles a minimal ffmpeg build."
            )
        return self.binary

    async def capabilities(self, refresh: bool = False) -> FFmpegCapabilities:
        if self._caps is not None and not refresh:
            return self._caps
        caps = FFmpegCapabilities()
        if not self.available:
            self._caps = caps
            return caps
        binary = self._require()
        version = await run_command([binary, "-version"], timeout=30)
        caps.version_line = (version.stdout.splitlines() or [""])[0]
        for args, target in (
            (["-hide_banner", "-encoders"], "encoders"),
            (["-hide_banner", "-muxers"], "muxers"),
            (["-hide_banner", "-filters"], "filters"),
        ):
            result = await run_command([binary, *args], timeout=45)
            names = _parse_listing(result.stdout + result.stderr, kind=target)
            getattr(caps, target).update(names)
        self._caps = caps
        return caps

    async def version(self) -> str | None:
        if not self.available:
            return None
        caps = await self.capabilities()
        return caps.version_line or None

    async def probe(self, path: Path) -> MediaInfo:
        path = Path(path)
        info = MediaInfo(path=path)
        if path.exists():
            info.size_bytes = path.stat().st_size
        if not self.available:
            return info
        result = await run_command([self._require(), "-hide_banner", "-i", str(path)], timeout=60)
        text = (result.stderr or "") + (result.stdout or "")
        duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
        if duration:
            hours, minutes, seconds = duration.groups()
            info.duration_ms = int((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)
        video = re.search(r"Video:\s*([\w0-9]+).*?(\d{2,5})x(\d{2,5})", text)
        if video:
            info.codec = video.group(1)
            info.width = int(video.group(2))
            info.height = int(video.group(3))
        fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
        if fps:
            info.fps = float(fps.group(1))
        info.has_audio = bool(re.search(r"Audio:\s*[\w0-9]+", text))
        info.raw = {"head": text[:1500]}
        return info

    # -- video -----------------------------------------------------------
    async def to_mp4(
        self,
        source: Path,
        target: Path,
        max_width: int | None = None,
        crf: int = 26,
        on_line=None,
    ) -> MediaResult:
        self._require()
        caps = await self.capabilities()
        missing = caps.missing_for("mp4")
        if missing:
            raise MediaCapabilityError(
                "The available ffmpeg cannot encode MP4 (missing "
                + ", ".join(missing)
                + f"). It is the minimal Playwright build: {Path(self.binary or '').name}. "
                "Install a full FFmpeg build (winget install Gyan.FFmpeg, brew install ffmpeg) "
                "or export WebM instead."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        filters = ["-vf", f"scale='min({max_width},iw)':-2"] if max_width and caps.scale_filter else []
        args = [
            self._require(),
            "-y",
            "-i",
            str(source),
            *filters,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            str(target),
        ]
        return MediaResult.from_command(await run_command(args, timeout=1800, on_line=on_line))

    async def to_webm(self, source: Path, target: Path, crf: int = 34, on_line=None) -> MediaResult:
        self._require()
        caps = await self.capabilities()
        missing = caps.missing_for("webm")
        if missing:
            raise MediaCapabilityError(
                "The available ffmpeg cannot encode WebM (missing " + ", ".join(missing) + ")."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        encoder = "libvpx-vp9" if "libvpx-vp9" in caps.encoders else "libvpx"
        args = [
            self._require(),
            "-y",
            "-i",
            str(source),
            "-c:v",
            encoder,
            "-crf",
            str(crf),
            "-b:v",
            "0",
            "-an",
            str(target),
        ]
        return MediaResult.from_command(await run_command(args, timeout=1800, on_line=on_line))

    async def crop(
        self, source: Path, target: Path, width: int, height: int, x: int = 0, y: int = 0
    ) -> MediaResult:
        self._require()
        caps = await self.capabilities()
        if "crop" not in caps.filters:
            raise MediaCapabilityError("the available ffmpeg has no crop filter")
        target.parent.mkdir(parents=True, exist_ok=True)
        args = [
            self._require(),
            "-y",
            "-i",
            str(source),
            "-vf",
            f"crop={width}:{height}:{x}:{y}",
            "-c:a",
            "copy",
            str(target),
        ]
        return MediaResult.from_command(await run_command(args, timeout=900))

    async def trim(self, source: Path, target: Path, start_s: float, duration_s: float) -> MediaResult:
        target.parent.mkdir(parents=True, exist_ok=True)
        args = [
            self._require(),
            "-y",
            "-ss",
            f"{start_s:.3f}",
            "-t",
            f"{duration_s:.3f}",
            "-i",
            str(source),
            "-c",
            "copy",
            str(target),
        ]
        return MediaResult.from_command(await run_command(args, timeout=900))

    async def cover(self, source: Path, target: Path, at_s: float = 1.0, width: int = 1280) -> MediaResult:
        caps = await self.capabilities()
        target.parent.mkdir(parents=True, exist_ok=True)
        scale = ["-vf", f"scale={width}:-2"] if caps.scale_filter else []
        args = [
            self._require(),
            "-y",
            "-ss",
            f"{at_s:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            *scale,
            str(target),
        ]
        return MediaResult.from_command(await run_command(args, timeout=300))

    # -- GIF -------------------------------------------------------------
    async def to_gif(
        self,
        source: Path,
        target: Path,
        start_s: float = 0.0,
        duration_s: float = 6.0,
        fps: int = 12,
        width: int = 960,
        max_size_kb: int | None = 5000,
        on_line=None,
    ) -> MediaResult:
        """Render a GIF, shrinking it until it fits the size budget."""
        self._require()
        caps = await self.capabilities()
        if caps.native_gif:
            return await self._gif_native(
                source, target, start_s, duration_s, fps, width, max_size_kb, on_line
            )
        if caps.png_frames:
            return await self._gif_via_frames(
                source, target, start_s, duration_s, fps, width, max_size_kb
            )
        raise MediaCapabilityError(
            "The available ffmpeg cannot extract frames for GIF creation (needs a png encoder "
            "and the image2 muxer). Install a full FFmpeg build to export GIFs."
        )

    async def _gif_native(
        self,
        source: Path,
        target: Path,
        start_s: float,
        duration_s: float,
        fps: int,
        width: int,
        max_size_kb: int | None,
        on_line,
    ) -> MediaResult:
        target.parent.mkdir(parents=True, exist_ok=True)
        palette = target.with_suffix(".palette.png")
        attempts = [
            (fps, width),
            (max(8, fps - 3), int(width * 0.8)),
            (max(6, fps - 5), int(width * 0.6)),
            (max(5, fps - 7), int(width * 0.45)),
        ]
        last = MediaResult(ok=False, detail="gif encode failed")
        for attempt_fps, attempt_width in attempts:
            vf_base = f"fps={attempt_fps},scale={attempt_width}:-1:flags=lanczos"
            gen = await run_command(
                [
                    self._require(),
                    "-y",
                    "-ss",
                    f"{start_s:.3f}",
                    "-t",
                    f"{duration_s:.3f}",
                    "-i",
                    str(source),
                    "-vf",
                    f"{vf_base},palettegen=max_colors=192:stats_mode=diff",
                    str(palette),
                ],
                timeout=1200,
            )
            if not gen.ok:
                last = MediaResult.from_command(gen)
                continue
            use = await run_command(
                [
                    self._require(),
                    "-y",
                    "-ss",
                    f"{start_s:.3f}",
                    "-t",
                    f"{duration_s:.3f}",
                    "-i",
                    str(source),
                    "-i",
                    str(palette),
                    "-lavfi",
                    f"{vf_base} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4",
                    str(target),
                ],
                timeout=1200,
                on_line=on_line,
            )
            last = MediaResult.from_command(use)
            if not use.ok:
                continue
            if max_size_kb is None:
                break
            if target.exists() and target.stat().st_size <= max_size_kb * 1024:
                break
        if palette.exists():
            palette.unlink(missing_ok=True)
        last.tool = "ffmpeg-palette"
        return last

    async def _gif_via_frames(
        self,
        source: Path,
        target: Path,
        start_s: float,
        duration_s: float,
        fps: int,
        width: int,
        max_size_kb: int | None,
    ) -> MediaResult:
        """Extract PNG frames with ffmpeg, then assemble the GIF with Pillow."""
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - Pillow ships with the project
            raise MediaCapabilityError(
                "GIF creation needs Pillow because this ffmpeg build has no GIF encoder. "
                "Install it with: pip install Pillow"
            ) from exc

        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="openlab-gif-") as tmp:
            frame_dir = Path(tmp)
            args = [
                self._require(),
                "-y",
                "-ss",
                f"{start_s:.3f}",
                "-t",
                f"{duration_s:.3f}",
                "-i",
                str(source),
                "-vf",
                f"scale={width}:-2",
                "-r",
                str(max(1, fps)),
                str(frame_dir / "frame-%04d.png"),
            ]
            extract = await run_command(args, timeout=1200)
            frames = sorted(frame_dir.glob("frame-*.png"))
            if not extract.ok or not frames:
                result = MediaResult.from_command(extract)
                result.ok = False
                result.detail = (
                    "frame extraction failed: " + (result.tail(10) or "no frames were written")
                )
                result.tool = "ffmpeg-frames+pillow"
                return result

            delay_ms = max(20, int(1000 / max(1, fps)))
            frame_duration = max(2, round(delay_ms / 10))  # GIF stores 1/100 s units
            attempts = [(1.0, 128), (0.75, 96), (0.55, 64), (0.4, 48), (0.3, 32)]
            written = False
            for scale, colors in attempts:
                images = []
                for frame in frames:
                    with Image.open(frame) as image:
                        rgb = image.convert("RGB")
                        if scale < 1.0:
                            size = (max(2, int(rgb.width * scale)), max(2, int(rgb.height * scale)))
                            rgb = rgb.resize(size, Image.LANCZOS)
                        images.append(rgb.convert("P", palette=Image.ADAPTIVE, colors=colors))
                if not images:
                    break
                images[0].save(
                    target,
                    save_all=True,
                    append_images=images[1:],
                    duration=frame_duration * 10,
                    loop=0,
                    optimize=True,
                    disposal=2,
                )
                written = True
                if max_size_kb is None:
                    break
                if target.exists() and target.stat().st_size <= max_size_kb * 1024:
                    break
            if not written:
                return MediaResult(
                    ok=False,
                    tool="ffmpeg-frames+pillow",
                    detail="Pillow could not assemble the extracted frames",
                )
            size_kb = target.stat().st_size / 1024 if target.exists() else 0
            under = max_size_kb is None or size_kb <= max_size_kb
            return MediaResult(
                ok=target.exists() and target.stat().st_size > 0,
                tool="ffmpeg-frames+pillow",
                code=0 if under else 1,
                detail=(
                    f"assembled {len(frames)} frame(s) via ffmpeg PNG extraction + Pillow "
                    f"({size_kb:.0f}KB, target ≤{max_size_kb or 0}KB)"
                ),
            )

    async def concat(self, parts: list[Path], target: Path, workdir: Path) -> MediaResult:
        if not parts:
            raise ValueError("no clips to concatenate")
        list_file = workdir / "concat.txt"
        list_file.write_text("\n".join(f"file '{Path(p).as_posix()}'" for p in parts), encoding="utf-8")
        args = [
            self._require(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(target),
        ]
        return MediaResult.from_command(await run_command(args, timeout=1800))


def media_summary(path: Path) -> dict:
    """Cheap synchronous summary used by the asset listing endpoint."""
    info = {"filename": Path(path).name, "size_bytes": 0}
    try:
        info["size_bytes"] = Path(path).stat().st_size
    except OSError:
        pass
    return info


def bundled_ffmpeg() -> str | None:
    return find_ffmpeg()


def ffmpeg_dir() -> Path | None:
    binary = find_ffmpeg()
    return Path(binary).parent if binary else None


def system_ffmpeg_present() -> bool:
    return which("ffmpeg") is not None


def clear_cache() -> None:  # pragma: no cover - test helper
    shutil.rmtree(Path.home() / ".openlab-ffmpeg", ignore_errors=True)
