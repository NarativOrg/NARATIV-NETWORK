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
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e .

# 3. Configure
mkdir -p ~/.narativ-network
cp narativ_network/config.example.toml ~/.narativ-network/config.toml
$EDITOR ~/.narativ-network/config.toml         # admin_token, gdrive SA path, OBS pwd

# 4. Set up OBS once (~5 minutes)
open ops/obs/SETUP.md

# 5. Set up nginx-rtmp (replace stream keys)
cp ops/nginx-rtmp/nginx.conf /opt/homebrew/etc/nginx/nginx.conf
brew services start nginx

# 6. Initialize, drop in a slate, kick the tires
nn doctor
nn migrate
cp /path/to/your/slate.mp4 data/slates/we_will_be_right_back.mp4
nn admin           # http://127.0.0.1:8765

# 7. Auto-start at login
./ops/scripts/install_launchd.sh
```

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

nn admin               start the FastAPI admin UI
nn ingest[-once]       poller (forever | one pass)
nn process-once        drive every actionable episode forward one stage
nn schedule            regenerate rolling playlist forever
nn regen-once          regenerate now
nn playout             ffmpeg → localhost nginx-rtmp
nn watchdog            local-process watchdog

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
- ✅ Processor: loudness normalize, silence trim, ad-break markers, optional Descript stage
- ✅ Schedule grid (drag-drop), 4 rule types, 4-level fallback
- ✅ Rolling playlist generator
- ✅ ffmpeg playout → localhost nginx-rtmp
- ✅ OBS scene control (BREAK IN / RETURN TO AIR / STANDBY) via WebSocket
- ✅ Admin UI dashboard, shows inventory, schedule
- ✅ Watchdog
- ✅ macOS launchd auto-start
- ✅ Fallback path: upstream.so daily-build + upload (manual mode functional)

## Phase 2 (next)

- Show-management UI (right now you'd add a show via SQLite)
- Contributor portal: each contributor sees their show's status, last-aired, no-show alerts
- Per-platform variant transcodes (1080p YouTube, 720p X)
- Phone alerting on outages (Pushover / Twilio)
- Member auth + HLS paywall via nginx-rtmp + custom player (or Cloudflare Stream / Mux)
- Ad server: drop ads into computed marker positions at runtime
- Marathon / stunt-block UI affordances
- Daily run-log digest email
