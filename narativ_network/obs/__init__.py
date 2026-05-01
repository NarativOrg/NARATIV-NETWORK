"""OBS WebSocket integration.

OBS is the local scene switcher for the channel. It runs three scenes:

    SCHEDULED   plays the rolling playlist (delivered via local RTMP)
    LIVE        captures the live RTMP input from a presenter
    STANDBY     a slate / "we'll be right back"

This module gives our app (and the dashboard) a thin sync API to switch
between them.
"""
from .controller import (
    OBSController,
    SCENE_SCHEDULED, SCENE_LIVE, SCENE_STANDBY,
    break_in, return_to_air, go_standby,
)

__all__ = [
    "OBSController",
    "SCENE_SCHEDULED", "SCENE_LIVE", "SCENE_STANDBY",
    "break_in", "return_to_air", "go_standby",
]
