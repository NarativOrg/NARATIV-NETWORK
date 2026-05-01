"""Daily orchestrator.

Runs forever; each day at cfg.upstream.daily_build_at_local (default 23:00),
builds tomorrow's plan and runs the uploader. Lightweight scheduler — we
just sleep until the next trigger.

We also drive an event into run_log so the dashboard shows the daily build
landing.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import Config
from ..db import connect
from .daily_build import build_daily_plan
from .uploader import build_uploader

log = logging.getLogger(__name__)


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _next_trigger(now_local: datetime, hh: int, mm: int) -> datetime:
    candidate = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate


def run_once(cfg: Config, target: date | None = None) -> dict:
    """Build + upload one day's plan. Returns the uploader status."""
    plan = build_daily_plan(cfg, target_date=target)
    uploader = build_uploader(cfg)
    status = uploader.upload(plan)

    conn = connect(cfg)
    try:
        conn.execute(
            "INSERT INTO run_log (started_at, status, detail) VALUES (?, ?, ?)",
            (datetime.utcnow().isoformat(timespec="seconds"),
             "daily_build_uploaded",
             f"date={plan.air_date} mode={uploader.mode} entries={len(plan.entries)} "
             f"runtime={plan.total_seconds/3600:.2f}h"),
        )
    finally:
        conn.close()
    return status


def run_forever(cfg: Config) -> None:
    upstream_cfg = cfg.raw.get("upstream", {})
    hh, mm = _parse_hhmm(upstream_cfg.get("daily_build_at_local", "23:00"))
    tz = ZoneInfo(cfg.timezone)

    while True:
        now = datetime.now(tz)
        trigger = _next_trigger(now, hh, mm)
        sleep_for = (trigger - now).total_seconds()
        log.info("orchestrator: next daily build at %s (%.0fs)", trigger, sleep_for)
        # Sleep in 60s chunks so we wake reasonably from clock changes / DST.
        while sleep_for > 0:
            time.sleep(min(60, sleep_for))
            sleep_for = (trigger - datetime.now(tz)).total_seconds()
        try:
            target = (datetime.now(tz) + timedelta(days=1)).date()
            log.info("orchestrator: building plan for %s", target)
            run_once(cfg, target=target)
        except Exception:
            log.exception("orchestrator: build/upload failed; will retry tomorrow")
