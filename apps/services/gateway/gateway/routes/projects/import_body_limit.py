"""Projects · file import — a cap on the upload body, before it is read
(WS-41 I-7).

Spec: ``project-docs/specs/project_import.md`` §7.4, §9 row I-7.

FastAPI reads and spools a whole multipart body to disk BEFORE any
dependency runs: the sign-in check, the permission and the import flag
included. So the route's own 50 MB-per-file check acts only after the bytes
have landed, and anybody could fill the disk with one request. This pure-ASGI
wrapper refuses an import upload larger than ``LIMIT_BYTES`` before the route
sees it: at once when the declared length is too large, and part way through
when a body with no declared length runs past the cap.

The cap lives here and not in Caddy because a new Caddy matcher changes the
sign-in lines that `test_caddy_auth_gate.py` holds for the owner, and because
the browser's path (app.*, the Next proxy, the gateway on loopback) never
passes Caddy's api host at all.
"""

from __future__ import annotations

import json
from typing import Any

#: Five files of 50 MB (``imports.MAX_FILES`` times ``imports.MAX_FILE_BYTES``)
#: and the multipart envelope.
LIMIT_BYTES = 260 * 1024 * 1024
PREFIX = "/projects/import/"

_REFUSAL = json.dumps(
    {"detail": "The upload is larger than an import can take. Export one space at a time."}
).encode("utf-8")


class _TooLarge(Exception):
    pass


async def _refuse(send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_REFUSAL)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _REFUSAL})


class ImportBodyLimit:
    """Wraps the app. Touches only a POST under ``/projects/import/``."""

    def __init__(self, app: Any, limit: int = LIMIT_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not str(scope.get("path", "")).startswith(PREFIX)
        ):
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await _refuse(send)
            return

        seen = 0
        started = False

        async def counted() -> Any:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise _TooLarge
            return message

        async def watched(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except _TooLarge:
            if not started:
                await _refuse(send)
