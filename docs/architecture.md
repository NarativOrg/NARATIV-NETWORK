# Narativ Network — Architecture

## What this is

A self-running 24/7 narrative TV channel running on a Mac mini. It pulls
shows from contributors (Google Drive, etc.), processes them to a uniform
broadcast spec, schedules them on a predictable daily grid, and pushes a
continuous live stream to YouTube + Facebook + X + (anywhere else).

It also handles **live break-ins** — a presenter can hit a button and
override the scheduled programming with a live broadcast in under a second.

## Two architecturally distinct paths

We support two output paths. **Path B is the default.**

### Path B (default) — homegrown multi-stream

```
                ┌────────────────────────────────────────────────────┐
                │  Mac mini (24/7 host)                              │
                │                                                    │
ingest ─► proc ─►─► archive ─► scheduler ─► ffmpeg playout           │
                │                              │                     │
                │              rtmp://localhost/internal/scheduled   │
                │                              ▼                     │
                │                       ┌──────────────┐             │
remote          │                       │              │             │
presenter ──────┼─►  rtmp://mac/internal/live ─► nginx-rtmp           │
(StreamYard,    │                       │              │             │
 OBS, Larix)    │                       └──────┬───────┘             │
                │                              │                     │
                │              ┌───────────────▼─────────┐           │
                │              │   OBS Studio            │           │
                │              │ Scenes:                 │           │
                │              │   SCHEDULED (default)   │           │
                │              │   LIVE      (break-in)  │ ◄─── our  │
                │              │   STANDBY   (slate)     │  WebSocket│
                │              │ Encoder: VT H.264 hw    │           │
                │              └───────────────┬─────────┘           │
                │                              │                     │
                │   OBS Multi-RTMP plugin OR nginx-rtmp `distribute`:│
                │                              │                     │
                └──────────────────────────────┼─────────────────────┘
                                               │
            ┌──────────────────┬───────────────┴─────────────┬─────────┐
            ▼                  ▼                             ▼         ▼
        YouTube Live      Facebook Live                  X Live    Twitch
```

Components on the Mac:

- **ffmpeg playout** (`narativ_network/playout/`) — reads
  `data/run_logs/current_playlist.ffconcat` and pushes to
  `rtmp://127.0.0.1:1935/internal/scheduled`.
- **nginx-rtmp** (`ops/nginx-rtmp/nginx.conf`) — receives the scheduled
  feed and any live-presenter feed, exposes them locally for OBS, and
  optionally fans out to public destinations.
- **OBS Studio** (`ops/obs/SETUP.md`) — the scene switcher + final
  encoder. Reads the scheduled feed as a Media Source. Has a LIVE scene
  reading the live RTMP input. Pushes the active scene out to YouTube
  etc. (via OBS Multi-RTMP plugin OR via nginx-rtmp `distribute`).
- **OBS controller** (`narativ_network/obs/`) — our app's WebSocket
  client. The dashboard's BREAK IN button calls `break_in()` →
  `SetCurrentProgramScene LIVE` → presenter is on air.
- **Watchdog** (`narativ_network/watchdog/`) — confirms the playout
  ffmpeg is alive; logs silence/black detection.

Live break-ins are clean because OBS does the cut, not ffmpeg. ffmpeg
keeps producing the scheduled program into nginx; OBS just stops looking
at that feed during the break-in.

### Path A (fallback) — upload to upstream.so

If we ever need to fall back to upstream.so as a managed fan-out, we
have a complete implementation in `narativ_network/upstream/`:

- `daily_build.py` resolves an entire calendar day to the second and
  writes a folder of files + a manifest + an upload checklist.
- `uploader.py` ships the artifact to upstream.so. Three modes:
  `manual` (writes the artifact, you upload by hand), `api` (stub),
  `playwright` (stub, drives upstream.so's web UI).
- `orchestrator.py` runs forever; nightly at the configured local time
  it builds and uploads the next day.
- `monitor.py` polls a YouTube live URL or a status URL to confirm
  upstream is actually airing.

To switch paths: stop `nn playout` / `nn watchdog`, start
`nn orchestrator` / `nn upstream-monitor`, set `[upstream] upload_mode`.

## The five upstream-agnostic subsystems

These run identically regardless of output path:

| Subsystem | Purpose |
| --- | --- |
| **Ingest** | Polls every Show's source on its cadence (Google Drive via service account, local watch folder, more). New files land in `data/inbox/`. |
| **Processor** | EBU R128 loudness normalize, head/tail silence trim, ffprobe duration, ad-break marker computation. Optional Descript hand-off stage parks files in `data/descript_queue/` and picks up exports from `data/descript_done/`. Output: broadcast-uniform 1080p30 H.264+AAC in `data/archive/`. |
| **Archive** | The library. Each `episodes` row knows its show, file path, duration, ad-break marks, last-aired, air count. |
| **Scheduler** | A 7-day × 24-hour grid (30-min slots), four rule types (`fixed_episode`, `show_rotation`, `category_pool`, `stunt_block`), date-specific overrides. The resolver picks an episode for each slot using the rule, with a 4-level fallback chain (re-air → show evergreen → network pool → slate). |
| **Admin web UI** | FastAPI + Jinja + vanilla JS. Dashboard, Shows inventory, drag-and-drop schedule grid, BREAK IN button. Token-auth for state-changing endpoints. |

## Data model (SQLite)

See `narativ_network/db/schema.sql`. Core tables:

- **shows** — series metadata (title, default duration, tags, source config)
- **sources** — where we fetch new episodes from (gdrive, local folder, etc.)
- **episodes** — one processed file: show, duration, ad-break marks, status, last-aired, air count
- **slots** — programming-grid entries (day_of_week, start_minute, length, rule_type, rule_payload, recurrence)
- **slot_overrides** — date-specific overrides
- **fallback_pool** — evergreen replacement episodes for L3 fallback
- **playlist** — concrete planned air log (timestamped)
- **run_log** — what actually played + watchdog events

## Slot rule types

| rule_type        | rule_payload                                  | resolver behavior                                              |
| ---------------- | --------------------------------------------- | -------------------------------------------------------------- |
| `fixed_episode`  | `{ "episode_id": 42 }`                        | Play exactly that episode.                                     |
| `show_rotation`  | `{ "show_id": 7, "policy": "newest_unaired" }` | Pick newest un-aired episode of show 7, fall back to oldest.   |
| `category_pool`  | `{ "tags": ["music"], "policy": "least_recent" }` | Pick from tagged pool, prefer ones aired least recently.    |
| `stunt_block`    | `{ "show_id": 7, "policy": "marathon" }`      | Fill slot with consecutive episodes of show 7.                 |

## No-show fallback chain

When a slot's primary rule produces no usable episode:

1. **L1** — most-recent re-air for the same show, older than N days
2. **L2** — show-tagged evergreen episode
3. **L3** — network-wide `fallback_pool`, lowest priority first
4. **L4** — the standby slate (`data/slates/we_will_be_right_back.mp4`)

The level is recorded in the playlist so the admin UI can flag persistent
no-shows.

## Scheduled live shows (the cue runner)

Distinct from the BREAK IN button, which is for unscheduled breaking news.
A `live_show` slot type lets the schedule say "this 30 minutes is contributor
X going live" — even back-to-back (two consecutive live_show slots from two
different contributors).

### Show config

Each show carries:

- `live_capable` — boolean
- `live_source_kind` — `rtmp_push` | `dynamic_pull` | `hybrid`
- `live_stream_key` — for `rtmp_push`: the per-contributor secret. The
  contributor configures their broadcasting software to push to
  `rtmp://YOUR_MAC:1935/live` with stream key = this value. nginx-rtmp's
  `on_publish` hook calls our admin server to validate.
- `live_default_url` — for `hybrid` / `dynamic_pull`: a fallback URL if
  the producer hasn't armed a session-specific one.

### Live Cue UI (`/live`)

For each upcoming `live_show` slot (auto-created from the schedule), a
`live_sessions` row exists with status `pending`. The Live Cue page shows
them in chronological order. A producer (or the contributor) pastes the
session URL — Substack's HLS, the StreamYard RTMP, whatever — and clicks
ARM. Status becomes `armed`.

If the URL hasn't arrived by slot start, the cue runner records it as
`failed` and viewers see the slate underneath. The producer can paste the
URL afterward and click GO NOW to force-cut to LIVE mid-slot.

### Cue runner

A 1-second tick loop that:

1. Reads `live_sessions` where `status IN ('pending','armed')` and
   `scheduled_at <= now`.
2. For each: retargets OBS's LIVE-scene Media Source via WebSocket
   `SetInputSettings` and switches scene to LIVE. Stamps `started_at`.
3. Reads `live_sessions` where `status='live'` and `now >= start + duration`.
4. For each: switches scene back to SCHEDULED. Stamps `ended_at`.

Two consecutive live slots = the runner cuts to LIVE for the first,
retargets the URL at the second's start time and stays on LIVE
seamlessly, then back to SCHEDULED at the end.

### Underlying ffmpeg never stops

The ffmpeg playout keeps pushing to localhost nginx the whole time —
during a live, it's pushing the slate. OBS just stops looking at that
feed for the duration of the live. Means: ZERO interruption to the
multistream encoder. Cuts are clean.

## Live break-in path (Path B)

```
[presenter, anywhere]  ──RTMP──►  nginx-rtmp internal/live (Mac)
                                            │
                                            ▼
                                  OBS Media Source (LIVE scene)
                                            │
       [admin dashboard "BREAK IN LIVE"] ───┼───► OBS WebSocket
                                            │
                                            ▼
                                  OBS switches active scene → LIVE
                                            │
                                            ▼
                                  OBS encoder pushes out to all destinations
```

Cut latency: ~500–800 ms in practice (OBS scene transitions are tight).
The scheduled program continues to flow into nginx during the break-in;
OBS just isn't looking at it. Hitting RETURN TO AIR resumes seamlessly.

## What this system intentionally does NOT do

- Per-platform variant transcodes (1080p YouTube, 720p X) — possible
  with a second ffmpeg pipeline if needed; not in Phase 1.
- Member auth / paywalls. Path B's HLS output via nginx-rtmp + an auth
  layer (or Cloudflare Stream / Mux) is the future-proof story for that;
  it's a Phase 3+ add-on.
- Editorial QC. Descript handles polish; we do mechanical normalize/trim only.
