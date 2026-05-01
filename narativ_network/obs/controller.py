"""High-level OBS scene controller.

Three named scenes, configurable by name in [obs] config (defaults
match the OBS setup doc in ops/obs/SETUP.md).

The break-in/return helpers also stamp run_log so the dashboard reflects
who/when.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import Config
from ..db import connect
from .client import OBSClient

log = logging.getLogger(__name__)

SCENE_SCHEDULED = "SCHEDULED"
SCENE_LIVE = "LIVE"
SCENE_STANDBY = "STANDBY"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OBSController:
    def __init__(self, cfg: Config):
        obs_cfg = cfg.raw.get("obs", {})
        url = obs_cfg.get("websocket_url") or "ws://127.0.0.1:4455"
        self.client = OBSClient(url, obs_cfg.get("websocket_password"))
        self.scene_scheduled = obs_cfg.get("scene_scheduled", SCENE_SCHEDULED)
        self.scene_live = obs_cfg.get("scene_live", SCENE_LIVE)
        self.scene_standby = obs_cfg.get("scene_standby", SCENE_STANDBY)
        # Name of the Media Source inside the LIVE scene whose `input` URL
        # we retarget per session. Default matches ops/obs/SETUP.md.
        self.live_input_name = obs_cfg.get("live_input_name", "live_source")
        self.cfg = cfg

    def current(self) -> str:
        return self.client.get_current_scene()

    def to_scheduled(self) -> str:
        self.client.set_current_scene(self.scene_scheduled)
        return self.scene_scheduled

    def to_live(self) -> str:
        self.client.set_current_scene(self.scene_live)
        return self.scene_live

    def to_standby(self) -> str:
        self.client.set_current_scene(self.scene_standby)
        return self.scene_standby

    def stream_status(self) -> dict:
        return self.client.get_stream_status()

    def set_live_source_url(self, url: str) -> None:
        """Retarget the LIVE scene's Media Source input URL.
        OBS's ffmpeg_source uses the `input` field for URL.
        """
        self.client.set_input_settings(
            self.live_input_name,
            {"is_local_file": False, "input": url, "looping": False,
             "restart_on_activate": True},
            overlay=True,
        )
        # Force a media restart so the new URL is picked up immediately
        # even if the scene is already active.
        try:
            self.client.trigger_media_input_action(
                self.live_input_name,
                "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
            )
        except Exception:
            log.debug("media restart action failed; continuing")


def _record(cfg: Config, status: str, detail: str) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            "INSERT INTO run_log (started_at, status, detail) VALUES (?, ?, ?)",
            (_utcnow(), status, detail),
        )
    finally:
        conn.close()


def break_in(cfg: Config, reason: str = "") -> dict:
    """One-shot helper: switch to LIVE scene and log it."""
    ctl = OBSController(cfg)
    previous = ctl.current()
    new = ctl.to_live()
    _record(cfg, "preempted", f"BREAK IN: {reason or '(no reason)'} — was {previous!r}")
    log.warning("BREAK IN to %s (was %s) — %s", new, previous, reason)
    return {"ok": True, "from": previous, "to": new, "reason": reason}


def return_to_air(cfg: Config) -> dict:
    """One-shot helper: switch back to SCHEDULED."""
    ctl = OBSController(cfg)
    previous = ctl.current()
    new = ctl.to_scheduled()
    _record(cfg, "ok", f"RETURN TO AIR — was {previous!r}")
    log.info("RETURN TO AIR to %s (was %s)", new, previous)
    return {"ok": True, "from": previous, "to": new}


def go_standby(cfg: Config, reason: str = "") -> dict:
    ctl = OBSController(cfg)
    previous = ctl.current()
    new = ctl.to_standby()
    _record(cfg, "preempted", f"STANDBY: {reason or '(no reason)'} — was {previous!r}")
    log.warning("STANDBY (was %s) — %s", previous, reason)
    return {"ok": True, "from": previous, "to": new, "reason": reason}
