# OBS setup on the Mac mini

OBS is the local scene switcher for the channel. Set it up once; our app
just sends scene-switch commands to its WebSocket from then on.

## 1. Install

```sh
brew install --cask obs
```

Optional but recommended: `brew install --cask obs-multi-rtmp` — lets OBS
push to multiple RTMP destinations directly without nginx-rtmp's
`distribute` app. Either approach works; pick one and skip the other.

## 2. WebSocket plugin

OBS 28+ ships obs-websocket built in. Open OBS:

- Tools → WebSocket Server Settings
- ☑ Enable WebSocket server
- Server port: `4455`
- ☑ Enable Authentication
- Click **Show Connect Info** → copy the password
- Paste the password into `~/.narativ-network/config.toml` under
  `[obs] websocket_password = "..."`

## 3. Scenes

Create three scenes (left dock → "+" under Scenes):

### `SCHEDULED`

The default on-air scene — plays the rolling playlist.

- Add Source → **Media Source**
- Local File: ☐ (uncheck)
- Input: `rtmp://127.0.0.1:1935/scheduled` (or `internal/scheduled`)
- Restart playback when source becomes active: ☑
- Use hardware decoding when available: ☑

### `LIVE`

For break-ins AND scheduled live shows. The cue runner retargets this
source's URL per session via OBS WebSocket — so the URL field below is
only the default; for `dynamic_pull` shows it'll be replaced live.

- Add Source → **Media Source**
- **Set the source's NAME to `live_source`** — exact name matters; that's
  what `[obs] live_input_name` in config maps to.
- Local File: ☐
- Input: leave blank, OR put `rtmp://127.0.0.1:1935/live/<one-of-your-keys>`
  for a default sanity-check value.
- Restart playback when source becomes active: ☑
- Show nothing when playback ends: ☑
- Use hardware decoding when available: ☑

### `STANDBY`

The "we'll be right back" slate.

- Add Source → **Image** OR **Media Source** pointing at
  `data/slates/we_will_be_right_back.mp4`

## 4. Output (one of these two)

### Option A: OBS pushes to nginx-rtmp `distribute`

- Settings → Stream
- Service: Custom
- Server: `rtmp://127.0.0.1:1935/distribute`
- Stream Key: `nn` (anything; nginx-rtmp ignores it for `distribute`)

nginx-rtmp's `push` lines fan out to YouTube/FB/X. Edit `ops/nginx-rtmp/nginx.conf`.

### Option B: OBS Multi-RTMP plugin

If you installed `obs-multi-rtmp`:

- Tools → Multiple Output → add one entry per destination, with the
  RTMP URL and stream key from each platform.
- Disable the default Stream output OR set it to nginx-rtmp localhost
  so the watchdog has something to watch.

## 5. Encoder

- Settings → Output → Output Mode: Advanced
- Encoder: **Apple VT H264 Hardware** (uses the Mac mini's media engine
  — extremely efficient on Apple Silicon)
- Rate Control: CBR
- Bitrate: 6000 Kbps (1080p30) — match `[playout].video_bitrate_kbps`
- Keyframe Interval: 2s
- Profile: high
- Audio Encoder: AAC, 192 Kbps, 48 kHz, Stereo

## 6. Auto-start

OBS Studio supports launching at login (System Settings → General → Login
Items). Tick "Show OBS in dock when starting" off, "Start streaming when
launching" on (Settings → General).

For headless 24/7 reliability, consider running OBS via `caffeinate` so
sleep doesn't kick in:

```sh
caffeinate -dis open -a "OBS"
```

A LaunchAgent for that wrapper lives at
`ops/launchd/org.narativ.obs.plist.template`.

## 7. Sanity check

From another machine on the LAN, install Larix Broadcaster (free, iOS/Android)
or use OBS, and push to `rtmp://YOUR_MAC_LAN_IP:1935/internal/live` with
stream key `presenter1`. Switch the OBS scene to LIVE — you should see the
remote video.

The dashboard's BREAK IN button does exactly that switch via WebSocket.
