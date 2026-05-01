"""Slot resolver — picks the actual episode to play for a given slot+date.

The fallback chain when a slot's primary rule produces nothing:

    L0  primary rule succeeded
    L1  most-recent re-air for the same show (older than `min_age_days`)
    L2  any episode tagged 'evergreen' for the same show
    L3  any episode in the network fallback_pool, lowest priority first
    L4  None — caller fills with the slate

The resolver records the level it hit so the admin UI can flag persistent
no-shows. It does NOT mark `last_aired_at` — that's the playout/run_log's
job, since "scheduled" and "actually played" are different things.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class Resolution:
    episode_id: int | None
    rule_used: str
    fallback_level: int
    archive_path: str | None
    duration_sec: float | None


def _row_episode(row) -> dict:
    return {
        "id": row["id"],
        "archive_path": row["archive_path"],
        "duration_sec": row["duration_sec"],
    }


def resolve_slot(
    conn: sqlite3.Connection,
    slot: dict,
    air_date: datetime,
    min_reair_age_days: int = 7,
) -> Resolution:
    """Resolve a planned slot to a concrete episode."""
    payload = json.loads(slot["rule_payload"] or "{}")
    rule_type = slot["rule_type"]

    # live_show is special: no episode resolution; the cue runner takes over
    # at slot start. Return early with a sentinel (archive_path=None tells
    # the playlist generator to substitute the slate as the under-feed).
    if rule_type == "live_show":
        live = resolve_live_slot(conn, slot, air_date)
        if live:
            return live
        # Fall through to fallback chain if the live show is misconfigured.

    # ── L0: primary rule ───────────────────────────────────────────────
    primary = _resolve_primary(conn, rule_type, payload, air_date)
    if primary:
        return Resolution(
            episode_id=primary["id"],
            rule_used=f"L0:{rule_type}",
            fallback_level=0,
            archive_path=primary["archive_path"],
            duration_sec=primary["duration_sec"],
        )

    # Determine which show (if any) we were trying to fill for
    show_id = payload.get("show_id")

    # ── L1: most-recent re-air for that show ───────────────────────────
    if show_id:
        cutoff = (air_date - timedelta(days=min_reair_age_days)).isoformat()
        row = conn.execute(
            """SELECT id, archive_path, duration_sec FROM episodes
               WHERE show_id=? AND status='ready'
                 AND (last_aired_at IS NULL OR last_aired_at < ?)
               ORDER BY COALESCE(last_aired_at, '1970-01-01') ASC, id DESC
               LIMIT 1""",
            (show_id, cutoff),
        ).fetchone()
        if row:
            return Resolution(row["id"], "L1:reair_show", 1,
                              row["archive_path"], row["duration_sec"])

    # ── L2: show-tagged evergreen ─────────────────────────────────────
    if show_id:
        row = conn.execute(
            """SELECT id, archive_path, duration_sec FROM episodes
               WHERE show_id=? AND status='ready'
                 AND tags LIKE '%"evergreen"%'
               ORDER BY COALESCE(last_aired_at, '1970-01-01') ASC LIMIT 1""",
            (show_id,),
        ).fetchone()
        if row:
            return Resolution(row["id"], "L2:show_evergreen", 2,
                              row["archive_path"], row["duration_sec"])

    # ── L3: network fallback pool ─────────────────────────────────────
    row = conn.execute(
        """SELECT episodes.id, episodes.archive_path, episodes.duration_sec
           FROM fallback_pool
           JOIN episodes ON episodes.id = fallback_pool.episode_id
           WHERE episodes.status='ready'
           ORDER BY fallback_pool.priority ASC,
                    COALESCE(episodes.last_aired_at, '1970-01-01') ASC
           LIMIT 1"""
    ).fetchone()
    if row:
        return Resolution(row["id"], "L3:network_fallback", 3,
                          row["archive_path"], row["duration_sec"])

    # ── L4: nothing — caller fills with slate ────────────────────────
    return Resolution(None, "L4:slate", 4, None, None)


def resolve_live_slot(conn, slot, air_date):
    """live_show resolution: there IS no archive_path. Return a special
    Resolution carrying show_id; the playlist generator emits a slate as
    the underlying ffmpeg-side filler, and the cue runner does the real
    cut to LIVE at slot start.
    """
    payload = json.loads(slot["rule_payload"] or "{}")
    show_id = payload.get("show_id")
    if not show_id:
        return None
    row = conn.execute(
        "SELECT id, title, live_capable, live_source_kind, live_default_url FROM shows WHERE id=?",
        (show_id,),
    ).fetchone()
    if not row or not row["live_capable"]:
        return None
    return Resolution(
        episode_id=None,
        rule_used=f"LIVE:show_id={show_id}:kind={row['live_source_kind']}",
        fallback_level=0,
        archive_path=None,         # caller substitutes the slate
        duration_sec=slot["length_min"] * 60.0,
    )


def _resolve_primary(conn, rule_type, payload, air_date):
    if rule_type == "fixed_episode":
        ep_id = payload.get("episode_id")
        if not ep_id:
            return None
        row = conn.execute(
            "SELECT id, archive_path, duration_sec FROM episodes "
            "WHERE id=? AND status='ready'", (ep_id,)
        ).fetchone()
        return _row_episode(row) if row else None

    if rule_type == "show_rotation":
        show_id = payload.get("show_id")
        policy = payload.get("policy", "newest_unaired")
        if not show_id:
            return None
        if policy == "newest_unaired":
            row = conn.execute(
                """SELECT id, archive_path, duration_sec FROM episodes
                   WHERE show_id=? AND status='ready' AND air_count = 0
                   ORDER BY fetched_at DESC, id DESC LIMIT 1""",
                (show_id,),
            ).fetchone()
        elif policy == "oldest_first":
            row = conn.execute(
                """SELECT id, archive_path, duration_sec FROM episodes
                   WHERE show_id=? AND status='ready'
                   ORDER BY COALESCE(last_aired_at, '1970-01-01') ASC, id ASC LIMIT 1""",
                (show_id,),
            ).fetchone()
        else:
            return None
        return _row_episode(row) if row else None

    if rule_type == "category_pool":
        tags = payload.get("tags") or []
        if not tags:
            return None
        # Match if ANY of the requested tags appear in the episode's tags JSON.
        clauses = " OR ".join(["tags LIKE ?"] * len(tags))
        params = [f'%"{t}"%' for t in tags]
        row = conn.execute(
            f"""SELECT id, archive_path, duration_sec FROM episodes
                WHERE status='ready' AND ({clauses})
                ORDER BY COALESCE(last_aired_at, '1970-01-01') ASC LIMIT 1""",
            params,
        ).fetchone()
        return _row_episode(row) if row else None

    if rule_type == "stunt_block":
        # Returns the seed episode; playlist generator will keep pulling more
        # consecutive episodes of the same show until the slot is filled.
        return _resolve_primary(conn, "show_rotation",
                                {"show_id": payload.get("show_id"),
                                 "policy": "oldest_first"}, air_date)

    if rule_type == "live_show":
        # Sentinel: not file-resolvable. The outer resolver (resolve_slot)
        # special-cases this via resolve_live_slot.
        return None

    return None
