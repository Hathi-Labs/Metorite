"""WS-55 EA-2a — the authorization server core, both halves, in process.

Spec: ``project-docs/specs/external_agent_access.md`` §5.3, §5.5, §7, §8, §10
and §15 EA-2a · decision D97.

The two halves under test:

* the PUBLIC half, the edge app ``agent_edge.app.create_app()``: the RFC 8414
  and RFC 9728 documents, ``POST /oauth/token`` and ``POST /oauth/revoke``;
* the AUTHENTICATED half, the gateway router ``gateway.routes.agent_access``:
  ``/agent-access/authorize/check`` and ``/agent-access/authorize/approve``,
  with the client's metadata document fetched through ``gateway.outbound_guard``.

The done-when of EA-2a, each one a test below:

* the two documents carry the issuer, the endpoints, ``["S256"]``,
  ``client_id_metadata_document_supported``,
  ``authorization_response_iss_parameter_supported``, ``resource``,
  ``authorization_servers`` and ``scopes_supported``;
* the server refuses an authorize request with no PKCE or with ``plain``;
* it refuses a token request with a wrong verifier, resource, redirect URI or
  client, a code used twice, and a code in a query string;
* the redirect carries ``iss``;
* a CIMD URL that resolves to a private address, a body over the cap and a
  slow host are refused;
* ``revoke`` answers 200 for an unknown token;
* with the flag OFF every new route answers 404;
* a member can never get a connection for another organization or another
  member by sending those values in the body;
* a structural fence: no ``agent_edge`` or ``agent_access`` module reads a
  tenant or a member from request input, and ``agent_edge`` imports neither
  the gateway nor ``outbound_guard``.

The classes marked R8 run the store on a real database, as a NON-privileged
role, on a phase-4-promoted catalog of their own (the shape of
``test_agent_connection_tokens.py``). The gate is
``TENANT_LADDER_DATABASE_URL``. A skip is not a pass.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_oauth_server.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth, agent_tokens, get_current_user, require_authenticated
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant
from agent_edge import app as edge_app_module
from agent_edge import metadata as edge_metadata
from fastapi import FastAPI
from gateway import outbound_guard
from gateway.routes import agent_access
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_ROOT = Path(__file__).resolve().parents[2]
_LADDER = _ROOT / "infra" / "postgres"
_GENERATED = _LADDER / "generated"
_EDGE_DIR = _ROOT / "apps" / "services" / "agent_edge" / "agent_edge"
_ACCESS_FILE = (
    _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "agent_access.py")

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it. ⚠️ Do NOT export "
        "it as DATABASE_URL — see test_org_provisioning.py's docstring."
    ),
)

#: Suite-private role and database suffix, so this suite never shares a
#: catalog with the EA-1 suite.
_APP_ROLE = "acb_app_agentoauth"
_APP_PW = "agentoauth_scratch_pw"

ISSUER = "https://mcp.metorite.com"
RESOURCE = "https://mcp.metorite.com/mcp"
AUTHORIZE = "https://app.metorite.com/connect/authorize"

CLIENT = "https://client.example/oauth/metadata.json"
OTHER_CLIENT = "https://other.example/oauth/metadata.json"
REDIRECT = "http://127.0.0.1:33418/callback"
VERIFIER = "v1-" + "a" * 50
CHALLENGE = agent_oauth.s256(VERIFIER)
MEMBER = "dev@agentoauth.test"

#: An address the fake resolver gives every public host. It is not an address
#: of this machine, so the guard's local check passes it.
_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """The flag is ON for every test, unless a test turns it off itself."""
    monkeypatch.setattr(get_settings(), "agent_access_enabled", True)


def _document(client_id: str = CLIENT, **over) -> dict:
    doc = {
        "client_id": client_id,
        "client_name": "Test Agent",
        "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    doc.update(over)
    return doc


class _FakeWeb:
    """A fake internet behind the REAL ``outbound_guard.check_url``.

    The test replaces only the resolver and the socket half (``send``). The
    guard's address check runs for real, so a private address is refused by
    the guard itself, before ``send`` is ever called.
    """

    def __init__(self) -> None:
        self.docs: dict[str, tuple[int, bytes]] = {}
        self.private: set[str] = set()
        self.delay = 0.0
        self.sent: list[str] = []

    def resolve(self, host: str, port: int) -> list[str]:
        return ["10.9.8.7"] if host in self.private else [_PUBLIC_IP]

    async def send(self, target, method, *, headers=None, content=None, json=None):
        self.sent.append(str(target.url))
        if self.delay:
            await asyncio.sleep(self.delay)
        status, body = self.docs.get(str(target.url), (404, b""))
        return outbound_guard.Answer(status_code=status, body=body)


@pytest.fixture
def web(monkeypatch) -> _FakeWeb:
    fake = _FakeWeb()
    monkeypatch.setattr(outbound_guard, "_resolve", fake.resolve)
    monkeypatch.setattr(outbound_guard, "send", fake.send)
    fake.docs[CLIENT] = (200, json.dumps(_document()).encode())
    fake.docs[OTHER_CLIENT] = (200, json.dumps(_document(OTHER_CLIENT)).encode())
    return fake


def _edge_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=edge_app_module.create_app()),
        base_url=ISSUER,
    )


def _member_ctx(org: str | None, email: str = MEMBER) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=EffectiveAccess(is_active=True),
    )


def _gateway_app(org: str | None, email: str = MEMBER) -> FastAPI:
    """The agent_access router behind the same default-deny guard as main.py.

    The user override binds the tenant, as ``get_current_user`` does on the
    request path. Each request runs in its own task (:func:`_gw`), so the
    binding ends with the request.
    """
    app = FastAPI(dependencies=[require_authenticated(public=frozenset())])
    app.include_router(agent_access.router)

    async def _user() -> UserContext:
        if org is not None:
            bind_tenant(org)
        return _member_ctx(org, email)

    app.dependency_overrides[get_current_user] = _user
    return app


async def _gw(app: FastAPI, path: str, body: dict) -> httpx.Response:
    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            return await client.post(path, json=body)

    return await asyncio.create_task(_go())


def _authorize(**over) -> dict:
    params = {
        "client_id": CLIENT,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
        "scope": "projects:read offline_access",
        "state": "st-123",
    }
    params.update(over)
    return {k: v for k, v in params.items() if v is not None}


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# ═══════════════════════════════════════════════════════════════════════════
# DB-free: the documents, the flag, malformed input, the CIMD fetch, fences
# ═══════════════════════════════════════════════════════════════════════════


class TestTheMetadataDocuments:
    async def test_the_authorization_server_document(self):
        async with _edge_client() as edge:
            r = await edge.get("/.well-known/oauth-authorization-server")
        assert r.status_code == 200
        doc = r.json()
        assert doc["issuer"] == ISSUER
        assert doc["authorization_endpoint"] == AUTHORIZE
        assert doc["token_endpoint"] == f"{ISSUER}/oauth/token"
        assert doc["revocation_endpoint"] == f"{ISSUER}/oauth/revoke"
        assert doc["code_challenge_methods_supported"] == ["S256"]
        assert doc["client_id_metadata_document_supported"] is True
        assert doc["authorization_response_iss_parameter_supported"] is True
        assert doc["scopes_supported"] == [
            "projects:read", "projects:write", "context:read", "offline_access"]
        assert doc["grant_types_supported"] == ["authorization_code", "refresh_token"]
        assert doc["response_types_supported"] == ["code"]
        assert doc["token_endpoint_auth_methods_supported"] == ["none"]
        assert "registration_endpoint" not in doc, "dynamic registration is EA-2b"

    @pytest.mark.parametrize(
        "path",
        ["/.well-known/oauth-protected-resource",
         "/.well-known/oauth-protected-resource/mcp"],
    )
    async def test_the_protected_resource_document(self, path):
        async with _edge_client() as edge:
            r = await edge.get(path)
        assert r.status_code == 200
        doc = r.json()
        assert doc["resource"] == RESOURCE
        assert doc["authorization_servers"] == [ISSUER]
        assert doc["scopes_supported"] == ["projects:read", "projects:write", "context:read"]
        assert "offline_access" not in doc["scopes_supported"]

    def test_the_addresses_are_settings_with_the_spec_defaults(self):
        from acb_common.settings import Settings

        fields = Settings.model_fields
        assert fields["agent_issuer"].default == ISSUER
        assert fields["agent_resource"].default == RESOURCE
        assert fields["agent_authorize_url"].default == AUTHORIZE

    def test_the_documents_follow_the_settings(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "agent_issuer", "https://edge.test")
        monkeypatch.setattr(settings, "agent_resource", "https://edge.test/mcp")
        assert edge_metadata.authorization_server_metadata()["issuer"] == "https://edge.test"
        assert edge_metadata.protected_resource_metadata()["authorization_servers"] == [
            "https://edge.test"]


class TestTheFlag:
    """With the flag OFF every new route answers 404 and does nothing else."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [("GET", "/.well-known/oauth-authorization-server"),
         ("GET", "/.well-known/oauth-protected-resource"),
         ("GET", "/.well-known/oauth-protected-resource/mcp"),
         ("POST", "/oauth/token"),
         ("POST", "/oauth/revoke")],
    )
    async def test_every_edge_route_is_404(self, monkeypatch, method, path):
        monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
        touched = []

        async def _spy(*a, **k):
            touched.append(a)

        for name in ("get_client", "consume_code", "rotate_refresh", "lookup_token"):
            monkeypatch.setattr(agent_tokens, name, _spy)
        async with _edge_client() as edge:
            r = await edge.request(
                method, path, data={"grant_type": "authorization_code",
                                    "client_id": CLIENT, "token": "x"}
                if method == "POST" else None)
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}
        assert not touched, "a route did work with the flag OFF"

    @pytest.mark.parametrize("path", ["/agent-access/authorize/check",
                                      "/agent-access/authorize/approve"])
    async def test_every_gateway_route_is_404(self, monkeypatch, path):
        monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
        touched = []

        async def _spy(*a, **k):
            touched.append(a)

        for name in ("get_client", "create_connection", "mint_code"):
            monkeypatch.setattr(agent_tokens, name, _spy)
        r = await _gw(_gateway_app(str(uuid.uuid4())), path, _authorize())
        assert r.status_code == 404
        assert not touched, "a route did work with the flag OFF"

    def test_the_flag_defaults_off(self):
        from acb_common.settings import Settings

        assert Settings.model_fields["agent_access_enabled"].default is False


class TestTheEdgeRefusesMalformedInput:
    """Every refusal here happens before the store is touched."""

    @pytest.fixture(autouse=True)
    def _no_store(self, monkeypatch):
        async def _boom(*a, **k):
            raise AssertionError("the store was reached")

        for name in ("get_client", "consume_code", "rotate_refresh", "lookup_token",
                     "issue_tokens", "revoke_connection"):
            monkeypatch.setattr(agent_tokens, name, _boom)

    @pytest.mark.parametrize("path", ["/oauth/token", "/oauth/revoke"])
    async def test_a_parameter_in_the_query_string_is_refused(self, path):
        async with _edge_client() as edge:
            r = await edge.post(
                f"{path}?code=abc", data={"grant_type": "authorization_code",
                                          "client_id": CLIENT, "token": "t"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_request"
        assert r.headers["cache-control"] == "no-store"

    async def test_an_access_token_in_the_url_is_refused_on_any_route(self):
        async with _edge_client() as edge:
            r = await edge.get("/.well-known/oauth-authorization-server?access_token=x")
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_request"

    async def test_json_is_refused(self):
        async with _edge_client() as edge:
            r = await edge.post("/oauth/token", json={"grant_type": "authorization_code"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_request"

    async def test_a_body_over_the_cap_is_413(self):
        async with _edge_client() as edge:
            r = await edge.post(
                "/oauth/token", content=b"grant_type=" + b"x" * 9000,
                headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 413

    async def test_a_parameter_sent_twice_is_refused(self):
        async with _edge_client() as edge:
            r = await edge.post(
                "/oauth/token",
                content=b"grant_type=refresh_token&grant_type=authorization_code",
                headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_request"

    async def test_an_unknown_grant_type(self):
        async with _edge_client() as edge:
            r = await edge.post(
                "/oauth/token", data={"grant_type": "password", "client_id": CLIENT})
        assert r.status_code == 400
        assert r.json()["error"] == "unsupported_grant_type"

    async def test_no_client_id_is_invalid_client(self):
        async with _edge_client() as edge:
            r = await edge.post("/oauth/token", data={"grant_type": "authorization_code"})
            rv = await edge.post("/oauth/revoke", data={"token": "x"})
        assert r.status_code == 401 and r.json()["error"] == "invalid_client"
        assert rv.status_code == 401 and rv.json()["error"] == "invalid_client"

    async def test_client_authentication_is_refused(self):
        async with _edge_client() as edge:
            r = await edge.post(
                "/oauth/token", data={"grant_type": "authorization_code", "client_id": CLIENT},
                headers={"authorization": "Basic eDp5"})
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_client"


class TestTheSharedRules:
    def test_pkce_is_s256(self):
        assert agent_oauth.pkce_matches(VERIFIER, CHALLENGE)
        assert not agent_oauth.pkce_matches(VERIFIER + "x", CHALLENGE)
        # A plain challenge (the verifier itself) never matches.
        assert not agent_oauth.pkce_matches(VERIFIER, VERIFIER)
        assert not agent_oauth.pkce_matches("short", agent_oauth.s256("short"))

    @pytest.mark.parametrize("client_id", [
        "http://client.example/m.json",
        "https://client.example",
        "https://client.example/",
        "https://client.example/a/../m.json",
        "https://user:pw@client.example/m.json",
        "https://client.example/m.json?x=1",
        "https://client.example/m.json#f",
        "https://client.example\\@evil.example/m.json",
        "client.example/m.json",
    ])
    def test_a_bad_cimd_client_id(self, client_id):
        assert not agent_oauth.is_cimd_client_id(client_id)

    def test_a_good_cimd_client_id(self):
        assert agent_oauth.is_cimd_client_id(CLIENT)

    @pytest.mark.parametrize("uri", [
        "javascript:alert(1)", "data:text/html,x", "http://evil.example/cb",
        "https://client.example/cb#frag", "https://u:p@client.example/cb",
        "file:///etc/passwd", "vbscript:x",
    ])
    def test_a_redirect_that_is_refused(self, uri):
        assert not agent_oauth.is_allowed_redirect_uri(uri)

    @pytest.mark.parametrize("uri", [
        REDIRECT, "http://localhost:9/cb", "http://[::1]:9/cb",
        "https://claude.ai/api/mcp/auth_callback",
        "cursor://anysphere.cursor-retrieval/oauth/callback",
    ])
    def test_a_redirect_that_is_allowed(self, uri):
        assert agent_oauth.is_allowed_redirect_uri(uri)

    def test_scopes_are_filtered_to_the_supported_ones(self):
        assert agent_oauth.parse_scope("offline_access email projects:read") == (
            "projects:read", "offline_access")
        with pytest.raises(ValueError):
            agent_oauth.parse_scope('projects:read "x"')

    def test_the_redirect_keeps_the_client_query(self):
        url = agent_oauth.authorization_redirect(
            "https://c.example/cb?a=1", {"code": "C", "state": None, "iss": ISSUER})
        assert _query(url) == {"a": "1", "code": "C", "iss": ISSUER}


class TestTheCimdFetchGoesThroughTheGuard:
    """A private address, a body over the cap and a slow host are refused."""

    @pytest.mark.parametrize("ip", ["10.9.8.7", "127.0.0.1", "169.254.169.254", "::1"])
    async def test_a_private_address_is_refused_before_any_request(self, web, monkeypatch, ip):
        monkeypatch.setattr(outbound_guard, "_resolve", lambda host, port: [ip])
        with pytest.raises(agent_access.ClientRefused) as caught:
            await agent_access._fetch_document(CLIENT)
        assert caught.value.reason in ("not public", "local")
        assert web.sent == [], "a request left for a private address"

    async def test_a_body_over_the_cap_is_refused(self, web):
        web.docs[CLIENT] = (200, json.dumps(_document(client_name="x" * 6000)).encode())
        with pytest.raises(agent_access.ClientRefused) as caught:
            await agent_access._fetch_document(CLIENT)
        assert caught.value.reason == "too large"

    async def test_the_cap_stays_under_the_guards_own_cap(self, monkeypatch):
        """The guard cuts a body at its cap and says nothing. A cut body must
        still read as too large, so our cap is always smaller."""
        monkeypatch.setattr(get_settings(), "agent_cimd_max_bytes", 10**9)
        assert agent_access._cap() < outbound_guard.MAX_ANSWER_BYTES

    async def test_a_slow_host_is_refused(self, web, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_cimd_timeout_s", 0.2)
        web.delay = 5.0
        with pytest.raises(agent_access.ClientRefused) as caught:
            await asyncio.wait_for(agent_access._fetch_document(CLIENT), 3)
        assert caught.value.reason == "timeout"

    async def test_a_status_other_than_200_is_refused(self, web):
        web.docs[CLIENT] = (500, b"{}")
        with pytest.raises(agent_access.ClientRefused):
            await agent_access._fetch_document(CLIENT)

    async def test_a_good_document_is_fetched(self, web):
        doc = await agent_access._fetch_document(CLIENT)
        assert doc["client_id"] == CLIENT
        assert web.sent == [CLIENT]

    @pytest.mark.parametrize(("over", "reason"), [
        ({"client_id": "https://evil.example/m.json"}, "client_id does not match the URL"),
        ({"client_secret": "s"}, "a metadata document holds no secret"),
        ({"token_endpoint_auth_method": "client_secret_basic"},
         "token_endpoint_auth_method must be none"),
        ({"redirect_uris": []}, "redirect_uris must list 1 to 20 URIs"),
        ({"redirect_uris": ["javascript:alert(1)"]}, "no redirect URI is allowed"),
        ({"response_types": ["token"]}, "response_types must include code"),
    ])
    def test_the_document_checks(self, over, reason):
        with pytest.raises(agent_access.ClientRefused) as caught:
            agent_access._validated_metadata(CLIENT, _document(**over))
        assert caught.value.reason == reason


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


#: Input names that would carry a tenant or a member. No module of either
#: half may read one: not as a form key, a header, a query key or a field.
_IDENTITY_INPUTS = frozenset({
    "organization_id", "org_id", "organization", "org", "tenant", "tenant_id",
    "member", "member_email", "user_email", "email", "user", "user_id",
    "x-user-email", "x-user-role", "x-organization-id", "x-tenant-id", "x-org-id",
})


def _edge_files() -> list[Path]:
    files = sorted(_EDGE_DIR.glob("*.py"))
    assert files, "the edge package moved"
    return files


class TestTheStructuralFences:
    """R7: the rules of the two halves, read from the source."""

    def test_the_edge_imports_neither_the_gateway_nor_the_guard(self):
        for path in _edge_files():
            for node in ast.walk(_module_tree(path)):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                for name in names:
                    assert not name.startswith("gateway"), f"{path.name} imports {name}"
                    assert "outbound_guard" not in name, f"{path.name} imports {name}"

    @pytest.mark.parametrize("path", [*sorted(_EDGE_DIR.glob("*.py")), _ACCESS_FILE],
                             ids=lambda p: p.name)
    def test_no_module_reads_a_tenant_or_a_member_from_input(self, path):
        """A string that names an identity input appears nowhere but a docstring."""
        tree = _module_tree(path)
        docs = _docstring_ids(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docs):
                assert node.value.strip().lower() not in _IDENTITY_INPUTS, (
                    f"{path.name}:{node.lineno} names {node.value!r}")

    @pytest.mark.parametrize("path", [*sorted(_EDGE_DIR.glob("*.py")), _ACCESS_FILE],
                             ids=lambda p: p.name)
    def test_no_module_binds_a_tenant_itself(self, path):
        for node in ast.walk(_module_tree(path)):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                assert not name.endswith(("bind_tenant", "tenant_session")), (
                    f"{path.name}:{node.lineno} calls {name}")

    def test_the_edge_never_creates_a_connection(self):
        for path in _edge_files():
            for node in ast.walk(_module_tree(path)):
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    assert not name.endswith(("create_connection", "mint_code")), (
                        f"{path.name}:{node.lineno} calls {name}")

    def test_the_connection_takes_the_member_from_the_session(self):
        calls = [
            node for node in ast.walk(_module_tree(_ACCESS_FILE))
            if isinstance(node, ast.Call)
            and ast.unparse(node.func).endswith("create_connection")
        ]
        assert len(calls) == 1
        assert ast.unparse(calls[0].args[0]) == "user.email"
        assert not {k.arg for k in calls[0].keywords} & {"organization_id", "org", "member_email"}

    def test_the_request_models_carry_no_identity_and_forbid_extras(self):
        for model in (agent_access.AuthorizeRequest, agent_access.ApproveRequest):
            assert model.model_config.get("extra") == "forbid", model.__name__
            assert not set(model.model_fields) & _IDENTITY_INPUTS, model.__name__

    def test_the_gateway_mounts_the_router_behind_default_deny(self):
        import gateway.main as main

        from tests.unit._routes import served_routes
        from tests.unit.test_default_deny_auth import _is_guarded

        routes = {
            r.path: r for r in served_routes(main.app.routes)
            if getattr(r, "path", "").startswith("/agent-access/")
        }
        assert set(routes) == {"/agent-access/authorize/check",
                               "/agent-access/authorize/approve"}, (
            "the router did not mount — main.py swallows an import error")
        for path, route in routes.items():
            assert _is_guarded(route), path
            assert path not in main.PUBLIC_ROUTES


# ═══════════════════════════════════════════════════════════════════════════
# R8: the store on a phase-4-promoted catalog, as a non-privileged role
# ═══════════════════════════════════════════════════════════════════════════


def _exec_sql_file(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _drop_database(admin_url, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


@pytest.fixture(scope="module")
def promoted():
    """A dedicated, fully phase-4-promoted catalog and a non-privileged role."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_agentoauth"
    _drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))

    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn:
            _exec_sql_file(conn, (_GENERATED / phase).read_text(encoding="utf-8"))

    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_APP_ROLE}"))

    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    with eng.begin() as conn:
        for oid, slug in ((org_a, "agentoauth-a"), (org_b, "agentoauth-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :slug, :name)"),
                {"id": oid, "slug": slug, "name": slug})

    class _NS:
        pass

    ns = _NS()
    ns.admin_engine = eng
    ns.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    ns.org_a = org_a
    ns.org_b = org_b
    yield ns

    eng.dispose()
    _drop_database(admin_url, dedicated)


def _admin_one(promoted, sql: str, **params):
    with promoted.admin_engine.connect() as c:
        return c.execute(text(sql), params).one()


def _connection(promoted, connection_id: str):
    return _admin_one(
        promoted,
        "SELECT organization_id::text AS org, member_email, scopes, "
        "       pinned_projects::text[] AS pins, include_personal, "
        "       revoked_at IS NOT NULL AS revoked, client_origin, client_name "
        "  FROM agent_connection WHERE id = :i", i=connection_id)


def _connection_count(promoted) -> int:
    return _admin_one(promoted, "SELECT count(*) FROM agent_connection")[0]


async def _approve(promoted, session_org: str | None = None, *, session_email: str = MEMBER,
                   **over) -> httpx.Response:
    """Approve as the SESSION's member and org. ``over`` is the request body."""
    app = _gateway_app(session_org or promoted.org_a, session_email)
    return await _gw(app, "/agent-access/authorize/approve", _authorize(**over))


async def _code(promoted, **over) -> str:
    r = await _approve(promoted, **over)
    assert r.status_code == 200, r.text
    return _query(r.json()["redirect_to"])["code"]


def _exchange(code: str, **over) -> dict:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "code_verifier": VERIFIER,
        "client_id": CLIENT,
        "resource": RESOURCE,
    }
    form.update(over)
    return {k: v for k, v in form.items() if v is not None}


async def _token(form: dict) -> httpx.Response:
    async with _edge_client() as edge:
        return await edge.post("/oauth/token", data=form)


async def _connection_of(access_token: str) -> str | None:
    found = await agent_tokens.lookup_token(access_token)
    return found.connection_id if found else None


def _connection_id_of_code(promoted, code: str) -> str:
    return str(_admin_one(
        promoted, "SELECT connection_id FROM agent_token WHERE token_hash = :h",
        h=agent_tokens.hash_token(code))[0])


_INVALID_GRANT = {"error": "invalid_grant", "error_description": "the grant is not valid"}


@_DB_GATE
class TestTheConsentHalf:
    """``/agent-access`` — validate, approve, and the R11 rule."""

    async def test_check_returns_what_the_consent_screen_needs(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _gw(_gateway_app(promoted.org_a),
                          "/agent-access/authorize/check", _authorize())
        assert r.status_code == 200, r.text
        view = r.json()
        assert view["client"] == {
            "client_id": CLIENT, "name": "Test Agent", "origin": "https://client.example"}
        assert view["redirect_host"] == "127.0.0.1"
        assert view["scopes"] == ["projects:read", "offline_access"]
        assert view["resource"] == RESOURCE
        assert view["issuer"] == ISSUER
        deny = _query(view["deny_redirect_to"])
        assert deny == {"error": "access_denied", "state": "st-123", "iss": ISSUER}

    @pytest.mark.parametrize("over", [
        {"code_challenge": None, "code_challenge_method": None},
        {"code_challenge_method": None},
        {"code_challenge_method": "plain"},
        {"code_challenge": VERIFIER, "code_challenge_method": "plain"},
        {"code_challenge": "short"},
    ], ids=["no-pkce", "no-method", "plain", "plain-verifier", "bad-challenge"])
    async def test_an_authorize_request_without_s256_is_refused(self, promoted, web, over):
        async with tenant_engine_scope(promoted.app_dsn):
            before = _connection_count(promoted)
            check = await _gw(_gateway_app(promoted.org_a),
                              "/agent-access/authorize/check", _authorize(**over))
            approve = await _approve(promoted, **over)
        for r in (check, approve):
            assert r.status_code == 400, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "invalid_request"
            assert _query(detail["redirect_to"])["error"] == "invalid_request"
            assert _query(detail["redirect_to"])["iss"] == ISSUER
        assert _connection_count(promoted) == before, "a refused request made a connection"

    @pytest.mark.parametrize("uri", [
        REDIRECT + "/", REDIRECT + "?x=1", "http://127.0.0.1:33419/callback",
        "HTTP://127.0.0.1:33418/callback",
    ])
    async def test_the_redirect_uri_must_match_exactly(self, promoted, web, uri):
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _approve(promoted, redirect_uri=uri)
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error"] == "invalid_request"
        assert detail["redirect_to"] is None, "an unverified redirect was offered"

    @pytest.mark.parametrize("resource", [None, "https://other.example/mcp", RESOURCE + "/"])
    async def test_a_wrong_resource_is_refused(self, promoted, web, resource):
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _approve(promoted, resource=resource)
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_target"

    async def test_an_unknown_scope_alone_is_refused(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _approve(promoted, scope="email admin:*")
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_scope"

    async def test_approve_redirects_with_code_state_and_iss(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _approve(promoted)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        redirect = r.json()["redirect_to"]
        assert redirect.startswith(REDIRECT + "?")
        params = _query(redirect)
        assert set(params) == {"code", "state", "iss"}
        assert params["state"] == "st-123"
        assert params["iss"] == ISSUER
        row = _connection(promoted, _connection_id_of_code(promoted, params["code"]))
        assert row.org == promoted.org_a
        assert row.member_email == MEMBER
        assert row.scopes == ["projects:read", "offline_access"]
        assert row.include_personal is False
        assert row.client_origin == "https://client.example"

    async def test_the_member_can_narrow_but_never_widen(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            narrow = await _approve(promoted, scopes=["projects:read"])
            widen = await _approve(promoted, scope="projects:read",
                                   scopes=["projects:read", "projects:write"])
        assert narrow.status_code == 200, narrow.text
        code = _query(narrow.json()["redirect_to"])["code"]
        assert _connection(promoted, _connection_id_of_code(promoted, code)).scopes == [
            "projects:read"]
        assert widen.status_code == 400
        assert widen.json()["detail"]["error"] == "invalid_scope"

    async def test_pins_and_the_personal_opt_in_are_stored(self, promoted, web):
        pin = str(uuid.uuid4())
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _approve(promoted, pinned_projects=[pin], include_personal=True)
        code = _query(r.json()["redirect_to"])["code"]
        row = _connection(promoted, _connection_id_of_code(promoted, code))
        assert row.pins == [pin]
        assert row.include_personal is True

    @pytest.mark.parametrize("smuggled", [
        {"organization_id": "ORG_B"},
        {"member_email": "victim@agentoauth.test"},
        {"org": "ORG_B"},
        {"tenant_id": "ORG_B"},
        {"email": "victim@agentoauth.test"},
    ])
    async def test_the_body_cannot_name_another_org_or_member(self, promoted, web, smuggled):
        """R11: the member is the session's and the org is the bound tenant."""
        body = {k: (promoted.org_b if v == "ORG_B" else v) for k, v in smuggled.items()}
        async with tenant_engine_scope(promoted.app_dsn):
            before = _connection_count(promoted)
            r = await _approve(promoted, **body)
        assert r.status_code == 422, r.text
        assert _connection_count(promoted) == before, "a smuggled body made a connection"

    async def test_the_connection_belongs_to_the_session_not_the_caller(self, promoted, web):
        """A member of org B gets a connection in org B, for themself only."""
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _approve(promoted, promoted.org_b, session_email="b@agentoauth.test")
        code = _query(r.json()["redirect_to"])["code"]
        row = _connection(promoted, _connection_id_of_code(promoted, code))
        assert (row.org, row.member_email) == (promoted.org_b, "b@agentoauth.test")

    @pytest.mark.parametrize("email", ["system:internal", None])
    async def test_a_caller_who_is_not_a_member_is_refused(self, promoted, web, email):
        async with tenant_engine_scope(promoted.app_dsn):
            app = _gateway_app(promoted.org_a, email)
            if email is None:
                # An anonymous caller is stopped by the default-deny guard.
                r = await _gw(app, "/agent-access/authorize/approve", _authorize())
                assert r.status_code == 401
                return
            r = await _gw(app, "/agent-access/authorize/approve", _authorize())
        assert r.status_code == 403

    async def test_no_bound_tenant_is_refused(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            before = _connection_count(promoted)
            r = await _gw(_gateway_app(None), "/agent-access/authorize/approve", _authorize())
        assert r.status_code == 403
        assert _connection_count(promoted) == before

    async def test_the_document_is_cached(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            await _gw(_gateway_app(promoted.org_a), "/agent-access/authorize/check",
                      _authorize(client_id=OTHER_CLIENT))
            await _gw(_gateway_app(promoted.org_a), "/agent-access/authorize/check",
                      _authorize(client_id=OTHER_CLIENT))
        assert web.sent.count(OTHER_CLIENT) <= 1, "the cache did not hold"
        via, live = _admin_one(
            promoted, "SELECT registered_via, expires_at > now() FROM agent_client "
                      "WHERE client_id = :c", c=OTHER_CLIENT)
        assert (via, live) == ("cimd", True)

    async def test_a_private_client_host_is_refused_through_the_route(self, promoted, web):
        client = "https://intranet.example/m.json"
        web.private.add("intranet.example")
        web.docs[client] = (200, json.dumps(_document(client)).encode())
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _approve(promoted, client_id=client)
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error"] == "invalid_client"
        assert detail["redirect_to"] is None
        assert client not in web.sent


@_DB_GATE
class TestTheTokenEndpoint:
    """``POST /oauth/token`` with the code and the refresh grant."""

    async def test_a_code_exchange_issues_tokens(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            r = await _token(_exchange(code))
            assert r.status_code == 200, r.text
            body = r.json()
            assert await _connection_of(body["access_token"]) == _connection_id_of_code(
                promoted, code)
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["pragma"] == "no-cache"
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 3600
        assert body["scope"] == "projects:read offline_access"
        assert body["refresh_token"]

    async def test_no_offline_access_means_no_refresh_token(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted, scope="projects:read")
            r = await _token(_exchange(code))
        assert r.status_code == 200, r.text
        assert "refresh_token" not in r.json()

    @pytest.mark.parametrize("over", [
        {"code_verifier": "w1-" + "b" * 50},
        {"resource": "https://other.example/mcp"},
        {"redirect_uri": REDIRECT + "/"},
        {"client_id": OTHER_CLIENT},
    ], ids=["wrong-verifier", "wrong-resource", "wrong-redirect", "wrong-client"])
    async def test_a_mismatch_is_invalid_grant_and_ends_the_connection(
        self, promoted, web, over
    ):
        async with tenant_engine_scope(promoted.app_dsn):
            # Make OTHER_CLIENT a known client, so the refusal is about the
            # grant and not about an unknown client.
            await _gw(_gateway_app(promoted.org_a), "/agent-access/authorize/check",
                      _authorize(client_id=OTHER_CLIENT))
            code = await _code(promoted)
            bad = await _token(_exchange(code, **over))
            # The code is spent: the right request now fails too.
            good = await _token(_exchange(code))
        assert bad.status_code == 400
        assert bad.json() == _INVALID_GRANT
        assert good.json() == _INVALID_GRANT
        assert _connection(promoted, _connection_id_of_code(promoted, code)).revoked

    async def test_a_code_used_twice_is_refused_and_revokes_its_tokens(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            first = await _token(_exchange(code))
            assert first.status_code == 200, first.text
            second = await _token(_exchange(code))
            access_after = await agent_tokens.lookup_token(first.json()["access_token"])
            refresh_after = await agent_tokens.lookup_token(
                first.json()["refresh_token"], kind="refresh")
        assert second.status_code == 400
        assert second.json() == _INVALID_GRANT
        assert access_after is None, "a replayed code left its access token live"
        assert refresh_after is None, "a replayed code left its refresh token live"
        assert _connection(promoted, _connection_id_of_code(promoted, code)).revoked

    async def test_a_code_in_the_query_string_is_refused_and_not_spent(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            async with _edge_client() as edge:
                r = await edge.post(f"/oauth/token?code={code}", data=_exchange(code))
            after = await _token(_exchange(code))
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_request"
        assert after.status_code == 200, "the refused request spent the code"

    async def test_an_unknown_client_is_invalid_client(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            r = await _token(_exchange(code, client_id="https://never.example/m.json"))
            after = await _token(_exchange(code))
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_client"
        assert after.status_code == 200, "an unknown client spent the code"

    async def test_an_unknown_code_is_invalid_grant(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            await _code(promoted)  # makes CLIENT known
            r = await _token(_exchange(agent_tokens.new_token()))
        assert r.json() == _INVALID_GRANT

    async def test_issue_tokens_runs_once_per_spent_code(self, promoted, web, monkeypatch):
        calls: list[str] = []
        real = agent_tokens.issue_tokens

        async def _counting(grant, **kw):
            calls.append(grant.connection_id)
            return await real(grant, **kw)

        monkeypatch.setattr(agent_tokens, "issue_tokens", _counting)
        async with tenant_engine_scope(promoted.app_dsn):
            good = await _code(promoted)
            await _token(_exchange(good))
            await _token(_exchange(good))  # a replay
            bad = await _code(promoted)
            await _token(_exchange(bad, code_verifier="w1-" + "c" * 50))
        assert len(calls) == 1

    async def test_a_refresh_rotates_and_may_omit_resource(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            issued = (await _token(_exchange(code))).json()
            r = await _token({"grant_type": "refresh_token", "client_id": CLIENT,
                              "refresh_token": issued["refresh_token"]})
            assert r.status_code == 200, r.text
            rotated = r.json()
            again = await _token({"grant_type": "refresh_token", "client_id": CLIENT,
                                  "refresh_token": rotated["refresh_token"],
                                  "resource": RESOURCE})
        assert rotated["refresh_token"] != issued["refresh_token"]
        assert again.status_code == 200, again.text

    async def test_a_reused_refresh_token_is_invalid_grant(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            issued = (await _token(_exchange(code))).json()
            form = {"grant_type": "refresh_token", "client_id": CLIENT,
                    "refresh_token": issued["refresh_token"]}
            assert (await _token(form)).status_code == 200
            reused = await _token(form)
        assert reused.json() == _INVALID_GRANT
        assert _connection(promoted, _connection_id_of_code(promoted, code)).revoked

    async def test_a_refresh_for_a_wider_scope_spends_nothing(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            issued = (await _token(_exchange(code))).json()
            wide = await _token({"grant_type": "refresh_token", "client_id": CLIENT,
                                 "refresh_token": issued["refresh_token"],
                                 "scope": "projects:write"})
            after = await _token({"grant_type": "refresh_token", "client_id": CLIENT,
                                  "refresh_token": issued["refresh_token"]})
        assert wide.status_code == 400
        assert wide.json()["error"] == "invalid_scope"
        assert after.status_code == 200, "a refused refresh spent the token"


@_DB_GATE
class TestTheRevocationEndpoint:
    async def test_an_unknown_token_is_200(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn), _edge_client() as edge:
            r = await edge.post("/oauth/revoke", data={
                "token": agent_tokens.new_token(), "client_id": CLIENT})
        assert r.status_code == 200

    async def test_a_revoke_by_its_client_ends_the_connection(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            issued = (await _token(_exchange(code))).json()
            async with _edge_client() as edge:
                r = await edge.post("/oauth/revoke", data={
                    "token": issued["refresh_token"], "client_id": CLIENT,
                    "token_type_hint": "refresh_token"})
            access = await agent_tokens.lookup_token(issued["access_token"])
        assert r.status_code == 200
        assert access is None
        assert _connection(promoted, _connection_id_of_code(promoted, code)).revoked

    async def test_another_client_cannot_revoke(self, promoted, web):
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            issued = (await _token(_exchange(code))).json()
            async with _edge_client() as edge:
                r = await edge.post("/oauth/revoke", data={
                    "token": issued["access_token"], "client_id": OTHER_CLIENT})
            access = await agent_tokens.lookup_token(issued["access_token"])
        assert r.status_code == 200
        assert access is not None, "another client revoked the connection"
