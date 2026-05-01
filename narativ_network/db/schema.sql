-- Narrative Network — SQLite schema
-- Phase 1: shows, sources, episodes, slots, playlist, run_log, fallback_pool

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS shows (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                 TEXT NOT NULL UNIQUE,
    title                TEXT NOT NULL,
    contributor          TEXT,
    default_duration_min INTEGER NOT NULL DEFAULT 30,
    ad_breaks_per_hour   INTEGER NOT NULL DEFAULT 2,
    tags                 TEXT NOT NULL DEFAULT '[]',
    needs_descript       INTEGER NOT NULL DEFAULT 0,
    audio_preset         TEXT NOT NULL DEFAULT 'DIALOG_TIGHT',
    live_capable         INTEGER NOT NULL DEFAULT 0,
    live_source_kind     TEXT NOT NULL DEFAULT 'rtmp_push',  -- rtmp_push | dynamic_pull | hybrid
    live_stream_key      TEXT,
    live_default_url     TEXT,
    notes                TEXT,
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id      INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('local','gdrive','rss','youtube','webdav','dropbox')),
    config       TEXT NOT NULL DEFAULT '{}',
    poll_minutes INTEGER NOT NULL DEFAULT 15,
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_polled  TEXT,
    last_error   TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sources_show ON sources(show_id);

CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id         INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    source_id       INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    external_id     TEXT,
    title           TEXT,
    raw_path        TEXT,
    archive_path    TEXT,
    duration_sec    REAL,
    ad_break_count  INTEGER NOT NULL DEFAULT 0,
    ad_break_marks  TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending','fetching','fetched','descript_queued',
                        'descript_done','processing','ready','failed','retired'
                    )),
    needs_descript  INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT,
    processed_at    TEXT,
    last_aired_at   TEXT,
    air_count       INTEGER NOT NULL DEFAULT 0,
    failure_reason  TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_episodes_show       ON episodes(show_id);
CREATE INDEX IF NOT EXISTS idx_episodes_status     ON episodes(status);
CREATE INDEX IF NOT EXISTS idx_episodes_lastaired  ON episodes(last_aired_at);

CREATE TABLE IF NOT EXISTS slots (
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

CREATE INDEX IF NOT EXISTS idx_slots_dow_start ON slots(day_of_week, start_minute);

CREATE TABLE IF NOT EXISTS slot_overrides (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    air_date     TEXT NOT NULL,
    start_minute INTEGER NOT NULL,
    length_min   INTEGER NOT NULL,
    rule_type    TEXT NOT NULL,
    rule_payload TEXT NOT NULL DEFAULT '{}',
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (air_date, start_minute)
);

CREATE TABLE IF NOT EXISTS fallback_pool (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id  INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    priority    INTEGER NOT NULL DEFAULT 100,
    tags        TEXT NOT NULL DEFAULT '[]',
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (episode_id)
);

CREATE TABLE IF NOT EXISTS playlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scheduled_at    TEXT NOT NULL,
    slot_id         INTEGER REFERENCES slots(id) ON DELETE SET NULL,
    episode_id      INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
    rule_used       TEXT NOT NULL,
    fallback_level  INTEGER NOT NULL DEFAULT 0,
    duration_sec    REAL,
    status          TEXT NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned','aired','failed','preempted','skipped')),
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_playlist_when   ON playlist(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_playlist_status ON playlist(status);

CREATE TABLE IF NOT EXISTS run_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id   INTEGER REFERENCES playlist(id) ON DELETE SET NULL,
    episode_id    INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    status        TEXT NOT NULL DEFAULT 'started'
                  CHECK (status IN (
                      'started','ok','underrun','overrun','silence_detected',
                      'black_detected','process_died','cut_short','preempted'
                  )),
    detail        TEXT
);

CREATE INDEX IF NOT EXISTS idx_runlog_started ON run_log(started_at);

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

CREATE TABLE IF NOT EXISTS migrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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

CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    full_text,
    episode_id UNINDEXED,
    show_id UNINDEXED,
    title UNINDEXED,
    tokenize = 'porter unicode61'
);
