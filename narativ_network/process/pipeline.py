"""Processor pipeline.

Drives an episode through:

   fetched ──► (needs_descript?) ──yes──► descript_queued
                                          (manual export → descript_done/)
                                          ─────────┐
                                          ─────────│
                                                   ▼
                ◄──no──── descript_done ──► processing
                                              │
                                              ├─ ffmpeg measure loudness
                                              ├─ ffmpeg normalize + trim
                                              ├─ ffprobe duration
                                              ├─ compute ad-break marks
                                              ▼
                                            ready (in archive)

Errors set status=failed with `failure_reason`. The poller / scheduler
ignore non-ready episodes.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config, absolute_path
from ..db import connect
from . import ad_breaks, audio, ffmpeg

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in ".-_" else "_" for c in s)[:200]


def process_one(cfg: Config, episode_id: int) -> dict:
    """Process a single episode through whatever stage it currently needs.
    Idempotent — safe to call repeatedly. Returns a status dict.
    """
    conn = connect(cfg)
    row = conn.execute(
        """SELECT episodes.*, shows.slug AS show_slug, shows.ad_breaks_per_hour,
                  shows.audio_preset AS audio_preset
           FROM episodes JOIN shows ON shows.id = episodes.show_id
           WHERE episodes.id = ?""",
        (episode_id,),
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "episode not found"}

    status = row["status"]
    needs_descript = bool(row["needs_descript"])

    descript_queue = absolute_path(cfg, cfg.process.descript_queue_dir)
    descript_done = absolute_path(cfg, cfg.process.descript_done_dir)
    archive_dir = absolute_path(cfg, cfg.process.archive_dir)

    try:
        # Stage A: park needs_descript files in the queue and stop.
        if status == "fetched" and needs_descript:
            raw = Path(row["raw_path"])
            queued = descript_queue / f"ep{episode_id}__{_safe_name(raw.name)}"
            queued.parent.mkdir(parents=True, exist_ok=True)
            if not queued.exists():
                shutil.move(str(raw), str(queued))
            conn.execute(
                "UPDATE episodes SET status='descript_queued', raw_path=? WHERE id=?",
                (str(queued), episode_id),
            )
            conn.close()
            return {"ok": True, "stage": "descript_queued", "path": str(queued)}

        # Stage B: pick up Descript exports.
        # Convention: a file in descript_done/ whose name starts with `ep{id}__`
        # is treated as the export for that episode.
        if status == "descript_queued":
            export = next(
                (p for p in descript_done.glob(f"ep{episode_id}__*") if p.is_file()),
                None,
            )
            if not export:
                conn.close()
                return {"ok": True, "stage": "awaiting_descript_export"}
            conn.execute(
                "UPDATE episodes SET status='descript_done', raw_path=? WHERE id=?",
                (str(export), episode_id),
            )
            row = dict(row); row["status"] = "descript_done"; row["raw_path"] = str(export)

        # Stage C: normalize/trim/probe for both descript_done and (no-descript) fetched.
        if row["status"] in ("fetched", "descript_done"):
            conn.execute("UPDATE episodes SET status='processing' WHERE id=?", (episode_id,))
            ffmpeg.require_binaries()
            src = Path(row["raw_path"])
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest = archive_dir / f"{row['show_slug']}__ep{episode_id}.mp4"

            preset = audio.get_preset(row["audio_preset"])
            measured = ffmpeg.measure_loudness(src, target_lufs=preset.target_lufs)
            chain = audio.per_file_audio_chain(
                preset, measured,
                silence_db=cfg.process.silence_db,
                silence_min_sec=cfg.process.silence_min_sec,
            )
            ffmpeg.normalize_and_trim(
                src, dest, measured,
                target_lufs=preset.target_lufs,
                silence_db=cfg.process.silence_db,
                silence_min_sec=cfg.process.silence_min_sec,
                width=cfg.playout.width, height=cfg.playout.height, fps=cfg.playout.fps,
                video_kbps=cfg.playout.video_bitrate_kbps,
                audio_kbps=cfg.playout.audio_bitrate_kbps,
                keyframe_sec=cfg.playout.keyframe_interval_sec,
                audio_chain=chain,
            )
            log.info("processed ep=%s preset=%s target_lufs=%s",
                     episode_id, preset.name, preset.target_lufs)
            duration = ffmpeg.probe_duration_sec(dest)
            marks = ad_breaks.compute_break_marks(duration, row["ad_breaks_per_hour"])
            conn.execute(
                """UPDATE episodes
                   SET archive_path=?, duration_sec=?, ad_break_marks=?,
                       ad_break_count=?, status='ready', processed_at=?
                   WHERE id=?""",
                (str(dest), duration, json.dumps(marks), len(marks), _utcnow(), episode_id),
            )
            conn.close()
            return {"ok": True, "stage": "ready", "duration_sec": duration,
                    "ad_break_marks": marks, "archive_path": str(dest)}

        conn.close()
        return {"ok": True, "stage": status, "noop": True}

    except Exception as e:
        log.exception("process_one failed for episode_id=%s", episode_id)
        conn.execute(
            "UPDATE episodes SET status='failed', failure_reason=? WHERE id=?",
            (str(e)[:1000], episode_id),
        )
        conn.close()
        return {"ok": False, "error": str(e)}


def process_all_pending(cfg: Config) -> dict:
    """Drive every actionable episode forward by one stage."""
    conn = connect(cfg)
    rows = conn.execute(
        "SELECT id FROM episodes WHERE status IN ('fetched','descript_queued','descript_done')"
    ).fetchall()
    conn.close()

    counters = {"processed": 0, "failed": 0, "awaiting_descript": 0, "noop": 0}
    for r in rows:
        result = process_one(cfg, r["id"])
        if not result.get("ok"):
            counters["failed"] += 1
        elif result.get("stage") == "awaiting_descript_export":
            counters["awaiting_descript"] += 1
        elif result.get("noop"):
            counters["noop"] += 1
        else:
            counters["processed"] += 1
    return counters
