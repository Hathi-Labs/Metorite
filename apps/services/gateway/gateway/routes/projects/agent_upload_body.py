"""Projects · the signed body of an agent upload (CIV-14b).

Spec: ``project-docs/specs/external_agent_access.md`` §12.9.

Branch 0 of ``acb_auth.deps.get_current_user`` checks the signature of a
signed agent write over the bytes of its body. A JSON route reads its body
with ``request.body()``, and that keeps the bytes. A form route does not:
FastAPI parses a multipart body BEFORE any dependency runs, so the bytes are
gone when branch 0 asks for them. The upload of ``upload_task_image``,
``POST /projects/tasks/{task_id}/attachments``, is the one form route of
``agent_oauth.MCP_WRITE_ROUTES``.

This pure-ASGI wrapper keeps a copy of the body of a SIGNED POST to that path
while the form parser reads it. The copy goes in the scope under
``agent_signing.SIGNED_BODY_SCOPE_KEY``, and branch 0 reads it there. A
signed body over :data:`LIMIT_BYTES` gets 413 before the route sees it. A
request with no signature header passes through untouched, so a person's
upload does not change.

The scope is built by the server. A client cannot set a scope key, so the
copy is always the bytes that came in.
"""

from __future__ import annotations

import json
import re
from typing import Any

from acb_auth import agent_oauth, agent_signing
from fastapi import HTTPException

#: The largest image of a signed agent and the multipart envelope around it.
LIMIT_BYTES = agent_oauth.AGENT_IMAGE_MAX_BYTES + 64 * 1024

#: The one path that this wrapper reads: the upload of a file to a task.
_UPLOAD_PATH = re.compile(r"/projects/tasks/[^/]+/attachments")

#: The signature header, as ASGI carries it.
_SIGNATURE = agent_signing.HEADER_SIGNATURE.lower().encode("latin-1")

_TOO_LARGE = "The image is larger than an agent can upload."
_REFUSAL = json.dumps({"detail": _TOO_LARGE}).encode("utf-8")


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


def _signed_upload(scope: Any) -> bool:
    """True for a POST to the upload path that carries a signature header."""
    if scope.get("type") != "http" or scope.get("method") != "POST":
        return False
    if not _UPLOAD_PATH.fullmatch(str(scope.get("path", ""))):
        return False
    return any(name.lower() == _SIGNATURE for name, _ in scope.get("headers") or ())


class AgentUploadBody:
    """Wraps the app. Touches only a signed POST to the upload path."""

    def __init__(self, app: Any, limit: int = LIMIT_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if not _signed_upload(scope):
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await _refuse(send)
            return

        held = bytearray()

        async def copied() -> Any:
            message = await receive()
            if message["type"] == "http.request":
                held.extend(message.get("body", b""))
                if len(held) > self.limit:
                    # FastAPI reads the form inside the app, and it raises an
                    # HTTPException again as it is. Any other error there
                    # becomes its 400 "There was an error parsing the body".
                    held.clear()
                    raise HTTPException(status_code=413, detail=_TOO_LARGE)
                if not message.get("more_body", False):
                    # The whole body, once. Branch 0 reads only `bytes`.
                    scope[agent_signing.SIGNED_BODY_SCOPE_KEY] = bytes(held)
            return message

        await self.app(scope, copied, send)
