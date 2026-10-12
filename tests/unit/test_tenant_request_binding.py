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

#: The functions that make up branch 0, by name in ``acb_auth.deps``. The scan
#: reads each one, and any function that receives the request must be here.
_BRANCH_ZERO = (
    "get_current_user", "_is_agent_request", "_one_header", "_agent_user",
    "_verify_signed_request", "_matched_template", "_effective_template",
)

#: The only attribute chains a scanned function may take from the request (or
#: from a name assigned from it). ``()`` is the request itself.
_ALLOWED_CHAINS = frozenset({
    (), ("method",), ("method", "upper"),
    ("headers",), ("headers", "get"), ("headers", "getlist"),
    ("scope",), ("scope", "get"),
})
#: ``route`` and ``app`` find the matched route template. ``path`` and
#: ``query_string`` are the signed path and query (§10.1).
_SCOPE_KEYS = frozenset({"route", "app", "path", "query_string"})
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
#: A function outside branch 0 that may receive the request's SCOPE, with the
#: reason. Nothing outside branch 0 may receive the request or its headers.
_SCOPE_HANDOFFS = {
    "matches": (
        "Starlette's route matcher, in `_effective_template`. It answers which "
        "route FastAPI matched, from the scope's type, path and method."
    ),
}


def _sources() -> dict[str, str]:
    import inspect
    import textwrap

    return {
        name: textwrap.dedent(inspect.getsource(getattr(deps, name)))
        for name in _BRANCH_ZERO
    }


def _docstring_ids(tree) -> set[int]:
    import ast

    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _fold(node) -> str | None:
    """The text a constant, a ``+`` of constants, an f-string of constants or
    an ``agent_signing.HEADER_*`` name spells. None when it is computed."""
    import ast

    from acb_auth import agent_signing

    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            return node.value.decode("latin-1")
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left), _fold(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts = [_fold(v) for v in node.values]
        return None if any(p is None for p in parts) else "".join(parts)  # type: ignore[arg-type]
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "agent_signing"):
        value = getattr(agent_signing, node.attr, None)
        return value if isinstance(value, str) else None
    return None


def _chain(node, aliases: dict[str, tuple[str, ...]]) -> tuple[str, ...] | None:
    """The attribute chain of *node* from the request, or None."""
    import ast

    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _chain(node.value, aliases)
        return None if base is None else (*base, node.attr)
    return None


def _callee(call) -> str:
    return getattr(call.func, "id", None) or getattr(call.func, "attr", "") or ""


class _FunctionScan:
    """The checks of one function. Each finding names the function and line."""

    def __init__(self, name: str, source: str, sources: dict[str, str]) -> None:
        import ast

        self.name = name
        self.sources = sources
        self.tree = ast.parse(source)
        self.docstrings = _docstring_ids(self.tree)
        self.parents = {
            child: parent
            for parent in ast.walk(self.tree)
            for child in ast.iter_child_nodes(parent)
        }
        self.aliases: dict[str, tuple[str, ...]] = {"request": ()}
        for node in ast.walk(self.tree):  # `h = request.headers` and the like
            if isinstance(node, ast.Assign):
                chain = _chain(node.value, self.aliases)
                if chain is not None:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.aliases[target.id] = chain
        self.problems: list[str] = []

    def flag(self, node, what: str) -> None:
        self.problems.append(f"{self.name}:{getattr(node, 'lineno', '?')} {what}")

    def run(self) -> list[str]:
        import ast

        for node in ast.walk(self.tree):
            self.strings(node)
            if isinstance(node, ast.Attribute):
                self.attribute(node)
            if isinstance(node, ast.Call):
                self.call(node)
            if isinstance(node, ast.Subscript):
                self.subscript(node)
            if isinstance(node, ast.Compare):
                self.membership(node)
        return self.problems

    def strings(self, node) -> None:
        """No string or bytes literal, and no ``+`` or f-string of them, names
        a host-derived input."""
        import ast

        if isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr)):
            if id(node) in self.docstrings:
                return
            text = _fold(node)
            if text is not None and text.strip().lower() in _HOST_NAMES:
                self.flag(node, f"names {text!r}")

    def attribute(self, node) -> None:
        """Every attribute chain from the request ends inside the allowlist."""
        import ast

        parent = self.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            return  # only the outermost attribute of a chain is judged
        chain = _chain(node, self.aliases)
        if chain is not None and chain not in _ALLOWED_CHAINS:
            self.flag(node, f"reads request.{'.'.join(chain)}")

    def key(self, node, allowed: frozenset[str], what: str, *, param_ok: bool) -> None:
        import ast

        text = _fold(node)
        if text is None:
            fn = self.tree.body[0]
            params = {a.arg for a in (*fn.args.args, *fn.args.kwonlyargs)}
            if param_ok and isinstance(node, ast.Name) and node.id in params - {"request"}:
                return  # `_one_header(request, name)`: its call sites are checked
            self.flag(node, f"reads a computed {what}")
        elif text.lower() not in allowed:
            self.flag(node, f"reads {what} {text!r}")

    def call(self, node) -> None:
        chain = _chain(node.func, self.aliases)
        if chain in (("headers", "get"), ("headers", "getlist")) and node.args:
            self.key(node.args[0], _HEADERS, "header",
                     param_ok=self.name == "_one_header")
        if chain == ("scope", "get") and node.args:
            self.key(node.args[0], _SCOPE_KEYS, "scope key", param_ok=False)
        if _callee(node) == "_one_header":
            if len(node.args) > 1:
                self.key(node.args[1], _HEADERS, "header", param_ok=False)
            else:
                self.flag(node, "calls _one_header with no header name")
        values = [*node.args, *(k.value for k in node.keywords)]
        for value in values:
            handed = _chain(value, self.aliases)
            if handed is None:
                continue
            callee = _callee(node)
            if handed == ():
                if callee not in self.sources:
                    self.flag(node, f"hands the request to {callee or '?'}")
                elif "request" not in self.sources[callee].split("(", 1)[1].split(")", 1)[0]:
                    self.flag(node, f"{callee} takes the request under another name")
            elif handed == ("method",):
                continue  # the method is a plain string, and it is signed
            elif handed == ("scope",) and callee in _SCOPE_HANDOFFS:
                continue
            else:
                self.flag(node, f"hands request.{'.'.join(handed)} to {callee or '?'}")

    def subscript(self, node) -> None:
        chain = _chain(node.value, self.aliases)
        if chain == ("headers",):
            self.key(node.slice, _HEADERS, "header", param_ok=False)
        elif chain == ("scope",):
            self.key(node.slice, _SCOPE_KEYS, "scope key", param_ok=False)
        elif chain is not None:
            self.flag(node, f"subscripts request.{'.'.join(chain) or '(itself)'}")

    def membership(self, node) -> None:
        for comparator in node.comparators:
            chain = _chain(comparator, self.aliases)
            if chain == ("headers",):
                self.key(node.left, _HEADERS, "header", param_ok=False)
            elif chain == ("scope",):
                self.key(node.left, _SCOPE_KEYS, "scope key", param_ok=False)


def _scan(sources: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name, source in sources.items():
        problems += _FunctionScan(name, source, sources).run()
    return problems


def test_branch_zero_reads_no_host_derived_input():
    """A static scan of the functions in ``_BRANCH_ZERO``. What it proves:

    * no string or bytes literal, and no ``+`` or f-string of literals, in
      those functions names a host, a forwarded header, an origin, a referer,
      ``server`` or ``client`` (docstrings aside);
    * every attribute chain from the request, or from a name assigned from it,
      is one of ``_ALLOWED_CHAINS``: the method, the headers, the scope, and
      ``get``/``getlist`` on them. So no ``request.url``, ``base_url``,
      ``client``, ``headers.raw`` or ``headers.items()``;
    * every header read names one of ``_HEADERS``, and every scope read one of
      ``_SCOPE_KEYS``, as a literal. A computed name is refused, except the
      parameter of ``_one_header``, whose call sites must pass a literal;
    * the request, as a positional or a keyword argument, goes only to a
      function in ``_BRANCH_ZERO`` that takes it as ``request``, so the scan
      follows it. Its scope goes only to ``_SCOPE_HANDOFFS``, and its headers
      nowhere.

    What it does not prove: code that reaches the request through a dynamic
    path the AST cannot see, such as ``eval``. A review must still read a
    change to these functions.

    Mutations, measured 2026-10-12: :func:`test_the_scan_turns_red_for_each_bypass`.
    """
    assert _scan(_sources()) == []


def test_every_scanned_function_exists_and_reads_the_request():
    """The scan is not vacuous: each name is a real function that reads the
    request."""
    import ast

    for name, source in _sources().items():
        tree = ast.parse(source)
        assert any(isinstance(n, ast.Name) and n.id == "request"
                   for n in ast.walk(tree)), name


def test_branch_zero_never_calls_the_url_fallback():
    """``_route_template`` falls back to ``request.url``. Branch 0 calls
    ``_matched_template``, which has no fallback."""
    import inspect

    source = inspect.getsource(deps._agent_user)
    assert "_route_template(" not in source
    assert "_matched_template(request)" in source


#: One bypass each, inserted into ``_agent_user``. B3 and B4 also add the
#: helper they call, so the scan sees the helper too.
_HELPER_CLIENT = "def _peer(request):\n    return request.client\n"
_HELPER_SCOPE = "def _peer(scope):\n    return scope.get('client')\n"
_BYPASSES = {
    "B1 request.headers['host']": ("_h = request.headers['host']", None),
    "B2 request.url.hostname": ("_h = request.url.hostname", None),
    "B3 a helper that reads request.client": ("_h = _peer(request)", _HELPER_CLIENT),
    "B3b the same helper, not scanned": ("_h = _peer(request)", None),
    "B4 _peer(request.scope)": ("_h = _peer(request.scope)", _HELPER_SCOPE),
    "B5 getattr(request, 'url')": ("_h = getattr(request, 'url')", None),
    "B6 list(request.headers.raw)": ("_h = list(request.headers.raw)", None),
    "B6b request.headers.items()": ("_h = dict(request.headers.items())", None),
    "B7 request.scope['server']": ("_h = request.scope['server']", None),
    "B8 helper(req=request)": ("_h = _helper(req=request)", None),
    "B9 a bytes literal": ("_h = b'x-forwarded-host'", None),
    "B10 a built header name": ("_h = request.headers.get('x-forwarded-' + 'host')", None),
    "B11 an alias": ("_r = request\n    _h = _r.url", None),
    "B12 request.scope.get('client')": ("_h = request.scope.get('client')", None),
}


@pytest.mark.parametrize("label", sorted(_BYPASSES))
def test_the_scan_turns_red_for_each_bypass(label):
    """Each bypass goes into a COPY of the source, parsed with ``ast``. The real
    file never changes."""
    line, helper = _BYPASSES[label]
    sources = _sources()
    anchor = "    keys = agent_signing.usable_keys()\n"
    assert sources["_agent_user"].count(anchor) == 1
    sources["_agent_user"] = sources["_agent_user"].replace(
        anchor, f"    {line}\n{anchor}")
    if helper is not None:
        sources["_peer"] = helper
    problems = _scan(sources)
    assert any(_EXPECT[label] in p for p in problems), (label, problems)


#: The finding each bypass must produce, so a bypass cannot pass on a finding
#: about something else.
_EXPECT = {
    "B1 request.headers['host']": "reads header 'host'",
    "B2 request.url.hostname": "reads request.url.hostname",
    "B3 a helper that reads request.client": "_peer:2 reads request.client",
    "B3b the same helper, not scanned": "hands the request to _peer",
    "B4 _peer(request.scope)": "hands request.scope to _peer",
    "B5 getattr(request, 'url')": "hands the request to getattr",
    "B6 list(request.headers.raw)": "reads request.headers.raw",
    "B6b request.headers.items()": "reads request.headers.items",
    "B7 request.scope['server']": "reads scope key 'server'",
    "B8 helper(req=request)": "hands the request to _helper",
    "B9 a bytes literal": "names 'x-forwarded-host'",
    "B10 a built header name": "reads header 'x-forwarded-host'",
    "B11 an alias": "reads request.url",
    "B12 request.scope.get('client')": "reads scope key 'client'",
}


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
