-- Migration 0001: live shows + per-session live cue.
--
-- Adds the columns/tables needed for scheduled live broadcasts, with
-- two source kinds:
--
--   rtmp_push     contributor pushes RTMP into our nginx-rtmp using
--                 a per-show stream key. Best for OBS / Larix users.
--   dynamic_pull  contributor goes live on Substack / YouTube / X / etc.
--                 and pastes that session's URL into the Live Cue panel
--                 before air. We pull from there.
--   hybrid        try the show's `live_default_url`; if not reachable
--                 at slot start, fall through to whatever the cue says.

ALTER TABLE shows ADD COLUMN live_capable INTEGER NOT NULL DEFAULT 0;
ALTER TABLE shows ADD COLUMN live_source_kind TEXT NOT NULL DEFAULT 'rtmp_push';
ALTER TABLE shows ADD COLUMN live_stream_key TEXT;
ALTER TABLE shows ADD COLUMN live_default_url TEXT;

-- Per-scheduled-slot live session: the URL the producer armed, status,
-- and the runtime timestamps the cue runner stamps on cut/return.
CREATE TABLE IF NOT EXISTS live_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id  INTEGER REFERENCES playlist(id) ON DELETE CASCADE,
    show_id      INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    scheduled_at TEXT NOT NULL,
    duration_sec REAL,
    source_url   TEXT,
    source_kind  TEXT NOT NULL DEFAULT 'dynamic_pull'
                 CHECK (source_kind IN ('rtmp_push','dynamic_pull','hybrid')),
    armed_at     TEXT,
    armed_by     TEXT,
    started_at   TEXT,
    ended_at     TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','armed','live','ended','failed','skipped')),
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_livesessions_scheduled ON live_sessions(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_livesessions_status    ON live_sessions(status);
CREATE INDEX IF NOT EXISTS idx_livesessions_show      ON live_sessions(show_id);

-- Allow rule_type='live_show' on slots. SQLite doesn't allow ALTER on a
-- CHECK constraint, so we recreate the slots table with the expanded check.
-- Existing rows are preserved.
CREATE TABLE slots__new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT,
    day_of_week   INTEGER CHECK (day_of_week BETWEEN 0 AND 6),
    start_minute  INTEGER NOT NULL CHECK (start_minute BETWEEN 0 AND 1439),
    length_min    INTEGER NOT NULL CHECK (length_min > 0),
    rule_type     TEXT NOT NULL CHECK (rule_type IN (
                      'fixed_episode','show_rotation','category_pool',
                      'stunt_block','live_show'
                  )),
    rule_payload  TEXT NOT NULL DEFAULT '{}',
    recurrence    TEXT NOT NULL DEFAULT 'weekly'
                  CHECK (recurrence IN ('once','daily','weekdays','weekends','weekly')),
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO slots__new
  SELECT id, label, day_of_week, start_minute, length_min,
         rule_type, rule_payload, recurrence, enabled, created_at
  FROM slots;
DROP TABLE slots;
ALTER TABLE slots__new RENAME TO slots;
CREATE INDEX IF NOT EXISTS idx_slots_dow_start ON slots(day_of_week, start_minute);
