# Local smoke test (before the Mac mini is set up)

You can prove the entire pipeline works on whatever Mac you have right
now, without nginx-rtmp, without OBS, without a real RTMP destination.

## What this tests

- DB migration creates every table
- ffmpeg is installed and our processor invokes it correctly
- Ingest picks up a file, the processor normalizes it (loudnorm + EQ +
  compressor + silence trim per the audio preset), the result lands in
  `data/archive/`
- Scheduler resolves a slot to that archive file and writes
  `data/run_logs/current_playlist.ffconcat`
- The playout encoder chain runs end-to-end (audio master bus + H.264
  + AAC + master bus limiter) and produces a file you can play in VLC

## What this does NOT test

- nginx-rtmp fan-out (skipped — no RTMP target)
- OBS scene switching (skipped — no OBS)
- Live break-ins or scheduled live shows (skipped — needs OBS)
- upstream.so / public-platform delivery (skipped — local only)

Those all need the Mac mini's full stack. Worth testing once it's set up.

## Prereqs (10 minutes)

```sh
brew install python@3.12 ffmpeg
# Optional (transcripts / search step):
brew install whisper-cpp
mkdir -p ~/whisper-models
curl -L -o ~/whisper-models/ggml-medium.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin
```

## Run the smoke test

```sh
git clone <your-repo-or-tarball-path> narativ-network
cd narativ-network
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e .

mkdir -p ~/.narativ-network
cp narativ_network/config.example.toml ~/.narativ-network/config.toml
# Set admin_token to any long string. Set [transcripts] model_path if you
# want the optional whisper step. Everything else can stay default.

nn smoke-test
```

You should see:

```
>> migrate
   applied: ['0001_live_shows.sql', '0002_transcripts.sql', '0003_audio_preset.sql']
>> generate test clip
   test clip: data/inbox/_smoke_test.mp4
>> seed show + source + slot + episode
   show_id=1 source_id=1 episode_id=1
   wrote slate stub: data/slates/we_will_be_right_back.mp4
>> process the episode (loudnorm, EQ, comp, scale, faststart)
   {'ok': True, 'stage': 'ready', 'duration_sec': 20.0, 'archive_path': '...'}
>> regenerate the rolling playlist
   {'slots_resolved': 12, 'entries': 12, ...}

done.
  archive dir         : .../data/archive
  rolling playlist    : .../data/run_logs/current_playlist.ffconcat
```

## Verify each piece

### Admin UI

```sh
nn admin
# open http://127.0.0.1:8765
```

You should see:

- Dashboard with the test slot in "Up next"
- Shows page listing `smoke-test` with audio preset `DIALOG_TIGHT`
- Schedule page showing the daily slot at 00:00
- Live Cue page (empty, no live slots seeded)
- Search page (works once you transcribe)

### Playout chain (no RTMP needed)

```sh
nn playout-test --seconds 20 --output /tmp/nn_test_output.ts
open /tmp/nn_test_output.ts          # opens in QuickTime
# or:  vlc /tmp/nn_test_output.ts
```

You should see SMPTE color bars with a 1 kHz beep. If audio is at a
sensible level (around -16 LUFS) and there's no clipping, the loudnorm
+ master bus chain works.

### Transcripts (optional)

If you installed whisper-cpp + a model:

```sh
# point config at the model first:
#   [transcripts] model_path = "~/whisper-models/ggml-medium.en.bin"
nn transcribe 1
nn search "beep"
# or: open http://127.0.0.1:8765/search
```

The smoke clip is just a tone, so transcription will be empty / minimal,
but it confirms the engine runs.

### Audio preset switch (optional)

```sh
nn audio-presets                    # list
nn show-preset smoke-test PANEL     # set
nn process-once                      # re-renders with new preset
```

Re-run `nn playout-test` and you should hear a difference in tonal
balance / level on the new render.

## Resetting between runs

```sh
rm -rf data/archive/* data/run_logs/* data/inbox/_smoke_test.mp4
rm -f data/narativ_network.sqlite*
```

Then `nn smoke-test` again will recreate everything cleanly.

## What's left to test once the Mac mini is up

Same project, same code, but now wire up:

1. nginx-rtmp (`ops/nginx-rtmp/nginx.conf`, edit stream keys)
2. OBS Studio (`ops/obs/SETUP.md`)
3. Real upstream RTMP keys (YouTube test stream is fine)
4. The five LaunchAgents (`./ops/scripts/install_launchd.sh`)

At that point `nn playout` (without `-test`) becomes the real on-air
encoder, and the BREAK IN / Live Cue features come online.
