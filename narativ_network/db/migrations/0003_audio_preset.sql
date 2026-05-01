-- Migration 0003: per-show audio preset.
--
-- Each show picks one of: DIALOG_TIGHT, DOC_NATURAL, MUSIC_LIGHT,
-- PANEL, NEWS_HARD. The processor applies that preset's filter chain
-- (highpass + EQ + compressor + loudnorm to preset's LUFS target) at
-- ingest. New shows default to DIALOG_TIGHT. See process/audio.py.

ALTER TABLE shows ADD COLUMN audio_preset TEXT NOT NULL DEFAULT 'DIALOG_TIGHT';
