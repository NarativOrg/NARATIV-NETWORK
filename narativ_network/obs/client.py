"""Minimal OBS WebSocket v5 client.

We don't pull in obs-websocket-py because:
  - It pins websockets versions that fight with our other deps.
  - We only need ~5 ops: identify, GetSceneList, SetCurrentProgramScene,
    GetCurrentProgramScene, GetStreamStatus.

Spec reference: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md
We implement v5, which is shipped with OBS 28+.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import uuid
from typing import Any

# `websockets` is sync via its `sync` submodule in v12+.
from websockets.sync.client import connect as ws_connect

log = logging.getLogger(__name__)

OP_HELLO = 0
OP_IDENTIFY = 1
OP_IDENTIFIED = 2
OP_REQUEST = 6
OP_REQUEST_RESPONSE = 7


class OBSError(RuntimeError):
    pass


class OBSClient:
    """One short-lived connection per command. Keeps the implementation
    trivial and avoids needing a reconnect loop. ~50ms per call locally.
    """

    def __init__(self, url: str, password: str | None, timeout: float = 5.0):
        self.url = url
        self.password = password or ""
        self.timeout = timeout

    # ── high-level ────────────────────────────────────────────────────
    def get_current_scene(self) -> str:
        resp = self._request("GetCurrentProgramScene", {})
        return resp.get("currentProgramSceneName") or resp.get("sceneName") or ""

    def set_current_scene(self, scene_name: str) -> None:
        self._request("SetCurrentProgramScene", {"sceneName": scene_name})

    def list_scenes(self) -> list[str]:
        resp = self._request("GetSceneList", {})
        return [s["sceneName"] for s in resp.get("scenes", [])]

    def get_stream_status(self) -> dict:
        return self._request("GetStreamStatus", {})

    def set_input_settings(self, input_name: str, settings: dict, overlay: bool = True) -> None:
        """Update settings on a Source/Input. `overlay=True` means partial
        merge — only the fields supplied are changed.
        """
        self._request("SetInputSettings", {
            "inputName": input_name,
            "inputSettings": settings,
            "overlay": overlay,
        })

    def get_input_settings(self, input_name: str) -> dict:
        return self._request("GetInputSettings", {"inputName": input_name})

    def trigger_media_input_action(self, input_name: str, action: str) -> None:
        """action ∈ {OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART, _STOP, _PLAY, ...}."""
        self._request("TriggerMediaInputAction", {
            "inputName": input_name, "mediaAction": action,
        })

    # ── plumbing ──────────────────────────────────────────────────────
    def _request(self, request_type: str, request_data: dict) -> dict:
        with ws_connect(self.url, open_timeout=self.timeout, close_timeout=self.timeout) as ws:
            hello = json.loads(ws.recv(timeout=self.timeout))
            if hello.get("op") != OP_HELLO:
                raise OBSError(f"expected Hello, got op={hello.get('op')}")
            d = hello.get("d", {})
            identify_payload: dict[str, Any] = {"rpcVersion": d.get("rpcVersion", 1)}
            auth = d.get("authentication")
            if auth:
                if not self.password:
                    raise OBSError("OBS requires a password but none configured")
                secret = base64.b64encode(
                    hashlib.sha256(
                        (self.password + auth["salt"]).encode("utf-8")
                    ).digest()
                ).decode()
                challenge = base64.b64encode(
                    hashlib.sha256(
                        (secret + auth["challenge"]).encode("utf-8")
                    ).digest()
                ).decode()
                identify_payload["authentication"] = challenge

            ws.send(json.dumps({"op": OP_IDENTIFY, "d": identify_payload}))
            identified = json.loads(ws.recv(timeout=self.timeout))
            if identified.get("op") != OP_IDENTIFIED:
                raise OBSError(f"OBS rejected Identify: {identified}")

            req_id = uuid.uuid4().hex
            ws.send(json.dumps({
                "op": OP_REQUEST,
                "d": {
                    "requestType": request_type,
                    "requestId": req_id,
                    "requestData": request_data,
                },
            }))

            while True:
                msg = json.loads(ws.recv(timeout=self.timeout))
                if msg.get("op") != OP_REQUEST_RESPONSE:
                    continue
                d = msg["d"]
                if d.get("requestId") != req_id:
                    continue
                status = d.get("requestStatus", {})
                if not status.get("result"):
                    raise OBSError(f"{request_type}: {status.get('comment') or status}")
                return d.get("responseData") or {}
