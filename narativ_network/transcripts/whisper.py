"""whisper.cpp wrapper.

We shell out to `whisper-cli` (the binary that ships with `whisper-cpp`
on Homebrew, or built from https://github.com/ggerganov/whisper.cpp).
On Apple Silicon it uses Metal automatically and is fast — typically
4-8x realtime on a Mac mini M-series.

If `whisper-cli` is missing, we fall back to looking for `main`
(the older binary name from whisper.cpp builds), and finally try the
Python `openai-whisper` package if installed. The first one that works
is used.

Configuration:

  [transcripts]
  engine        = "whisper.cpp"     # or "openai-whisper" or "auto"
  model_path    = "/opt/homebrew/share/whisper.cpp/ggml-medium.en.bin"
  language      = ""                # "" = auto-detect
  threads       = 8
  extra_args    = []                # any additional CLI args to pass

Output: a dict {"language": "en", "segments": [...], "full_text": "..."}.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _which_whisper() -> str | None:
    for name in ("whisper-cli", "whisper", "main"):
        path = shutil.which(name)
        if path:
            return path
    return None


def transcribe_with_whisper_cpp(audio_path: Path, model_path: Path,
                                language: str = "",
                                threads: int = 8,
                                extra_args: list | None = None) -> dict:
    """Run whisper.cpp on `audio_path` and return parsed JSON."""
    binary = _which_whisper()
    if not binary:
        raise RuntimeError(
            "whisper-cli / whisper / main not found in PATH. "
            "Install with `brew install whisper-cpp` or build whisper.cpp."
        )
    if not model_path.exists():
        raise RuntimeError(f"whisper model not found at {model_path}")

    with tempfile.TemporaryDirectory() as td:
        out_prefix = Path(td) / "out"
        cmd = [
            binary,
            "-m", str(model_path),
            "-f", str(audio_path),
            "-of", str(out_prefix),
            "-oj",                              # output JSON
            "-t", str(threads),
            "-pc",                              # print colors off (quiet)
        ]
        if language:
            cmd += ["-l", language]
        if extra_args:
            cmd += list(extra_args)

        log.info("whisper.cpp: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"whisper.cpp failed (rc={proc.returncode}):\n{proc.stderr[-2000:]}")

        json_path = out_prefix.with_suffix(".json")
        if not json_path.exists():
            # Some whisper.cpp builds emit `<prefix>.json.json`
            for cand in (Path(str(out_prefix) + ".json"),
                         out_prefix.parent / "out.json"):
                if cand.exists():
                    json_path = cand
                    break
        if not json_path.exists():
            raise RuntimeError(f"whisper.cpp did not produce JSON at {json_path}")

        raw = json.loads(json_path.read_text())

    return _normalize_whisper_cpp(raw)


def _normalize_whisper_cpp(raw: dict) -> dict:
    """whisper.cpp JSON looks like:
       {"systeminfo": "...", "model": {...}, "params": {...},
        "result": {"language": "en"},
        "transcription": [
          {"timestamps": {"from": "00:00:00,000", "to": "00:00:03,500"},
           "offsets": {"from": 0, "to": 3500},
           "text": " Hello there."},
          ...
        ]}
    """
    segments = []
    full_text_parts = []
    for seg in raw.get("transcription", []):
        offsets = seg.get("offsets") or {}
        start = (offsets.get("from") or 0) / 1000.0
        end = (offsets.get("to") or 0) / 1000.0
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        segments.append({"start": start, "end": end, "text": text})
        full_text_parts.append(text)
    return {
        "language": (raw.get("result") or {}).get("language", ""),
        "segments": segments,
        "full_text": " ".join(full_text_parts).strip(),
    }


def transcribe_with_openai_whisper(audio_path: Path, model_name: str = "medium.en",
                                   language: str = "") -> dict:
    """Fallback: openai-whisper Python package. Slower but pip-installable."""
    try:
        import whisper  # type: ignore
    except ImportError as e:
        raise RuntimeError("openai-whisper not installed; "
                           "`pip install openai-whisper` or use whisper.cpp") from e
    model = whisper.load_model(model_name)
    result = model.transcribe(str(audio_path), language=language or None, verbose=False)
    segments = [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        for s in result.get("segments", []) if s.get("text", "").strip()
    ]
    return {
        "language": result.get("language", ""),
        "segments": segments,
        "full_text": (result.get("text") or "").strip(),
    }
