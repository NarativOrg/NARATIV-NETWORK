"""Thin ffmpeg/ffprobe wrappers.

We shell out rather than depend on a Python ffmpeg binding, so the Mac
mini just needs `brew install ffmpeg` and we work with whatever version
they have.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def require_binaries() -> dict[str, str]:
    found = {}
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        if not path:
            raise RuntimeError(f"{name} not found in PATH — install with `brew install ffmpeg`")
        found[name] = path
    return found


def probe_duration_sec(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    )
    return float(out.strip())


def probe_streams(path: Path) -> dict:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        text=True,
    )
    return json.loads(out)


_LOUDNORM_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def measure_loudness(path: Path, target_lufs: float = -23.0) -> dict:
    """Run loudnorm in measurement (pass 1) mode. Returns the JSON it prints."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-af", f"loudnorm=I={target_lufs}:TP=-2:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    # loudnorm prints JSON on stderr. Take the LAST JSON object.
    matches = _LOUDNORM_RE.findall(proc.stderr)
    if not matches:
        raise RuntimeError(f"loudnorm measure failed; stderr:\n{proc.stderr[-2000:]}")
    return json.loads(matches[-1])


def normalize_and_trim(
    src: Path,
    dest: Path,
    measured: dict,
    target_lufs: float = -23.0,
    silence_db: float = -40.0,
    silence_min_sec: float = 1.5,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    video_kbps: int = 6000,
    audio_kbps: int = 192,
    keyframe_sec: int = 2,
    audio_chain: str | None = None,
) -> None:
    """Pass 2: apply measured loudnorm + scale/pad + head/tail silence trim
    + (optional) a custom audio_chain (presets), output a broadcast-uniform
    MP4 (H.264 + AAC, faststart).

    If `audio_chain` is provided it REPLACES the default chain entirely
    (the caller is responsible for including loudnorm + silenceremove
    in the chain). If None, the legacy default chain is used.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    gop = fps * keyframe_sec

    if audio_chain:
        af = audio_chain
    else:
        af = (
            f"loudnorm=I={target_lufs}:TP=-2:LRA=11:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true:print_format=summary,"
            f"silenceremove=start_periods=1:start_silence={silence_min_sec}:start_threshold={silence_db}dB,"
            f"areverse,"
            f"silenceremove=start_periods=1:start_silence={silence_min_sec}:start_threshold={silence_db}dB,"
            f"areverse"
        )
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={fps}"
    )

    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(src),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "medium", "-tune", "film",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps*2}k",
        "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(dest),
    ]
    log.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg normalize failed (rc={proc.returncode}):\n{proc.stderr[-3000:]}")
