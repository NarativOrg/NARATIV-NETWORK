"""Command-line entry point.

Subcommands:

  nn doctor        sanity-check ffmpeg, paths, db, rtmp config
  nn migrate       create/upgrade the SQLite schema
  nn admin         start the FastAPI admin UI
  nn ingest        run the ingest poller forever
  nn ingest-once   one ingest pass and exit
  nn process-once  drive every actionable episode forward by one stage
  nn schedule      run the rolling-playlist regen forever
  nn regen-once    regenerate the rolling playlist now and exit
  nn playout       run the ffmpeg RTMP pusher (auto-restarts itself)
  nn watchdog      run the watchdog forever

Each subcommand reads ~/.narativ-network/config.toml unless $NN_CONFIG is set.
"""
from __future__ import annotations

import logging
import shutil
import sys
import time
from pathlib import Path

import typer

from .config import absolute_path, load_config
from .db import migrate as db_migrate

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False)


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


@app.command()
def doctor(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Sanity-check the install."""
    _setup_logging(verbose)
    cfg = load_config()
    problems = []
    print(f"project_root : {cfg.project_root}")
    print(f"db_path      : {absolute_path(cfg, cfg.db_path)}")
    print(f"timezone     : {cfg.timezone}")

    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        print(f"{name:13s}: {path or 'MISSING'}")
        if not path:
            problems.append(f"{name} not in PATH")

    rtmp = cfg.playout.rtmp_url
    print(f"rtmp_url     : {rtmp}")
    if "REPLACE_ME" in rtmp or "REPLACE_ME" in cfg.playout.stream_key:
        problems.append("rtmp_url / stream_key still placeholder")

    if not cfg.admin.admin_token or "REPLACE_ME" in cfg.admin.admin_token:
        problems.append("admin_token not set (state-changing API will refuse)")

    inbox = absolute_path(cfg, cfg.ingest.inbox_dir)
    archive = absolute_path(cfg, cfg.process.archive_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)

    slate = absolute_path(cfg, cfg.schedule.slate_path)
    if not slate.exists():
        problems.append(f"slate file missing: {slate} (put a 30s mp4 there)")

    if problems:
        print("\nproblems:")
        for p in problems:
            print(f"  - {p}")
        raise typer.Exit(code=1)
    print("\nall good.")


@app.command()
def migrate():
    """Create/upgrade the SQLite schema."""
    _setup_logging(False)
    cfg = load_config()
    applied = db_migrate(cfg)
    print(f"schema applied. new migrations: {applied or 'none'}")


@app.command()
def admin(host: str = typer.Option(None), port: int = typer.Option(None),
          verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Run the FastAPI admin UI."""
    _setup_logging(verbose)
    import uvicorn
    cfg = load_config()
    from .admin.server import create_app
    uvicorn.run(create_app(cfg),
                host=host or cfg.admin.bind_host,
                port=port or cfg.admin.bind_port,
                log_level="info")


@app.command()
def ingest(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Run the ingest poller forever."""
    _setup_logging(verbose)
    cfg = load_config()
    from .ingest.poller import run_forever
    run_forever(cfg)


@app.command("ingest-once")
def ingest_once(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """One ingest pass; exit."""
    _setup_logging(verbose)
    cfg = load_config()
    from .ingest.poller import poll_once
    print(poll_once(cfg))


@app.command("process-once")
def process_once(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Drive every actionable episode forward one stage; exit."""
    _setup_logging(verbose)
    cfg = load_config()
    from .process import process_all_pending
    print(process_all_pending(cfg))


@app.command()
def schedule(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Regenerate the rolling playlist on a fixed cadence forever."""
    _setup_logging(verbose)
    cfg = load_config()
    from .schedule import regenerate_rolling_playlist
    interval = cfg.schedule.regenerate_every_minutes * 60
    while True:
        try:
            print(regenerate_rolling_playlist(cfg))
        except Exception:
            logging.exception("regen failed; continuing")
        time.sleep(interval)


@app.command("regen-once")
def regen_once(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Regenerate the rolling playlist now; exit."""
    _setup_logging(verbose)
    cfg = load_config()
    from .schedule import regenerate_rolling_playlist
    print(regenerate_rolling_playlist(cfg))


@app.command()
def playout(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Run the ffmpeg RTMP pusher (auto-restarting)."""
    _setup_logging(verbose)
    cfg = load_config()
    from .playout import run_forever
    run_forever(cfg)


@app.command()
def watchdog(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Run the local-process watchdog forever (Path B: ffmpeg + nginx)."""
    _setup_logging(verbose)
    cfg = load_config()
    from .watchdog import run_forever
    run_forever(cfg)


# ── OBS scene control ────────────────────────────────────────────────
@app.command("obs-test")
def obs_test():
    """Connect to OBS and print the current scene + stream status."""
    cfg = load_config()
    from .obs import OBSController
    ctl = OBSController(cfg)
    print(f"current scene: {ctl.current()}")
    print(f"stream status: {ctl.stream_status()}")


@app.command("break-in")
def cmd_break_in(reason: str = typer.Argument("")):
    """Switch OBS to LIVE scene (cuts scheduled programming)."""
    cfg = load_config()
    from .obs import break_in
    print(break_in(cfg, reason=reason))


@app.command("return-to-air")
def cmd_return_to_air():
    """Switch OBS back to SCHEDULED scene."""
    cfg = load_config()
    from .obs import return_to_air
    print(return_to_air(cfg))


@app.command()
def standby(reason: str = typer.Argument("")):
    """Switch OBS to STANDBY scene (slate)."""
    cfg = load_config()
    from .obs import go_standby
    print(go_standby(cfg, reason=reason))


# ── upstream.so fallback path ───────────────────────────────────────
@app.command("daily-build")
def daily_build(date: str = typer.Option("", help="ISO date YYYY-MM-DD; default = tomorrow")):
    """Render tomorrow's per-second plan to data/daily_builds/."""
    from datetime import date as _date
    cfg = load_config()
    from .upstream import build_daily_plan
    target = _date.fromisoformat(date) if date else None
    plan = build_daily_plan(cfg, target_date=target)
    print(f"built: {plan.out_dir}  entries={len(plan.entries)}  runtime={plan.total_seconds/3600:.2f}h")


@app.command("daily-upload")
def daily_upload(date: str = typer.Option("", help="ISO date YYYY-MM-DD; default = tomorrow")):
    """Build tomorrow's plan and run the configured upstream uploader."""
    from datetime import date as _date
    cfg = load_config()
    from .upstream.orchestrator import run_once
    target = _date.fromisoformat(date) if date else None
    print(run_once(cfg, target=target))


@app.command("orchestrator")
def cmd_orchestrator(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Run the upstream.so daily-build orchestrator forever
    (only useful if you've chosen the upstream.so fallback path).
    """
    _setup_logging(verbose)
    cfg = load_config()
    from .upstream.orchestrator import run_forever
    run_forever(cfg)


@app.command("upstream-monitor")
def cmd_upstream_monitor(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """External monitor: poll YouTube/upstream.so to confirm we're on air."""
    _setup_logging(verbose)
    cfg = load_config()
    from .upstream.monitor import run_forever
    run_forever(cfg)


# ── Live cue runner ─────────────────────────────────────────────────
@app.command("cue-runner")
def cmd_cue_runner(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """1-second tick loop that auto-cuts to LIVE at scheduled live-show
    slot starts, and back to SCHEDULED at slot ends.
    """
    _setup_logging(verbose)
    cfg = load_config()
    from .cue_runner import run_forever
    run_forever(cfg)


@app.command("cue-tick")
def cmd_cue_tick():
    """One pass of the cue runner; exit. Useful for cron-style debug."""
    cfg = load_config()
    from .cue_runner import tick_once
    print(tick_once(cfg))


@app.command("arm-live")
def cmd_arm_live(session_id: int, source_url: str):
    """Arm a live session with a source URL (stand-in for the Live Cue UI)."""
    cfg = load_config()
    from .db import connect
    from datetime import datetime, timezone
    conn = connect(cfg)
    conn.execute(
        "UPDATE live_sessions SET source_url=?, status='armed', armed_at=?, armed_by=? WHERE id=?",
        (source_url,
         datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "cli", session_id),
    )
    conn.close()
    print(f"armed session {session_id} with {source_url}")


# ── Transcripts ─────────────────────────────────────────────────────
@app.command()
def transcribe(episode_id: int, force: bool = typer.Option(False, "--force")):
    """Transcribe one episode by id."""
    cfg = load_config()
    from .transcripts import transcribe_one
    print(transcribe_one(cfg, episode_id, force=force))


@app.command("transcribe-pending")
def transcribe_pending(limit: int = typer.Option(10, "--limit")):
    """Transcribe all ready-but-untranscribed episodes (one pass)."""
    cfg = load_config()
    from .transcripts import transcribe_all_pending
    print(transcribe_all_pending(cfg, limit=limit))


@app.command("transcribe-watch")
def transcribe_watch(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Run the transcripts worker forever."""
    _setup_logging(verbose)
    cfg = load_config()
    from .transcripts import run_forever
    run_forever(cfg)


# ── Audio polish ────────────────────────────────────────────────────
@app.command("audio-presets")
def audio_presets():
    """List available audio presets with descriptions and target LUFS."""
    from .process.audio import PRESETS
    for name, p in PRESETS.items():
        comp = "yes" if p.compressor else "off"
        eq = f"{len(p.eq_bands)} band(s)" if p.eq_bands else "off"
        print(f"  {name:<14} LUFS={p.target_lufs:>5}  HP={p.highpass_hz:>4}Hz  EQ={eq:<10}  comp={comp}")
        print(f"      {p.description}")


@app.command("show-preset")
def show_preset(slug: str, preset: str):
    """Set a show's audio preset by slug. Use `nn audio-presets` to list."""
    from .process.audio import PRESETS
    if preset not in PRESETS:
        print(f"unknown preset {preset!r}; available: {', '.join(PRESETS)}")
        raise typer.Exit(1)
    cfg = load_config()
    from .db import connect
    conn = connect(cfg)
    cur = conn.execute("UPDATE shows SET audio_preset=? WHERE slug=?", (preset, slug))
    conn.close()
    if cur.rowcount == 0:
        print(f"no show with slug {slug!r}")
        raise typer.Exit(1)
    print(f"set {slug} → {preset}")


@app.command()
def search(query: str, limit: int = typer.Option(10, "--limit")):
    """Full-text search the transcript archive."""
    cfg = load_config()
    from .transcripts import search as do_search
    hits = do_search(cfg, query, limit=limit)
    if not hits:
        print("(no matches)")
        return
    for h in hits:
        print(f"  ep{h['episode_id']:>4} · {h.get('show_title') or '—':<24} · {h['title'] or ''}")
        print(f"        {h['snippet']}")
        print(f"        bm25={h['bm25']:.2f}")


# ── Local smoke test (no nginx / no OBS / no RTMP destination) ─────
@app.command("make-test-clip")
def make_test_clip(
    out: str = typer.Argument("data/inbox/_smoke_test.mp4"),
    seconds: int = typer.Option(20, "--seconds"),
):
    """Generate a synthetic test clip (color bars + 1 kHz tone) via ffmpeg.
    Useful for first-boot smoke testing without a real video file."""
    cfg = load_config()
    out_path = absolute_path(cfg, out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        print("ffmpeg not in PATH"); raise typer.Exit(1)
    import subprocess
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i", f"smptebars=size=1920x1080:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=1000:beep_factor=4:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-1500:]); raise typer.Exit(proc.returncode)
    print(f"wrote {out_path} ({out_path.stat().st_size // 1024} KiB)")


@app.command("smoke-test")
def smoke_test(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """End-to-end local sanity check. No RTMP / OBS / nginx required.

    Creates a `smoke-test` show + a daily slot, generates a test clip,
    drives it through ingest → process → archive → schedule. Reports
    where everything landed and tells you what to run next.
    """
    _setup_logging(verbose)
    cfg = load_config()
    from .db import connect, migrate as db_migrate
    import json as _json
    import subprocess

    print(">> migrate")
    print("   applied:", db_migrate(cfg) or "none (already up to date)")

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe missing — install with `brew install ffmpeg`")
        raise typer.Exit(1)

    inbox = absolute_path(cfg, cfg.ingest.inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    test_clip = inbox / "_smoke_test.mp4"
    if not test_clip.exists():
        print(">> generate test clip")
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i", "smptebars=size=1920x1080:rate=30:duration=20",
            "-f", "lavfi", "-i", "sine=frequency=1000:beep_factor=4:sample_rate=48000:duration=20",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(test_clip),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stderr[-1500:]); raise typer.Exit(1)
    print(f"   test clip: {test_clip}")

    print(">> seed show + source + slot + episode")
    conn = connect(cfg)
    conn.execute(
        """INSERT OR IGNORE INTO shows (slug, title, contributor,
            default_duration_min, ad_breaks_per_hour, audio_preset)
           VALUES ('smoke-test','Smoke Test','smoke-test',5,0,'DIALOG_TIGHT')"""
    )
    show_id = conn.execute("SELECT id FROM shows WHERE slug='smoke-test'").fetchone()["id"]
    conn.execute(
        """INSERT OR IGNORE INTO sources (show_id, kind, config, poll_minutes)
           VALUES (?, 'local', ?, 60)""",
        (show_id, _json.dumps({"path": str(inbox), "patterns": ["_smoke_test.mp4"]})),
    )
    src_id = conn.execute("SELECT id FROM sources WHERE show_id=?", (show_id,)).fetchone()["id"]
    # Insert episode directly in 'fetched' status (skip the polling step).
    conn.execute(
        """INSERT OR IGNORE INTO episodes
            (show_id, source_id, external_id, title, raw_path, status, fetched_at)
           VALUES (?, ?, ?, 'Smoke Test Clip', ?, 'fetched', datetime('now'))""",
        (show_id, src_id, str(test_clip.resolve()), str(test_clip)),
    )
    ep_id = conn.execute(
        "SELECT id FROM episodes WHERE source_id=? AND external_id=?",
        (src_id, str(test_clip.resolve())),
    ).fetchone()["id"]
    # A daily slot snapped to the next 30-min boundary so the playlist
    # immediately resolves the smoke-test episode (not at midnight).
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    _tz = ZoneInfo(cfg.timezone)
    _now = _dt.now(_tz)
    _slot_min = cfg.schedule.slot_minutes
    _next = ((_now.hour * 60 + _now.minute) // _slot_min + 1) * _slot_min
    conn.execute(
        """INSERT OR IGNORE INTO slots
            (label, day_of_week, start_minute, length_min, rule_type,
             rule_payload, recurrence)
           VALUES ('Smoke Test', NULL, ?, 30, 'show_rotation', ?, 'daily')""",
        (_next, _json.dumps({"show_id": show_id, "policy": "newest_unaired"})),
    )
    conn.close()
    print(f"   show_id={show_id} source_id={src_id} episode_id={ep_id}")

    # Make sure a slate exists (tiny black mp4) so fallbacks have something.
    slate = absolute_path(cfg, cfg.schedule.slate_path)
    if not slate.exists():
        slate.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-y",
             "-f", "lavfi", "-i", "color=black:size=1920x1080:rate=30:duration=10",
             "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
             "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", str(slate)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print("warning: failed to generate slate:", proc.stderr[-500:])
        else:
            print(f"   wrote slate stub: {slate}")

    print(">> process the episode (loudnorm, EQ, comp, scale, faststart)")
    from .process import process_one
    print("  ", process_one(cfg, ep_id))

    print(">> regenerate the rolling playlist")
    from .schedule import regenerate_rolling_playlist
    print("  ", regenerate_rolling_playlist(cfg))

    archive = absolute_path(cfg, cfg.process.archive_dir)
    ffconcat = absolute_path(cfg, "data/run_logs/current_playlist.ffconcat")
    print()
    print("done.")
    print(f"  archive dir         : {archive}")
    print(f"  rolling playlist    : {ffconcat}")
    print()
    print("next steps to test on your current Mac (no nginx / OBS / RTMP needed):")
    print("  1. nn admin                    # open http://127.0.0.1:8765")
    print("  2. nn playout-test             # writes /tmp/nn_test_output.ts")
    print("       open it in VLC or QuickTime to verify playout")
    print("  3. nn transcribe %d           # if whisper-cpp + a model are installed" % ep_id)
    print("  4. nn search 'beep'            # FTS5 across the archive")


@app.command("playout-test")
def playout_test(
    output: str = typer.Option("/tmp/nn_test_output.ts", "--output", "-o"),
    duration_sec: int = typer.Option(30, "--seconds"),
):
    """Run the playout pipeline against a LOCAL FILE instead of RTMP.

    Useful for verifying the encode chain (loudnorm + master bus +
    H.264/AAC) without needing nginx-rtmp or OBS or an upstream
    destination. Open the resulting .ts in VLC or QuickTime.
    """
    cfg = load_config()
    from .playout.pusher import build_command
    import subprocess
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_command(cfg, output_override=[
        "-t", str(duration_sec), "-f", "mpegts", str(out_path),
    ], realtime=False)
    print("running:", " ".join(cmd))
    rc = subprocess.call(cmd)
    print(f"exit={rc}  output={out_path}  size={out_path.stat().st_size if out_path.exists() else 'missing'}")


@app.command()
def preview(
    port: int = typer.Option(8888, "--port", "-p", help="HTTP port for HLS playback"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Preview the channel live in a browser or VLC (no RTMP / nginx needed).

    Runs the full broadcast encode chain (H.264 + AAC + master bus) and
    writes HLS segments to /tmp/nn_preview_hls/, then serves them over
    HTTP so any HLS-capable player can watch.

      Open in Safari (native HLS):  http://127.0.0.1:<port>/live.m3u8
      Open in VLC:  vlc http://127.0.0.1:<port>/live.m3u8
    """
    import functools
    import http.server
    import subprocess
    import threading

    _setup_logging(verbose)
    cfg = load_config()
    from .playout.pusher import build_command

    hls_dir = Path("/tmp/nn_preview_hls")
    hls_dir.mkdir(parents=True, exist_ok=True)
    m3u8 = hls_dir / "live.m3u8"

    cmd = build_command(cfg, output_override=[
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments",
        str(m3u8),
    ], realtime=True)

    # Serve the HLS directory over HTTP.
    # Safari requires correct MIME types to play HLS — Python's default
    # mime database doesn't know .m3u8 or .ts.
    _MIME = {
        ".m3u8": "application/x-mpegurl",
        ".ts":   "video/MP2T",
    }

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # suppress per-request noise

        def guess_type(self, path):
            ext = Path(str(path)).suffix.lower()
            return _MIME.get(ext) or super().guess_type(path)

    handler = functools.partial(_QuietHandler, directory=str(hls_dir))
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/live.m3u8"
    print(f"\n  Channel preview is live:")
    print(f"    Safari / QuickTime : {url}")
    print(f"    VLC                : open -a VLC '{url}'")
    print(f"\n  Ctrl+C to stop.\n")

    proc = subprocess.Popen(cmd)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    app()
