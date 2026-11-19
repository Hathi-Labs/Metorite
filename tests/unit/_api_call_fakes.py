"""Fakes of the API-3 call suites (task 3789). No real network, no real token.

* :class:`FakeRedis` is the redis-py surface of the limiter and the daily
  budget, behind the real ``TenantRedis``. ``down`` makes each call raise.
* :class:`Wire` replaces ``outbound_guard.send`` and ``outbound_guard._resolve``.
  So ``request_https`` and ``check_mcp_url`` run for real, and no socket opens.
* :func:`mock_wire` keeps the REAL ``send`` and gives httpx a mock transport,
  for the redirect and the cut answer.

The tokens are sentinels. Each suite asserts that no answer, error text or
log line holds one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from gateway import outbound_guard
from gateway.connections.api import connect
from gateway.connections.api.zoho_books import ZOHO_BOOKS
from gateway.crm_sources.base import SourceCredential
from gateway.crm_sources.zoho import auth

S_ACCESS = "SENTINEL-API3-ACCESS-7f3a9c41"
S_REFRESH = "SENTINEL-API3-REFRESH-2b8e06d7"
S_ACCESS_B = "SENTINEL-API3-ACCESS-B-91c0e2"
S_REFRESH_B = "SENTINEL-API3-REFRESH-B-5d4f13"
SENTINELS = (S_ACCESS, S_REFRESH, S_ACCESS_B, S_REFRESH_B)

ORG_A = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de3a01"
ORG_B = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de3a02"
ADMIN = "admin@api3.test"
MEMBER = "member@api3.test"
BOOKS_ORG = "60001"
BOOKS_ORG_B = "60002"
API_HOST = "https://www.zohoapis.in"
PUBLIC_IP = "93.184.216.34"

ADMIN_PERMS = ["integrations:use:*", "integrations:manage"]
MEMBER_PERMS = ["integrations:use:*"]

REAL_CLIENT = httpx.AsyncClient
REAL_SEND = outbound_guard.send


def user(email: str = ADMIN, org: str | None = ORG_A, perms: list[str] | None = None) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(perms if perms is not None else ADMIN_PERMS),
        organization_id=org,
    )


def credential(access: str = S_ACCESS, refresh: str = S_REFRESH, *,
               books_org: str = BOOKS_ORG, api_domain: str = API_HOST,
               template: Any = ZOHO_BOOKS) -> SourceCredential:
    """A sentinel credential with the ``provider_meta`` of API-2."""
    meta = connect.provider_meta(template, {
        auth.META_LOCATION: "in", auth.META_API_DOMAIN: api_domain,
        auth.META_ACCOUNTS_SERVER: "https://accounts.zoho.in",
    }, organization_id=books_org)
    meta[auth.META_API_DOMAIN] = api_domain  # a test may name a bad host
    return SourceCredential(
        access_token=access, refresh_token=refresh,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=connect.ZOHO_BOOKS_SCOPES, provider_meta=meta,
    )


# ── Redis ────────────────────────────────────────────────────────────────────


class _Pipe:
    def __init__(self, owner: FakeRedis, transaction: bool) -> None:
        self.owner, self.transaction, self.ops = owner, transaction, []

    def incr(self, key: str, amount: int = 1) -> _Pipe:
        self.ops.append(("incr", key, amount))
        return self

    def expireat(self, key: str, when: int) -> _Pipe:
        self.ops.append(("expireat", key, when))
        return self

    async def execute(self) -> list[Any]:
        self.owner._check()
        self.owner.multi.append((self.transaction, [op[0] for op in self.ops]))
        out: list[Any] = []
        for op, key, value in self.ops:
            if op == "incr":
                self.owner.data[key] = int(self.owner.data.get(key, 0)) + value
                out.append(self.owner.data[key])
            else:
                self.owner.ends[key] = value
                out.append(True)
        return out


@dataclass
class FakeRedis:
    """The calls of ``mcp_tools._spend`` and of the daily budget."""

    data: dict[str, Any] = field(default_factory=dict)
    ttls: dict[str, int] = field(default_factory=dict)
    ends: dict[str, int] = field(default_factory=dict)
    multi: list[tuple[bool, list[str]]] = field(default_factory=list)
    down: bool = False

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("redis is down")

    async def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None) -> bool:
        self._check()
        if nx and key in self.data:
            return False
        self.data[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def incr(self, key: str, amount: int = 1) -> int:
        self._check()
        self.data[key] = int(self.data.get(key, 0)) + amount
        return int(self.data[key])

    async def ttl(self, key: str) -> int:
        self._check()
        return self.ttls.get(key, -1)

    async def expire(self, key: str, seconds: int) -> bool:
        self._check()
        self.ttls[key] = seconds
        return True

    async def get(self, key: str) -> Any:
        self._check()
        return self.data.get(key)

    async def setex(self, key: str, seconds: int, value: Any) -> bool:
        self._check()
        self.data[key], self.ttls[key] = value, seconds
        return True

    def pipeline(self, transaction: bool = False) -> _Pipe:
        return _Pipe(self, transaction)


def use_redis(monkeypatch: Any, raw: FakeRedis) -> None:
    """The fake behind the limiter and the budget, through ``TenantRedis``."""
    from acb_common.tenant_redis import TenantRedis
    from gateway.connections import mcp_tools
    from gateway.routes import api_connections as routes

    monkeypatch.setattr(routes, "_redis", lambda: TenantRedis(raw))
    monkeypatch.setattr(mcp_tools, "_redis", lambda: TenantRedis(raw))


# ── The wire ─────────────────────────────────────────────────────────────────


def books_page(count: int, *, amount: Any = 10, currency: str = "INR",
               extra: dict[str, Any] | None = None) -> bytes:
    invoices = [{"invoice_id": str(1000 + i), "total": amount, "currency_code": currency}
                for i in range(count)]
    return json.dumps({"code": 0, "message": "success", "invoices": invoices,
                       **(extra or {})}).encode()


@dataclass
class Sent:
    host: str
    ip: str
    method: str
    url: str
    headers: dict[str, str]


@dataclass
class Wire:
    """``outbound_guard.send``: it records each request and answers with
    ``answer`` (an Answer, a list of them, a callable, or an exception)."""

    answer: Any = None
    sent: list[Sent] = field(default_factory=list)

    async def send(self, target: outbound_guard.Target, method: str, *,
                   headers: dict[str, str] | None = None, content: Any = None,
                   json: Any = None) -> outbound_guard.Answer:
        self.sent.append(Sent(host=target.host, ip=target.ip, method=method,
                              url=str(target.url), headers=dict(headers or {})))
        answer = self.answer
        if callable(answer):
            answer = answer(self.sent[-1])
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            return outbound_guard.Answer(status_code=200, body=books_page(1))
        return answer


def use_wire(monkeypatch: Any, wire: Wire) -> Wire:
    monkeypatch.setattr(outbound_guard, "_resolve", lambda host, port: [PUBLIC_IP])
    monkeypatch.setattr(outbound_guard, "send", wire.send)
    return wire


class _Chunks(httpx.AsyncByteStream):
    """A body that arrives in chunks of 16 KiB, as from a socket."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self) -> Any:
        for at in range(0, len(self.body), 16 * 1024):
            yield self.body[at:at + 16 * 1024]


def streamed(body: bytes, status: int = 200) -> httpx.Response:
    """An answer that the real ``send`` streams, as from a socket."""
    return httpx.Response(status, stream=_Chunks(body))


def mock_wire(monkeypatch: Any, handler: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Request]:
    """The REAL ``send``, over a mock transport. Each request is recorded."""
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(_record)

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.setdefault("transport", transport)
        return REAL_CLIENT(*args, **kwargs)

    monkeypatch.setattr(outbound_guard, "_resolve", lambda host, port: [PUBLIC_IP])
    monkeypatch.setattr(outbound_guard, "send", REAL_SEND)
    monkeypatch.setattr(httpx, "AsyncClient", _client)
    return seen


# ── The hermetic store ───────────────────────────────────────────────────────


@dataclass
class Rows:
    """The ``api:<id>`` row of each org, for the hermetic suites."""

    rows: dict[tuple[str, str], tuple[str, SourceCredential, str]] = field(default_factory=dict)
    reads: list[tuple[str, str]] = field(default_factory=list)

    def put(self, org: str, cred: SourceCredential, *, provider: str = "api:zoho_books",
            status: str = "connected") -> None:
        self.rows[(org, provider)] = ("0b0e8c1e-0000-4000-8000-0000000a3a3" + org[-1], cred, status)

    @asynccontextmanager
    async def session(self, org: str) -> Any:
        yield object()

    async def get(self, db: Any, org: str, provider: str, owning_app: str, **kw: Any) -> Any:
        from gateway.connections import store

        self.reads.append((org, provider))
        found = self.rows.get((org, provider))
        if found is None:
            return None
        row_id, cred, status = found
        return store.Connection(
            id=row_id, organization_id=org, provider=provider, owning_app=owning_app,
            scope_kind="org", member_email=None, status=status,
            provider_meta=dict(cred.provider_meta), scopes=tuple(cred.scopes),
            connected_by=ADMIN, last_sync_at=None, last_error=None,
            created_at=None, updated_at=None,
        )

    async def fresh(self, org: str, *, provider: str, accept: Any = None, **kw: Any) -> Any:
        found = self.rows.get((org, provider))
        return found[1] if found else None


def use_rows(monkeypatch: Any, rows: Rows) -> Rows:
    from gateway.connections import shared_token, store
    from gateway.routes import api_connections as routes

    monkeypatch.setattr(routes, "tenant_session", rows.session)
    monkeypatch.setattr(store, "get", rows.get)
    monkeypatch.setattr(shared_token, "fresh_credential", rows.fresh)
    return rows


def flag(monkeypatch: Any, value: str = "*") -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "api_connections_orgs", value, raising=False)


def hermetic(monkeypatch: Any, *, wire: Wire | None = None) -> tuple[Rows, Wire, FakeRedis]:
    """The flag on, one row for org A, a fake wire and a fake Redis."""
    flag(monkeypatch)
    rows = use_rows(monkeypatch, Rows())
    rows.put(ORG_A, credential())
    raw = FakeRedis()
    use_redis(monkeypatch, raw)
    return rows, use_wire(monkeypatch, wire or Wire()), raw


async def call(who: Any, method: str, path: str, *, body: Any = None,
               content: bytes | None = None,
               params: dict[str, str] | None = None,
               headers: dict[str, str] | None = None) -> httpx.Response:
    """One request through the real router, as *who*. The tenant comes from
    the user, never from the request."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import api_connections as routes

    async def _caller() -> Any:
        if who is not None and who.organization_id:
            bind_tenant(who.organization_id)
        return who

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with REAL_CLIENT(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
        ) as client:
            return await client.request(method, path, json=body, content=content, params=params,
                                        headers=headers)
    finally:
        release_tenant(scope)


def read(op: str = "list_invoices", **kw: Any) -> dict[str, Any]:
    total = kw.pop("total", False)
    return {"op": op, "args": kw, **({"total": True} if total else {})}


def use_template(monkeypatch: Any, template: Any) -> None:
    """Serve *template* as ``zoho_books``."""
    from gateway.connections import api as api_templates

    monkeypatch.setattr(api_templates, "template",
                        lambda name: template if name == "zoho_books" else None)


def with_op(template: Any, op: Any) -> Any:
    """*template* with one more op."""
    return replace(template, operations=(*template.operations, op))
