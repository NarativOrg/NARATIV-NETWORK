"""Configuration loading.

Looks in (first found wins):
  1. $NN_CONFIG (path to a TOML file)
  2. ~/.narativ-network/config.toml
  3. ./narativ_network/config.example.toml (fallback for `nn doctor`)
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CONFIG_PATHS = [
    Path(os.environ["NN_CONFIG"]) if os.environ.get("NN_CONFIG") else None,
    Path.home() / ".narativ-network" / "config.toml",
    Path(__file__).parent / "config.example.toml",
]


@dataclass
class PlayoutConfig:
    rtmp_url: str = "rtmp://localhost/test/stream"
    stream_key: str = ""
    video_bitrate_kbps: int = 6000
    audio_bitrate_kbps: int = 192
    width: int = 1920
    height: int = 1080
    fps: int = 30
    keyframe_interval_sec: int = 2


@dataclass
class IngestConfig:
    poll_seconds: int = 900
    gdrive_service_account_json: str = ""
    inbox_dir: str = "data/inbox"


@dataclass
class ProcessConfig:
    archive_dir: str = "data/archive"
    descript_queue_dir: str = "data/descript_queue"
    descript_done_dir: str = "data/descript_done"
    target_lufs: float = -23.0
    silence_db: float = -40.0
    silence_min_sec: float = 1.5


@dataclass
class ScheduleConfig:
    slot_minutes: int = 30
    fallback_reair_min_age_days: int = 7
    rolling_horizon_hours: int = 6
    regenerate_every_minutes: int = 5
    slate_path: str = "data/slates/we_will_be_right_back.mp4"


@dataclass
class WatchdogConfig:
    check_interval_sec: int = 10
    silence_threshold_sec: int = 5
    black_threshold_sec: int = 5
    restart_max_per_minute: int = 3


@dataclass
class AdminConfig:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    admin_token: str = ""


@dataclass
class Config:
    project_root: Path = field(default_factory=lambda: Path.cwd())
    db_path: str = "data/narativ_network.sqlite"
    timezone: str = "America/New_York"
    playout: PlayoutConfig = field(default_factory=PlayoutConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    process: ProcessConfig = field(default_factory=ProcessConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    raw: dict = field(default_factory=dict)


def _load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_config() -> Config:
    cfg = Config()
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate and candidate.exists():
            data = _load_toml(candidate)
            cfg.raw = data
            # project_root: honor what's in the file, but if the configured
            # path doesn't exist (typical when copied from the example),
            # silently fall back to the cwd. Saves users a "permission
            # denied: /Users/narativ" gotcha on first install.
            configured_root = data.get("project_root")
            if configured_root:
                p = Path(configured_root).expanduser()
                cfg.project_root = p if p.exists() else Path.cwd()
            cfg.db_path = data.get("db_path", cfg.db_path)
            cfg.timezone = data.get("timezone", cfg.timezone)
            for section, dataclass_field in (
                ("playout", cfg.playout),
                ("ingest", cfg.ingest),
                ("process", cfg.process),
                ("schedule", cfg.schedule),
                ("watchdog", cfg.watchdog),
                ("admin", cfg.admin),
            ):
                values = data.get(section, {})
                for key, value in values.items():
                    if hasattr(dataclass_field, key):
                        setattr(dataclass_field, key, value)
            break
    return cfg


def absolute_path(cfg: Config, relative_or_absolute: str) -> Path:
    p = Path(relative_or_absolute).expanduser()
    if p.is_absolute():
        return p
    return (cfg.project_root / p).resolve()
