"""Local-folder source.

config: {"path": "/Users/.../show-folder", "patterns": ["*.mp4","*.mov"]}

Useful for: a creator who AirDrops or syncs files via Dropbox/iCloud
into a folder the Mac mini can see.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .source import FetchedFile, Source

DEFAULT_PATTERNS = ("*.mp4", "*.mov", "*.mkv", "*.m4v", "*.mxf")


class LocalSource(Source):
    kind = "local"

    def list_available(self):
        root = Path(self.config["path"]).expanduser()
        if not root.exists():
            return
        patterns = self.config.get("patterns") or DEFAULT_PATTERNS
        for pattern in patterns:
            for path in sorted(root.glob(pattern)):
                if not path.is_file():
                    continue
                stat = path.stat()
                yield FetchedFile(
                    external_id=str(path.resolve()),
                    title=path.stem,
                    suggested_filename=path.name,
                    bytes=stat.st_size,
                    modified_at=str(stat.st_mtime),
                    metadata={"source_path": str(path)},
                )

    def download(self, file: FetchedFile, dest: Path) -> Path:
        src = Path(file.metadata["source_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size != file.bytes:
            shutil.copy2(src, dest)
        return dest
