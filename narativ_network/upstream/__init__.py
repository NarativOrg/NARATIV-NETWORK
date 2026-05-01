"""upstream.so integration.

Two halves:

  daily_build  —  produce a per-second timed artifact for one calendar day
                  (ordered files + manifest.json + manifest.csv + checklist.md)
  uploader     —  ship that artifact to upstream.so. Three modes:
                    'manual'    : just renders the artifact; you upload by hand.
                    'api'       : calls upstream.so's REST API (stub until docs).
                    'playwright': drives upstream.so's UI in headless Chromium.

Pick the mode in config: [upstream].upload_mode.
"""
from .daily_build import build_daily_plan, DailyPlan
from .uploader import build_uploader, Uploader

__all__ = ["build_daily_plan", "DailyPlan", "build_uploader", "Uploader"]
