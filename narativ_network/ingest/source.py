"""Source abstraction.

A Source knows how to enumerate available episode files for a Show and
download a specific one. Each `sources` row in the DB hydrates into a
concrete Source via `build_source`.

Adding a new backend = add a kind here + implement the two methods.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

SourceKind = Literal["local", "gdrive", "rss", "youtube", "webdav", "dropbox"]


@dataclass
class FetchedFile:
    external_id: str
    title: str
    suggested_filename: str
    bytes: int | None
    modified_at: str | None
    metadata: dict


class Source(ABC):
    kind: SourceKind

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def list_available(self) -> Iterable[FetchedFile]:
        """Enumerate candidate episode files at the source."""

    @abstractmethod
    def download(self, file: FetchedFile, dest: Path) -> Path:
        """Download `file` to `dest`. Returns the actual local path written."""


def build_source(kind: str, config_json: str | dict) -> Source:
    config = json.loads(config_json) if isinstance(config_json, str) else config_json
    if kind == "local":
        from .local import LocalSource
        return LocalSource(config)
    if kind == "gdrive":
        from .gdrive import GDriveSource
        return GDriveSource(config)
    raise NotImplementedError(f"Source kind {kind!r} not yet implemented")
