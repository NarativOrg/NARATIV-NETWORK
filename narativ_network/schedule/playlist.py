"""Rolling-playlist generator.

Every N minutes (cfg.schedule.regenerate_every_minutes) we compute
what should air over the next `rolling_horizon_hours` and emit:

  data/run_logs/current_playlist.ffconcat   ← what playout actually reads
  + rows in the `playlist` table             ← the audit log

Playout reads the .ffconcat continuously; ffmpeg's concat demuxer
handles boundaries between files cleanly.

If a slot's resolved duration is *shorter* than the slot, we tail-fill
from the network fallback pool (no dead air). If *longer*, we either
(a) cut to next slot at slot boundary, or (b) let it overrun and shift
the next slot — Phase 1 default is (a) for predictability. The actual
slot-end cut happens at playout time via ffmpeg `-t` if needed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Config, absolute_path
from ..db import connect, transaction
from .resolver import resolve_slot

log = logging.getLogger(__name__)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _slots_for_window(conn, start_utc: datetime, end_utc: datetime, slot_minutes: int):
    """Yield (slot_start_utc, slot_dict) pairs covering the window.

    Phase 1: timezone-naive in the sense that we treat the schedule grid
    as 'channel local time' = cfg.timezone, but emit UTC timestamps for the
    playlist. The conversion happens in `regenerate_rolling_playlist`.
    """
    pass  # implemented inline in regenerate_rolling_playlist


def regenerate_rolling_playlist(cfg: Config) -> dict:
    """Plan the next `rolling_horizon_hours` of air, write `.ffconcat`,
    and return counters.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(cfg.timezone)
    now_local = datetime.now(tz)
    horizon_end = now_local + timedelta(hours=cfg.schedule.rolling_horizon_hours)
    slot_minutes = cfg.schedule.slot_minutes

    # Snap "now" down to the nearest slot boundary, so the playlist starts cleanly.
    minutes_into_day = now_local.hour * 60 + now_local.minute
    slot_index = minutes_into_day // slot_minutes
    cursor_local = now_local.replace(
        hour=(slot_index * slot_minutes) // 60,
        minute=(slot_index * slot_minutes) % 60,
        second=0, microsecond=0,
    )

    conn = connect(cfg)
    counters = {"slots_resolved": 0, "fallback_l1": 0, "fallback_l2": 0,
                "fallback_l3": 0, "slate_used": 0, "stunt_episodes": 0}

    plan: list[dict] = []  # ordered list of {"file","duration","episode_id","scheduled_utc","slot_id","rule_used","fallback_level"}

    while cursor_local < horizon_end:
        dow = cursor_local.weekday()  # Mon=0..Sun=6
        start_minute = cursor_local.hour * 60 + cursor_local.minute

        # 1) Date-specific override beats recurring slot.
        override = conn.execute(
            "SELECT * FROM slot_overrides WHERE air_date=? AND start_minute=?",
            (cursor_local.date().isoformat(), start_minute),
        ).fetchone()
        slot = None
        if override:
            slot = dict(override)
            slot["id"] = -1  # synthetic; not a real slots row
            slot["length_min"] = override["length_min"]
        else:
            slot = conn.execute(
                """SELECT * FROM slots
                   WHERE enabled=1 AND start_minute=?
                     AND (
                       recurrence='daily'
                       OR (recurrence='weekdays' AND ? BETWEEN 0 AND 4)
                       OR (recurrence='weekends' AND ? BETWEEN 5 AND 6)
                       OR (recurrence='weekly' AND day_of_week=?)
                     )
                   ORDER BY id DESC LIMIT 1""",
                (start_minute, dow, dow, dow),
            ).fetchone()
            slot = dict(slot) if slot else None

        # 2) No slot defined → fallback chain anyway, but mark as filler-only.
        if not slot:
            slot = {
                "id": None, "length_min": slot_minutes,
                "rule_type": "category_pool",
                "rule_payload": json.dumps({"tags": ["filler", "evergreen"]}),
            }

        # 3) Resolve.
        air_dt = cursor_local
        resolution = resolve_slot(conn, slot, air_dt,
                                  min_reair_age_days=cfg.schedule.fallback_reair_min_age_days)
        if resolution.fallback_level == 1: counters["fallback_l1"] += 1
        if resolution.fallback_level == 2: counters["fallback_l2"] += 1
        if resolution.fallback_level == 3: counters["fallback_l3"] += 1
        if resolution.fallback_level == 4: counters["slate_used"] += 1

        slot_seconds = slot["length_min"] * 60
        scheduled_utc = _utc(cursor_local)

        is_live = (slot.get("rule_type") == "live_show"
                   and resolution.rule_used.startswith("LIVE:"))

        if resolution.episode_id and resolution.archive_path:
            plan.append({
                "file": resolution.archive_path,
                "duration_sec": min(resolution.duration_sec or slot_seconds, slot_seconds),
                "episode_id": resolution.episode_id,
                "scheduled_utc": scheduled_utc,
                "slot_id": slot.get("id"),
                "rule_used": resolution.rule_used,
                "fallback_level": resolution.fallback_level,
                "is_live": False,
            })
            counters["slots_resolved"] += 1
        else:
            # Either a true L4 slate, OR a live_show slot — both use the
            # slate as the underlying ffmpeg feed. Live cuts happen on top
            # of that via OBS scene switching (cue runner).
            slate = absolute_path(cfg, cfg.schedule.slate_path)
            plan.append({
                "file": str(slate),
                "duration_sec": slot_seconds,
                "episode_id": None,
                "scheduled_utc": scheduled_utc,
                "slot_id": slot.get("id"),
                "rule_used": resolution.rule_used if is_live else "L4:slate",
                "fallback_level": 0 if is_live else 4,
                "is_live": is_live,
                "live_show_id": (
                    json.loads(slot["rule_payload"] or "{}").get("show_id") if is_live else None
                ),
            })
            if is_live:
                counters.setdefault("live_slots", 0)
                counters["live_slots"] += 1

        # Advance cursor by the SLOT duration (not the file duration) — the
        # playout layer enforces the boundary with `-t` if the file overruns.
        cursor_local += timedelta(minutes=slot["length_min"])

    # Persist plan to DB and write the .ffconcat
    with transaction(conn):
        conn.execute("DELETE FROM playlist WHERE scheduled_at >= ? AND status='planned'",
                     (_utc(now_local),))
        # Also clear pending/armed live_sessions in the regen window — they'll
        # be re-created from the new plan. We never delete live or ended ones.
        conn.execute(
            "DELETE FROM live_sessions "
            "WHERE scheduled_at >= ? AND status IN ('pending','armed')",
            (_utc(now_local),),
        )
        for entry in plan:
            cur = conn.execute(
                """INSERT INTO playlist
                   (scheduled_at, slot_id, episode_id, rule_used, fallback_level,
                    duration_sec, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'planned')""",
                (entry["scheduled_utc"], entry["slot_id"], entry["episode_id"],
                 entry["rule_used"], entry["fallback_level"], entry["duration_sec"]),
            )
            playlist_id = cur.lastrowid
            if entry.get("is_live") and entry.get("live_show_id"):
                show_row = conn.execute(
                    "SELECT live_source_kind, live_default_url FROM shows WHERE id=?",
                    (entry["live_show_id"],),
                ).fetchone()
                kind = (show_row["live_source_kind"] if show_row else None) or "dynamic_pull"
                default_url = show_row["live_default_url"] if show_row else None
                conn.execute(
                    """INSERT INTO live_sessions
                       (playlist_id, show_id, scheduled_at, duration_sec,
                        source_url, source_kind, status,
                        armed_at, armed_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (playlist_id, entry["live_show_id"], entry["scheduled_utc"],
                     entry["duration_sec"], default_url, kind,
                     "armed" if default_url else "pending",
                     _utc(now_local) if default_url else None,
                     "system:default_url" if default_url else None),
                )

    out_path = absolute_path(cfg, "data/run_logs/current_playlist.ffconcat")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    for entry in plan:
        lines.append(f"file {_quote(entry['file'])}")
        lines.append(f"duration {entry['duration_sec']:.3f}")
    # ffconcat needs the last file repeated (no `duration`) so the demuxer
    # knows where to stop.
    if plan:
        lines.append(f"file {_quote(plan[-1]['file'])}")
    out_path.write_text("\n".join(lines) + "\n")

    counters["entries"] = len(plan)
    counters["written"] = str(out_path)
    conn.close()
    return counters


def _quote(path: str) -> str:
    # ffconcat path quoting: escape ' and wrap in single quotes.
    return "'" + str(path).replace("'", r"'\''") + "'"
