"""Uploader abstraction for upstream.so.

Three modes (chosen via cfg.raw['upstream']['upload_mode']):

  manual     — does NOT touch upstream.so. Just confirms the daily build
               artifact is on disk and writes a small status file. You
               upload by hand using checklist.md.
  api        — STUB. Once we have upstream.so's API spec, fill in
               `_upload_api()` with auth + endpoint calls. Today this
               raises NotImplementedError if selected.
  playwright — STUB. Drive upstream.so's web UI in headless Chromium.
               Last resort if no API exists. Requires `pip install
               playwright && playwright install chromium`.

The `Uploader.upload(plan)` contract is the same across modes: take a
DailyPlan, push it to upstream.so, return a dict of what happened.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from .daily_build import DailyPlan

log = logging.getLogger(__name__)


class Uploader(ABC):
    mode: str

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @abstractmethod
    def upload(self, plan: DailyPlan) -> dict: ...


class ManualUploader(Uploader):
    """Doesn't talk to upstream.so. Stamps the artifact ready and gets out
    of the way. The checklist.md tells you what to do.
    """
    mode = "manual"

    def upload(self, plan: DailyPlan) -> dict:
        out = Path(plan.out_dir)
        status = {
            "mode": "manual",
            "air_date": plan.air_date,
            "out_dir": str(out),
            "checklist": str(out / "checklist.md"),
            "manifest": str(out / "manifest.json"),
            "files_dir": str(out / "files"),
            "ready_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "instruction": "Open the checklist and upload to upstream.so by hand.",
        }
        (out / "_status.json").write_text(json.dumps(status, indent=2))
        log.info("manual uploader: artifact ready at %s", out)
        return status


class ApiUploader(Uploader):
    """Stub for upstream.so REST API. Fill in once we have docs."""
    mode = "api"

    def upload(self, plan: DailyPlan) -> dict:
        raise NotImplementedError(
            "upstream.so API client not implemented yet. "
            "Configure [upstream].upload_mode='manual' or fill in this stub "
            "once you've got the API docs/credentials."
        )


class PlaywrightUploader(Uploader):
    """Stub for browser-automation upload. Last resort."""
    mode = "playwright"

    def upload(self, plan: DailyPlan) -> dict:
        raise NotImplementedError(
            "Playwright uploader not implemented. Install with "
            "`pip install playwright && playwright install chromium`, "
            "then implement the login + library-upload + playlist-build flow."
        )


def build_uploader(cfg: Config) -> Uploader:
    upstream_cfg = cfg.raw.get("upstream", {})
    mode = upstream_cfg.get("upload_mode", "manual")
    if mode == "manual":
        return ManualUploader(cfg)
    if mode == "api":
        return ApiUploader(cfg)
    if mode == "playwright":
        return PlaywrightUploader(cfg)
    raise ValueError(f"unknown upstream.upload_mode: {mode!r}")
