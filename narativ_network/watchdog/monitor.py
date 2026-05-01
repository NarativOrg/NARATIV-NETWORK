"""Watchdog.

Three independent checks run in a loop:

  1. PLAYOUT_PROCESS  — is `nn playout` (or its child ffmpeg) actually running?
                        If not, we don't restart from here (launchd / the
                        playout pusher's internal restart loop owns that).
                        We only ALERT.
  2. SILENCE          — sample the local audio of the active concat playlist
                        and complain if RMS dB stays below threshold for N s.
  3. BLACK            — same idea, mean luminance below threshold for N s.

Phase 1 keeps this passive — it logs and writes to `run_log` rather than
trying heroic recoveries. Active recovery (cut to slate, swap playlist)
goes in Phase 2.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import os
import signal as _signal

from ..config import Config, absolute_path
from ..db import connect

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_playout_alive() -> bool:
    """Crude: ask the OS if any ffmpeg process has the playlist file open.
    Real implementation on the Mac will inspect launchd job state. For now,
    grep `pgrep ffmpeg`.
    """
    try:
        out = subprocess.check_output(["pgrep", "-f", "current_playlist.ffconcat"],
                                      text=True, stderr=subprocess.DEVNULL)
        return bool(out.strip())
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return True  # pgrep missing — don't false-alarm


def _sample_av(playlist_path: Path, seconds: int) -> tuple[float | None, float | None]:
    """Return (rms_db, mean_luma) sampled over `seconds` of the live concat.
    Returns (None, None) if ffmpeg/the file aren't usable.
    """
    if not shutil.which("ffmpeg"):
        return (None, None)
    if not playlist_path.exists():
        return (None, None)
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-f", "concat", "-safe", "0", "-i", str(playlist_path),
            "-t", str(seconds),
            "-vf", "blackdetect=d=0.1:pix_th=0.10",
            "-af", "volumedetect",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=seconds + 30,
    )
    err = proc.stderr
    rms_db = None
    for line in err.splitlines():
        if "mean_volume" in line:
            try:
                rms_db = float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except Exception:
                pass
    # blackdetect emits lines like "blackdetect ... black_duration:X"
    luma_proxy = 0.0 if "blackdetect" in err else 1.0
    return (rms_db, luma_proxy)


def run_forever(cfg: Config) -> None:
    silence_streak = 0
    black_streak = 0
    interval = cfg.watchdog.check_interval_sec

    while True:
        try:
            playlist = absolute_path(cfg, "data/run_logs/current_playlist.ffconcat")
            alive = _is_playout_alive()
            rms_db, luma = _sample_av(playlist, seconds=2)

            if not alive:
                _record(cfg, "process_died", f"no ffmpeg holding {playlist.name}")

            if rms_db is not None and rms_db < -55.0:
                silence_streak += interval
                if silence_streak >= cfg.watchdog.silence_threshold_sec:
                    _record(cfg, "silence_detected", f"rms={rms_db:.1f}dB streak={silence_streak}s")
            else:
                silence_streak = 0

            if luma is not None and luma < 0.5:
                black_streak += interval
                if black_streak >= cfg.watchdog.black_threshold_sec:
                    _record(cfg, "black_detected", f"streak={black_streak}s")
            else:
                black_streak = 0

        except Exception:
            log.exception("watchdog tick failed")
        time.sleep(interval)


def _record(cfg: Config, status: str, detail: str) -> None:
    log.warning("watchdog: %s — %s", status, detail)
    conn = connect(cfg)
    try:
        conn.execute(
            "INSERT INTO run_log (started_at, status, detail) VALUES (?, ?, ?)",
            (_utcnow(), status, detail),
        )
    finally:
        conn.close()
