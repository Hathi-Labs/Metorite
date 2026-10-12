"""MT-1c / H2 — the request path binds its tenant once, centrally.

`saas_multitenancy_handover.md` H2: *"Add `bind_tenant(user.organization_id)`
in the gateway middleware / the app-wide dependency that already resolves
`UserContext`, and release it after the response. Then the 561 sites need no
tenant argument at all."*

The claims pinned here are the ones a refactor could silently lose:

* resolving an authenticated identity BINDS the tenant contextvar — this is
  what lets every converted `tenant_session()` call site take no argument;
* the tenant comes from the `app_user` row (`resolve_identity`), NEVER from a
  header the caller controls (R11) — asserted by construction: the fake
  resolver ignores the asserted role header entirely;
* an identity with no organization binds NOTHING, so a converted handler fails
  closed with `TenantUnbound` instead of defaulting to "the usual tenant";
* the `system:internal` service identity binds nothing — jobs bind their own
  tenant explicitly (H4) or fail closed;
* the gateway middleware opens a fresh scope per request and releases it after
  the response, so a binding cannot leak from one request into the next even
  on a server that reuses one task for sequential requests.

Hermetic: `resolve_access` / `resolve_identity` are monkeypatched; no DB.
"""

from __future__ import annotations

import pytest
from acb_common.db import bind_tenant, clear_tenant, current_tenant, release_tenant

import acb_auth.deps as deps
from acb_auth.permissions import NO_ACCESS

ORG = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _fresh_scope():
    """Every test runs in its own empty tenant scope, released afterwards."""
    token = clear_tenant()
    yield
    release_tenant(token)


@pytest.fixture
def identity(monkeypatch: pytest.MonkeyPatch):
    """Resolve every email to (user-id, ORG) without a database."""
    async def fake_access(email, legacy_role=None, record_request=False):
        return NO_ACCESS

    async def fake_identity(email):
        return ("22222222-2222-2222-2222-222222222222", ORG)

    monkeypatch.setattr(deps, "resolve_access", fake_access)
    monkeypatch.setattr(deps, "resolve_identity", fake_identity)
    monkeypatch.setattr(deps, "_get_internal_token", lambda: "tok")


async def test_an_authenticated_request_binds_its_organization(identity):
    user = await deps.get_current_user(
        x_user_email="priya@fracktal.in",
        x_user_role="employee",
        authorization="Bearer tok",
    )
    assert user.organization_id == ORG
    assert current_tenant() == ORG


async def test_the_tenant_comes_from_identity_resolution_not_the_caller(identity):
    """R11 by construction: the resolver saw only the email; the asserted role
    header (the only other caller-controlled input) cannot steer the tenant."""
    await deps.get_current_user(
        x_user_email="priya@fracktal.in",
        x_user_role="executive",
        authorization="Bearer tok",
    )
    assert current_tenant() == ORG


async def test_an_unresolved_identity_binds_nothing(monkeypatch):
    async def fake_access(email, legacy_role=None, record_request=False):
        return NO_ACCESS

    async def fake_identity(email):
        return (None, None)

    monkeypatch.setattr(deps, "resolve_access", fake_access)
    monkeypatch.setattr(deps, "resolve_identity", fake_identity)
    monkeypatch.setattr(deps, "_get_internal_token", lambda: "tok")

    user = await deps.get_current_user(
        x_user_email="stranger@fracktal.in",
        x_user_role="employee",
        authorization="Bearer tok",
    )
    assert user.organization_id is None
    assert current_tenant() is None


async def test_the_service_identity_binds_nothing(identity):
    """`system:internal` has no organization. A job that needs one binds it
    explicitly from its own record (H4) — never inherits an ambient one."""
    user = await deps.get_current_user(authorization="Bearer tok")
    assert user.email == "system:internal"
    assert current_tenant() is None


async def test_the_middleware_scope_does_not_leak_across_requests(identity):
    """Two sequential requests in ONE task: the second starts empty even
    though the first bound a tenant and never explicitly unbound it."""
    from gateway.main import TenantScopeMiddleware

    seen: list[str | None] = []

    async def app(scope, receive, send):
        seen.append(current_tenant())
        bind_tenant(ORG)  # what _with_resolved_access does mid-request

    mw = TenantScopeMiddleware(app)
    http = {"type": "http"}
    await mw(http, None, None)
    await mw(http, None, None)
    assert seen == [None, None]
    assert current_tenant() is None


def test_a_workspace_host_header_cannot_steer_the_tenant(identity):
    """WS-29 **MT-1f slice 1, done-when 7** — the tenant binding is UNMOVED.

    MT-1f gives every tenant a hostname, which makes ``Host`` the most plausible
    place for a tenant claim to grow: it looks like infrastructure rather than
    input, and a header that says ``acme`` in front of a request that says
    ``globex`` is the one-line cross-tenant read §1.5's binding rule forbids by
    name. **A ``Host`` header is request input**, so R11 applies to it exactly as
    it applies to ``X-Organization-Id``: the acting tenant comes from the
    authenticated identity and from nowhere else.

    The proxy's subdomain check (`workbench/control_plane/src/proxy.ts`) decides
    which hostname a person should be LOOKING at; it never decides what they may
    see. This is the assertion that keeps those two apart.

    Driven through a REAL request rather than a direct call, because the direct
    call cannot express the hazard — the header has to actually be on the wire.
    Sync (not ``async def``) on purpose: ``TestClient`` runs its own loop and
    cannot be driven from inside one.

    Mutation, measured: give ``get_current_user`` an
    ``x_forwarded_host``/``Host`` parameter and bind from its label, and
    ``seen == [ORG]`` goes red with the host's org instead.
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from gateway.main import TenantScopeMiddleware

    seen: list[str | None] = []

    app = FastAPI()
    app.add_middleware(TenantScopeMiddleware)

    @app.get("/whoami")
    async def whoami(user=Depends(deps.get_current_user)):
        # Captured INSIDE the request: the middleware releases the scope on the
        # way out, so an assertion afterwards would see None either way.
        seen.append(current_tenant())
        return {"org": user.organization_id}

    with TestClient(app) as client:
        answer = client.get(
            "/whoami",
            headers={
                "Host": "other-org.metorite.com",
                "X-User-Email": "priya@fracktal.in",
                "X-User-Role": "employee",
                "Authorization": "Bearer tok",
            },
        )

    assert answer.status_code == 200
    # The identity's organization, not the hostname's.
    assert answer.json()["org"] == ORG
    assert seen == [ORG]


def test_the_dependency_takes_no_host_derived_input_at_all(identity):
    """The same claim by construction, so it cannot be satisfied by a lookup
    that happens to agree today.

    ``get_current_user`` is the ONE place a request binds its tenant (H2).

    ⚠️ **The signature widened on purpose (WS-55 EA-3a, 2026-10-12).** Until
    then this test pinned the parameters to three headers and banned a
    ``request`` parameter, so a host could not be read without widening the
    signature here first. Branch 0 of ``get_current_user``, the signed request
    of the MCP edge, needs the ``Request``: its method, its matched route, its
    path and query and four ``X-Agent-*`` headers (``external_agent_access.md``
    §10.1). So the parameter set now holds ``request`` as well, and the rule
    it carried, *no host-derived input*, moved into the source scan of
    :func:`test_branch_zero_reads_no_host_derived_input`. A parameter named for
    a host is still banned here.
    """
    import inspect

    params = set(inspect.signature(deps.get_current_user).parameters)
    assert params == {"x_user_email", "x_user_role", "authorization", "request"}
    for banned in ("host", "x_forwarded_host", "origin", "referer"):
        assert banned not in params


# ── WS-55 EA-3a: branch 0 reads no host input and takes no tenant from a header
#
# Spec ``external_agent_access.md`` §10.1. The rule of the test above, now that
# ``get_current_user`` holds the ``Request``: the request may be read only for
# what the signed string covers, and the tenant comes from the connection row.

#: The functions that make up branch 0. ``_route_template`` is the shared
#: default-deny helper. Branch 0 refuses a request with no matched route BEFORE
#: it calls it, so that helper's URL fallback cannot run from branch 0
#: (:func:`test_branch_zero_checks_the_route_before_the_template`).
_BRANCH_ZERO = ("get_current_user", "_is_agent_request", "_one_header", "_agent_user")

#: What branch 0 may read from the request, and nothing else.
_REQUEST_ATTRS = frozenset({"method", "headers", "scope"})
#: ``route`` is the matched route. ``path`` and ``query_string`` are the
#: signed path and query (§10.1). Never ``server``, ``client`` or ``headers``
#: by scope key.
_SCOPE_KEYS = frozenset({"route", "path", "query_string"})
#: The four signed headers, and the two that tell branch 0 to refuse a body.
_HEADERS = frozenset({
    "x-agent-connection", "x-agent-timestamp", "x-agent-signature", "x-actor-via",
    "content-length", "transfer-encoding",
})
#: A header or scope key that names where the request came from.
_HOST_NAMES = frozenset({
    "host", "x-forwarded-host", "x-forwarded-for", "x-forwarded-proto",
    "x-real-ip", "forwarded", "origin", "referer", "server", "client",
})


def _function_tree(name: str):
    import ast
    import inspect
    import textwrap

    return ast.parse(textwrap.dedent(inspect.getsource(getattr(deps, name))))


def _docstring_ids(tree) -> set[int]:
    import ast

    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _literal(node) -> str | None:
    """The string a header or key argument names, or None when it is computed."""
    import ast

    from acb_auth import agent_signing

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "agent_signing"):
        value = getattr(agent_signing, node.attr, None)
        return value if isinstance(value, str) else None
    return None


def _is_request(node, attr: str | None = None) -> bool:
    import ast

    if attr is None:
        return isinstance(node, ast.Name) and node.id == "request"
    return (isinstance(node, ast.Attribute) and node.attr == attr
            and _is_request(node.value))


def _key_is(key: str | None, allowed: frozenset[str]) -> bool:
    return key is not None and key.lower() in allowed


def _check_strings(name: str, node, docstrings: set[int]) -> list[str]:
    """(1) No string anywhere names a host-derived input."""
    import ast

    if (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings
            and node.value.strip().lower() in _HOST_NAMES):
        return [f"{name} names {node.value!r}"]
    return []


def _check_attributes(name: str, node, _docstrings: set[int]) -> list[str]:
    """(2) Only request.method, request.headers and request.scope."""
    import ast

    if (isinstance(node, ast.Attribute) and _is_request(node.value)
            and node.attr not in _REQUEST_ATTRS):
        return [f"{name} reads request.{node.attr}"]
    return []


def _check_method_calls(name: str, node, _docstrings: set[int]) -> list[str]:
    """(3) ``request.headers.get/getlist`` and ``request.scope.get`` name an
    allowed key. Only ``_one_header`` reads a computed header name: its own
    parameter, which (4) checks at every call site."""
    import ast

    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return []
    owner, method = node.func.value, node.func.attr
    key = _literal(node.args[0]) if node.args else None
    if _is_request(owner, "headers"):
        if method not in ("get", "getlist"):
            return [f"{name} calls request.headers.{method}"]
        if key is None and name == "_one_header":
            return []
        if not _key_is(key, _HEADERS):
            return [f"{name} reads header {key!r}"]
    if _is_request(owner, "scope") and key not in _SCOPE_KEYS:
        return [f"{name} reads scope {key!r}"]
    return []


def _check_one_header_calls(name: str, node, _docstrings: set[int]) -> list[str]:
    """(4) Every ``_one_header`` call names an allowed header."""
    import ast

    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_one_header"):
        key = _literal(node.args[1]) if len(node.args) > 1 else None
        if not _key_is(key, _HEADERS):
            return [f"{name} _one_header({key!r})"]
    return []


def _check_subscripts(name: str, node, _docstrings: set[int]) -> list[str]:
    """(5) A subscript of the headers or the scope names an allowed key."""
    import ast

    if not isinstance(node, ast.Subscript):
        return []
    key = _literal(node.slice)
    if _is_request(node.value, "headers") and not _key_is(key, _HEADERS):
        return [f"{name} request.headers[{key!r}]"]
    if _is_request(node.value, "scope") and key not in _SCOPE_KEYS:
        return [f"{name} request.scope[{key!r}]"]
    return []


def _check_membership(name: str, node, _docstrings: set[int]) -> list[str]:
    """(6) ``"x" in request.headers`` names an allowed header."""
    import ast

    if isinstance(node, ast.Compare) and any(
            _is_request(c, "headers") for c in node.comparators):
        key = _literal(node.left)
        if not _key_is(key, _HEADERS):
            return [f"{name} {key!r} in request.headers"]
    return []


def _check_handoffs(name: str, node, _docstrings: set[int]) -> list[str]:
    """(7) The request goes to no other function, except the route template
    and the branch-0 helpers themselves."""
    import ast

    if isinstance(node, ast.Call) and any(_is_request(a) for a in node.args):
        callee = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if callee not in {*_BRANCH_ZERO, "_route_template"}:
            return [f"{name} hands the request to {callee}"]
    return []


_CHECKS = (
    _check_strings, _check_attributes, _check_method_calls,
    _check_one_header_calls, _check_subscripts, _check_membership,
    _check_handoffs,
)


def _branch_zero_problems() -> list[str]:
    import ast

    problems: list[str] = []
    for name in _BRANCH_ZERO:
        tree = _function_tree(name)
        docstrings = _docstring_ids(tree)
        for node in ast.walk(tree):
            for check in _CHECKS:
                found = check(name, node, docstrings)
                problems += [f"{p} (line {getattr(node, 'lineno', '?')})" for p in found]
    return problems


def test_branch_zero_reads_no_host_derived_input():
    """The source of branch 0 reads no host, no forwarded header, no origin and
    no referer, and never ``request.url``, ``request.base_url`` or
    ``request.client``. It reads the method, the matched route, the signed path
    and query, the four signed headers and the two body headers. Nothing else.

    Mutation, measured 2026-10-12: put ``request.headers["host"]`` into
    ``_agent_user`` and this goes red.
    """
    assert _branch_zero_problems() == []


def test_the_scan_sees_what_it_scans():
    """The scan is not vacuous: each scanned function reads the request."""
    import ast

    seen = {
        name: any(_is_request(n) for n in ast.walk(_function_tree(name)))
        for name in _BRANCH_ZERO
    }
    assert all(seen.values()), seen


def test_branch_zero_checks_the_route_before_the_template():
    import inspect

    source = inspect.getsource(deps._agent_user)
    refusal = source.index('request.scope.get("route") is None')
    assert refusal < source.index("_route_template(request)")


def _signed_app(monkeypatch, *, row_org: str, member_org: str):
    """A one-route app behind the tenant middleware, branch 0 armed, and a
    connection row whose organization is ``row_org``."""
    from acb_auth import agent_tokens
    from acb_auth.agent_tokens import LiveConnection
    from acb_auth.permissions import build_access
    from acb_common import get_settings
    from fastapi import Depends, FastAPI
    from gateway.main import TenantScopeMiddleware

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    monkeypatch.setattr(settings, "agent_edge_signing_key", _AGENT_KEY)
    monkeypatch.setattr(settings, "agent_edge_signing_key_previous", "")

    async def read(org, conn):
        return LiveConnection(
            connection_id=conn, organization_id=row_org,
            member_email="priya@fracktal.in", client_origin="https://client.example",
            scopes=("projects:read",), pinned_projects=(), include_personal=True)

    async def access(email, legacy_role=None, record_request=False):
        return build_access(["feature:projects"], [], is_active=True)

    async def identity(email):
        return ("22222222-2222-2222-2222-222222222222", member_org)

    monkeypatch.setattr(agent_tokens, "read_live_connection", read)
    monkeypatch.setattr(deps, "resolve_access", access)
    monkeypatch.setattr(deps, "resolve_identity", identity)

    seen: list[str | None] = []
    app = FastAPI()
    app.add_middleware(TenantScopeMiddleware)

    @app.get("/projects/tasks/{task_id}")
    async def task(task_id: str, user=Depends(deps.get_current_user)):
        seen.append(current_tenant())
        return {"email": user.email, "org": user.organization_id}

    return app, seen


_AGENT_KEY = "tenant-binding-suite-signing-key-0123456789"
_OTHER_ORG = "33333333-3333-3333-3333-333333333333"
_CONN = "44444444-4444-4444-4444-444444444444"
_TASK = "/projects/tasks/55555555-5555-5555-5555-555555555555"


def _signed_headers(signed_org: str) -> dict[str, str]:
    from acb_auth import agent_signing

    return agent_signing.signed_headers(
        _AGENT_KEY, organization_id=signed_org, connection_id=_CONN,
        actor_via="mcp:client.example", method="GET", path=_TASK)


def test_branch_zero_binds_the_rows_organization_and_no_header_moves_it(monkeypatch):
    """Host, forwarded and organization headers beside a signed request do
    not move the tenant. It is the organization of the connection row, which
    the member's own resolve agrees with."""
    from fastapi.testclient import TestClient

    app, seen = _signed_app(monkeypatch, row_org=ORG, member_org=ORG)
    headers = {
        **_signed_headers(ORG),
        "Host": "other-org.metorite.com",
        "X-Forwarded-Host": "other-org.metorite.com",
        "X-Organization-Id": _OTHER_ORG,
        "X-Tenant-Id": _OTHER_ORG,
    }
    with TestClient(app) as client:
        answer = client.get(_TASK, headers=headers)
    assert answer.status_code == 200, answer.text
    assert answer.json()["org"] == ORG
    assert seen == [ORG]


def test_a_signed_organization_that_differs_from_the_row_is_refused(monkeypatch):
    """The row and the member agree on ``_OTHER_ORG``, and the request signs
    ``ORG``. The signed organization must EQUAL the row's, so this is the
    anonymous context, and no route runs."""
    from fastapi.testclient import TestClient

    app, seen = _signed_app(monkeypatch, row_org=_OTHER_ORG, member_org=_OTHER_ORG)
    with TestClient(app) as client:
        answer = client.get(_TASK, headers=_signed_headers(ORG))
    assert answer.status_code == 200
    assert answer.json() == {"email": None, "org": None}
    assert seen == [None], "a refused branch 0 binds no tenant"


async def test_a_non_http_scope_is_passed_through_untouched(identity):
    from gateway.main import TenantScopeMiddleware

    called: list[str] = []

    async def app(scope, receive, send):
        called.append(scope["type"])

    bind_tenant(ORG)
    await TenantScopeMiddleware(app)({"type": "lifespan"}, None, None)
    assert called == ["lifespan"]
    # a lifespan passthrough neither opens nor closes a scope
    assert current_tenant() == ORG
