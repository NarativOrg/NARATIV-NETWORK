"""Audio polish presets.

A preset is a named chain of ffmpeg filters tuned for a kind of content.
Each show picks one. Presets cover three concerns:

  - tone shaping     (highpass, EQ bands)
  - dynamics         (compressor)
  - loudness target  (loudnorm I=)

Loudness targets follow the venue:
  -23 LUFS  EBU R128 broadcast (preserves dynamics, slate-style)
  -16 LUFS  YouTube / Spotify / TikTok normalization (sounds "loud")

We deliberately do NOT use Studio-Sound-style aggressive denoise + dialog
isolation here. Those break music, ambience, and live-room sound. A
preset is conservative by default; opt-in to harder processing per show.

Two-stage application:

  per-file (process time)  applied once during processing, baked into the
                           archive copy.  Heavy lifting goes here.
  master bus (playout)     applied to the live encoder output.  Just a
                           catch-all compressor + true-peak limiter so
                           nothing leaves the channel above broadcast
                           ceiling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompressorParams:
    threshold_db: float = -18.0
    ratio: float = 3.0
    attack_ms: float = 20.0
    release_ms: float = 200.0
    makeup_db: float = 2.0


@dataclass
class EqBand:
    freq: float
    q: float
    gain_db: float


@dataclass
class AudioPreset:
    name: str
    description: str
    highpass_hz: float = 0.0
    eq_bands: list = field(default_factory=list)
    compressor: Optional[CompressorParams] = None
    target_lufs: float = -23.0


PRESETS: dict[str, AudioPreset] = {
    "DIALOG_TIGHT": AudioPreset(
        name="DIALOG_TIGHT",
        description="Tight talking-head / podcast / monologue. Streaming-loud.",
        highpass_hz=80,
        eq_bands=[
            EqBand(freq=200,  q=1.0, gain_db=-2.0),   # mud cut
            EqBand(freq=3000, q=1.0, gain_db=+1.0),   # presence lift
        ],
        compressor=CompressorParams(threshold_db=-18, ratio=3.0,
                                     attack_ms=20, release_ms=200,
                                     makeup_db=2.0),
        target_lufs=-16.0,
    ),
    "DOC_NATURAL": AudioPreset(
        name="DOC_NATURAL",
        description="Documentary / interview / b-roll w/ ambience. Broadcast-safe.",
        highpass_hz=60,
        eq_bands=[
            EqBand(freq=200,  q=0.7, gain_db=-1.0),
            EqBand(freq=4000, q=1.0, gain_db=+0.5),
        ],
        compressor=CompressorParams(threshold_db=-22, ratio=2.0,
                                     attack_ms=50, release_ms=400,
                                     makeup_db=1.0),
        target_lufs=-23.0,
    ),
    "MUSIC_LIGHT": AudioPreset(
        name="MUSIC_LIGHT",
        description="Music-heavy or scored content. Loudnorm only — no EQ, no compression.",
        highpass_hz=0,
        eq_bands=[],
        compressor=None,
        target_lufs=-23.0,
    ),
    "PANEL": AudioPreset(
        name="PANEL",
        description="Multi-mic panel discussion. Aggressive leveling so quietest "
                    "guest matches loudest.",
        highpass_hz=80,
        eq_bands=[
            EqBand(freq=200,  q=1.0, gain_db=-2.0),
            EqBand(freq=3000, q=1.0, gain_db=+2.0),
        ],
        compressor=CompressorParams(threshold_db=-20, ratio=4.0,
                                     attack_ms=10, release_ms=150,
                                     makeup_db=2.5),
        target_lufs=-16.0,
    ),
    "NEWS_HARD": AudioPreset(
        name="NEWS_HARD",
        description="Broadcast news desk style. Polished, forward, brittle on purpose.",
        highpass_hz=80,
        eq_bands=[
            EqBand(freq=250,   q=0.8, gain_db=-3.0),
            EqBand(freq=4000,  q=1.0, gain_db=+2.0),
            EqBand(freq=10000, q=0.7, gain_db=+1.0),
        ],
        compressor=CompressorParams(threshold_db=-16, ratio=4.0,
                                     attack_ms=5, release_ms=100,
                                     makeup_db=3.0),
        target_lufs=-16.0,
    ),
}

DEFAULT_PRESET = "DIALOG_TIGHT"


def get_preset(name: str | None) -> AudioPreset:
    if not name:
        return PRESETS[DEFAULT_PRESET]
    return PRESETS.get(name) or PRESETS[DEFAULT_PRESET]


# ── filter-chain builders ───────────────────────────────────────────

def _eq_filter(band: EqBand) -> str:
    return f"equalizer=f={band.freq}:width_type=q:w={band.q}:g={band.gain_db}"


def _compressor_filter(c: CompressorParams) -> str:
    return (f"acompressor=threshold={c.threshold_db}dB"
            f":ratio={c.ratio}"
            f":attack={c.attack_ms}"
            f":release={c.release_ms}"
            f":makeup={c.makeup_db}")


def per_file_audio_chain(preset: AudioPreset, measured: dict,
                         silence_db: float, silence_min_sec: float) -> str:
    """Build the audio filter chain for the per-file (processing) pass.

    `measured` is the loudnorm pass-1 measurement dict; we apply pass-2
    with `linear=true` so the loudness landing is exact.
    """
    parts: list[str] = []

    if preset.highpass_hz and preset.highpass_hz > 0:
        parts.append(f"highpass=f={preset.highpass_hz}")
    for band in preset.eq_bands:
        parts.append(_eq_filter(band))
    if preset.compressor:
        parts.append(_compressor_filter(preset.compressor))

    parts.append(
        f"loudnorm=I={preset.target_lufs}:TP=-2:LRA=11:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=summary"
    )

    # Trim silence at head and tail (never the middle).
    parts.append(
        f"silenceremove=start_periods=1:start_silence={silence_min_sec}:start_threshold={silence_db}dB"
    )
    parts.append("areverse")
    parts.append(
        f"silenceremove=start_periods=1:start_silence={silence_min_sec}:start_threshold={silence_db}dB"
    )
    parts.append("areverse")
    return ",".join(parts)


def master_bus_chain(master_cfg: dict | None) -> str | None:
    """Build the playout-time master-bus chain.

    Default: gentle 2:1 compressor + true-peak limiter at -1 dBTP.
    Configurable via [audio.master_bus] in config.
    """
    cfg = master_cfg or {}
    if cfg.get("disabled"):
        return None
    parts: list[str] = []

    comp = cfg.get("compressor") if isinstance(cfg.get("compressor"), dict) else {}
    if comp is None:
        comp = {}
    if not cfg.get("compressor_disabled"):
        parts.append(
            f"acompressor=threshold={comp.get('threshold_db', -8.0)}dB"
            f":ratio={comp.get('ratio', 2.0)}"
            f":attack={comp.get('attack_ms', 30)}"
            f":release={comp.get('release_ms', 300)}"
            f":makeup={max(1, comp.get('makeup_db', 1))}"
        )

    limit_db = cfg.get("limiter_dbtp", -1.0)
    if not cfg.get("limiter_disabled"):
        parts.append(f"alimiter=limit={limit_db}dB:level=disabled")

    return ",".join(parts) if parts else None
