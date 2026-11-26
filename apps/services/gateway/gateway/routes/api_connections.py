"""API connections: the four call routes of the chat (API-3, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.3, §2.3a, §2.7 and §2.8.
The pure steps are in ``gateway/connections/api/call.py``.

    GET   /connections/api/{name}/ops?q=      find ops by words
    GET   /connections/api/{name}/ops/{op}    describe one op
    POST  /connections/api/{name}/read        {op, args, total}: class A only
    POST  /connections/api/{name}/write       {op, args}: refuses every call

⚠️ **Dark by org.** The router's one dependency is the flag dependency of
API-2 (``require_api_connections``). While ``API_CONNECTIONS_ORGS`` does not
hold the org of the session, each route answers 404 as if it did not exist.

**The caller (§2.3a, decision e).** The org and the member come from the
session (``get_current_user``, which runs ``resolve_identity``). No org is
read from the body, the query or a header. The body model forbids each key
that it does not name.

**The steps of read and write**, in the order of §2.3:

1. and 2. :func:`_resolve`: the grant and the row, in ONE code path. A
   refused name, a missing grant and an unknown name give the same 404, and
   each one reads the row (§2.3a, decision a). Then the approved ops: the
   ``ops_hash`` of the row must equal the hash of the template now.
3. The class of the op comes from the template. ``read`` refuses B and C.
   ``write`` refuses A, and then refuses every other call with
   :data:`WRITES_OFF`. No setting opens it (D-API-4).
4. :func:`_spend`: one call of the limiter of the Studio live read, in its
   own namespace :data:`API_NAMESPACE`, then one call of the daily budget
   (:data:`BUDGET_NAMESPACE`). Over a limit is 429 with ``Retry-After``.
   Redis down fails closed with 503.
5. ``template.build`` checks each argument against the row. The builder
   percent-encodes each path value, and ``connect.call_url`` sets the
   context keys after the arguments.
6. ``shared_token.fresh_credential`` with the refresh of the template's
   client. For Zoho, the CRM's client: ``routes/crm/connection.py
   zoho_oauth_config`` is its one reader, as in API-2 (§2.3a, decision i).
7. ``call.check_host``, then ``outbound_guard.request_https`` with
   ``connect.auth_headers`` (``Accept-Encoding: identity``).
8. A cut answer (``Answer.cut``) is refused. Hidden characters and the
   secret fields go. The row's cap holds.
9. Each credential form goes from the answer and from each error text.
10. One ``api.call`` log line: the connection id, the template, the op, the
    status, the time and the size. Never an argument value, a header or a
    token.

**The gateway total.** ``read`` with ``total`` reads the pages of a list op
with a total mark, up to ``MAX_TOTAL_PAGES``. Each page after the first
spends one call of the limiter and the budget. A limit stops the loop, and the
answer says ``incomplete`` with no ``total`` (§2.3a, decision d).

Fences: ``tests/unit/test_api_call_egress.py``,
``tests/unit/test_api_call_limits.py``,
``tests/unit/test_api_connections_r8.py`` (R8) and
``tests/unit/test_api_no_env_credentials.py``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from acb_auth import UserContext, get_current_user
from acb_auth.permissions import integration_use_permission
from acb_common import get_logger, tenant_redis
from acb_common.app_manifests.base import ARG_KEY_PATTERN, BadCall
from acb_skills.hidden_text import strip_hidden
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from gateway import outbound_guard
from gateway.connections import api as api_templates
from gateway.connections import mcp_tools, shared_token, store
from gateway.connections.api import call, connect
from gateway.connections.api.template import (
    MAX_TOTAL_PAGES,
    TEMPLATE_ID_PATTERN,
    Operation,
    Template,
    build,
)
from gateway.crm_sources.base import (
    NeedsReconnect,
    SourceCredential,
    SourceError,
    UntrustedHost,
)
from gateway.crm_sources.zoho import auth
from gateway.db import tenant_session
from gateway.routes.crm import connection as crm_connection
from gateway.routes.integrations_api_connect import (
    MANAGE_PERMISSION,
    OWNING_APP,
    require_api_connections,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_log = get_logger("gateway.api_connections")

#: The namespace of the limiter counters of an API call. The Studio live read
#: keeps ``mcp_tools.LIVE_NAMESPACE``, so the two never share a counter.
API_NAMESPACE = "api-call"
#: The namespace of the daily budget of each connection.
BUDGET_NAMESPACE = "api-budget"

#: The refusal of every call of the write route (D-API-4). It is a constant:
#: no setting, flag or column opens writes in API-3.
WRITES_OFF = "writes_off"

#: The one answer of a name that the caller may not see.
NOT_FOUND = "Not Found"

#: The most ops that one find answers.
MAX_FIND = 20
#: The most argument keys of one call.
MAX_ARGS = 32

_TEMPLATE_ID = re.compile(TEMPLATE_ID_PATTERN)
_ARG_KEY = re.compile(ARG_KEY_PATTERN)
_WORD = re.compile(r"[a-z0-9]+")

#: The values of ``provider_meta.grant`` that the gateway knows. Each one, and
#: each unknown value, asks for ``integrations:manage`` today (D-API-3).
_GRANTS: tuple[str, ...] = (connect.GRANT_ADMIN,)

router = APIRouter(
    prefix="/connections/api", tags=["integrations"],
    # The flag runs first, so a dark route is a 404 for every caller. There
    # is no feature gate: the member role holds no ``feature:integrations``,
    # and a member reads through the chat (§2.3a).
    dependencies=[Depends(require_api_connections)],
)


class CallIn(BaseModel):
    """The body of ``read`` and ``write``. No org, no host, no path."""

    model_config = ConfigDict(extra="forbid")

    op: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    #: The gateway total of a list op with a total mark (``read`` only).
    total: bool = False


# ── Seams that a test replaces ──────────────────────────────────────────────


def _redis() -> tenant_redis.TenantRedis:
    """The tenant-prefixed client of the daily budget."""
    return tenant_redis.get_tenant_redis()


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _https(method: str, url: str, headers: dict[str, str]) -> outbound_guard.Answer:
    """One request to a template host: https on 443 only, through the guard."""
    return await outbound_guard.request_https(method, url, headers=headers)


# ── Refusals ────────────────────────────────────────────────────────────────


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


def _refused(http_status: int, code: str, /, **extra: Any) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, **extra})


def _limited(retry_after: int, code: str) -> HTTPException:
    return HTTPException(status_code=429, detail={"code": code, "retry_after": retry_after},
                         headers={"Retry-After": str(retry_after)})


# ── Steps 1 and 2: the caller, the grant and the row ────────────────────────


@dataclass(frozen=True)
class _Found:
    org: str
    member: str
    template: Template
    row: store.Connection


def _granted(user: UserContext, name: str, row: store.Connection) -> bool:
    """The grant of §2.3a, decision a."""
    if not user.has_permission(integration_use_permission(f"api-{name}")):
        return False
    grant = row.provider_meta.get(connect.META_GRANT)
    needs_manage = grant == connect.GRANT_ADMIN or grant not in _GRANTS
    return not needs_manage or user.has_permission(MANAGE_PERMISSION)


async def _resolve(user: UserContext, name: str) -> _Found:
    """The connection *name* of the caller's org, or the one 404.

    ⚠️ ONE code path for every refusal. A name of the right shape always
    reads the row in the caller's tenant session, also when the template is
    unknown or the grant is missing. So the time and the text do not tell a
    member which connections exist.
    """
    org = str(user.organization_id or "")
    member = (user.email or "").strip().lower()
    shaped = bool(org and member and isinstance(name, str) and _TEMPLATE_ID.fullmatch(name))
    row: store.Connection | None = None
    if shaped:
        async with tenant_session(org) as db:
            row = await store.get(db, org, f"api:{name}", OWNING_APP)
    template = api_templates.template(name)
    if (
        template is None or row is None
        or row.status not in (store.STATUS_CONNECTED, store.STATUS_NEEDS_RECONNECT)
        or not _granted(user, name, row)
    ):
        raise _not_found()
    if row.provider_meta.get(connect.META_OPS_HASH) != connect.ops_hash(template):
        # §2.2: a changed template asks the admin again. No op is approved.
        raise _refused(409, "operations_changed")
    return _Found(org=org, member=member, template=template, row=row)


def _visible_op(template: Template, name: str) -> Operation | None:
    """An op that the model may see: class A only, while writes are off."""
    op = template.op(name)
    return op if op is not None and op.cls == "A" else None


# ── Step 4: the limiter and the daily budget ────────────────────────────────


async def _spend_limiter(found: _Found) -> None:
    try:
        await mcp_tools._spend(found.org, found.member, namespace=API_NAMESPACE)
    except mcp_tools.LiveLimited as exc:
        raise _limited(exc.retry_after, "rate_limit") from None
    except mcp_tools.ListFailed:
        raise _refused(503, "limiter_unavailable") from None


async def _spend_day(found: _Found) -> None:
    """One call of the connection's daily budget, in ONE MULTI block.

    The INCR and an EXPIREAT at the end of the UTC day run together, so no
    key lives without an end and no second command races the first.
    """
    day, ends_at, left = call.utc_day(_utcnow())
    cap = call.daily_budget(found.template)
    try:
        with tenant_redis.organization_scope(found.org):
            client = _redis()
            key = client.key(BUDGET_NAMESPACE, str(found.row.id), day)
            pipe = client.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expireat(key, ends_at)
            result = await pipe.execute()
        count = int(result[0])
    except Exception as exc:  # the class only: fail closed
        _log.warning("api.budget_unavailable", error=type(exc).__name__)
        raise _refused(503, "budget_unavailable") from None
    if count > cap:
        raise _limited(left, "daily_budget")


async def _spend(found: _Found) -> None:
    await _spend_limiter(found)
    await _spend_day(found)


# ── Steps 5 to 9: one request ───────────────────────────────────────────────


def _check_args(found: _Found, op: Operation, args: dict[str, Any]) -> None:
    """Step 5. The ONE builder refuses a key outside the row, a key that the
    gateway sets, and a value outside its shape. The error names the key
    only when it has the shape of a key."""
    if len(args) > MAX_ARGS:
        raise _refused(422, "bad_argument", why="too_many")
    try:
        build(found.template, op.name, args)
    except BadCall as exc:
        named = exc.name if _ARG_KEY.fullmatch(str(exc.name)) else None
        raise _refused(422, "bad_argument", argument=named, why=exc.why) from None


def _refresher(template: Template) -> Callable[[SourceCredential], Awaitable[SourceCredential]]:
    """The refresh of the template's client (§2.3a, decision i)."""
    if template.sign_in.client == "zoho":
        async def _zoho(credential: SourceCredential) -> SourceCredential:
            return await auth.refresh(crm_connection.zoho_oauth_config(), credential)
        return _zoho
    raise _refused(503, "sign_in_unsupported")


@dataclass(frozen=True)
class _Ready:
    found: _Found
    op: Operation
    host: str
    token: str
    context: dict[str, str]
    forms: tuple[str, ...]


async def _ready(found: _Found, op: Operation) -> _Ready:
    """Steps 6 and 7: a fresh credential, and the checked host."""
    row_id = found.row.id
    try:
        credential = await shared_token.fresh_credential(
            found.org, provider=found.template.provider, owning_app=OWNING_APP,
            refresh=_refresher(found.template), needs_refresh=auth.needs_refresh,
            now=auth.utcnow, accept=lambda row: row.id == row_id,
        )
    except NeedsReconnect:
        raise _refused(409, "needs_reconnect") from None
    except SourceError:
        raise _refused(503, "refresh_failed") from None
    if credential is None:
        raise _not_found()
    forms = call.secret_forms(
        (credential.access_token, credential.refresh_token, credential.client_secret),
        scheme=found.template.sign_in.scheme)
    # §2.3a: each secret is plain and at least call.MIN_SECRET long, so the
    # remover never takes a short value. An empty refresh token or client
    # secret is absent, and passes.
    if not call.token_is_plain(credential.access_token) or not all(
            not value or call.token_is_plain(value)
            for value in (credential.refresh_token, credential.client_secret)):
        raise _refused(503, "credential_unusable")
    domain = (credential.provider_meta.get(auth.META_API_DOMAIN)
              or found.row.provider_meta.get(auth.META_API_DOMAIN))
    try:
        host = call.check_host(found.template, domain, api_templates.CLIENT_HOSTS)
        context = call.context_values(found.template, found.row.provider_meta)
    except UntrustedHost:
        raise _refused(502, "untrusted_host") from None
    except call.AnswerRefused as exc:
        raise _refused(409, exc.code) from None
    return _Ready(found=found, op=op, host=host, token=credential.access_token,
                  context=context, forms=forms)


def _too_large(op: Operation) -> HTTPException:
    page = op.paging.size_key if op.paging is not None else None
    return _refused(422, "answer_too_large", page_argument=page)


@dataclass
class _Record:
    """The facts of the one ``api.call`` line. No value of the caller."""

    status: int = 0
    upstream: int | None = None
    size: int = 0


async def _fetch(ready: _Ready, args: dict[str, Any], record: _Record, *,
                 exact: bool = False) -> Any:
    """Steps 7 to 9 for one request. Returns the cleaned JSON."""
    op, template = ready.op, ready.found.template
    try:
        url = connect.call_url(ready.host, template, op.name, args, ready.context)
    except BadCall as exc:
        raise _refused(422, "bad_argument", why=exc.why) from None
    headers = connect.auth_headers(template, ready.token)
    try:
        answer = await _https(op.method, url, headers)
    except outbound_guard.OutboundRefused as exc:
        raise _refused(502, "refused", reason=call.clean_text(exc.reason, ready.forms, strip_hidden)) from None
    except httpx.HTTPError as exc:
        raise _refused(502, "transport_error", kind=type(exc).__name__) from None
    record.upstream = answer.status_code
    record.size += len(answer.body)
    if answer.cut:
        raise _too_large(op)
    if not answer.is_success:
        raise _refused(502, "upstream_error", status=answer.status_code,
                       message=call.error_message(answer.body, ready.forms, strip_hidden))
    try:
        data = call.clean(call.parse(answer.body, exact=exact),
                          secret_fields=op.secret_fields, forms=ready.forms,
                          strip=strip_hidden)
    except call.AnswerRefused as exc:
        raise _refused(502, exc.code) from None
    if call.size_of(data) > op.answer_cap:
        raise _too_large(op)
    return data


async def _total(ready: _Ready, args: dict[str, Any], record: _Record) -> dict[str, Any]:
    """The gateway total (§2.3a, decision d). Page 1 has spent already."""
    op = ready.op
    if op.total is None or op.paging is None:
        raise _refused(422, "no_total")
    adder = call.Adder(op.total)
    pages = min(op.total.max_pages, MAX_TOTAL_PAGES)
    size = op.paging.size
    stopped: str | None = "page_limit"
    for page in range(1, pages + 1):
        if page > 1:
            try:
                await _spend(ready.found)
            except HTTPException as exc:
                if exc.status_code != 429:
                    raise
                detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
                stopped = str(detail.get("code") or "rate_limit")
                break
        page_args = {**args, op.paging.page_key: str(page), op.paging.size_key: str(size)}
        data = await _fetch(ready, page_args, record, exact=True)
        try:
            count = adder.add_page(data)
        except call.AnswerRefused as exc:
            raise _refused(422 if exc.code == "not_exact" else 502, exc.code) from None
        if count < size:
            stopped = None
            break
    mixed = adder.mixed()
    if mixed is not None:
        raise _refused(422, "mixed_currency", currencies=mixed)
    try:
        out = adder.answer(incomplete=stopped is not None, stopped_by=stopped)
    except call.AnswerRefused as exc:
        raise _refused(422, exc.code) from None
    if call.size_of(out) > op.answer_cap:
        raise _too_large(op)
    return out


# ── The log line ────────────────────────────────────────────────────────────


def _log_call(found: _Found | None, op: str | None, record: _Record, started: float) -> None:
    """Step 10: ONE ``api.call`` line. Ids and counts only."""
    _log.info(
        "api.call",
        connection=str(found.row.id) if found else "",
        template=found.template.id if found else "",
        op=op or "",
        status=record.status,
        upstream=record.upstream,
        ms=int((time.monotonic() - started) * 1000),
        bytes=record.size,
    )


def _known_op(found: _Found | None, op: str) -> str | None:
    """The op name for the log line, only when the template names it."""
    return op if found is not None and found.template.op(op) is not None else None


# ── Routes ──────────────────────────────────────────────────────────────────


async def _body(request: Request) -> CallIn:
    """The body of ``read`` and ``write``, read AFTER the flag dependency.

    A ``CallIn`` parameter makes FastAPI parse the body before any
    dependency runs, so a dark route would answer 422 for a bad body. Here
    the router dependency runs first, so a dark route is 404 for ANY body
    (§2.3a). An error names the place and the type, never the input.
    """
    raw = await request.body()
    try:
        return CallIn.model_validate_json(raw or b"null")
    except ValidationError as exc:
        errors = [{"loc": list(e.get("loc", ())), "type": e.get("type")}
                  for e in exc.errors(include_url=False, include_input=False)]
        raise _refused(422, "bad_body", errors=errors[:10]) from None


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower().replace("_", " ")))


@router.get("/{name}/ops")
async def find_operations(
    name: str,
    user: Annotated[UserContext, Depends(get_current_user)],
    q: str = Query(default="", max_length=200),
) -> dict[str, Any]:
    """The approved ops of the connection that match the words of *q*."""
    found = await _resolve(user, name)
    await _spend_limiter(found)
    wanted = _words(q)
    scored: list[tuple[int, Operation]] = []
    for op in found.template.operations:
        if _visible_op(found.template, op.name) is None:
            continue
        hits = len(wanted & (_words(op.name) | _words(op.title)))
        if hits or not wanted:
            scored.append((hits, op))
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return {
        "connection": found.template.id,
        "label": found.template.label,
        "summary": found.template.summary,
        "operations": [{"op": op.name, "class": op.cls, "title": op.title}
                       for _, op in scored[:MAX_FIND]],
    }


@router.get("/{name}/ops/{op}")
async def describe_operation(
    name: str, op: str,
    user: Annotated[UserContext, Depends(get_current_user)],
) -> dict[str, Any]:
    """The arguments of one approved op, with their shapes."""
    found = await _resolve(user, name)
    await _spend_limiter(found)
    row = _visible_op(found.template, op)
    if row is None:
        raise _refused(404, "operation_not_found")
    arguments = [
        *({"name": key, "in": "path", "shape": shape, "required": True}
          for key, shape in row.path_shapes),
        *({"name": key, "in": "query", "shape": shape, "required": False}
          for key, shape in row.query),
    ]
    return {
        "connection": found.template.id,
        "op": row.name, "class": row.cls, "title": row.title,
        "arguments": arguments,
        "paging": ({"page": row.paging.page_key, "size": row.paging.size_key,
                    "page_size": row.paging.size} if row.paging else None),
        "total": ({"field": row.total.field, "currency": row.total.currency or None}
                  if row.total else None),
    }


@router.post("/{name}/read")
async def read_operation(
    name: str,
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)],
) -> dict[str, Any]:
    """Run one class A op (§2.3). With ``total``, the gateway total."""
    started = time.monotonic()
    record = _Record()
    found: _Found | None = None
    op_name = ""
    try:
        found = await _resolve(user, name)
        body = await _body(request)
        op_name = body.op
        # A hidden op (class B or C while writes are off) is the same 404 as
        # an unknown op, so a member cannot list the write ops.
        op = _visible_op(found.template, body.op)
        if op is None:
            raise _refused(404, "operation_not_found")
        if op.cls != "A":
            raise _refused(403, "wrong_class")
        if body.total and (op.total is None or op.paging is None):
            raise _refused(422, "no_total")
        await _spend(found)
        args = dict(body.args)
        if body.total and op.paging is not None:
            args.update({op.paging.page_key: "1", op.paging.size_key: str(op.paging.size)})
        _check_args(found, op, args)
        ready = await _ready(found, op)
        if body.total:
            out = {"connection": found.template.id, "op": op.name,
                   **await _total(ready, dict(body.args), record)}
        else:
            data = await _fetch(ready, args, record)
            out = {"connection": found.template.id, "op": op.name,
                   "status": record.upstream, "data": data}
        record.status = 200
        return out
    except HTTPException as exc:
        record.status = exc.status_code
        raise
    except Exception:
        record.status = 500
        raise
    finally:
        _log_call(found, _known_op(found, op_name), record, started)


@router.post("/{name}/write")
async def write_operation(
    name: str,
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)],
) -> dict[str, Any]:
    """Steps 1 to 3, then the refusal of every call (D-API-4).

    It spends one call of the limiter, so a loop cannot probe op names
    fast. A hidden op is the same 404 as an unknown op. A class A op is 403
    ``wrong_class``. ⚠️ Nothing here sends a request. No setting, flag or
    column opens this route in API-3. API-9 builds writes.
    """
    started = time.monotonic()
    record = _Record()
    found: _Found | None = None
    op_name = ""
    try:
        found = await _resolve(user, name)
        body = await _body(request)
        op_name = body.op
        await _spend_limiter(found)
        op = _visible_op(found.template, body.op)
        if op is None:
            raise _refused(404, "operation_not_found")
        if op.cls == "A":
            raise _refused(403, "wrong_class")
        raise _refused(403, WRITES_OFF)
    except HTTPException as exc:
        record.status = exc.status_code
        raise
    except Exception:
        record.status = 500
        raise
    finally:
        _log_call(found, _known_op(found, op_name), record, started)


__all__ = ["API_NAMESPACE", "BUDGET_NAMESPACE", "WRITES_OFF", "router"]
