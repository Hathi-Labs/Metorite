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
from acb_auth.deps import get_current_user
from acb_common import app_manifests, get_logger, get_settings, tenant_redis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

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

#: The values of a decision.
APPROVED = "approved"
REFUSED = "refused"
EXPIRED = "expired"

#: The two rows that can name an assignee. An ``agent:`` assignee starts an
#: agent run (``routes/projects/agent_dispatch.py``), so the batch refuses it.
AGENT_ASSIGNEE_ROWS: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/projects/tasks"),
    ("PUT", "/projects/tasks/{task_id}/assignees"),
})
AGENT_PREFIX = "agent:"

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


def _names_an_agent(body: Mapping[str, Any]) -> bool:
    """True when the body names an ``agent:`` assignee."""
    assignees = body.get("assignees")
    if not isinstance(assignees, list):
        return False
    return any(isinstance(one, str) and one.strip().lower().startswith(AGENT_PREFIX)
               for one in assignees)


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
    if (not isinstance(path, str) or _PATH.fullmatch(path) is None
            or ".." in path or "//" in path):
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
    # No agent assignee: it would start an agent run.
    if pair in AGENT_ASSIGNEE_ROWS and _names_an_agent(body):
        raise _Refused(422, sandbox_capabilities.BAD_BATCH, index)
    assert template is not None
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


__all__ = [
    "APPROVED",
    "DECISION_NAMESPACE",
    "EXPIRED",
    "HELD_NAMESPACE",
    "REFUSED",
    "RUN_NAMESPACE",
    "batch_wait_s",
    "decide",
    "router",
    "stamp_of",
]
