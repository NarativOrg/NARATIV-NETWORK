"""Google Drive source via service account.

config: {"folder_id": "1AbC...", "service_account_json": "/path/to/sa.json"}

Operating model: each contributor shares their Drive folder with the
service account's email address. We poll the folder, list video files,
and download new ones into data/inbox/.

Service-account JSON is loaded from `service_account_json` in the source's
config, OR from the global `ingest.gdrive_service_account_json` config.
"""
from __future__ import annotations

import io
from pathlib import Path

from .source import FetchedFile, Source

VIDEO_MIME_PREFIXES = ("video/",)
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class GDriveSource(Source):
    kind = "gdrive"

    def __init__(self, config: dict):
        super().__init__(config)
        self._service = None

    def _client(self):
        if self._service is not None:
            return self._service
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sa_path = self.config.get("service_account_json")
        if not sa_path:
            raise RuntimeError(
                "gdrive source missing service_account_json (set in source config "
                "or global ingest.gdrive_service_account_json)"
            )
        creds = service_account.Credentials.from_service_account_file(
            sa_path, scopes=SCOPES
        )
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def list_available(self):
        folder_id = self.config["folder_id"]
        service = self._client()
        page_token = None
        query = (
            f"'{folder_id}' in parents and trashed = false "
            "and (mimeType contains 'video/' or mimeType = 'application/octet-stream')"
        )
        while True:
            resp = service.files().list(
                q=query,
                fields="nextPageToken, files(id,name,size,mimeType,modifiedTime,md5Checksum)",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for item in resp.get("files", []):
                yield FetchedFile(
                    external_id=item["id"],
                    title=item["name"].rsplit(".", 1)[0],
                    suggested_filename=item["name"],
                    bytes=int(item["size"]) if item.get("size") else None,
                    modified_at=item.get("modifiedTime"),
                    metadata={
                        "mime": item.get("mimeType"),
                        "md5": item.get("md5Checksum"),
                    },
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def download(self, file: FetchedFile, dest: Path) -> Path:
        from googleapiclient.http import MediaIoBaseDownload

        service = self._client()
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = service.files().get_media(fileId=file.external_id, supportsAllDrives=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with io.FileIO(tmp, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024 * 8)
            done = False
            while not done:
                _status, done = downloader.next_chunk()
        tmp.rename(dest)
        return dest
