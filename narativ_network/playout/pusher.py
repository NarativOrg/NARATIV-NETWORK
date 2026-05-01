"""ffmpeg RTMP pusher.

We run a single ffmpeg process that:
  - reads the rolling concat list (`current_playlist.ffconcat`)
  - re-encodes once to broadcast spec (because contributor inputs vary)
  - pushes RTMP to the configured upstream endpoint

The pusher itself doesn't decide WHAT plays — that's the scheduler. It
just streams whatever the .ffconcat currently says, and is restarted by
the watchdog on failure.

For Phase 1 we re-encode rather than stream-copy. Stream-copy is faster
but assumes every archive file is already in matching codec/bitrate/keyframe
spec. The processor *does* normalize them, so a future optimization is
to switch to `-c copy` once we trust that pipeline.
"""
from __future__ import annotations

import logging
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ..config import Config, absolute_path

log = logging.getLogger(__name__)


def build_command(cfg: Config, output_override: list[str] | None = None) -> list[str]:
    """Build the ffmpeg playout command.

    `output_override`, if provided, replaces the default RTMP output args
    (e.g. ["-f", "mpegts", "/tmp/nn_test.ts"]). Used by `nn playout-test`
    so the chain runs without an RTMP destination.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found in PATH")
    playlist = absolute_path(cfg, "data/run_logs/current_playlist.ffconcat")
    rtmp_url = cfg.playout.rtmp_url.rstrip("/")
    if cfg.playout.stream_key:
        target = f"{rtmp_url}/{cfg.playout.stream_key}"
    else:
        target = rtmp_url

    gop = cfg.playout.fps * cfg.playout.keyframe_interval_sec

    # Master bus: catch-all compressor + true-peak limiter so nothing
    # ever leaves the channel above broadcast ceiling.
    from ..process.audio import master_bus_chain
    af = master_bus_chain((cfg.raw.get("audio") or {}).get("master_bus"))

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-re",
        "-fflags", "+genpts",
        "-f", "concat", "-safe", "0", "-stream_loop", "-1",
        "-i", str(playlist),
        "-vf",
        f"scale={cfg.playout.width}:{cfg.playout.height}:force_original_aspect_ratio=decrease,"
        f"pad={cfg.playout.width}:{cfg.playout.height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={cfg.playout.fps}",
    ]
    if af:
        cmd += ["-af", af]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-b:v", f"{cfg.playout.video_bitrate_kbps}k",
        "-maxrate", f"{cfg.playout.video_bitrate_kbps}k",
        "-bufsize", f"{cfg.playout.video_bitrate_kbps * 2}k",
        "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", f"{cfg.playout.audio_bitrate_kbps}k",
        "-ar", "48000", "-ac", "2",
    ]
    if output_override:
        cmd += output_override
    else:
        cmd += ["-f", "flv", target]
    return cmd


def run_forever(cfg: Config) -> None:
    """Run the ffmpeg pusher, restarting on death. Honors SIGTERM/SIGINT
    for clean shutdown when launchd or a person stops us.
    """
    stopping = {"flag": False}

    def _stop(signum, _frame):
        log.info("playout: signal %s — stopping", signum)
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    backoff = 1.0
    while not stopping["flag"]:
        cmd = build_command(cfg)
        log.info("playout: starting ffmpeg → %s", cfg.playout.rtmp_url)
        proc = subprocess.Popen(cmd)
        rc = None
        try:
            while not stopping["flag"]:
                rc = proc.poll()
                if rc is not None:
                    break
                time.sleep(1.0)
            if stopping["flag"] and rc is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return
        except KeyboardInterrupt:
            proc.terminate()
            return

        log.warning("playout: ffmpeg exited rc=%s; restart in %.1fs", rc, backoff)
        time.sleep(backoff)
        backoff = min(backoff * 2, 30.0)
