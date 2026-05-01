"""Daily-plan renderer.

Resolves every slot of one calendar day in cfg.timezone, producing:

  data/daily_builds/YYYY-MM-DD/
      manifest.json    machine-readable plan, per-second start times
      manifest.csv     same data, CSV for spreadsheet eyeballing
      checklist.md     human upload checklist (filenames in order + cue times)
      files/           hard-linked copies of each archive file in air-order
                       (renamed `001__HHMMSS__show-slug__ep42.mp4` so they
                       sort correctly when uploaded in alphabetical batches)

Optionally (cfg.upstream.exact_slot_lengths=true) each linked clip is
re-rendered to its exact slot length: pad-with-black if short, trim if long.
That way a dumb sequential player hits slot boundaries to the second.

The schedule grid still drives this — same resolver, same fallback chain.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import Config, absolute_path
from ..db import connect
from ..schedule.resolver import resolve_slot

log = logging.getLogger(__name__)


@dataclass
class PlanEntry:
    seq: int
    air_local: str
    air_utc: str
    duration_sec: float
    show_title: str | None
    episode_title: str | None
    episode_id: int | None
    rule_used: str
    fallback_level: int
    source_path: str
    out_filename: str


@dataclass
class DailyPlan:
    air_date: str
    timezone: str
    entries: list[PlanEntry]
    out_dir: str
    total_seconds: float


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in (s or ""))[:80]


def build_daily_plan(cfg: Config, target_date: date | None = None,
                     exact_slot_lengths: bool | None = None) -> DailyPlan:
    """Render the full plan for one local-calendar day. Idempotent — safe
    to re-run; output dir is wiped and rebuilt.
    """
    tz = ZoneInfo(cfg.timezone)
    if target_date is None:
        target_date = (datetime.now(tz) + timedelta(days=1)).date()
    if exact_slot_lengths is None:
        exact_slot_lengths = bool(cfg.raw.get("upstream", {}).get("exact_slot_lengths", False))

    slot_minutes = cfg.schedule.slot_minutes
    day_start_local = datetime.combine(target_date, time(0, 0), tz)
    day_end_local = day_start_local + timedelta(days=1)

    out_dir = absolute_path(cfg, f"data/daily_builds/{target_date.isoformat()}")
    files_dir = out_dir / "files"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    files_dir.mkdir(parents=True)

    conn = connect(cfg)
    entries: list[PlanEntry] = []
    cursor = day_start_local
    seq = 0

    while cursor < day_end_local:
        dow = cursor.weekday()
        start_minute = cursor.hour * 60 + cursor.minute

        override = conn.execute(
            "SELECT * FROM slot_overrides WHERE air_date=? AND start_minute=?",
            (cursor.date().isoformat(), start_minute),
        ).fetchone()

        if override:
            slot = dict(override); slot["id"] = -1
        else:
            row = conn.execute(
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
            slot = dict(row) if row else {
                "id": None, "length_min": slot_minutes,
                "rule_type": "category_pool",
                "rule_payload": json.dumps({"tags": ["filler", "evergreen"]}),
            }

        resolution = resolve_slot(conn, slot, cursor,
                                  min_reair_age_days=cfg.schedule.fallback_reair_min_age_days)

        # Look up titles for the manifest.
        ep_title = None
        show_title = None
        if resolution.episode_id:
            row = conn.execute(
                """SELECT episodes.title AS et, shows.title AS st
                   FROM episodes LEFT JOIN shows ON shows.id=episodes.show_id
                   WHERE episodes.id=?""",
                (resolution.episode_id,),
            ).fetchone()
            if row:
                ep_title = row["et"]; show_title = row["st"]

        slot_seconds = slot["length_min"] * 60.0
        if resolution.archive_path:
            src_path = Path(resolution.archive_path)
            duration = min(resolution.duration_sec or slot_seconds, slot_seconds)
        else:
            src_path = absolute_path(cfg, cfg.schedule.slate_path)
            duration = slot_seconds

        seq += 1
        out_name = f"{seq:03d}__{cursor.strftime('%H%M%S')}__{_safe(show_title or 'slate')}__ep{resolution.episode_id or 0}.mp4"
        out_path = files_dir / out_name

        if exact_slot_lengths:
            _render_to_exact_length(src_path, out_path, slot_seconds, cfg)
            duration = slot_seconds
        else:
            _hardlink_or_copy(src_path, out_path)

        entries.append(PlanEntry(
            seq=seq,
            air_local=cursor.isoformat(timespec="seconds"),
            air_utc=cursor.astimezone(timezone.utc).isoformat(timespec="seconds"),
            duration_sec=round(duration, 3),
            show_title=show_title,
            episode_title=ep_title,
            episode_id=resolution.episode_id,
            rule_used=resolution.rule_used,
            fallback_level=resolution.fallback_level,
            source_path=str(src_path),
            out_filename=out_name,
        ))
        cursor += timedelta(minutes=slot["length_min"])

    conn.close()

    plan = DailyPlan(
        air_date=target_date.isoformat(),
        timezone=cfg.timezone,
        entries=entries,
        out_dir=str(out_dir),
        total_seconds=sum(e.duration_sec for e in entries),
    )

    _write_manifests(out_dir, plan)
    log.info("daily plan built: %s entries, %.1fh", len(entries), plan.total_seconds / 3600)
    return plan


def _hardlink_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except (OSError, NotImplementedError):
        shutil.copy2(src, dest)


def _render_to_exact_length(src: Path, dest: Path, target_sec: float, cfg: Config) -> None:
    """Re-render to exactly target_sec. Pads with black+silence if shorter,
    hard-cuts if longer. Same broadcast spec as the processor.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not in PATH (needed for exact_slot_lengths)")
    gop = cfg.playout.fps * cfg.playout.keyframe_interval_sec
    # tpad+apad pads short, then -t hard-caps the result.
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(src),
        "-vf",
        f"scale={cfg.playout.width}:{cfg.playout.height}:force_original_aspect_ratio=decrease,"
        f"pad={cfg.playout.width}:{cfg.playout.height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={cfg.playout.fps},"
        f"tpad=stop_mode=add:stop_duration={target_sec}",
        "-af", f"apad=whole_dur={target_sec}",
        "-t", f"{target_sec}",
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{cfg.playout.video_bitrate_kbps}k",
        "-maxrate", f"{cfg.playout.video_bitrate_kbps}k",
        "-bufsize", f"{cfg.playout.video_bitrate_kbps * 2}k",
        "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", f"{cfg.playout.audio_bitrate_kbps}k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"exact-length render failed:\n{proc.stderr[-2000:]}")


def _write_manifests(out_dir: Path, plan: DailyPlan) -> None:
    (out_dir / "manifest.json").write_text(
        json.dumps({
            "air_date": plan.air_date,
            "timezone": plan.timezone,
            "total_seconds": plan.total_seconds,
            "entries": [asdict(e) for e in plan.entries],
        }, indent=2)
    )
    with (out_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seq", "air_local", "duration_sec", "show", "episode",
                    "fallback_level", "out_filename", "rule_used"])
        for e in plan.entries:
            w.writerow([e.seq, e.air_local, e.duration_sec,
                        e.show_title or "", e.episode_title or "",
                        e.fallback_level, e.out_filename, e.rule_used])

    lines = [
        f"# Upload checklist — {plan.air_date} ({plan.timezone})",
        "",
        f"Total runtime: **{plan.total_seconds / 3600:.2f} h**  ",
        f"Entries: **{len(plan.entries)}**  ",
        f"Files: `{out_dir}/files/`",
        "",
        "## Steps",
        "",
        "1. Open upstream.so → your channel's library.",
        "2. Upload all files in `files/` (they sort by name → air order).",
        "3. Build a playlist using the order from `manifest.csv`.",
        "4. Set the playlist start time to **00:00 local** on the date below.",
        "5. Sanity-check the first three air times against the manifest.",
        "",
        "## Order",
        "",
        "| # | Air (local) | Duration | Show | Episode | File |",
        "|--:|:---|--:|:---|:---|:---|",
    ]
    for e in plan.entries:
        lines.append(
            f"| {e.seq} | {e.air_local[11:19]} | {e.duration_sec:.0f}s | "
            f"{e.show_title or ''} | {e.episode_title or '(slate)'} | `{e.out_filename}` |"
        )
    (out_dir / "checklist.md").write_text("\n".join(lines) + "\n")
