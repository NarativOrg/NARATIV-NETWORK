"""Transcription worker.

Picks up `episodes` rows in status='ready' that don't yet have a
transcript, runs Whisper, writes the transcript + populates the FTS
index.

Designed to run separately from the main process pipeline because it's
the slowest stage (4-8x realtime even on Apple Silicon, slower on a
busy machine). Keeping it independent means a backlog never blocks
the main process pipeline.

Idempotent: a second run on the same episode is a no-op.

Audio extraction:

We extract a 16 kHz mono WAV from the archive MP4 with ffmpeg, then
hand the WAV to Whisper. WAV is what whisper.cpp expects; it lets us
also crop very long files into ~30-min chunks if we ever need to (not
implemented yet — Whisper handles long files natively).
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config, absolute_path
from ..db import connect, transaction
from . import whisper as whisper_engine

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _extract_wav(src: Path, dest: Path) -> None:
    """Extract 16-kHz mono WAV via ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not in PATH")
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(src),
        "-ac", "1", "-ar", "16000", "-vn", "-c:a", "pcm_s16le",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg WAV extract failed:\n{proc.stderr[-1500:]}")


def _engine_choice(cfg_section: dict) -> str:
    explicit = (cfg_section.get("engine") or "auto").strip()
    if explicit != "auto":
        return explicit
    if shutil.which("whisper-cli") or shutil.which("whisper") or shutil.which("main"):
        return "whisper.cpp"
    return "openai-whisper"


def transcribe_one(cfg: Config, episode_id: int, force: bool = False) -> dict:
    """Run Whisper on one episode. Returns a status dict."""
    cfg_section = cfg.raw.get("transcripts", {}) or {}
    engine = _engine_choice(cfg_section)

    conn = connect(cfg)
    row = conn.execute(
        """SELECT episodes.*, shows.title AS show_title
           FROM episodes JOIN shows ON shows.id = episodes.show_id
           WHERE episodes.id = ?""",
        (episode_id,),
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "episode not found"}
    if row["status"] != "ready" or not row["archive_path"]:
        conn.close()
        return {"ok": False, "error": f"episode not ready (status={row['status']})"}
    existing = conn.execute(
        "SELECT id FROM transcripts WHERE episode_id=?", (episode_id,)
    ).fetchone()
    if existing and not force:
        conn.close()
        return {"ok": True, "skipped": True, "reason": "already transcribed"}

    archive = Path(row["archive_path"])
    if not archive.exists():
        conn.close()
        return {"ok": False, "error": f"archive missing: {archive}"}

    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / f"ep{episode_id}.wav"
            _extract_wav(archive, wav)

            if engine == "whisper.cpp":
                model_path = Path(cfg_section.get(
                    "model_path", "/opt/homebrew/share/whisper.cpp/ggml-medium.en.bin"
                )).expanduser()
                result = whisper_engine.transcribe_with_whisper_cpp(
                    wav, model_path,
                    language=cfg_section.get("language", "") or "",
                    threads=int(cfg_section.get("threads", 8)),
                    extra_args=cfg_section.get("extra_args") or [],
                )
                model_label = str(model_path.name)
            elif engine == "openai-whisper":
                model_label = cfg_section.get("model_name", "medium.en")
                result = whisper_engine.transcribe_with_openai_whisper(
                    wav, model_name=model_label,
                    language=cfg_section.get("language", "") or "",
                )
            else:
                raise RuntimeError(f"unknown transcripts.engine={engine!r}")

        full_text = result["full_text"]
        segments = result["segments"]
        word_count = len(full_text.split())

        with transaction(conn):
            if existing:
                conn.execute(
                    """UPDATE transcripts
                       SET language=?, duration_sec=?, full_text=?, segments_json=?,
                           word_count=?, model=?, engine=?, transcribed_at=?, failure=NULL
                       WHERE episode_id=?""",
                    (result["language"], row["duration_sec"], full_text,
                     json.dumps(segments), word_count, model_label, engine,
                     _utcnow(), episode_id),
                )
            else:
                conn.execute(
                    """INSERT INTO transcripts
                       (episode_id, language, duration_sec, full_text, segments_json,
                        word_count, model, engine, transcribed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (episode_id, result["language"], row["duration_sec"], full_text,
                     json.dumps(segments), word_count, model_label, engine, _utcnow()),
                )
            # Maintain FTS: delete any old row for this episode, insert fresh.
            conn.execute(
                "DELETE FROM transcripts_fts WHERE episode_id=?", (episode_id,)
            )
            conn.execute(
                """INSERT INTO transcripts_fts (full_text, episode_id, show_id, title)
                   VALUES (?, ?, ?, ?)""",
                (full_text, episode_id, row["show_id"], row["title"] or row["show_title"]),
            )

        elapsed = time.monotonic() - started
        log.info("transcribed ep=%s words=%d in %.1fs (%s)",
                 episode_id, word_count, elapsed, engine)
        conn.close()
        return {"ok": True, "episode_id": episode_id, "engine": engine,
                "words": word_count, "elapsed_sec": round(elapsed, 1)}

    except Exception as e:
        log.exception("transcribe failed for ep=%s", episode_id)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO transcripts
                   (episode_id, full_text, failure, engine, transcribed_at)
                   VALUES (?, '', ?, ?, ?)""",
                (episode_id, str(e)[:1000], engine, _utcnow()),
            )
        except Exception:
            pass
        conn.close()
        return {"ok": False, "error": str(e)}


def transcribe_all_pending(cfg: Config, limit: int = 50) -> dict:
    """Transcribe up to `limit` ready-but-untranscribed episodes."""
    conn = connect(cfg)
    rows = conn.execute(
        """SELECT episodes.id FROM episodes
           LEFT JOIN transcripts t ON t.episode_id = episodes.id
           WHERE episodes.status='ready' AND episodes.archive_path IS NOT NULL
             AND (t.id IS NULL OR (t.full_text='' AND t.failure IS NOT NULL))
           ORDER BY episodes.processed_at ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    results = {"ok": 0, "failed": 0, "skipped": 0, "ids": []}
    for r in rows:
        out = transcribe_one(cfg, r["id"])
        results["ids"].append({"id": r["id"], **{k: v for k, v in out.items() if k != "ok"}})
        if not out.get("ok"):
            results["failed"] += 1
        elif out.get("skipped"):
            results["skipped"] += 1
        else:
            results["ok"] += 1
    return results


def run_forever(cfg: Config) -> None:
    """Wake every minute, transcribe anything new."""
    interval = int((cfg.raw.get("transcripts", {}) or {}).get("watch_interval_sec", 60))
    while True:
        try:
            counters = transcribe_all_pending(cfg, limit=10)
            if counters["ok"] or counters["failed"]:
                log.info("transcripts pass: %s", counters)
        except Exception:
            log.exception("transcripts watch loop crashed; continuing")
        time.sleep(interval)
