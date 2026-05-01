"""Live-cue runner.

Watches `live_sessions` rows. When the wall clock crosses a session's
`scheduled_at`, the runner:

  1. Picks the source URL (armed > show.live_default_url > fall back to slate).
  2. Updates OBS's LIVE scene Media Source URL via WebSocket.
  3. Switches OBS scene → LIVE.
  4. Stamps started_at, status='live'.

When the wall clock crosses scheduled_at + duration_sec:

  5. Switches OBS back to SCHEDULED.
  6. Stamps ended_at, status='ended'.

Two consecutive live slots = the runner cuts to LIVE for the first,
retargets the URL at the second's start time and stays on LIVE
seamlessly, then back to SCHEDULED at the end.

Tick interval is 1 s — that's the resolution of "to the second" cuts.

If a session has no source_url at start time, status='failed' and we
DO NOT cut to LIVE — viewers see the underlying slate. Safer than
dropping into a dead RTMP source. The Live Cue UI's GO NOW button
lets a producer arm the URL and force-cut after the fact.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from ..config import Config
from ..db import connect

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _stamp_run_log(conn, status: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO run_log (started_at, status, detail) VALUES (?, ?, ?)",
        (_iso(_utcnow()), status, detail),
    )


def tick_once(cfg: Config) -> dict:
    """One pass over live_sessions. Returns counters for logging."""
    from ..obs import OBSController  # lazy: don't crash when OBS is down

    conn = connect(cfg)
    now = _utcnow()
    counters = {"live_started": 0, "live_ended": 0, "live_failed": 0, "noop": 0}

    # ── start any pending/armed sessions whose time has come ──────────
    due = conn.execute(
        """SELECT * FROM live_sessions
           WHERE status IN ('pending','armed') AND scheduled_at <= ?""",
        (_iso(now),),
    ).fetchall()

    if due or _has_running_session_to_close(conn, now):
        try:
            obs = OBSController(cfg)
        except Exception as e:
            log.error("cue runner: OBS unreachable, sessions will fail: %s", e)
            obs = None

    for s in due:
        if obs is None:
            conn.execute(
                "UPDATE live_sessions SET status='failed', note=?, ended_at=? WHERE id=?",
                ("OBS unreachable at cut time", _iso(now), s["id"]),
            )
            counters["live_failed"] += 1
            _stamp_run_log(conn, "live_failed",
                           f"session {s['id']} show {s['show_id']}: OBS unreachable")
            continue

        url = s["source_url"]
        if not url:
            conn.execute(
                "UPDATE live_sessions SET status='failed', note=?, ended_at=? WHERE id=?",
                ("no source_url armed at cut time", _iso(now), s["id"]),
            )
            counters["live_failed"] += 1
            _stamp_run_log(conn, "live_failed",
                           f"session {s['id']} show {s['show_id']}: no URL armed")
            continue

        try:
            obs.client.set_input_settings(
                obs.live_input_name,
                {"is_local_file": False, "input": url, "restart_on_activate": True},
                overlay=True,
            )
            obs.to_live()
            conn.execute(
                "UPDATE live_sessions SET status='live', started_at=? WHERE id=?",
                (_iso(now), s["id"]),
            )
            counters["live_started"] += 1
            _stamp_run_log(conn, "preempted",
                           f"LIVE START session={s['id']} show={s['show_id']} url={url[:60]}")
        except Exception as e:
            log.exception("cue: failed to start live session %s", s["id"])
            conn.execute(
                "UPDATE live_sessions SET status='failed', note=?, ended_at=? WHERE id=?",
                (str(e)[:500], _iso(now), s["id"]),
            )
            counters["live_failed"] += 1

    # ── end any 'live' sessions whose time is up ──────────────────────
    running = conn.execute("SELECT * FROM live_sessions WHERE status='live'").fetchall()
    for s in running:
        scheduled = _parse(s["scheduled_at"])
        end = scheduled + timedelta(seconds=s["duration_sec"] or 0)
        if now >= end:
            try:
                if obs is None:
                    obs = OBSController(cfg)
                obs.to_scheduled()
                conn.execute(
                    "UPDATE live_sessions SET status='ended', ended_at=? WHERE id=?",
                    (_iso(now), s["id"]),
                )
                counters["live_ended"] += 1
                _stamp_run_log(conn, "ok",
                               f"LIVE END session={s['id']} show={s['show_id']}")
            except Exception as e:
                log.exception("cue: failed to end live session %s", s["id"])
                conn.execute(
                    "UPDATE live_sessions SET status='failed', note=?, ended_at=? WHERE id=?",
                    (f"end failed: {e}"[:500], _iso(now), s["id"]),
                )

    if not (due or running):
        counters["noop"] += 1
    conn.close()
    return counters


def _has_running_session_to_close(conn, now: datetime) -> bool:
    row = conn.execute("SELECT scheduled_at, duration_sec FROM live_sessions WHERE status='live'").fetchone()
    if not row:
        return False
    end = _parse(row["scheduled_at"]) + timedelta(seconds=row["duration_sec"] or 0)
    return now >= end


def run_forever(cfg: Config) -> None:
    log.info("cue runner: starting (1s tick)")
    while True:
        try:
            tick_once(cfg)
        except Exception:
            log.exception("cue runner tick failed; continuing")
        time.sleep(1.0)
