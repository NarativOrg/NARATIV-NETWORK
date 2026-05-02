# Narativ Network

A self-running 24/7 narrative TV channel on a Mac mini.

- **Ingests** shows from contributors (Google Drive, watch folders, more)
- **Processes** them to a uniform broadcast spec (loudness, trim, format)
- **Schedules** them on a drag-and-drop weekly grid with auto-fallback if a creator misses a day
- **Multistreams** to YouTube + Facebook + X + Twitch simultaneously
- **One-button live break-ins** — a presenter goes live in <1 second
- Set-and-forget: auto-starts, auto-restarts, monitors itself

See `docs/architecture.md` for the full picture.

## Quick start (Mac mini)

```sh
# 1. Install runtime deps
brew install python@3.12 ffmpeg nginx
brew install --cask obs                            # scene switcher + encoder
# Optional: brew install --cask obs-multi-rtmp     # if you don't want nginx fanout

# 2. Get the code, install Python deps
git clone <your-repo-url> narativ-network
cd narativ-network
pip install -e .                                   # installs nn CLI globally

# 3. Configure
mkdir -p ~/.narativ-network
cp narativ_network/config.example.toml ~/.narativ-network/config.toml
$EDITOR ~/.narativ-network/config.toml             # admin_token, gdrive SA path, OBS pwd

# 4. Initialize and run the local smoke test (no nginx/OBS needed)
nn migrate
nn smoke-test                                      # generates test clip, seeds DB, verifies pipeline
nn admin                                           # http://127.0.0.1:8765
nn preview                                         # live HLS preview → http://127.0.0.1:8888/live.m3u8

# 5. Set up OBS once (~5 minutes) — only needed for multistream
open ops/obs/SETUP.md

# 6. Set up nginx-rtmp (replace stream keys)
cp ops/nginx-rtmp/nginx.conf /opt/homebrew/etc/nginx/nginx.conf
brew services start nginx

# 7. Auto-start at login
./ops/scripts/install_launchd.sh
```

## Local testing (no OBS / nginx / RTMP destination)

The full pipeline can be verified without any streaming infrastructure:

```sh
nn smoke-test        # end-to-end: generate clip → ingest → process → schedule
nn admin             # admin UI at http://127.0.0.1:8765
nn playout-test      # fast encode to /tmp/nn_test_output.ts (open in VLC/QuickTime)
nn preview           # live HLS at http://127.0.0.1:8888/live.m3u8 (Safari plays natively)
```

`nn preview` runs the full broadcast encode chain (H.264 + AAC + master bus compressor/limiter)
and outputs real HLS. What you see in Safari is exactly what would go to air.

## What runs as a long-lived service

| Service          | What it does                                              | Plist                                  |
| ---------------- | --------------------------------------------------------- | -------------------------------------- |
| Admin UI         | Dashboard, schedule grid, BREAK IN button, JSON API       | `org.narativ.nn.admin.plist`           |
| Ingest poller    | Polls every Show's source on its cadence                  | `org.narativ.nn.ingest.plist`          |
| Schedule         | Regenerates the rolling 6-hour playlist every 5 minutes   | `org.narativ.nn.schedule.plist`        |
| Playout          | ffmpeg pushes the playlist to localhost nginx-rtmp        | `org.narativ.nn.playout.plist`         |
| Watchdog         | Process liveness + silence/black detection                | `org.narativ.nn.watchdog.plist`        |

OBS Studio runs separately (it's a GUI app — install as a Login Item).
nginx-rtmp runs via `brew services`.

## CLI

```
nn doctor              sanity-check ffmpeg/paths/db/RTMP/OBS
nn migrate             create/upgrade SQLite schema

nn doctor              sanity-check ffmpeg/paths/db/RTMP/OBS
nn migrate             create/upgrade SQLite schema
nn smoke-test          end-to-end local pipeline test (no RTMP/OBS needed)

nn admin               admin UI at http://127.0.0.1:8765
nn ingest[-once]       poller (forever | one pass)
nn process-once        drive every actionable episode forward one stage
nn schedule            regenerate rolling playlist forever
nn regen-once          regenerate now
nn playout             ffmpeg → localhost nginx-rtmp
nn watchdog            local-process watchdog

nn preview             live HLS channel preview → http://127.0.0.1:8888/live.m3u8
nn playout-test        fast encode to /tmp/nn_test_output.ts (verify encode chain)

nn obs-test            verify OBS WebSocket connection
nn break-in REASON     force OBS to LIVE scene
nn return-to-air       force OBS to SCHEDULED scene
nn standby REASON      force OBS to STANDBY scene

# Fallback path (only if you ever switch off Path B):
nn daily-build         render tomorrow's per-second plan
nn daily-upload        build + run the configured upstream uploader
nn orchestrator        run the upload orchestrator forever
nn upstream-monitor    poll YouTube/upstream.so to confirm we're on air
```

## Adding a Show (Phase 1)

There's no UI for this yet. Insert directly:

```sql
INSERT INTO shows (slug, title, contributor, default_duration_min, ad_breaks_per_hour, needs_descript)
VALUES ('morning-news', 'Morning News', 'Jane Q.', 30, 2, 0);

INSERT INTO sources (show_id, kind, config, poll_minutes)
VALUES (
  (SELECT id FROM shows WHERE slug='morning-news'),
  'gdrive',
  '{"folder_id": "1AbC..."}',
  15
);
```

Then drag the show onto a slot in the schedule grid at `/schedule`.

## Phase 1 complete — what's working end-to-end

- ✅ Ingest from local folders + Google Drive (service account)
- ✅ Processor: loudness normalize (EBU R128), silence trim, 5 audio presets, optional Descript stage
- ✅ Schedule grid (drag-drop), 4 rule types, 4-level fallback chain
- ✅ Rolling 6-hour playlist generator (regenerates every 5 min)
- ✅ ffmpeg playout → localhost nginx-rtmp (Path B, primary)
- ✅ OBS scene control (BREAK IN / RETURN TO AIR / STANDBY) via WebSocket
- ✅ Live cue runner: scheduled live shows auto-cut at slot start/end
- ✅ Admin UI: dashboard, shows inventory, schedule grid, live cue panel, transcript search
- ✅ Watchdog: silence/black detection, auto-restart
- ✅ macOS launchd auto-start
- ✅ Fallback path: upstream.so daily-build + upload (manual mode functional)
- ✅ Local preview: `nn preview` → HLS → Safari (no nginx/OBS needed for testing)

## Phase 2 (next)

- Show-management UI (right now you'd add a show via SQLite)
- Contributor portal: each contributor sees their show's status, last-aired, no-show alerts
- Per-platform variant transcodes (1080p YouTube, 720p X)
- Phone alerting on outages (Pushover / Twilio)
- Member auth + HLS paywall via nginx-rtmp + custom player (or Cloudflare Stream / Mux)
- Ad server: drop ads into computed marker positions at runtime
- Marathon / stunt-block UI affordances
- Daily run-log digest email
