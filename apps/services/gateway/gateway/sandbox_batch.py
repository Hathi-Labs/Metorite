"""The writes of the sandbox door: the batch route and the held store.

SBX-3a, Metorite task 3802. Spec: ``project-docs/specs/agent_sandbox.md``
§12.5 decisions 3 to 6 and 9, and the owner answers B1 and B2.

**The one path in.** Code in the sandbox writes only through
``POST /sandbox/v1/batch``. A direct write keeps the 403
``sandbox_read_only`` of the door in every phase. The sibling of branch 0
(``acb_auth.deps._sandbox_user``) admits the batch pair, and this handler
checks each of its rows BEFORE anything is held. One bad row refuses the
whole batch, and the answer names its index.

**The hold (B2).** The class B rows of one batch share one card, and each
class C row gets a card of its own. Each card is one record in tenant Redis,
under ``sandbox_held`` and a card id of 32 hex digits, in the scope of the
org of the capability row. A list under ``sandbox_held_run`` names the open
cards of a run. The decision of a card is one key under
``sandbox_held_decision``, written once with ``SET NX``: :func:`decide`
writes ``approved`` with the ticked row ids, or ``refused``. At the end of
the wait the handler writes ``expired``. The first writer wins. Until SBX-3b
builds the card, only the tests call :func:`decide`.

**The run (B1).** The rows run only after every card has a decision. Then
the approved rows run in row order, one by one, each as its own request to
this app in the same process. Each request carries the same capability and
one new ``X-Sandbox-Ticket``, so the sibling runs every step again: the flag,
the live row, the member's access, the route's own checks and FORCE RLS.

**No database session for the wait.** The route takes no ``Depends(get_db)``.
It reads ``apps.slug`` in one short session and closes it before the wait.

**No secret in a log.** The line ``sandbox.door.write`` holds ids and counts
only: no body, no path value, no token and no hash.
Fences: ``tests/unit/test_sandbox_door_writes.py`` and
``tests/unit/test_sandbox_door_log_lines.py``.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlencode

import httpx
from acb_auth import UserContext, agent_oauth, sandbox_capabilities
from acb_auth.agent_oauth import ACTOR_VIA_PATTERN
from acb_auth.deps import get_current_user, require_internal_auth
from acb_common import app_manifests, get_logger, get_settings, tenant_redis
from acb_common.card_body import CARD_CLIP, CardBody, CardWontFit
from acb_common.tool_refusals import data
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from gateway.db import current_tenant, tenant_session

_log = get_logger("gateway.sandbox_batch")

router = APIRouter(tags=["sandbox"])

#: The rows of one batch, and the caps of decision 4.
MAX_ROWS = 25
ROW_CAP_BYTES = 64 * 1024
BODY_CAP_BYTES = 512 * 1024

#: The methods of a write row. A DELETE never joins (§5.3).
WRITE_METHODS: tuple[str, ...] = ("POST", "PUT", "PATCH")

#: The four keys of a row. Any other key is refused, so the code can put no
#: label on a card.
ROW_KEYS: frozenset[str] = frozenset({"method", "path", "query", "body"})

#: The shape of a row path: no query, no fragment and no percent sign.
_PATH = re.compile(r"/[A-Za-z0-9._~:@/-]{0,511}")

#: The batch answers before the step ends: the wait stops this many seconds
#: before the end of the capability (decision 6).
STEP_MARGIN_S = 30

#: Each key of the hold lives the wait plus this many seconds.
TTL_GRACE_S = 60

#: The handler reads the decision keys this often, in seconds.
POLL_S = 0.5

#: The tenant Redis namespaces of the hold (decision 4).
HELD_NAMESPACE = "sandbox_held"
RUN_NAMESPACE = "sandbox_held_run"
DECISION_NAMESPACE = "sandbox_held_decision"
#: The results of the rows of one card, for the watcher (SBX-3b decision 8).
RESULT_NAMESPACE = "sandbox_held_result"

#: A result lives this many seconds past the deadline of its batch, so the
#: watcher can read it for 30 seconds after the deadline (decision 8).
RESULT_GRACE_S = 90

#: The values of a decision.
APPROVED = "approved"
REFUSED = "refused"
EXPIRED = "expired"

#: The assignee fields of the body model of each write row, by pair. An
#: ``agent:`` assignee starts an agent run (``routes/projects/agent_dispatch.py``),
#: so the batch refuses a row whose body names one in any of these fields.
#: Fence: ``test_sandbox_door_writes.py::test_every_assignee_field_of_a_write_row_is_scanned``
#: walks the body model of each row of ``SANDBOX_WRITE_ROUTES`` on the live
#: router, and fails when a field that names an assignee is missing here.
AGENT_ASSIGNEE_FIELDS: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("POST", "/projects/tasks"): ("assignees",),
    ("POST", "/projects/tasks/{task_id}/move"): ("assignees",),
    ("PUT", "/projects/tasks/{task_id}/assignees"): ("assignees",),
}
AGENT_PREFIX = "agent:"

#: The fields that a batch row may not set at all, by pair. An agent runs the
#: Check of a task (``check_text``) as a shell command, so code in the sandbox
#: may not write one. A row whose body holds one of these keys is refused,
#: whatever its value. Fence:
#: ``test_sandbox_door_writes.py::test_every_check_field_of_a_write_row_is_refused``.
REFUSED_FIELDS: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("POST", "/projects/tasks"): ("check_text",),
    ("PATCH", "/projects/tasks/{task_id}"): ("check_text",),
}

#: One open batch for each capability token (review P2). The key is the token
#: hash under this namespace, set with ``SET NX`` before the hold, and deleted
#: when the batch ends. A second batch while one is open gets 429.
OPEN_NAMESPACE = "sandbox_batch_open"
BATCH_BUSY = "sandbox_batch_busy"

#: The host name of the in-process requests. It reaches nothing outside.
_INNER_BASE = "http://sandbox-door"


# ── The refusals ────────────────────────────────────────────────────────────


class _Refused(Exception):
    """One refusal of the batch, before anything is held."""

    def __init__(self, status: int, code: str, index: int | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.index = index

    def answer(self) -> JSONResponse:
        content: dict[str, Any] = {"detail": self.code}
        if self.index is not None:
            content["i"] = self.index
        return JSONResponse(status_code=self.status, content=content)


# ── The wait and the stamp (decisions 5 and 6) ──────────────────────────────


def batch_wait_s(seconds_left: int) -> int:
    """The wait of one batch: the setting, or the time left on the
    capability less :data:`STEP_MARGIN_S`, whichever is less. A value of 0 or
    less holds nothing."""
    setting = int(getattr(get_settings(), "sandbox_approval_timeout_seconds", 300))
    return min(setting, int(seconds_left) - STEP_MARGIN_S)


def stamp_of(slug: str | None, app_id: str, version: int | None) -> str:
    """The stamp ``sandbox:<slug>:v<n>`` of decision 5. A slug that makes it
    break :data:`ACTOR_VIA_PATTERN` gives ``sandbox:app-<8 hex>:v<n>``."""
    stamp = f"sandbox:{slug}:v{version}"
    if slug and ACTOR_VIA_PATTERN.fullmatch(stamp):
        return stamp
    return f"sandbox:app-{app_id.replace('-', '')[:8]}:v{version}"


async def _stamp(app_id: str, version: int | None) -> str:
    """Read the slug of the capability's app under RLS, in one short session
    that closes before the wait. The app id comes from the capability row."""
    from sqlalchemy import text

    async with tenant_session() as db:
        row = (await db.execute(
            text("SELECT slug FROM apps WHERE id = CAST(:id AS uuid)"), {"id": app_id},
        )).first()
    slug = str(row[0]) if row is not None and row[0] is not None else None
    return stamp_of(slug, app_id, version)


# ── The checks of decision 4 ────────────────────────────────────────────────


@dataclass(slots=True)
class _Row:
    """One checked row."""

    index: int
    method: str
    path: str
    template: str
    query: dict[str, str]
    body: dict[str, Any]
    cls: str

    @property
    def row_id(self) -> str:
        return f"row-{self.index}"


@dataclass(slots=True)
class _Card:
    """One card: the class B rows together, or one class C row."""

    card_id: str
    cls: str
    rows: list[_Row] = field(default_factory=list)


async def _read_body(request: Request) -> bytes:
    """The body, read up to the cap. Past the cap, 422 ``sandbox_batch_cap``."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > BODY_CAP_BYTES:
            raise _Refused(422, sandbox_capabilities.BATCH_CAP)
        chunks.append(chunk)
    return b"".join(chunks)


def _match_template(app: Any, method: str, path: str) -> str | None:
    """The template that the gateway router matches for *method* and *path*,
    as ``deps._matched_template`` gives it for a request. None for no route."""
    from fastapi.routing import iter_route_contexts
    from starlette.routing import Match

    scope = {"type": "http", "method": method, "path": path, "root_path": "",
             "headers": [], "query_string": b""}
    for context in iter_route_contexts(getattr(app, "routes", ())):
        try:
            match, _ = context.matches(scope)
        except Exception:
            continue
        if match == Match.FULL:
            return getattr(context, "path", None)
    return None


def _is_agent(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(AGENT_PREFIX)


def _names_an_agent(pair: tuple[str, str], body: Mapping[str, Any]) -> bool:
    """True when the body of a row of *pair* names an ``agent:`` assignee in
    any assignee field of :data:`AGENT_ASSIGNEE_FIELDS`. A field holds one
    value or a list of values."""
    for name in AGENT_ASSIGNEE_FIELDS.get(pair, ()):
        value = body.get(name)
        values = value if isinstance(value, list) else [value]
        if any(_is_agent(one) for one in values):
            return True
    return False


def _sets_a_refused_field(pair: tuple[str, str], body: Mapping[str, Any]) -> bool:
    """True when the body of a row of *pair* holds a key of
    :data:`REFUSED_FIELDS`, whatever its value."""
    return any(name in body for name in REFUSED_FIELDS.get(pair, ()))


def _path_is_plain(path: object) -> bool:
    """True when *path* has the row shape and reaches the same route after
    the in-process client builds its URL: no empty, ``.`` or ``..`` segment,
    and no change by ``httpx.URL`` (review P1)."""
    if not isinstance(path, str) or _PATH.fullmatch(path) is None:
        return False
    if any(segment in ("", ".", "..") for segment in path.split("/")[1:]):
        return False
    try:
        return httpx.URL(_INNER_BASE + path).path == path
    except Exception:
        return False


def _check_row(app: Any, index: int, raw: object, write_scopes: Iterable[str]) -> _Row:
    """The checks of one row, in order. The first failure raises."""
    if not isinstance(raw, dict) or set(raw) != ROW_KEYS:
        raise _Refused(422, sandbox_capabilities.BAD_BATCH, index)
    size = len(json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if size > ROW_CAP_BYTES:
        raise _Refused(422, sandbox_capabilities.BATCH_CAP, index)
    method, path, query, body = raw["method"], raw["path"], raw["query"], raw["body"]
    if method not in WRITE_METHODS:
        raise _Refused(422, sandbox_capabilities.BAD_BATCH, index)
    if not _path_is_plain(path):
        raise _Refused(422, sandbox_capabilities.BAD_BATCH, index)
    if not isinstance(query, dict) or not all(
            isinstance(k, str) and k and isinstance(v, str) for k, v in query.items()):
        raise _Refused(422, sandbox_capabilities.BAD_BATCH, index)
    if not isinstance(body, dict):
        raise _Refused(422, sandbox_capabilities.BAD_BATCH, index)
    # 3. The gateway router matches the path to one template.
    template = _match_template(app, method, path)
    # 4. The pair is a write row of the door.
    pair = (method, template)
    cls = app_manifests.SANDBOX_WRITE_ROUTES.get(pair) if template is not None else None
    if cls is None:
        if app_manifests.sandbox_excluded(pair):
            raise _Refused(403, sandbox_capabilities.ROUTE_EXCLUDED, index)
        raise _Refused(404, sandbox_capabilities.ROUTE_UNKNOWN, index)
    # 5. The write scope of the pair's app.
    needed = app_manifests.sandbox_write_scope(pair)
    if needed is None or not agent_oauth.scope_holds(write_scopes, needed):
        raise _Refused(403, sandbox_capabilities.SCOPE, index)
    assert template is not None
    # No agent assignee in any assignee field: it would start an agent run.
    if _names_an_agent((method, template), body):
        raise _Refused(422, sandbox_capabilities.BAD_BATCH, index)
    # No field that an agent later runs (the Check of a task).
    if _sets_a_refused_field((method, template), body):
        raise _Refused(422, sandbox_capabilities.BAD_BATCH, index)
    return _Row(index=index, method=method, path=path, template=template,
                query=dict(query), body=body, cls=cls)


async def _check_batch(request: Request, user: UserContext) -> list[_Row]:
    """Each check of decision 4, before anything is held."""
    scope = user.sandbox
    assert scope is not None
    # 1. The capability is not in the smoke phase.
    if scope.phase == "smoke":
        raise _Refused(403, sandbox_capabilities.READ_ONLY)
    # 2. The capability holds a write scope.
    if not scope.write_scopes:
        raise _Refused(403, sandbox_capabilities.SCOPE)
    raw = await _read_body(request)
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise _Refused(422, sandbox_capabilities.BAD_BATCH) from None
    if not isinstance(parsed, dict) or set(parsed) != {"rows"}:
        raise _Refused(422, sandbox_capabilities.BAD_BATCH)
    rows = parsed["rows"]
    if not isinstance(rows, list) or not rows:
        raise _Refused(422, sandbox_capabilities.BAD_BATCH)
    if len(rows) > MAX_ROWS:
        raise _Refused(422, sandbox_capabilities.BATCH_CAP)
    return [_check_row(request.app, i, row, scope.write_scopes) for i, row in enumerate(rows)]


def _cards(rows: list[_Row]) -> list[_Card]:
    """The cards of one batch, in the order of their first row."""
    cards: list[_Card] = []
    shared: _Card | None = None
    for row in rows:
        if row.cls == "C":
            cards.append(_Card(card_id=uuid.uuid4().hex, cls="C", rows=[row]))
            continue
        if shared is None:
            shared = _Card(card_id=uuid.uuid4().hex, cls="B")
            cards.append(shared)
        shared.rows.append(row)
    return cards


# ── The held store (B2) ─────────────────────────────────────────────────────


def _key(namespace: str, part: str) -> Any:
    return tenant_redis.key(namespace, part)


async def _hold(org: str, cards: list[_Card], record: Mapping[str, Any], ttl: int,
                run_id: str) -> None:
    """Write one record for each card, and name each card in the run list.

    ``SET NX`` with a TTL, in the scope of *org*. A fault deletes what this
    call wrote and raises the 503, so nothing stays held and nothing runs."""
    client = tenant_redis.get_tenant_redis()
    written: list[str] = []
    try:
        with tenant_redis.organization_scope(org):
            for card in cards:
                body = dict(record)
                body["class"] = card.cls
                body["rows"] = [{
                    "id": row.row_id, "i": row.index, "method": row.method,
                    "path": row.path, "template": row.template, "query": row.query,
                    "body": row.body,
                } for row in card.rows]
                done = await client.set(_key(HELD_NAMESPACE, card.card_id), json.dumps(body),
                                        nx=True, ex=ttl)
                if not done:
                    raise RuntimeError("the card was not recorded")
                written.append(card.card_id)
                run_key = _key(RUN_NAMESPACE, run_id)
                await client.rpush(run_key, card.card_id)
                await client.expire(run_key, ttl)
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("sandbox.held_record_failed", error=type(exc).__name__)
        await _drop(org, written)
        raise HTTPException(status_code=503, detail="The batch could not be held.",
                            headers={"Retry-After": "2"}) from None


async def _drop(org: str, card_ids: Iterable[str]) -> None:
    """Delete the held records of *card_ids*. The decision keys stay until
    their TTL, so a late answer still finds its card decided."""
    ids = list(card_ids)
    if not ids:
        return
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(org):
            await client.delete(*[_key(HELD_NAMESPACE, card_id) for card_id in ids])
    except Exception as exc:
        _log.warning("sandbox.held_drop_failed", error=type(exc).__name__)


def _read_decision(raw: object) -> tuple[str, frozenset[str]] | None:
    """A decision value as (decision, ticked ids), or None."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    decision = value.get("decision")
    if decision == APPROVED:
        ticked = value.get("ticked")
        ids = frozenset(t for t in ticked if isinstance(t, str)) if isinstance(ticked, list) \
            else frozenset()
        return APPROVED, ids
    if decision in (REFUSED, EXPIRED):
        return str(decision), frozenset()
    return None


async def _decisions(org: str, cards: list[_Card], wait_s: float
                     ) -> dict[str, tuple[str, frozenset[str]]]:
    """Wait until each card has a decision, or the wait ends.

    The handler reads the decision keys every :data:`POLL_S` seconds. At the
    end of the wait it writes ``expired`` with ``SET NX`` for each card with
    no decision, and reads the key once more. A card whose key cannot be
    read never counts as approved."""
    deadline = time.monotonic() + wait_s
    found: dict[str, tuple[str, frozenset[str]]] = {}
    client = tenant_redis.get_tenant_redis()
    ttl = int(wait_s) + TTL_GRACE_S
    while True:
        open_cards = [c for c in cards if c.card_id not in found]
        if not open_cards:
            return found
        for card in open_cards:
            try:
                with tenant_redis.organization_scope(org):
                    raw = await client.get(_key(DECISION_NAMESPACE, card.card_id))
            except Exception as exc:
                _log.warning("sandbox.held_decision_read_failed", error=type(exc).__name__)
                continue
            decision = _read_decision(raw)
            if decision is not None:
                found[card.card_id] = decision
        left = deadline - time.monotonic()
        if len(found) == len(cards):
            return found
        if left <= 0:
            break
        await asyncio.sleep(min(POLL_S, left))
    for card in cards:
        if card.card_id in found:
            continue
        try:
            with tenant_redis.organization_scope(org):
                key = _key(DECISION_NAMESPACE, card.card_id)
                await client.set(key, json.dumps({"decision": EXPIRED}), nx=True, ex=ttl)
                decision = _read_decision(await client.get(key))
        except Exception as exc:
            _log.warning("sandbox.held_decision_read_failed", error=type(exc).__name__)
            decision = None
        found[card.card_id] = decision if decision is not None else (EXPIRED, frozenset())
    return found


async def decide(card_id: str, ticked: Iterable[str] | None) -> bool:
    """Write the decision of one card, once. True when this call wrote it.

    *ticked* holds the ids of the approved rows. ``None`` refuses the card.
    ``SET NX``, so the first writer wins: an answer after the timeout runs
    nothing, and a timeout after an answer changes nothing. A card with no
    held record (it ended, or it never was) takes no decision.

    The org is the tenant that the session bound (R5), never an argument. The
    act of SBX-3b calls it after its own checks. Until then only the tests
    call it."""
    org = current_tenant()
    if not org:
        raise RuntimeError("decide needs a bound tenant")
    if ticked is None:
        value = json.dumps({"decision": REFUSED})
    else:
        value = json.dumps({"decision": APPROVED,
                            "ticked": sorted({str(one) for one in ticked})})
    client = tenant_redis.get_tenant_redis()
    with tenant_redis.organization_scope(org):
        ttl = await client.ttl(_key(HELD_NAMESPACE, str(card_id)))
        if not isinstance(ttl, int) or ttl <= 0:
            return False
        done = await client.set(_key(DECISION_NAMESPACE, str(card_id)), value, nx=True,
                                ex=ttl)
    return bool(done)


# ── The run of one approved row (B1) ────────────────────────────────────────


def _inner_url(row: _Row) -> httpx.URL:
    query = urlencode(sorted(row.query.items()))
    return httpx.URL(_INNER_BASE + row.path + (f"?{query}" if query else ""))


async def _send_row(app: Any, raw_capability: str, ticket: str, row: _Row, url: httpx.URL,
                    body: bytes, timeout_s: float) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, timeout=timeout_s) as client:
        return await client.request(row.method, url, content=body, headers={
            "x-sandbox-capability": raw_capability,
            "x-sandbox-ticket": ticket,
            "content-type": "application/json",
        })


async def _run_row(app: Any, capability: sandbox_capabilities.Capability,
                   raw_capability: str, row: _Row, stamp: str, held_id: str,
                   end: float) -> dict[str, Any]:
    """Run one approved row as the member, and give its entry."""
    left = end - time.monotonic()
    if left <= 0:
        return {"i": row.index, "status": 401, "code": sandbox_capabilities.NOT_RUN}
    url = _inner_url(row)
    body = json.dumps(row.body, separators=(",", ":")).encode("utf-8")
    try:
        ticket = await sandbox_capabilities.issue_ticket(
            org=capability.organization_id, token_sha256=capability.token_sha256,
            method=row.method, path=unquote(url.path), query=url.query, body=body,
            stamp=stamp, held_id=held_id)
    except Exception as exc:
        _log.warning("sandbox.ticket_failed", error=type(exc).__name__)
        return {"i": row.index, "status": 503, "code": sandbox_capabilities.NOT_RUN}
    # Its own task, with a copy of the context, so the inner request cannot
    # clear the tenant of the batch request.
    task = asyncio.create_task(_send_row(app, raw_capability, ticket, row, url, body,
                                         max(left, 1.0)))
    try:
        answer = await task
    except Exception as exc:
        _log.warning("sandbox.row_failed", error=type(exc).__name__)
        return {"i": row.index, "status": 401, "code": sandbox_capabilities.NOT_RUN}
    if answer.status_code == 401:
        return {"i": row.index, "status": 401, "code": sandbox_capabilities.NOT_RUN}
    try:
        parsed: Any = answer.json()
    except ValueError:
        parsed = None
    return {"i": row.index, "status": answer.status_code, "body": parsed}


# ── The log line (decision 9) ───────────────────────────────────────────────


def _log_write(act: str, *, org: str, run_id: str, phase: str, held_id: str,
               rows: Iterable[_Row], ran: Mapping[int, int] | None = None) -> None:
    """The line ``sandbox.door.write``: ids and counts only. The field
    ``act`` holds the event of the spec, because the logger names its own
    first argument ``event``."""
    by_class: dict[str, int] = {"B": 0, "C": 0}
    for row in rows:
        by_class[row.cls] = by_class.get(row.cls, 0) + 1
    _log.info("sandbox.door.write", act=act, org=org, run=run_id, phase=phase,
              held=held_id, rows_b=by_class["B"], rows_c=by_class["C"],
              ran={str(status): n for status, n in sorted((ran or {}).items())})


# ── The route ───────────────────────────────────────────────────────────────


@router.post("/sandbox/v1/batch")
async def sandbox_batch(
    request: Request, user: UserContext = Depends(get_current_user),
) -> Any:
    """Hold the write rows of one batch for the member's cards, then run the
    approved rows one by one, as the member (decision 4, B1 and B2).

    Only a sandbox call reaches it. Any other caller gets 404."""
    scope = user.sandbox
    if scope is None:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        rows = await _check_batch(request, user)
    except _Refused as refused:
        sandbox_capabilities.log_door_call(
            status=refused.status, code=refused.code, method="POST",
            template=app_manifests.SANDBOX_BATCH_PAIR[1], org=user.organization_id or "",
            run_id=scope.run_id, phase=scope.phase, warn=True)
        return refused.answer()

    # The live row of this request: the token hash and the time left.
    raw_capability = request.headers.get("x-sandbox-capability") or ""
    capability = await sandbox_capabilities.lookup(raw_capability)
    if (capability is None or capability.organization_id != user.organization_id
            or capability.run_id != scope.run_id):
        raise HTTPException(status_code=401, detail="Authentication required")
    org, run_id, phase = capability.organization_id, capability.run_id, capability.phase
    end = time.monotonic() + capability.seconds_left
    held_id = uuid.uuid4().hex

    wait_s = batch_wait_s(capability.seconds_left)
    if wait_s <= 0:
        _log_write(EXPIRED, org=org, run_id=run_id, phase=phase, held_id=held_id, rows=rows)
        return JSONResponse(status_code=200, content={"rows": [
            {"i": row.index, "status": 403, "code": sandbox_capabilities.APPROVAL_TIMEOUT}
            for row in rows]})

    try:
        stamp = await _stamp(str(scope.app_id or ""), scope.version)
    except Exception as exc:
        _log.warning("sandbox.stamp_read_failed", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="The batch could not be held.",
                            headers={"Retry-After": "2"}) from None

    cards = _cards(rows)
    record = {
        "owner": (user.email or "").strip().lower(), "run": run_id,
        "thread": capability.thread_id, "token_sha256": capability.token_sha256,
        "held": held_id, "stamp": stamp, "deadline": time.time() + wait_s,
    }
    if not await _open_batch(org, capability.token_sha256, held_id,
                             capability.seconds_left + TTL_GRACE_S):
        sandbox_capabilities.log_door_call(
            status=429, code=BATCH_BUSY, method="POST",
            template=app_manifests.SANDBOX_BATCH_PAIR[1], org=org, run_id=run_id,
            phase=phase, warn=True)
        return JSONResponse(status_code=429, content={"detail": BATCH_BUSY})
    try:
        return await _hold_and_run(request, capability, raw_capability, rows, cards,
                                   record, wait_s, stamp, held_id, end)
    finally:
        await _close_batch(org, capability.token_sha256)


async def _open_batch(org: str, token_sha256: str, held_id: str, ttl: int) -> bool:
    """Mark one open batch for the token (``SET NX``). False when another
    batch of the same token is open. A fault raises the 503, and nothing is
    held."""
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(org):
            done = await client.set(_key(OPEN_NAMESPACE, token_sha256), held_id, nx=True,
                                    ex=ttl)
    except Exception as exc:
        _log.warning("sandbox.batch_open_failed", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="The batch could not be held.",
                            headers={"Retry-After": "2"}) from None
    return bool(done)


async def _close_batch(org: str, token_sha256: str) -> None:
    """Delete the open mark of the token. A fault leaves it to its TTL."""
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(org):
            await client.delete(_key(OPEN_NAMESPACE, token_sha256))
    except Exception as exc:
        _log.warning("sandbox.batch_close_failed", error=type(exc).__name__)


async def _hold_and_run(request: Request,
                        capability: sandbox_capabilities.Capability, raw_capability: str,
                        rows: list[_Row], cards: list[_Card], record: Mapping[str, Any],
                        wait_s: int, stamp: str, held_id: str, end: float) -> JSONResponse:
    """Hold the cards, wait for each answer, then run the approved rows."""
    org, run_id, phase = capability.organization_id, capability.run_id, capability.phase
    await _hold(org, cards, record, wait_s + TTL_GRACE_S, run_id)
    _log_write("held", org=org, run_id=run_id, phase=phase, held_id=held_id, rows=rows)

    try:
        decisions = await _decisions(org, cards, wait_s)
    finally:
        await _drop(org, [card.card_id for card in cards])

    entries: dict[int, dict[str, Any]] = {}
    approved: list[_Row] = []
    for card in cards:
        decision, ticked = decisions[card.card_id]
        for row in card.rows:
            if decision == APPROVED and row.row_id in ticked:
                approved.append(row)
            elif decision == EXPIRED:
                entries[row.index] = {"i": row.index, "status": 403,
                                      "code": sandbox_capabilities.APPROVAL_TIMEOUT}
            else:
                entries[row.index] = {"i": row.index, "status": 403,
                                      "code": sandbox_capabilities.WRITE_REFUSED}
    for row in sorted(approved, key=lambda r: r.index):
        entries[row.index] = await _run_row(request.app, capability, raw_capability, row,
                                            stamp, held_id, end)

    await _write_results(org, cards, entries, record)
    ran_rows = {row.index for row in approved}
    for card in cards:
        ran: dict[int, int] = {}
        for row in card.rows:
            if row.index in ran_rows:
                status = int(entries[row.index]["status"])
                ran[status] = ran.get(status, 0) + 1
        _log_write(decisions[card.card_id][0], org=org, run_id=run_id, phase=phase,
                   held_id=held_id, rows=card.rows, ran=ran)
    return JSONResponse(status_code=200,
                        content={"rows": [entries[row.index] for row in rows]})


async def _write_results(org: str, cards: list[_Card], entries: Mapping[int, Mapping[str, Any]],
                         record: Mapping[str, Any]) -> None:
    """Write the result of each row of each card, for the watcher (decision 8).

    One key for each card, under :data:`RESULT_NAMESPACE`, with the ``owner``
    and the ``run`` of the held record. A result holds the row id, the status
    and the door code of each row, and no body. The TTL is the deadline of
    the batch plus :data:`RESULT_GRACE_S`. A fault loses the results only: the
    answer of the batch does not change."""
    ttl = max(1, int(float(record.get("deadline") or 0) - time.time()) + RESULT_GRACE_S)
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(org):
            for card in cards:
                rows = []
                for row in card.rows:
                    entry = entries[row.index]
                    one: dict[str, Any] = {"id": row.row_id, "i": row.index,
                                           "status": int(entry["status"])}
                    if isinstance(entry.get("code"), str):
                        one["code"] = entry["code"]
                    rows.append(one)
                value = {"owner": record.get("owner"), "run": record.get("run"), "rows": rows}
                await client.set(_key(RESULT_NAMESPACE, card.card_id), json.dumps(value),
                                 ex=ttl)
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("sandbox.held_result_write_failed", error=type(exc).__name__)


# ── The text of a card (SBX-3b, decision 7) ────────────────────────────────
#
# The gateway builds each word of a card from the held record, and from the
# code of this module. The batch has no free-text field, so no model text
# frames the card. Each value that code in the sandbox chose shows fenced
# («…», ``fenced=True``): a body key, a body value, a query key and value, a
# target id of the path, and the stamp. The method, the template and the
# names of its path parameters come from the gateway router, so they show as
# they are. Fence: ``tests/unit/test_sandbox_held_card.py``.

#: The first line of the context of a class B card (``projects_ai_chat.md``
#: §5.3: the warning line first).
CARD_WARNING = ("Code that a model wrote asks to make these changes as you. Read each "
                "row. Only a ticked row runs, one by one, as you.")

#: The warning of a class C card. Its line starts with the counts.
CARD_WARNING_C = ("This is hard to undo. Code that a model wrote asks to do it as you. "
                  "It runs only if you tick it.")

#: The count words of each class C pair, for one row and for more. A class C
#: card starts with its counts (``projects_ai_chat.md`` §5.2). Fence:
#: ``test_sandbox_held_card.py::test_each_class_c_row_has_its_counts``.
CLASS_C_COUNTS: Mapping[tuple[str, str], tuple[str, str]] = {
    ("POST", "/crm/leads/{lead_id}/convert"): ("lead converts", "leads convert"),
    ("POST", "/projects/tasks/{task_id}/archive"): ("task goes to the archive",
                                                     "tasks go to the archive"),
}

#: The caps of a card, as ``ask_tools.request_confirmation`` and
#: ``ask_tools.clean_card_rows`` clip them. A card that does not fit is not
#: drawn (:class:`CardWontFit`), because a clipped card shows less than the
#: write. Fence: ``test_sandbox_held_card.py::test_the_caps_are_the_caps_of_the_card``.
CARD_TITLE_MAX = 120
CARD_DETAIL_MAX = 500
CARD_LABEL_MAX = 500
CARD_HINT_MAX = 4000

#: The row id that :class:`_Row` gives.
_ROW_ID = re.compile(r"row-[0-9]{1,2}")

#: The keys of a held row, as :func:`_hold` writes them. A row with any other
#: key is not of the batch handler, and no card shows.
HELD_ROW_KEYS: frozenset[str] = frozenset(
    {"id", "i", "method", "path", "template", "query", "body"})


@dataclass(slots=True, frozen=True)
class _CardRow:
    """One held row, checked against the router list, for its card text."""

    row_id: str
    method: str
    template: str
    targets: tuple[tuple[str, str], ...]
    query: tuple[tuple[str, str], ...]
    body: tuple[tuple[str, Any], ...]


def _targets(template: str, path: str) -> tuple[tuple[str, str], ...]:
    """Each path parameter of *template*, with its value in *path*. A path
    that is not of the template raises ``ValueError``."""
    want = template.split("/")
    have = path.split("/")
    if len(want) != len(have):
        raise ValueError("the held path is not of its template")
    out: list[tuple[str, str]] = []
    for part, value in zip(want, have, strict=True):
        if part.startswith("{") and part.endswith("}"):
            if not value:
                raise ValueError("the held path has an empty id")
            out.append((part[1:-1], value))
        elif part != value:
            raise ValueError("the held path is not of its template")
    return tuple(out)


def _card_row(raw: object, cls: str) -> _CardRow:
    """One row of a held record, checked. A row that the batch handler could
    not have written raises ``ValueError``, and no card shows."""
    if not isinstance(raw, Mapping) or set(raw) != HELD_ROW_KEYS:
        raise ValueError("a held row has the keys of the batch handler")
    row_id, method, template = raw.get("id"), raw.get("method"), raw.get("template")
    path, query, body = raw.get("path"), raw.get("query"), raw.get("body")
    if not isinstance(row_id, str) or not _ROW_ID.fullmatch(row_id):
        raise ValueError("a held row has no row id")
    if not isinstance(method, str) or not isinstance(template, str):
        raise ValueError("a held row has no method or template")
    if app_manifests.SANDBOX_WRITE_ROUTES.get((method, template)) != cls:
        raise ValueError("a held row is not a write row of its class")
    if not isinstance(path, str) or not _PATH.fullmatch(path):
        raise ValueError("a held row has a bad path")
    if not isinstance(query, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in query.items()):
        raise ValueError("a held row has a bad query")
    if not isinstance(body, Mapping) or not all(isinstance(k, str) for k in body):
        raise ValueError("a held row has a bad body")
    return _CardRow(row_id=row_id, method=method, template=template,
                    targets=_targets(template, path), query=tuple(query.items()),
                    body=tuple(body.items()))


def _shown(value: Any) -> str:
    """A body value, fenced. Each value that is not a string shows as its JSON,
    so ``0``, ``false`` and ``null`` keep their text."""
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True)
    return str(data(text))


def _ids(row: _CardRow) -> str:
    """The target ids of the path, fenced: ``task_id «7f3c…»``."""
    return " ".join(f"{name} {data(value)}" for name, value in row.targets)


def _label(row: _CardRow) -> str:
    """The label of a row: the method and the template, code text only. It
    always fits in :data:`CARD_LABEL_MAX`, so a long id never cuts it."""
    return f"{row.method} {row.template}"


def _hint(row: _CardRow) -> str:
    """The hint of a row: the target ids, then the body keys and the query
    keys, each fenced. A path is at most 512 characters, so the ids always
    fit. Keys that do not fit are counted, and the context shows each one."""
    head = _ids(row)
    names = [data(key) for key, _value in row.body]
    names += [f"query {data(key)}" for key, _value in row.query]
    if not names:
        return f"{head} · no fields" if head else "No fields"
    lead = f"{head} · fields " if head else "Fields "
    shown: list[str] = []
    for i, name in enumerate(names):
        rest = len(names) - i - 1
        tail = f", and {rest} more in the card text" if rest else ""
        if len(lead + ", ".join([*shown, name]) + tail) > CARD_HINT_MAX:
            break
        shown.append(name)
    left = len(names) - len(shown)
    if not shown:
        return f"{lead}{left} in the card text"
    more = f", and {left} more in the card text" if left else ""
    return lead + ", ".join(shown) + more


def _counts(cls: str, rows: list[_CardRow]) -> str:
    """The counts of a class C card, for example "1 lead converts"."""
    one, many = CLASS_C_COUNTS[(rows[0].method, rows[0].template)]
    return f"{len(rows)} {one if len(rows) == 1 else many}"


def card_text(record: Mapping[str, Any]) -> dict[str, Any]:
    """The text of the card of one held record: ``title``, ``detail``,
    ``context`` and ``rows`` (decision 7).

    A pure function. It reads only the class, the stamp and the rows of
    *record*. It never shows the member, the run, the thread, the held id, the
    deadline or the token hash. The context starts with the warning line. A
    class C card starts its title, its detail and its warning line with the
    counts. Each line of a field names its row, and stays under that row, in
    row order. The context is at most :data:`CARD_CLIP` characters, and no
    text is cut: a card shows each value whole, or it shows nothing.

    Raises ``ValueError`` for a record that the batch handler could not have
    written, and :class:`CardWontFit` when the card does not fit whole.
    Either way no card shows, and the batch times out with nothing run.
    """
    cls = record.get("class")
    raw_rows = record.get("rows")
    if cls not in ("B", "C") or not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("a held record has a class and rows")
    if len(raw_rows) > MAX_ROWS or (cls == "C" and len(raw_rows) != 1):
        raise ValueError("a held record has too many rows for its class")
    rows = [_card_row(raw, str(cls)) for raw in raw_rows]
    if len({row.row_id for row in rows}) != len(rows):
        raise ValueError("a held record names a row twice")
    stamp = record.get("stamp")
    if not isinstance(stamp, str) or not ACTOR_VIA_PATTERN.fullmatch(stamp):
        raise ValueError("a held record has no stamp")
    app = data(stamp)

    n = len(rows)
    if cls == "C":
        counts = _counts(cls, rows)
        title = f"{counts}. Allow it?"
        detail = f"{counts}. The sandbox app {app} asks to do this as you. It is hard to undo."
        note = f"{counts}. {CARD_WARNING_C}"
    else:
        changes = "1 change" if n == 1 else f"{n} changes"
        title = f"Let a sandbox app make {changes}?"
        detail = (f"The sandbox app {app} asks to make {changes} as you. "
                  "Only a ticked row runs.")
        note = CARD_WARNING

    # Each line stays under its own row and names it, in row order (review
    # P1). ``card_body`` moves the long text of every row to the end, so the
    # text of row 1 showed under row 2, and an unticked row hid a write.
    # Nothing is cut: a card that does not fit whole is not drawn.
    lines: list[str] = [note]
    for number, row in enumerate(rows, start=1):
        ids = _ids(row)
        lines.append(f"{number}. {_label(row)}" + (f" {ids}" if ids else ""))
        lines += [f"   row {number}: query {data(key)} {data(value)}" for key, value in row.query]
        lines += [f"   row {number}: {data(key)} {_shown(value)}" for key, value in row.body]
        if not row.body:
            lines.append(f"   row {number}: (no fields)")
    text = "\n".join(lines)
    if len(text) > CARD_CLIP:
        raise CardWontFit("This change does not fit on one card, so nothing was changed. "
                          "Send fewer or shorter fields, or split the batch, and call the "
                          "tool once more.")
    context = CardBody(text)

    card_rows: list[dict[str, Any]] = [{"id": row.row_id, "label": _label(row), "hint": _hint(row), "checked": True}
                 for row in rows]
    too_long = (len(title) > CARD_TITLE_MAX or len(detail) > CARD_DETAIL_MAX
                or any(len(r["label"]) > CARD_LABEL_MAX or len(r["hint"]) > CARD_HINT_MAX
                       for r in card_rows))
    if too_long:
        raise CardWontFit("This change does not fit on one card, so nothing was changed. "
                          "Send fewer fields or shorter ids, and call the tool once more.")
    return {"title": title, "detail": detail, "context": context, "rows": card_rows}


# ── The three held routes (SBX-3b, decisions 7 and 8) ──────────────────────
#
# Internal routes: each needs the internal bearer (``require_internal_auth``)
# and the member (``get_current_user``), as the skills call the gateway. The
# door never reaches them, because they are not in ``SANDBOX_ROUTES``. Each
# answers 404 for an org that is not in ``SANDBOX_DOOR_ORGS``. The org and
# the member come from the session (R5), never from the body.

#: The shape of a run id in a body.
_RUN_ID = r"^[A-Za-z0-9._:-]{1,200}$"
#: The longest answer of a held card: ``ask_tools.MAX_ROWS_ANSWER`` with room.
_ANSWER_MAX = 8000
#: How long the act waits for the record of the answer, which respond-input
#: writes just after the run takes the answer (as ``held_card.ANSWER_WAIT_S``).
ANSWER_WAIT_S = 3.0


class HeldCardAsk(BaseModel):
    """The ask for the next open card of a run, or for its results."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=_RUN_ID)


class HeldActRequest(BaseModel):
    """The member's answer to one held card, as the watcher hands it on.

    ``answer`` is the exact text that the member's browser posted, for an
    approval. ``refused`` is True for a refusal, a timeout or no answer, and
    a refusal takes no answer text."""

    model_config = ConfigDict(extra="forbid")

    card_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    card_thread_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(default="", max_length=_ANSWER_MAX)
    refused: bool = False


def _held_gate(user: UserContext) -> tuple[str, str]:
    """The org and the member of a held route, or 404.

    A sandbox call, a call with no member and an org that is not in the door
    flag each get 404. The flag check is the check of the door (``deps.py``
    step 4)."""
    if user.sandbox is not None:
        raise HTTPException(status_code=404, detail="Not Found")
    org = str(user.organization_id or "").strip()
    member = str(user.email or "").strip().lower()
    if not org or not member:
        raise HTTPException(status_code=404, detail="Not Found")
    orgs = sandbox_capabilities.door_orgs()
    if "*" not in orgs and org.lower() not in orgs:
        raise HTTPException(status_code=404, detail="Not Found")
    return org, member


def _text(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw if isinstance(raw, str) else ""


def _record_of(raw: object) -> dict[str, Any] | None:
    try:
        value = json.loads(_text(raw))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def _open_cards(org: str, member: str, run_id: str) -> list[tuple[str, dict[str, Any]]]:
    """The open cards of a run of the member, in the order of the run list.

    A card is open when its held record exists, it has no decision key, its
    ``owner`` is the member and its ``run`` is the run. Reads only."""
    client = tenant_redis.get_tenant_redis()
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    with tenant_redis.organization_scope(org):
        ids = await client.lrange(_key(RUN_NAMESPACE, run_id), 0, -1)
        for raw_id in ids or []:
            card_id = _text(raw_id)
            if not re.fullmatch(r"[0-9a-f]{32}", card_id) or card_id in seen:
                continue
            seen.add(card_id)
            record = _record_of(await client.get(_key(HELD_NAMESPACE, card_id)))
            if record is None:
                continue
            if record.get("owner") != member or record.get("run") != run_id:
                continue
            if await client.get(_key(DECISION_NAMESPACE, card_id)) is not None:
                continue
            out.append((card_id, record))
    return out


@router.post("/sandbox/held/card", dependencies=[Depends(require_internal_auth)])
async def sandbox_held_card(
    req: HeldCardAsk, user: UserContext = Depends(get_current_user),
) -> Any:
    """The next open card of a run of the member, or 204 for none.

    The answer holds ``card_id``, ``title``, ``detail``, ``context``,
    ``rows`` and ``seconds_left``, the time to the deadline of the batch. It
    reads only the held record and the decision key, and it writes nothing.
    A record that cannot make a card shows no card, and its batch times out
    with nothing run."""
    org, member = _held_gate(user)
    try:
        cards = await _open_cards(org, member, req.run_id)
    except Exception as exc:
        _log.warning("sandbox.held_card_read_failed", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="The card could not be read.",
                            headers={"Retry-After": "1"}) from None
    for card_id, record in cards:
        seconds_left = int(float(record.get("deadline") or 0) - time.time())
        if seconds_left <= 0:
            continue
        try:
            text = card_text(record)
        except (ValueError, CardWontFit) as exc:
            _log.warning("sandbox.held_card_not_drawn", card=card_id[:12],
                         error=type(exc).__name__)
            continue
        return {"card_id": card_id, **text, "context": str(text["context"]),
                "seconds_left": seconds_left}
    return Response(status_code=204)


def _audit_act(card_id: str, record: Mapping[str, Any], decision: str,
               ticked: frozenset[str]) -> None:
    """The one audit line of an act: the card, its class and the counts. No
    body, no path, no answer text and no token reach the log."""
    rows = [r for r in record.get("rows") or [] if isinstance(r, dict)]
    count = sum(1 for r in rows if r.get("id") in ticked)
    _log.info("sandbox.held_card_acted", card=card_id[:12], cls=str(record.get("class")),
              decision=decision, ticked=count, left_out=len(rows) - count)


@router.post("/sandbox/held/act", dependencies=[Depends(require_internal_auth)])
async def sandbox_held_act(
    req: HeldActRequest, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Decide one held card with the member's answer (decision 7).

    1. Read the held record (``GET``). A missing record, another ``owner``
       or another ``thread`` decides nothing (409). This holds for an
       approval and for a refusal.
    2. An approval takes the answer once (``reply_card.take_reply_answer``).
    3. It checks the ticked ids against the RECORDED ids
       (``ask_tools.ticked_rows``). A forged id decides nothing (409).
    4. :func:`decide`. A card that has a decision takes no second one (409).

    It returns at once, and does not wait for the rows to run."""
    from acb_skills.ask_tools import ticked_rows
    from acb_skills.reply_card import take_reply_answer

    org, member = _held_gate(user)
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(org):
            record = _record_of(await client.get(_key(HELD_NAMESPACE, req.card_id)))
    except Exception as exc:
        _log.warning("sandbox.held_act_read_failed", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="The card could not be read.",
                            headers={"Retry-After": "1"}) from None
    if (record is None or record.get("owner") != member
            or record.get("thread") != req.card_thread_id):
        _log.warning("sandbox.held_act_refused", reason="no_card")
        raise HTTPException(status_code=409,
                            detail="This card is not open for you, so nothing ran.")
    if req.refused:
        if not await decide(req.card_id, None):
            raise HTTPException(status_code=409,
                                detail="This card has a decision already, so nothing ran.")
        _audit_act(req.card_id, record, REFUSED, frozenset())
        return {"decided": REFUSED}
    if not await take_reply_answer(org, req.card_id, member, req.answer, req.card_thread_id,
                                   wait_s=ANSWER_WAIT_S):
        _log.warning("sandbox.held_act_refused", reason="no_record")
        raise HTTPException(status_code=409,
                            detail="This answer was not given on its card, so nothing ran.")
    rows = [r for r in record.get("rows") or [] if isinstance(r, dict)]
    ticked = ticked_rows(req.answer, frozenset(str(r.get("id")) for r in rows))
    if ticked is None:
        _log.warning("sandbox.held_act_refused", reason="bad_ids")
        raise HTTPException(status_code=409,
                            detail="The answer names a row that the card did not offer, "
                                   "so nothing ran.")
    if not await decide(req.card_id, ticked):
        raise HTTPException(status_code=409,
                            detail="This card has a decision already, so nothing ran.")
    _audit_act(req.card_id, record, APPROVED, ticked)
    return {"decided": APPROVED, "ticked": sorted(ticked)}


@router.post("/sandbox/held/results", dependencies=[Depends(require_internal_auth)])
async def sandbox_held_results(
    req: HeldCardAsk, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The results of each card of a run of the member, each one once.

    A result goes back only when its ``owner`` is the member and its
    ``run`` is the run. It is read first, and spent with ``GETDEL`` only
    after that check, so a call of another member spends nothing."""
    org, member = _held_gate(user)
    out: list[dict[str, Any]] = []
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(org):
            ids = await client.lrange(_key(RUN_NAMESPACE, req.run_id), 0, -1)
            seen: set[str] = set()
            for raw_id in ids or []:
                card_id = _text(raw_id)
                if not re.fullmatch(r"[0-9a-f]{32}", card_id) or card_id in seen:
                    continue
                seen.add(card_id)
                key = _key(RESULT_NAMESPACE, card_id)
                found = _record_of(await client.get(key))
                if (found is None or found.get("owner") != member
                        or found.get("run") != req.run_id):
                    continue
                taken = _record_of(await client.getdel(key))
                if (taken is None or taken.get("owner") != member
                        or taken.get("run") != req.run_id):
                    continue
                rows = [{k: r[k] for k in ("id", "status", "code") if k in r}
                        for r in taken.get("rows") or [] if isinstance(r, dict)]
                out.append({"card_id": card_id, "rows": rows})
    except Exception as exc:
        _log.warning("sandbox.held_results_failed", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="The results could not be read.",
                            headers={"Retry-After": "1"}) from None
    return {"results": out}


__all__ = [
    "APPROVED",
    "CARD_WARNING",
    "CARD_WARNING_C",
    "CLASS_C_COUNTS",
    "DECISION_NAMESPACE",
    "EXPIRED",
    "HELD_NAMESPACE",
    "REFUSED",
    "RESULT_NAMESPACE",
    "RUN_NAMESPACE",
    "batch_wait_s",
    "card_text",
    "decide",
    "router",
    "stamp_of",
]
