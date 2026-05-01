"""FTS5 search across all transcripts.

Returns matches with snippets and the episode/show metadata so the UI
can render a useful result without a second query.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict

from ..config import Config
from ..db import connect

log = logging.getLogger(__name__)


@dataclass
class SearchHit:
    episode_id: int
    show_id: int | None
    title: str | None
    show_title: str | None
    archive_path: str | None
    snippet: str
    bm25: float


def _safe_match(query: str) -> str:
    """Treat the user's input as plain words to AND together. Stops them
    from accidentally hitting the FTS query DSL with a stray colon or
    parenthesis. If they want a phrase, they wrap in quotes.
    """
    query = (query or "").strip()
    if not query:
        return ""
    # Allow quoted phrases verbatim; otherwise word-split and AND.
    if query.startswith('"') and query.endswith('"') and len(query) > 1:
        return query
    words = []
    for w in query.split():
        clean = "".join(c for c in w if c.isalnum() or c in "-_")
        if clean:
            words.append(clean + "*")     # prefix-match each word
    return " ".join(words)


def search(cfg: Config, query: str, limit: int = 25,
           show_id: int | None = None) -> list[dict]:
    match = _safe_match(query)
    if not match:
        return []
    conn = connect(cfg)
    sql = """
      SELECT t_fts.episode_id AS episode_id,
             t_fts.show_id    AS show_id,
             t_fts.title      AS title,
             snippet(transcripts_fts, 0, '<b>', '</b>', '…', 12) AS snippet,
             bm25(transcripts_fts) AS bm25,
             episodes.archive_path AS archive_path,
             shows.title AS show_title
      FROM transcripts_fts AS t_fts
      JOIN episodes ON episodes.id = t_fts.episode_id
      LEFT JOIN shows ON shows.id  = t_fts.show_id
      WHERE transcripts_fts MATCH ?
    """
    params = [match]
    if show_id is not None:
        sql += " AND t_fts.show_id = ?"
        params.append(show_id)
    sql += " ORDER BY bm25 ASC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_with_timestamps(cfg: Config, query: str, limit: int = 10) -> list[dict]:
    """Same as `search` but also returns the matching segment(s) with
    timestamps (so a UI can deep-link into the right moment of the clip).
    """
    hits = search(cfg, query, limit=limit)
    if not hits:
        return []
    needle = (query or "").strip().strip('"').lower()
    if not needle:
        return hits
    needle_words = [w for w in needle.split() if w]

    conn = connect(cfg)
    by_id = {h["episode_id"]: h for h in hits}
    rows = conn.execute(
        f"SELECT episode_id, segments_json FROM transcripts WHERE episode_id IN ({','.join('?'*len(by_id))})",
        list(by_id.keys()),
    ).fetchall()
    conn.close()

    for r in rows:
        segments = json.loads(r["segments_json"] or "[]")
        matches = []
        for seg in segments:
            s_lower = (seg.get("text") or "").lower()
            if any(w in s_lower for w in needle_words):
                matches.append(seg)
        by_id[r["episode_id"]]["matched_segments"] = matches[:5]
    return list(by_id.values())
