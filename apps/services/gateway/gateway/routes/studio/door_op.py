"""The executor of a door op at the Studio action door (STU-B2b-2b).

Spec: ``project-docs/specs/ai_studio.md`` §14 B2b-2. The checks stay in
``invoke.py``. This module holds only the call: it builds it, sends it to the
app, and reads the answer.

**The call is a loopback (B2b-2a).** The door sends the call through
``request.app`` with ``httpx.ASGITransport``, in the process and with no
network. So the app's own router runs it, with each gate of that router: for
Knowledge, ``require_knowledge_enabled``, ``feature:knowledge`` and
``capture_actor_via``. The door calls no handler directly, and it keeps no
second table of handlers (D136).

**The one builder.** :func:`call_of` gives the body of the door call, the
args, to ``app_manifests.build_call`` (AD-7). A ``BadCall`` raises
:class:`BadArgs` with the key name.

**The identity.** The call carries the inbound ``Authorization``,
``X-User-Email``, ``X-User-Role`` and ``X-Actor-Via`` (:data:`FORWARDED`),
each one only when the inbound request sends it exactly once. The door never
makes a header from the ``UserContext``, and it never reads the internal
token. It sends no proof of a direct write (B2c-1), no cookie and no body.
``test_studio_direct_proof.py`` holds that this file never names that proof
header. So the app
decides the call as it decides a call of the member, and the app's own gates
and rules of who sees what hold.

**The context.** :func:`run` sends the call in a NEW task. A task runs in a
copy of the context, so a ContextVar that the inner request sets
(``ACTOR_VIA`` of ``routes/projects/core.py`` and ``_TENANT`` of
``acb_common/db.py`` among them) does not come back to the door. The caller
holds no database session during the call.

**The answer (AD-12).** A 2xx gives ``{result, truncated}``: the JSON of the
app with each list of its top level cut to :data:`MAX_ROWS` rows, then cut
to :data:`MAX_ANSWER_BYTES` bytes of JSON. The door adds no untrusted wrap,
because the chat tool wraps the answer. A 4xx of the app is that app's own
refusal: its status and its bytes pass unchanged, with ``Retry-After``. A 5xx,
an answer that is not JSON and a call past :data:`TIMEOUT_S` give
:attr:`Answer.unavailable`, which the door answers 503. ``Retry-After`` of
the app passes.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from acb_common import get_logger
from acb_common.app_manifests import BadCall, Call, DoorOp, build_call
from fastapi import Request
from fastapi.responses import Response

__all__ = ["FORWARDED", "LOOPBACK", "MAX_ANSWER_BYTES", "MAX_ROWS", "STUDIO_PREFIX",
           "TIMEOUT_S", "Answer", "BadArgs", "call_of", "cut", "forwarded", "run"]

_log = get_logger("gateway.studio.door_op")

#: The inbound headers that the call carries, and no other identity header.
FORWARDED: tuple[str, ...] = ("authorization", "x-user-email", "x-user-role", "x-actor-via")

#: The base of the loopback URL. The transport sends nothing on a network.
LOOPBACK = "http://studio-door.loopback"

#: A route under this prefix is a route of Studio itself. The door refuses a
#: door op whose template starts with it, so a call never comes back to the
#: door.
STUDIO_PREFIX = "/studio"

#: AD-12: the rows of one list, and the bytes of one answer.
MAX_ROWS = 50
MAX_ANSWER_BYTES = 256 * 1024

#: The longest wait for the app, in seconds. A call past it is 503.
TIMEOUT_S = 30.0

#: A ``Retry-After`` value that passes: a whole number of seconds.
_RETRY_AFTER = re.compile(r"[0-9]{1,6}")


class BadArgs(ValueError):
    """The body of the door call is not args that ``build_call`` can use.
    ``field`` is the key of the args, or None when the body is not a JSON
    object."""

    def __init__(self, field: str | None) -> None:
        super().__init__(field or "args")
        self.field = field


@dataclass(frozen=True)
class Answer:
    """What the app answered. Exactly one of ``reply``, ``unavailable`` and a
    2xx ``result`` holds."""

    #: The HTTP status of the app, or None when no answer came.
    status: int | None
    #: The JSON of a 2xx, cut by AD-12.
    result: Any = None
    truncated: bool = False
    #: A 4xx of the app, unchanged.
    reply: Response | None = None
    #: A 5xx, an answer that is not JSON, or no answer in time.
    unavailable: bool = False
    retry_after: str | None = None


def call_of(door: DoorOp, raw: bytes) -> Call:
    """The call of *door*, from the body *raw* of the door call. An empty body
    means ``{}``. The ONE builder is ``app_manifests.build_call`` (AD-7)."""
    args: Any = {}
    if raw:
        try:
            args = json.loads(raw)
        except (ValueError, RecursionError):
            raise BadArgs(None) from None
    if not isinstance(args, dict):
        raise BadArgs(None)
    try:
        return build_call(door.app, door.name, args)
    except BadCall as exc:
        raise BadArgs(exc.name[:64]) from None


def forwarded(headers: Any) -> dict[str, str]:
    """The headers of the call: each name of :data:`FORWARDED` that *headers*
    holds exactly once. A header sent twice is not sent at all."""
    out: dict[str, str] = {}
    for name in FORWARDED:
        values = headers.getlist(name)
        if len(values) == 1:
            out[name] = values[0]
    return out


async def _send(app: Any, call: Call, headers: Mapping[str, str]) -> httpx.Response:
    """Send *call* to *app* in the process. The client holds no cookie and
    follows no redirect. An error of the app comes back as its 500."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=LOOPBACK,
                                 follow_redirects=False, trust_env=False) as client:
        return await client.request(call.method, call.path, params=list(call.query),
                                    headers=dict(headers))


def _retry_after(reply: httpx.Response) -> str | None:
    found = reply.headers.get("retry-after")
    return found if found is not None and _RETRY_AFTER.fullmatch(found) else None


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _rows(result: Any) -> tuple[Any, bool]:
    """Each list of the top level of *result* cut to :data:`MAX_ROWS` rows."""
    if isinstance(result, list):
        return (result[:MAX_ROWS], True) if len(result) > MAX_ROWS else (result, False)
    if not isinstance(result, dict):
        return result, False
    cut_any = False
    out: dict[str, Any] = {}
    for name, value in result.items():
        if isinstance(value, list) and len(value) > MAX_ROWS:
            value, cut_any = value[:MAX_ROWS], True
        out[name] = value
    return out, cut_any


def _size(result: Any) -> tuple[Any, bool]:
    """*result* cut to at most :data:`MAX_ANSWER_BYTES` bytes of JSON. Each
    list of the top level loses rows from its end, the longest list first. A
    result that still does not fit is None."""
    if _json_size(result) <= MAX_ANSWER_BYTES:
        return result, False
    if isinstance(result, dict):
        out = dict(result)
        names = sorted((k for k, v in out.items() if isinstance(v, list)),
                       key=lambda k: -_json_size(out[k]))
        for name in names:
            rows = list(out[name])
            while rows and _json_size(out) > MAX_ANSWER_BYTES:
                rows = rows[: len(rows) // 2] if len(rows) > 1 else []
                out[name] = rows
            if _json_size(out) <= MAX_ANSWER_BYTES:
                return out, True
    if isinstance(result, list):
        rows = list(result)
        while rows and _json_size(rows) > MAX_ANSWER_BYTES:
            rows = rows[: len(rows) // 2] if len(rows) > 1 else []
        if _json_size(rows) <= MAX_ANSWER_BYTES:
            return rows, True
    return None, True


def cut(result: Any) -> tuple[Any, bool]:
    """``(result, truncated)``: *result* under the two caps of AD-12."""
    rows, by_rows = _rows(result)
    fitted, by_size = _size(rows)
    return fitted, by_rows or by_size


def _answer_of(reply: httpx.Response) -> Answer:
    status = reply.status_code
    retry = _retry_after(reply)
    if 200 <= status < 300:
        try:
            body = reply.json() if reply.content else None
        except ValueError:
            _log.warning("studio.door_op.not_json", status=status)
            return Answer(status, unavailable=True)
        result, truncated = cut(body)
        return Answer(status, result=result, truncated=truncated)
    if 400 <= status < 500:
        headers = {"Retry-After": retry} if retry is not None else None
        return Answer(status, reply=Response(
            content=reply.content, status_code=status,
            media_type=reply.headers.get("content-type"), headers=headers))
    _log.warning("studio.door_op.app_failed", status=status)
    return Answer(status, unavailable=True, retry_after=retry)


async def run(request: Request, call: Call) -> Answer:
    """Send *call* through ``request.app`` as the caller, in a new task, and
    read the answer. The caller holds no session during this await."""
    task = asyncio.create_task(_send(request.app, call, forwarded(request.headers)))
    try:
        reply = await asyncio.wait_for(task, TIMEOUT_S)
    except TimeoutError:
        _log.warning("studio.door_op.timeout", template=call.template)
        return Answer(None, unavailable=True)
    return _answer_of(reply)
