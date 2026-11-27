"""Shared parts of the sandbox door suites (SBX-2, ``agent_sandbox.md`` §12.2).

A small gateway: the real default deny, the real ``get_current_user`` under
the branch-0 tripwire, the real ``TenantScopeMiddleware`` and the real
``SandboxDoorLimits``. Its routes sit at real templates of ``SANDBOX_ROUTES``,
of the class X rows and of the rows off the list, so ``_matched_template``
gives the pairs of the manifests. Each route records that it ran and the
tenant that it saw. A probe inside the tenant middleware records the tenant
that is bound when the request ends.

The capability, the member resolve and the Redis store are stand-ins here.
The R8 half of SBX-F1 runs the same door on a real database.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

ORG_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORG_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
MEMBER = "priya@sandbox-door.test"
OTHER_MEMBER = "ravi@sandbox-door.test"
RAW = "msbx_" + "A" * 43
INTERNAL = "sandbox-door-internal-token"
PUBLIC_PATH = "/health"
BIG = 4_194_304 + 1
#: The byte of the big answer. No refusal text holds it.
BIG_BYTE = b"\x07"


def capability(**over: Any) -> Any:
    """A live row of org A, member Priya, with both read scopes."""
    from acb_auth.agent_tokens import hash_token
    from acb_auth.sandbox_capabilities import Capability

    fields: dict[str, Any] = {
        "token_sha256": hash_token(RAW),
        "organization_id": ORG_A,
        "member_email": MEMBER,
        "run_id": "run-1",
        "thread_id": "thread-1",
        "phase": "run",
        "app_id": None,
        "version": None,
        "read_scopes": ("crm:read", "projects:read"),
        "write_scopes": (),
        "expires_at": datetime.now(UTC) + timedelta(minutes=10),
        "seconds_left": 600,
    }
    fields.update(over)
    return Capability(**fields)


class FakePipeline:
    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.ops: list[tuple[str, Any]] = []

    def incr(self, k: Any, amount: int = 1) -> FakePipeline:
        self.ops.append(("incr", str(k)))
        return self

    def expire(self, k: Any, seconds: int) -> FakePipeline:
        self.ops.append(("expire", (str(k), seconds)))
        return self

    async def execute(self) -> list[Any]:
        if self.store.down:
            raise ConnectionError("redis is down")
        out: list[Any] = []
        for op, arg in self.ops:
            if op == "incr":
                self.store.counts[arg] = self.store.counts.get(arg, 0) + 1
                out.append(self.store.counts[arg])
            else:
                self.store.ttls[arg[0]] = arg[1]
                out.append(True)
        return out


class FakeRedis:
    """The two commands of the call cap and the refusal count, the read of
    the counts, and a switch for an outage.

    SBX-3a adds the commands of the held store and the ticket: ``set`` (with
    ``nx`` and ``ex``), ``get``, ``getdel``, ``delete``, ``ttl``, ``rpush``,
    ``expire`` and ``lrange``. Each key is the full tenant key text, so a
    test reads which org a value lives under."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.values: dict[str, Any] = {}
        self.lists: dict[str, list[Any]] = {}
        self.down = False
        self.pipelines = 0

    def _up(self) -> None:
        if self.down:
            raise ConnectionError("redis is down")

    async def set(self, k: Any, value: Any, nx: bool = False, ex: int | None = None) -> Any:
        self._up()
        name = str(k)
        if nx and name in self.values:
            return None
        self.values[name] = value
        if ex is not None:
            self.ttls[name] = int(ex)
        return True

    async def get(self, k: Any) -> Any:
        self._up()
        return self.values.get(str(k))

    async def getdel(self, k: Any) -> Any:
        self._up()
        self.ttls.pop(str(k), None)
        return self.values.pop(str(k), None)

    async def delete(self, *keys: Any) -> int:
        self._up()
        gone = 0
        for k in keys:
            gone += int(self.values.pop(str(k), None) is not None)
            self.lists.pop(str(k), None)
            self.ttls.pop(str(k), None)
        return gone

    async def ttl(self, k: Any) -> int:
        self._up()
        name = str(k)
        if name not in self.values and name not in self.lists:
            return -2
        return self.ttls.get(name, -1)

    async def rpush(self, k: Any, *values: Any) -> int:
        self._up()
        self.lists.setdefault(str(k), []).extend(values)
        return len(self.lists[str(k)])

    async def expire(self, k: Any, seconds: int) -> bool:
        self._up()
        self.ttls[str(k)] = int(seconds)
        return True

    async def lrange(self, k: Any, start: int, end: int) -> list[Any]:
        self._up()
        items = self.lists.get(str(k), [])
        return items[start:] if end == -1 else items[start:end + 1]

    def pipeline(self, transaction: bool = False) -> FakePipeline:
        assert transaction, "the INCR and its expiry go in one MULTI block"
        self.pipelines += 1
        return FakePipeline(self)

    async def mget(self, keys: list[Any]) -> list[Any]:
        """The read of ``door_counts`` (SBX-4b1). A missing key is None."""
        if self.down:
            raise ConnectionError("redis is down")
        return [self.counts.get(str(k)) for k in keys]


def make_app(monkeypatch: Any, *, tripwire: Any = None, big: bool = False) -> Any:
    """The small gateway. *tripwire* is an armed ``_branch_zero_tripwire``."""
    from acb_auth import deps, require_authenticated
    from acb_common.db import current_tenant
    from fastapi import Depends, FastAPI
    from fastapi.responses import Response
    from gateway.main import TenantScopeMiddleware
    from gateway.sandbox_door import SandboxDoorLimits

    ran: list[str] = []
    tenants: list[str | None] = []
    after: list[str | None] = []

    class _Probe:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            await self.inner(scope, receive, send)
            if scope.get("type") == "http":
                after.append(current_tenant())

    app = FastAPI(dependencies=[require_authenticated(public=frozenset({PUBLIC_PATH}))])
    app.add_middleware(_Probe)
    app.add_middleware(SandboxDoorLimits)
    app.add_middleware(TenantScopeMiddleware)

    def _route(path: str, method: str = "GET", body: bytes | None = None) -> None:
        async def handler(user: Any = Depends(deps.get_current_user)) -> Any:
            ran.append(f"{method} {path}")
            tenants.append(current_tenant())
            if body is not None:
                return Response(content=body, media_type="application/octet-stream")
            return {"email": user.email, "org": user.organization_id,
                    "sandbox": user.sandbox.phase if user.sandbox else None,
                    "agent": user.agent is not None}

        app.add_api_route(path, handler, methods=[method])

    _route("/crm/leads")
    _route("/crm/leads", "POST")
    _route("/crm/export/all.zip")
    _route("/crm/aggregate")
    _route("/crm/owners", body=BIG_BYTE * BIG if big else None)
    _route("/crm/views", body=b"y" * 4_194_304)
    _route("/projects/tasks")
    _route(PUBLIC_PATH)
    if tripwire is not None:
        armed = tripwire.arm(monkeypatch, deps)
        app.dependency_overrides[deps.get_current_user] = tripwire.current_user_override(armed)
    return SimpleNamespace(app=app, ran=ran, tenants=tenants, after=after)


def arm_door(monkeypatch: Any, *, cap: Any = None, flag: str = "*",
             member_org: str = ORG_A, active: bool = True,
             redis: FakeRedis | None = None, signup: str = "") -> SimpleNamespace:
    """Turn the door on with stand-ins for the row, the resolve and Redis."""
    from acb_auth import deps, sandbox_capabilities
    from acb_auth.permissions import build_access
    from acb_common import get_settings, tenant_redis

    settings = get_settings()
    monkeypatch.setattr(settings, "sandbox_door_orgs", flag)
    monkeypatch.setattr(settings, "self_serve_signup_enabled", signup)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    row = cap if cap is not None else capability()
    lookups: list[str] = []
    resolves: list[str] = []

    async def lookup(raw: str) -> Any:
        lookups.append("lookup")
        return row if raw == RAW else None

    async def access(email: str, legacy_role: Any = None, record_request: bool = False) -> Any:
        resolves.append(email)
        return build_access(["feature:crm", "feature:projects"], [], is_active=active)

    async def identity(email: str) -> Any:
        return ("22222222-2222-4222-8222-222222222222", member_org)

    store = redis if redis is not None else FakeRedis()
    monkeypatch.setattr(sandbox_capabilities, "lookup", lookup)
    monkeypatch.setattr(deps, "resolve_access", access)
    monkeypatch.setattr(deps, "resolve_identity", identity)
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: store)
    return SimpleNamespace(row=row, lookups=lookups, resolves=resolves, redis=store)


def headers(**extra: str) -> dict[str, str]:
    return {"X-Sandbox-Capability": RAW, **extra}
