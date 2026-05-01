"""Polling worker.

Iterates every enabled source on its configured cadence, finds new
external IDs, downloads them into the inbox, inserts an episodes row
in `pending` status. The processor pipeline takes it from there.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config, absolute_path
from ..db import connect
from .source import build_source

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def poll_once(cfg: Config) -> dict:
    """One pass over every enabled source. Returns counters."""
    conn = connect(cfg)
    inbox = absolute_path(cfg, cfg.ingest.inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)

    counters = {"checked": 0, "new": 0, "errors": 0}

    sources = conn.execute(
        "SELECT id, show_id, kind, config, poll_minutes, last_polled "
        "FROM sources WHERE enabled = 1"
    ).fetchall()

    now = datetime.now(timezone.utc)

    for src in sources:
        if src["last_polled"]:
            try:
                last = datetime.fromisoformat(src["last_polled"])
                age_min = (now - last).total_seconds() / 60.0
                if age_min < src["poll_minutes"]:
                    continue
            except ValueError:
                pass

        counters["checked"] += 1
        config = json.loads(src["config"])
        # Inject global gdrive SA path if a per-source one isn't set.
        if src["kind"] == "gdrive" and not config.get("service_account_json"):
            config["service_account_json"] = cfg.ingest.gdrive_service_account_json

        try:
            source = build_source(src["kind"], config)
        except Exception as e:
            log.exception("source build failed for source_id=%s", src["id"])
            conn.execute(
                "UPDATE sources SET last_error=?, last_polled=? WHERE id=?",
                (f"build: {e}", _utcnow(), src["id"]),
            )
            counters["errors"] += 1
            continue

        try:
            for file in source.list_available():
                exists = conn.execute(
                    "SELECT 1 FROM episodes WHERE source_id=? AND external_id=?",
                    (src["id"], file.external_id),
                ).fetchone()
                if exists:
                    continue
                dest = inbox / f"src{src['id']}__{_safe(file.suggested_filename)}"
                source.download(file, dest)
                conn.execute(
                    """INSERT INTO episodes
                       (show_id, source_id, external_id, title, raw_path, status,
                        needs_descript, fetched_at)
                       SELECT ?, ?, ?, ?, ?, 'fetched', shows.needs_descript, ?
                       FROM shows WHERE shows.id = ?""",
                    (
                        src["show_id"], src["id"], file.external_id, file.title,
                        str(dest), _utcnow(), src["show_id"],
                    ),
                )
                counters["new"] += 1
                log.info("ingested external_id=%s show_id=%s -> %s",
                         file.external_id, src["show_id"], dest)
            conn.execute(
                "UPDATE sources SET last_polled=?, last_error=NULL WHERE id=?",
                (_utcnow(), src["id"]),
            )
        except Exception as e:
            log.exception("poll failed for source_id=%s", src["id"])
            conn.execute(
                "UPDATE sources SET last_polled=?, last_error=? WHERE id=?",
                (_utcnow(), str(e)[:500], src["id"]),
            )
            counters["errors"] += 1

    conn.close()
    return counters


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)[:200]


def run_forever(cfg: Config) -> None:
    while True:
        try:
            counters = poll_once(cfg)
            log.info("poll pass %s", counters)
        except Exception:
            log.exception("poll_once crashed; sleeping then retrying")
        time.sleep(cfg.ingest.poll_seconds)
