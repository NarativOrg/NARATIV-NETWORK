"""External-playback monitor.

Since we no longer run a local playout, the watchdog can't pgrep ffmpeg.
Instead it samples whatever destination upstream.so is fanning out to —
typically a YouTube live URL — and confirms the channel is actually on air.

Phase 1: passive logging. Phase 2: alerting.

Two checks, both configurable in [upstream]:

  youtube_channel_url   — if set, we fetch the page and look for a
                          "live" indicator. (Cheap heuristic; replace
                          with the YouTube Data API if rate limits bite.)
  upstream_status_url   — if set, GET it and treat HTTP 200 as healthy.

If neither is set, the monitor just logs that it has nothing to watch.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import Config
from ..db import connect

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record(cfg: Config, status: str, detail: str) -> None:
    log.warning("upstream-monitor: %s — %s", status, detail)
    conn = connect(cfg)
    try:
        conn.execute(
            "INSERT INTO run_log (started_at, status, detail) VALUES (?, ?, ?)",
            (_utcnow(), status, detail),
        )
    finally:
        conn.close()


def _check_youtube_live(url: str, client: httpx.Client) -> Optional[bool]:
    try:
        r = client.get(url, timeout=10, follow_redirects=True)
        if r.status_code != 200:
            return False
        body = r.text
        # Cheap: YouTube live pages contain "isLive\":true" in the JSON blob.
        if '"isLive":true' in body or '"isLiveContent":true' in body:
            return True
        if "watch?v=" in body and "LIVE" in body:
            return True
        return False
    except Exception as e:
        log.debug("youtube check failed: %s", e)
        return None


def _check_status_url(url: str, client: httpx.Client) -> Optional[bool]:
    try:
        r = client.get(url, timeout=10)
        return r.status_code == 200
    except Exception:
        return None


def run_forever(cfg: Config) -> None:
    upstream_cfg = cfg.raw.get("upstream", {})
    yt_url = upstream_cfg.get("youtube_channel_url") or ""
    status_url = upstream_cfg.get("upstream_status_url") or ""
    interval = int(upstream_cfg.get("monitor_interval_sec", 60))
    miss_threshold = int(upstream_cfg.get("miss_threshold", 3))

    if not yt_url and not status_url:
        log.info("upstream-monitor: nothing to watch (no youtube_channel_url or upstream_status_url)")
        return

    miss_streak = 0
    with httpx.Client(headers={"User-Agent": "narativ-network/1.0"}) as client:
        while True:
            yt = _check_youtube_live(yt_url, client) if yt_url else None
            st = _check_status_url(status_url, client) if status_url else None

            healthy = True
            details = []
            if yt_url:
                healthy = healthy and bool(yt)
                details.append(f"yt_live={yt}")
            if status_url:
                healthy = healthy and bool(st)
                details.append(f"status_url={st}")

            if healthy:
                miss_streak = 0
            else:
                miss_streak += 1
                if miss_streak >= miss_threshold:
                    _record(cfg, "external_air_lost",
                            f"misses={miss_streak} {' '.join(details)}")
            time.sleep(interval)
