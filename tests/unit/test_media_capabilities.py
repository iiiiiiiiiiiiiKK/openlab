"""Unit tests for media capability detection and the GIF fallback ladder."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from engine.media_processor.ffmpeg import (
    FFmpegCapabilities,
    FFmpegUnavailable,
    MediaCapabilityError,
    MediaProcessor,
    MediaResult,
    find_ffmpeg,
)


def test_capabilities_flags_are_derived_from_real_lists() -> None:
    caps = FFmpegCapabilities(
        encoders={"png", "libvpx", "libx264", "gif"},
        muxers={"image2", "webm", "mp4"},
        filters={"scale", "palettegen", "paletteuse"},
    )
    assert caps.native_gif is True
    assert caps.png_frames is True
    assert caps.mp4 is True
    assert caps.webm is True
    assert caps.missing_for("gif") == []
    assert caps.missing_for("mp4") == []


def test_minimal_build_reports_what_is_missing() -> None:
    """The Playwright-bundled ffmpeg shape: PNG + VP8 only."""
    caps = FFmpegCapabilities(
        encoders={"png", "libvpx"},
        muxers={"image2", "webm"},
        filters={"scale"},
    )
    assert caps.native_gif is False
    assert caps.png_frames is True
    assert caps.mp4 is False
    assert caps.webm is True
    missing = caps.missing_for("mp4")
    assert any("H.264" in item for item in missing)
    assert any("mp4" in item for item in missing)
    assert caps.as_dict()["gif_strategy"] == "ffmpeg-frames+pillow"


def test_gif_unsupported_without_png_frames() -> None:
    caps = FFmpegCapabilities(encoders={"libvpx"}, muxers={"webm"}, filters={"scale"})
    assert caps.as_dict()["gif_strategy"] == "unsupported"
    assert caps.missing_for("gif")


def test_mp4_requires_both_encoder_and_muxer() -> None:
    only_encoder = FFmpegCapabilities(encoders={"libx264"}, muxers=set())
    assert only_encoder.mp4 is False
    only_muxer = FFmpegCapabilities(encoders=set(), muxers={"mp4"})
    assert only_muxer.mp4 is False


def test_media_result_tail() -> None:
    result = MediaResult(ok=False, detail="line1\nline2\nline3")
    assert result.tail(2) == "line2\nline3"
    assert result.tool == "ffmpeg"


def test_missing_binary_raises_unavailable(tmp_path: Path) -> None:
    processor = MediaProcessor(binary=None)
    processor.binary = None
    assert processor.available is False
    with pytest.raises(FFmpegUnavailable):
        asyncio.run(processor.to_gif(tmp_path / "a.webm", tmp_path / "a.gif"))
    with pytest.raises(FFmpegUnavailable):
        asyncio.run(processor.to_mp4(tmp_path / "a.webm", tmp_path / "a.mp4"))


def test_capability_error_names_the_missing_pieces(tmp_path: Path) -> None:
    processor = MediaProcessor(binary=None)
    # Force a minimal capability set and a fake binary path so no process is spawned.
    processor.binary = sys.executable
    processor._caps = FFmpegCapabilities(encoders={"png"}, muxers={"image2"}, filters={"scale"})
    with pytest.raises(MediaCapabilityError) as excinfo:
        asyncio.run(processor.to_mp4(tmp_path / "a.webm", tmp_path / "a.mp4"))
    message = str(excinfo.value)
    assert "H.264" in message
    assert "Install a full FFmpeg" in message


def test_gif_strategy_raises_when_nothing_is_supported(tmp_path: Path) -> None:
    processor = MediaProcessor(binary=None)
    processor.binary = sys.executable
    processor._caps = FFmpegCapabilities()
    with pytest.raises(MediaCapabilityError):
        asyncio.run(processor.to_gif(tmp_path / "a.webm", tmp_path / "a.gif"))


@pytest.mark.skipif(find_ffmpeg() is None, reason="no ffmpeg binary on this host")
def test_real_binary_capabilities_are_consistent() -> None:
    processor = MediaProcessor()
    caps = asyncio.run(processor.capabilities(refresh=True))
    assert caps.version_line
    # Whatever the build is, the derived properties must agree with the raw lists.
    assert caps.native_gif == (
        "gif" in caps.encoders and {"palettegen", "paletteuse"} <= caps.filters
    )
    assert caps.mp4 == (bool({"libx264", "h264", "libopenh264"} & caps.encoders) and "mp4" in caps.muxers)
    assert caps.as_dict()["gif_strategy"] in {"ffmpeg-palette", "ffmpeg-frames+pillow", "unsupported"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="a system ffmpeg is required")
def test_system_ffmpeg_can_make_a_gif(tmp_path: Path) -> None:
    """End-to-end GIF creation with a full ffmpeg build (skips on minimal builds)."""
    processor = MediaProcessor()
    caps = asyncio.run(processor.capabilities())
    if caps.as_dict()["gif_strategy"] == "unsupported":
        pytest.skip("this ffmpeg build cannot create GIFs at all")

    source = tmp_path / "src.mp4"
    # Generate a tiny 1s test clip using the same binary.
    generated = subprocess.run(
        [
            processor.binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    if generated.returncode != 0 or not source.exists():
        pytest.skip(f"could not synthesise a test clip: {generated.stderr[-200:]}")

    target = tmp_path / "out.gif"
    result = asyncio.run(processor.to_gif(source, target, duration_s=1.0, fps=6, width=160))
    assert result.ok, f"gif creation failed via {result.tool}: {result.tail(10)}"
    assert target.exists() and target.stat().st_size > 200
    assert target.read_bytes()[:6] in {b"GIF87a", b"GIF89a"}
