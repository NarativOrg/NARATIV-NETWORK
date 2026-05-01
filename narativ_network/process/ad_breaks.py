"""Compute ad-break insertion points for an episode.

Phase 1 model: even-spacing breaks based on the show's
`ad_breaks_per_hour`. Default is 2/hour (one ~midway, one toward the end),
but each show can override it. Markers are timestamps (seconds from the
start of the episode) that the playout layer can use to drop in ad
content at runtime — we do NOT splice ads into the file itself.

That keeps the archive editorially clean and lets us swap ad inventory
without re-rendering anything.
"""
from __future__ import annotations


def compute_break_marks(duration_sec: float, breaks_per_hour: int) -> list[float]:
    if duration_sec <= 0 or breaks_per_hour <= 0:
        return []
    expected = max(1, round(duration_sec / 3600.0 * breaks_per_hour))
    # Place breaks at evenly spaced fractions of the runtime, but never in
    # the first or last 60 seconds.
    head_pad = 60.0
    tail_pad = 60.0
    usable = max(0.0, duration_sec - head_pad - tail_pad)
    if usable <= 0:
        return []
    return [round(head_pad + usable * (i + 1) / (expected + 1), 2)
            for i in range(expected)]
