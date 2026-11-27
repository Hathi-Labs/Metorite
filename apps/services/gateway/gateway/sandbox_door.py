"""The size cap and the log line of the sandbox door (SBX-2, task 3801).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.2 decisions 8, 9 and 13.

``SandboxDoorLimits`` is a pure ASGI middleware. It opens one record of the
call for each HTTP request (``sandbox_capabilities.open_door_call``). The
sandbox sibling of branch 0 (``acb_auth.deps._sandbox_user``) fills it at its
last step. A request that the sibling did not admit passes through byte for
byte, with one step before its status. When the sibling refused a live
capability for a code of ``COUNTED_REFUSALS``, the middleware adds 1 to the
refusal counter of the token BEFORE it forwards ``http.response.start``
(``sandbox_capabilities.count_refusal``, §12.4 decision 9). So the count
exists before the client reads the status. For an admitted call, the middleware buffers the answer up to
:data:`~acb_auth.sandbox_capabilities.ANSWER_CAP_BYTES` (4 MB). A larger
answer becomes the 422 ``sandbox_response_too_large``, and no byte of it
leaves. After the answer it writes the line ``sandbox.door.call``.

It holds no token, no hash and no answer text. It reads no header.
Fence: ``tests/unit/test_sandbox_door_identity.py`` and
``tests/unit/test_sandbox_door_log_lines.py``.
"""
from __future__ import annotations

import json
from typing import Any

from acb_auth import sandbox_capabilities


def _too_large() -> tuple[dict[str, Any], bytes]:
    body = json.dumps({"detail": sandbox_capabilities.TOO_LARGE}).encode("utf-8")
    start = {
        "type": "http.response.start",
        "status": 422,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    }
    return start, body


class SandboxDoorLimits:
    """The 4 MB cap and the log line of each admitted sandbox call."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        holder, token = sandbox_capabilities.open_door_call()
        state: dict[str, Any] = {"start": None, "chunks": [], "size": 0, "over": False,
                                 "sent": False, "status": 0}

        async def capped_send(message: Any) -> None:
            if not holder.admitted:
                if message.get("type") == "http.response.start":
                    await sandbox_capabilities.count_refusal(holder)
                await send(message)
                return
            kind = message.get("type")
            if kind == "http.response.start":
                state["start"] = message
                state["status"] = int(message.get("status", 0))
                return
            if kind != "http.response.body" or state["sent"]:
                return
            if not state["over"]:
                body = message.get("body", b"") or b""
                state["size"] += len(body)
                if state["size"] > sandbox_capabilities.ANSWER_CAP_BYTES:
                    state["over"] = True
                    state["chunks"] = []
                else:
                    state["chunks"].append(body)
            if message.get("more_body", False):
                return
            state["sent"] = True
            if state["over"]:
                start, body = _too_large()
                state["status"] = 422
                await send(start)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            await send(state["start"])
            await send({"type": "http.response.body", "body": b"".join(state["chunks"]),
                        "more_body": False})

        try:
            await self.app(scope, receive, capped_send)
        except Exception:
            if holder.admitted:
                self._log(holder, 500, "error", 0)
            raise
        else:
            if holder.admitted:
                code = sandbox_capabilities.TOO_LARGE if state["over"] else "allowed"
                size = 0 if state["over"] else state["size"]
                self._log(holder, state["status"], code, size)
        finally:
            # A refusal whose answer never started still counts (no-op when
            # the count ran, or when the call was not refused).
            await sandbox_capabilities.count_refusal(holder)
            sandbox_capabilities.close_door_call(token)

    @staticmethod
    def _log(holder: sandbox_capabilities.DoorCall, status: int, code: str, size: int) -> None:
        sandbox_capabilities.log_door_call(
            status=status, code=code, method=holder.method, template=holder.template,
            org=holder.organization_id, run_id=holder.run_id, phase=holder.phase,
            call_n=holder.call_n, size=size, warn=code == sandbox_capabilities.TOO_LARGE,
        )
