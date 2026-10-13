"""A local fake world for the MCP OAuth suites (WS-54 IN-8a).

No test here reaches a real provider. The world is ONE ``httpx.MockTransport``
under each client that ``outbound_guard.send`` opens, and a fake resolver for
``outbound_guard._resolve``. So every request still passes the REAL guard:
the scheme, the port, the address check, the pin and the redirect refusal.

The world plays three parts, each on its own host:

* ``mcp.fake.test`` — the MCP server (a 401 that names its metadata) and its
  protected-resource metadata (RFC 9728).
* ``auth.fake.test`` — the auth server: its metadata (RFC 8414), DCR, the
  token endpoint and the revoke endpoint.
* a browser: :meth:`World.consent` reads a consent URL the way an auth server
  does, and issues a code bound to the S256 challenge of that URL.

The guard pins each request to the address that it resolved, and it sends the
name in ``Host``. The world routes on ``Host``, so a request that skipped the
pin would not find its handler.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

#: The real client, taken before a test replaces ``httpx.AsyncClient``.
REAL_CLIENT = httpx.AsyncClient

PUBLIC_IP = "93.184.216.34"
MCP_HOST = "mcp.fake.test"
AUTH_HOST = "auth.fake.test"
MCP_URL = f"https://{MCP_HOST}/mcp"
ISSUER = f"https://{AUTH_HOST}"
PRM_URL = f"https://{MCP_HOST}/.well-known/oauth-protected-resource/mcp"
AS_META_URL = f"{ISSUER}/.well-known/oauth-authorization-server"
WORKBENCH = "https://app.metorite.test"
REDIRECT_URI = f"{WORKBENCH}/api/integrations/mcp/oauth/callback"
CIMD_URL = f"{WORKBENCH}/oauth/mcp-client.json"

#: Hosts that resolve to an address the guard must refuse.
PRIVATE_HOSTS = {
    "loop.fake.test": "127.0.0.1",
    "meta.fake.test": "169.254.169.254",
    "lan.fake.test": "10.0.0.7",
}

SENTINEL_ACCESS = "SENTINEL-in8a-access-7f3e"
SENTINEL_REFRESH = "SENTINEL-in8a-refresh-41aa"
SENTINEL_SECRET = "SENTINEL-in8a-client-secret-9c0d"
SENTINELS = (SENTINEL_ACCESS, SENTINEL_REFRESH, SENTINEL_SECRET)


def s256(verifier: str) -> str:
    import hashlib

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass
class Grant:
    challenge: str
    method: str
    client_id: str
    redirect_uri: str
    resource: str


@dataclass
class World:
    """The fake providers. Change a field to change what they answer."""

    www_authenticate: str | None = (
        f'Bearer resource_metadata="{PRM_URL}", scope="read write"')
    prm: dict[str, Any] | None = field(default_factory=lambda: {
        "resource": MCP_URL,
        "authorization_servers": [ISSUER],
        "scopes_supported": ["read", "write"],
    })
    as_meta: dict[str, Any] = field(default_factory=lambda: {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "registration_endpoint": f"{ISSUER}/register",
        "revocation_endpoint": f"{ISSUER}/revoke",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["plain", "S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "authorization_response_iss_parameter_supported": True,
    })
    registration: dict[str, Any] = field(default_factory=lambda: {
        "client_id": "dcr-client-1",
        "client_secret": SENTINEL_SECRET,
        "token_endpoint_auth_method": "client_secret_post",
        "redirect_uris": [REDIRECT_URI],
    })
    #: (host, path) -> a fixed answer, for a redirect or a fault.
    fixed: dict[tuple[str, str], httpx.Response] = field(default_factory=dict)
    #: Each request that reached the transport, so a socket would have opened.
    requests: list[httpx.Request] = field(default_factory=list)
    grants: dict[str, Grant] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    access_token: str = SENTINEL_ACCESS
    #: ``expires_in`` of the token answer. ``None`` leaves the key out.
    expires_in: Any = 3600
    #: Every access token that the token endpoint issued. The MCP server
    #: answers 200 to a request that carries one of them.
    issued: set[str] = field(default_factory=set)
    refresh_token: str = SENTINEL_REFRESH

    # ── the resolver ────────────────────────────────────────────────────────

    def resolve(self, host: str, port: int) -> list[str]:
        del port
        try:
            ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            pass
        else:
            return [host.strip("[]")]  # a literal resolves to itself
        if host in PRIVATE_HOSTS:
            return [PRIVATE_HOSTS[host]]
        return [PUBLIC_IP]

    # ── the transport ───────────────────────────────────────────────────────

    def handle(self, request: httpx.Request) -> httpx.Response:
        """The answer as a raw stream: the guard reads it with ``aiter_raw``."""
        answer = self._answer(request)
        body = answer.content
        headers = httpx.Headers(answer.headers)
        # As many real origins do: gzip when the client accepts it.
        if body and "gzip" in request.headers.get("accept-encoding", ""):
            import gzip

            body = gzip.compress(body)
            headers["content-encoding"] = "gzip"
            headers["content-length"] = str(len(body))
        return httpx.Response(
            answer.status_code, headers=headers, stream=httpx.ByteStream(body))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers.get("host", "")
        path = request.url.path
        if (host, path) in self.fixed:
            return self.fixed[(host, path)]
        route: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {
            (MCP_HOST, "/mcp"): self._mcp,
            (MCP_HOST, "/.well-known/oauth-protected-resource/mcp"): self._prm,
            (AUTH_HOST, "/.well-known/oauth-authorization-server"): self._as,
            (AUTH_HOST, "/register"): self._register,
            (AUTH_HOST, "/token"): self._token,
            (AUTH_HOST, "/revoke"): self._revoke,
        }
        handler = route.get((host, path))
        if handler is None:
            return httpx.Response(404, json={"error": "not_found"})
        return handler(request)

    def _mcp(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and auth[7:] in self.issued:
            return httpx.Response(200, json={"jsonrpc": "2.0"})
        headers = {"WWW-Authenticate": self.www_authenticate} if self.www_authenticate else {}
        return httpx.Response(401, headers=headers, json={"error": "unauthorized"})

    def _prm(self, request: httpx.Request) -> httpx.Response:
        if self.prm is None:
            return httpx.Response(404)
        return httpx.Response(200, json=self.prm)

    def _as(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.as_meta)

    def _register(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=self.registration)

    def _form(self, request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(request.content.decode("ascii")).items()}

    def _client_ok(self, request: httpx.Request, form: dict[str, str]) -> bool:
        reg = self.registration
        method = reg.get("token_endpoint_auth_method", "none")
        if method == "client_secret_basic":
            raw = request.headers.get("authorization", "")
            if not raw.startswith("Basic "):
                return False
            pair = base64.b64decode(raw[6:]).decode()
            cid, _, sec = pair.partition(":")
            return unquote(cid) == reg["client_id"] and unquote(sec) == reg["client_secret"]
        if form.get("client_id") not in (reg.get("client_id"), CIMD_URL):
            return False
        if method == "client_secret_post":
            return form.get("client_secret") == reg.get("client_secret")
        return "client_secret" not in form

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = self._form(request)
        grant = self.grants.pop(form.get("code", ""), None)
        if (grant is None or form.get("grant_type") != "authorization_code"
                or form.get("redirect_uri") != grant.redirect_uri
                or form.get("resource") != grant.resource
                or s256(form.get("code_verifier", "")) != grant.challenge
                or not self._client_ok(request, form)):
            return httpx.Response(400, json={"error": "invalid_grant"})
        answer: dict[str, Any] = {
            "access_token": self.access_token, "refresh_token": self.refresh_token,
            "token_type": "bearer", "scope": "read write",
        }
        if self.expires_in is not None:
            answer["expires_in"] = self.expires_in
        self.issued.add(self.access_token)
        return httpx.Response(200, json=answer)

    def _revoke(self, request: httpx.Request) -> httpx.Response:
        self.revoked.append(self._form(request).get("token", ""))
        return httpx.Response(200)

    # ── the browser ─────────────────────────────────────────────────────────

    def consent(self, url: str) -> tuple[str, str]:
        """Read a consent URL as the auth server does. Returns (code, state)."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        code = "code-" + secrets.token_hex(8)
        self.grants[code] = Grant(
            challenge=query.get("code_challenge", ""),
            method=query.get("code_challenge_method", ""),
            client_id=query.get("client_id", ""),
            redirect_uri=query.get("redirect_uri", ""),
            resource=query.get("resource", ""),
        )
        return code, query.get("state", "")


def install(monkeypatch: Any, world: World) -> World:
    """Put *world* under the guard: its resolver and its transport."""
    from gateway import outbound_guard as g

    monkeypatch.setattr(g, "_resolve", world.resolve)
    monkeypatch.setattr(g, "_is_local_address", lambda _a: False)

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_CLIENT(*args, transport=httpx.MockTransport(world.handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    return world


class FakeRawRedis:
    """The redis-py calls that the pending flow makes. Keys are the full
    tenant-prefixed strings that ``TenantRedis`` built."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def setex(self, key: str, seconds: int, value: str) -> bool:
        del seconds
        self.data[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.data.pop(key, None)

    async def get(self, key: str) -> str | None:
        return self.data.get(key)


def configure(monkeypatch: Any, *, enabled: bool = True, keep_key: bool = False) -> FakeRawRedis:
    """The flag, the signing secret, the master key, the public origin and a
    fake Redis behind the real ``TenantRedis``."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings
    from acb_common.tenant_redis import TenantRedis
    from gateway.routes import integrations_mcp_oauth as routes

    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_oauth_enabled", enabled, raising=False)
    monkeypatch.setattr(settings, "gateway_session_secret",
                        "in8a-test-secret-not-a-default-0123456789", raising=False)
    if not keep_key:  # an R8 suite keeps the master key of its fixture
        monkeypatch.setattr(settings, "acb_master_key", "in8a-master-key-for-tests",
                            raising=False)
        monkeypatch.setattr(key_store, "_store", None)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", WORKBENCH)
    raw = FakeRawRedis()
    monkeypatch.setattr(routes, "_redis", lambda: TenantRedis(raw))
    return raw


def user(email: str, org: str, perms: list[str] | None = None) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(perms if perms is not None else [
            "feature:integrations", "integrations:manage", "admin:members:read"]),
        organization_id=org,
    )


async def call(
    who: Any, method: str, path: str, *, params: dict[str, str] | None = None,
    routers: tuple[Any, ...] = (),
) -> httpx.Response:
    """One request through the real router, as *who*. The tenant comes from
    the user, never from the request."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import integrations_mcp_oauth as routes

    async def _caller() -> Any:
        if who is not None and who.organization_id:
            bind_tenant(who.organization_id)
        return who

    app = FastAPI()
    app.include_router(routes.router)
    for extra in routers:
        app.include_router(extra)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with REAL_CLIENT(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
        ) as client:
            return await client.request(method, path, params=params)
    finally:
        release_tenant(scope)


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def text_of(events: list[dict[str, Any]], caplog: Any = None) -> str:
    parts = [json.dumps(e, default=str) for e in events]
    if caplog is not None:
        parts += [r.getMessage() for r in caplog.records]
    return "\n".join(parts)


@dataclass
class Registry:
    """A hermetic stand-in for the registry row of one MCP server."""

    org: str
    name: str = "notion"
    url: str = MCP_URL
    attached: list[dict[str, Any]] = field(default_factory=list)
    previous: Any = None


def fake_registry(monkeypatch: Any, org: str, *, name: str = "notion",
                  url: str = MCP_URL) -> Registry:
    """The server row of *org*, and a recorder for the save under the lock.
    Another org holds no row. The R8 suite runs the real store."""
    from contextlib import asynccontextmanager

    from gateway.connections import mcp as registry
    from gateway.connections import store
    from gateway.routes import integrations_mcp_oauth as routes

    reg = Registry(org=org, name=name, url=url)
    server = registry.StoredServer(
        name=name, label=name, description="", transport="http-sse", url=url,
        agent_scope=("*",), enabled=True, header_names=())

    async def _list(o: str) -> list[Any]:
        return [server] if o == reg.org else []

    @asynccontextmanager
    async def _session(o: str):
        yield object()

    def _conn(o: str, meta: dict[str, Any]) -> Any:
        return store.Connection(
            id="00000000-0000-0000-0000-0000000000aa", organization_id=o,
            provider=registry.provider_for(name), owning_app=registry.OWNING_APP,
            scope_kind="org", member_email=None, status="connected",
            provider_meta=meta, scopes=(), connected_by=None, last_sync_at=None,
            last_error=None, created_at=None, updated_at=None)

    async def _attach(db: Any, o: str, provider: str, owning_app: str, credential: Any, *,
                      connected_by: str, scopes: Any = (), check: Any = None) -> Any:
        before = _conn(o, {"url": reg.url, "name": name})
        if o != reg.org or provider != registry.provider_for(name):
            return None
        if check is not None and not check(before):
            return None
        reg.attached.append({"org": o, "credential": credential,
                             "connected_by": connected_by, "scopes": tuple(scopes)})
        return store.OAuthAttach(before=before, after=before, previous=reg.previous)

    monkeypatch.setattr(routes.mcp_registry, "list_servers", _list)
    monkeypatch.setattr(routes, "tenant_session", _session)
    monkeypatch.setattr(routes.store, "attach_oauth_credential", _attach)
    return reg


async def start_and_consent(who: Any, world: World, name: str = "notion") -> tuple[str, str, str]:
    """POST start as *who*, then consent in the fake browser.
    Returns (consent url, code, state)."""
    res = await call(who, "POST", f"/integrations/mcp/{name}/oauth/start")
    assert res.status_code == 200, res.text
    url = res.json()["url"]
    code, state = world.consent(url)
    return url, code, state


async def finish(who: Any, code: str | None, state: str | None, **extra: str) -> httpx.Response:
    params = {k: v for k, v in {"code": code, "state": state, **extra}.items() if v is not None}
    return await call(who, "GET", "/integrations/mcp/oauth/callback", params=params)
