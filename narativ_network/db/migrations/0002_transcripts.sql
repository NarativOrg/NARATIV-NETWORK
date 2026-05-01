-- Migration 0002: transcripts + full-text search.
--
-- One transcript row per episode. `segments_json` stores Whisper's
-- per-segment start/end/text so we can do timestamped lookups (chapter
-- markers, ad-break placement that avoids mid-sentence).
--
-- transcripts_fts is an FTS5 virtual table — populated by the worker
-- alongside the parent row (no triggers, simpler to reason about).

CREATE TABLE IF NOT EXISTS transcripts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id      INTEGER NOT NULL UNIQUE REFERENCES episodes(id) ON DELETE CASCADE,
    language        TEXT,
    duration_sec    REAL,
    full_text       TEXT NOT NULL,
    segments_json   TEXT NOT NULL DEFAULT '[]',
    word_count      INTEGER NOT NULL DEFAULT 0,
    model           TEXT,
    engine          TEXT,
    transcribed_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    failure         TEXT
);

CREATE INDEX IF NOT EXISTS idx_transcripts_ep ON transcripts(episode_id);

-- FTS5 contentless mode: we manage rowids ourselves so we can store
-- episode_id alongside the text for cheap joins.
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    full_text,
    episode_id UNINDEXED,
    show_id UNINDEXED,
    title UNINDEXED,
    tokenize = 'porter unicode61'
);

-- Convenience: last-aired and air-count touched whenever an ep airs.
-- (Not a transcripts thing, but easy migration to ship together.)
CREATE INDEX IF NOT EXISTS idx_episodes_processed_at ON episodes(processed_at);
