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
    it carried, *no host-derived input*, moved into two layers: the inverted
    static scan of :func:`test_branch_zero_reads_no_host_derived_input`, and
    the runtime tripwire (``tests/unit/_branch_zero_tripwire.py``) that every
    branch-0 test in ``test_mcp_edge_signing.py`` runs under. A parameter
    named for a host is still banned here.
    """
    import inspect

    params = set(inspect.signature(deps.get_current_user).parameters)
    assert params == {"x_user_email", "x_user_role", "authorization", "request"}
    for banned in ("host", "x_forwarded_host", "origin", "referer"):
        assert banned not in params


# ── WS-55 EA-3a: branch 0 reads no host input and takes no tenant from a header
#
# Spec ``external_agent_access.md`` §10.1. ⚠️ Since the parameter test above
# was widened, THIS is the H2 guard for branch 0. Two layers:
#
# 1. A STATIC scan that INVERTS the rule. It flags every occurrence of the name
#    `request` in the branch-0 functions, unless the occurrence is one of a few
#    exact, allowed shapes. Anything the scan does not recognise is a finding.
# 2. A RUNTIME tripwire (`tests/unit/_branch_zero_tripwire.py`). Every branch-0
#    test in `test_mcp_edge_signing.py` runs a second time with the request
#    wrapped, so any host-derived read on an EXERCISED path raises.

#: The functions that make up branch 0, by name in ``acb_auth.deps``. The
#: request may be handed only to one of these, and the scan reads each one.
_BRANCH_ZERO = (
    "get_current_user", "_is_agent_request", "_one_header", "_agent_user",
    "_verify_signed_request", "_matched_template", "_effective_template",
)

#: The attribute chains a scan accepts from `request`, each with the ONE use
#: it may have (see :meth:`_InvertedScan.chain_use`).
_ALLOWED_CHAINS = frozenset({
    ("method",), ("method", "upper"),
    ("headers",), ("headers", "get"), ("headers", "getlist"),
    ("scope",), ("scope", "get"),
})
#: ``route`` and ``app`` find the matched route template. ``path`` and
#: ``query_string`` are the signed path and query (§10.1). The runtime
#: tripwire allows the same keys, and `test_mcp_edge_signing.py` pins that
#: the success path reads exactly these.
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

#: The one call outside branch 0 that may receive the request's scope:
#: ``<receiver>.matches(request.scope)`` in ``_effective_template``, where the
#: receiver is the loop variable over FastAPI's route contexts. Starlette's
#: ``Route.matches`` answers which route matched, from the scope's type, path
#: and method. Starlette's own router already read the same scope to send the
#: request here. It returns a match, never a value from the scope.
#: :func:`test_the_matches_receiver_is_derived_from_the_real_source` derives
#: the name from the real code and pins it here.
_MATCHES_RECEIVER = "context"


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
    """The text a str or bytes literal, a ``+`` of literals, an f-string of
    literals or an ``agent_signing.HEADER_*`` name spells. None when computed."""
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


def _params(fn) -> list[str]:
    return [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]


class _InvertedScan:
    """Every `request` must be an allowed shape. Anything else is a finding."""

    def __init__(self, name: str, source: str, sources: dict[str, str]) -> None:
        import ast

        self.name = name
        self.sources = sources
        self.tree = ast.parse(source)
        self.fn = self.tree.body[0]
        self.docstrings = _docstring_ids(self.tree)
        self.parents = {
            child: parent
            for parent in ast.walk(self.tree)
            for child in ast.iter_child_nodes(parent)
        }
        self.problems: list[str] = []

    def flag(self, node, what: str) -> None:
        self.problems.append(f"{self.name}:{getattr(node, 'lineno', '?')} {what}")

    def run(self) -> list[str]:
        import ast

        if not isinstance(self.fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.flag(self.fn, "is not one function")
            return self.problems
        for node in ast.walk(self.tree):
            self.strings(node)
            if isinstance(node, ast.Name) and node.id == "request":
                self.occurrence(node)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_one_header":
                key = node.args[1] if len(node.args) > 1 else None
                if key is None or (_fold(key) or "\0").lower() not in _HEADERS:
                    self.flag(node, "_one_header without an allowed literal header")
        return self.problems

    def strings(self, node) -> None:
        """No str or bytes literal, ``+`` or f-string of them names a host."""
        import ast

        if isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr)) and (
                id(node) not in self.docstrings):
            text = _fold(node)
            if text is not None and text.strip().lower() in _HOST_NAMES:
                self.flag(node, f"names {text!r}")

    def occurrence(self, node) -> None:
        import ast

        parent = self.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            self.chain(parent)
        elif isinstance(parent, ast.Call) and node in parent.args:
            self.positional(parent, parent.args.index(node), node)
        elif isinstance(parent, ast.keyword):
            self.keyword(parent, node)
        elif (isinstance(parent, ast.Compare) and parent.left is node
              and all(isinstance(op, (ast.Is, ast.IsNot)) for op in parent.ops)
              and all(isinstance(c, ast.Constant) and c.value is None
                      for c in parent.comparators)):
            return  # `request is not None`
        else:
            self.flag(node, f"uses request in {type(parent).__name__}")

    def _bound_param(self, callee: str, index: int | None, keyword: str | None) -> str | None:
        import ast

        tree = ast.parse(self.sources[callee])
        params = _params(tree.body[0])
        if keyword is not None:
            names = {*params, *(a.arg for a in tree.body[0].args.kwonlyargs)}
            return keyword if keyword in names else None
        return params[index] if index is not None and index < len(params) else None

    def positional(self, call, index: int, node) -> None:
        callee = getattr(call.func, "id", None)
        if callee not in self.sources:
            self.flag(node, f"hands the request to {ast_name(call.func)}")
        elif self._bound_param(callee, index, None) != "request":
            self.flag(node, f"hands the request to {callee} as another parameter")

    def keyword(self, kw, node) -> None:
        call = self.parents.get(kw)
        callee = getattr(getattr(call, "func", None), "id", None)
        if callee not in self.sources or kw.arg != "request":
            self.flag(node, f"hands the request as {kw.arg or '**'}= to "
                            f"{ast_name(getattr(call, 'func', None))}")
        elif self._bound_param(callee, None, "request") != "request":
            self.flag(node, f"{callee} has no parameter named request")

    def chain(self, first) -> None:
        import ast

        top, chain = first, [first.attr]
        while True:
            parent = self.parents.get(top)
            if isinstance(parent, ast.Attribute) and parent.value is top:
                top, chain = parent, [*chain, parent.attr]
                continue
            break
        key = tuple(chain)
        if key not in _ALLOWED_CHAINS:
            self.flag(top, f"reads request.{'.'.join(key)}")
            return
        if not isinstance(getattr(top, "ctx", None), ast.Load):
            self.flag(top, f"writes request.{'.'.join(key)}")
            return
        self.chain_use(top, key, self.parents.get(top))

    def chain_use(self, top, key, use) -> None:
        """The ONE use each allowed chain may have."""
        import ast

        if key == ("method",):
            return  # a plain string, and it is signed
        if key in (("method", "upper"), ("headers", "get"), ("headers", "getlist"),
                   ("scope", "get")):
            if not (isinstance(use, ast.Call) and use.func is top):
                self.flag(top, f"request.{'.'.join(key)} is not called")
            elif key != ("method", "upper"):
                self.key_of(use, key)
            return
        if key == ("headers",):
            if isinstance(use, ast.For) and use.iter is top and self.name == "_is_agent_request":
                return  # the trigger reads header NAMES
            if isinstance(use, ast.Compare) and top in use.comparators and all(
                    isinstance(op, (ast.In, ast.NotIn)) for op in use.ops):
                self.literal(use.left, _HEADERS, "header")
                return
            if isinstance(use, ast.Subscript) and use.value is top:
                self.literal(use.slice, _HEADERS, "header")
                return
        if key == ("scope",):
            if isinstance(use, ast.Subscript) and use.value is top:
                self.literal(use.slice, _SCOPE_KEYS, "scope key")
                return
            if (isinstance(use, ast.Call) and use.args == [top] and not use.keywords
                    and isinstance(use.func, ast.Attribute) and use.func.attr == "matches"
                    and isinstance(use.func.value, ast.Name)
                    and use.func.value.id == _MATCHES_RECEIVER
                    and self.name == "_effective_template"):
                return
        self.flag(top, f"request.{'.'.join(key)} in {type(use).__name__}")

    def key_of(self, call, key) -> None:
        import ast

        given = call.args[0] if call.args else None
        for kw in call.keywords:
            if kw.arg == "key":
                given = kw.value
            elif kw.arg is None:
                self.flag(call, "passes ** to a header or scope read")
        if given is None:
            self.flag(call, "reads a header or scope key it does not name")
            return
        if key[0] == "headers":
            if (self.name == "_one_header" and isinstance(given, ast.Name)
                    and given.id in set(_params(self.fn)) - {"request"}):
                return  # its call sites are checked in `run`
            self.literal(given, _HEADERS, "header")
        else:
            self.literal(given, _SCOPE_KEYS, "scope key")

    def literal(self, node, allowed: frozenset[str], what: str) -> None:
        text = _fold(node)
        if text is None:
            self.flag(node, f"reads a computed {what}")
        elif text.lower() not in allowed:
            self.flag(node, f"reads {what} {text!r}")


def ast_name(func) -> str:
    return getattr(func, "id", None) or getattr(func, "attr", None) or "?"


def _scan(sources: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name, source in sources.items():
        problems += _InvertedScan(name, source, sources).run()
    return problems


def test_branch_zero_reads_no_host_derived_input():
    """The STATIC half of the H2 guard for branch 0. What it proves, for the
    functions in ``_BRANCH_ZERO``:

    * every occurrence of the name ``request`` is one of these shapes, and any
      other shape is a finding: an attribute chain of ``_ALLOWED_CHAINS`` used
      in its one allowed way; a DIRECT positional or ``request=`` keyword
      argument to a function of ``_BRANCH_ZERO``, bound there to a parameter
      named ``request``; ``request is (not) None``. So no alias, walrus,
      annotation, tuple, container, starred argument, default, lambda,
      decorator, loop or comprehension target can carry the request;
    * the allowed uses: ``request.method``; ``request.method.upper()``;
      ``request.headers.get/getlist(<key>)`` and ``request.headers[<key>]``
      and ``<key> in request.headers`` with a literal key of ``_HEADERS``;
      ``request.scope.get(<key>)`` and ``request.scope[<key>]`` with a literal
      key of ``_SCOPE_KEYS``; ``for name in request.headers`` in the trigger
      only (header NAMES); ``context.matches(request.scope)`` in
      ``_effective_template`` only. A key passed by keyword counts, and a
      computed key is a finding, except the parameter of ``_one_header``,
      whose call sites must pass an allowed literal;
    * no str or bytes literal, ``+`` or f-string of literals names a host, a
      forwarded header, an origin, a referer, ``server`` or ``client``.

    What it cannot prove: dynamic access the AST does not show (``eval``,
    ``globals()``, an object that reaches the request through another
    reference). The runtime tripwire covers the paths the tests EXERCISE.
    A review must still read every change to these functions.

    Mutations: :func:`test_the_scan_turns_red_for_each_bypass` (on parsed
    copies) and :func:`test_the_tripwire_bites_a_mutated_branch_zero`.
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


def test_the_matches_receiver_is_derived_from_the_real_source():
    """``_MATCHES_RECEIVER`` is the loop variable of the ONE ``.matches(...)``
    call in ``_effective_template``, and that loop walks the route contexts."""
    import ast

    tree = ast.parse(_sources()["_effective_template"])
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "matches"]
    assert len(calls) == 1
    receiver = calls[0].func.value
    assert isinstance(receiver, ast.Name) and receiver.id == _MATCHES_RECEIVER
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)
             and isinstance(n.target, ast.Name) and n.target.id == _MATCHES_RECEIVER]
    assert len(loops) == 1
    assert isinstance(loops[0].iter, ast.Name) and loops[0].iter.id == "contexts"


def test_branch_zero_never_calls_the_url_fallback():
    """``_route_template`` falls back to ``request.url``. Branch 0 calls
    ``_matched_template``, which has no fallback."""
    import inspect

    source = inspect.getsource(deps._agent_user)
    assert "_route_template(" not in source
    assert "_matched_template(request)" in source


#: Each bypass: the function it goes into, the lines inserted before that
#: function's anchor, and any helper added to the scanned set.
_AGENT_ANCHOR = "    keys = agent_signing.usable_keys()\n"
_TEMPLATE_ANCHOR = '    routes = getattr(request.scope.get("app"), "routes", None)\n'
_BYPASSES: dict[str, tuple[str, list[str], dict[str, str]]] = {
    "B1 request.headers['host']": ("_agent_user", ["_h = request.headers['host']"], {}),
    "B2 request.url.hostname": ("_agent_user", ["_h = request.url.hostname"], {}),
    "B3 a scanned helper reads request.client": (
        "_agent_user", ["_h = _peer(request)"],
        {"_peer": "def _peer(request):\n    return request.client\n"}),
    "B3b the helper is not scanned": ("_agent_user", ["_h = _peer(request)"], {}),
    "B4 _peer(request.scope)": (
        "_agent_user", ["_h = _peer(request.scope)"],
        {"_peer": "def _peer(scope):\n    return scope.get('client')\n"}),
    "B5 getattr(request, 'url')": ("_agent_user", ["_h = getattr(request, 'url')"], {}),
    "B6 list(request.headers.raw)": ("_agent_user", ["_h = list(request.headers.raw)"], {}),
    "B6b request.headers.items()": ("_agent_user", ["_h = dict(request.headers.items())"], {}),
    "B7 request.scope['server']": ("_agent_user", ["_h = request.scope['server']"], {}),
    "B8 helper(req=request)": ("_agent_user", ["_h = _helper(req=request)"], {}),
    "B9 a bytes literal": ("_agent_user", ["_h = b'x-forwarded-host'"], {}),
    "B10 a built header name": (
        "_agent_user", ["_h = request.headers.get('x-forwarded-' + 'host')"], {}),
    "B11 a plain alias": ("_agent_user", ["_r = request", "_h = _r.url"], {}),
    "B12 request.scope.get('client')": ("_agent_user", ["_h = request.scope.get('client')"], {}),
    "B13 a walrus alias": (
        "_agent_user", ["if (_r := request):", "    _h = _r.url"], {}),
    "B14 an inline walrus": ("_agent_user", ["_h = (_r := request).url"], {}),
    "B15 a tuple-unpack alias": (
        "_agent_user", ["_r, _s = request, None", "_h = _r.url"], {}),
    "B16 an annotated alias": (
        "_agent_user", ["_r: object = request", "_h = _r.url"], {}),
    "B17 a for-loop alias": (
        "_agent_user", ["for _r in [request]:", "    _h = _r.url"], {}),
    "B18 a comprehension": ("_agent_user", ["_h = [r.url for r in (request,)]"], {}),
    "B19 a nested def with default r=request": (
        "_agent_user", ["def _f(r=request):", "    return r.client", "_h = _f()"], {}),
    "B20 a lambda r=request": ("_agent_user", ["_h = (lambda r=request: r.url)()"], {}),
    "B21 headers.get(key=chr(104)+'ost')": (
        "_agent_user", ["_h = request.headers.get(key=chr(104) + 'ost')"], {}),
    "B22 scope.get(key=chr(99)+'lient')": (
        "_agent_user", ["_h = request.scope.get(key=chr(99) + 'lient')"], {}),
    "B23 _peer(*[request])": (
        "_agent_user", ["_h = _peer(*[request])"],
        {"_peer": "def _peer(request):\n    return request.client\n"}),
    "B24 _peer(**{'r': request})": (
        "_agent_user", ["_h = _peer(**{'r': request})"],
        {"_peer": "def _peer(r):\n    return r.client\n"}),
    "B25 _BOX[0] = request": ("_agent_user", ["_BOX[0] = request"], {}),
    "B26 a decorator with a default argument": (
        "_agent_user", ["@_deco", "def _f(r=request):", "    return r.client",
                        "_h = _f()"], {}),
    "B27 an if-expression alias": (
        "_agent_user", ["_r = request if True else None", "_h = _r.url"], {}),
    "B28 _peer(request, None) into def _peer(x, request=None)": (
        "_agent_user", ["_h = _peer(request, None)"],
        {"_peer": "def _peer(x, request=None):\n    return x.client\n"}),
    "B29 a local function named matches": (
        "_agent_user",
        ["def matches(s):", "    return s.get('client')", "_h = matches(request.scope)"], {}),
    "B30 req: object = request in _effective_template": (
        "_effective_template", ["req: object = request", "_h = req.client"], {}),
    "B31 return request": ("_agent_user", ["return request"], {}),
    "B32 request.headers.keys()": ("_agent_user", ["_h = list(request.headers.keys())"], {}),
}


def _mutated(target: str, lines: list[str], helpers: dict[str, str]):
    """The sources with the bypass inserted, and the inserted line numbers."""
    sources = _sources()
    anchor = _AGENT_ANCHOR if target == "_agent_user" else _TEMPLATE_ANCHOR
    source = sources[target]
    assert source.count(anchor) == 1, target
    first = source[: source.index(anchor)].count("\n") + 1
    block = "".join(f"    {line}\n" for line in lines)
    sources[target] = source.replace(anchor, block + anchor)
    sources.update(helpers)
    return sources, set(range(first, first + len(lines)))


@pytest.mark.parametrize("label", sorted(_BYPASSES))
def test_the_scan_turns_red_for_each_bypass(label):
    """Each bypass goes into a COPY of the source, parsed with ``ast``. The real
    file never changes. Each must give a finding ON an inserted line, or in an
    added helper, so it cannot pass on a finding about something else."""
    target, lines, helpers = _BYPASSES[label]
    sources, inserted = _mutated(target, lines, helpers)
    problems = _scan(sources)
    hits = [p for p in problems
            if any(p.startswith(f"{target}:{n} ") for n in inserted)
            or any(p.startswith(f"{h}:") for h in helpers)]
    assert hits, (label, problems)


# ── The runtime tripwire bites ───────────────────────────────────────────────

_TRIPWIRE_MUTANTS = {
    "request.url.hostname": ["_h = request.url.hostname"],
    "request.client": ["_h = request.client"],
    "headers.get('host')": ["_h = request.headers.get('host')"],
    "headers.get(chr(104)+'ost')": ["_h = request.headers.get(chr(104) + 'ost')"],
    "an annotated alias": ["_r: object = request", "_h = _r.url"],
}


def _mutant_agent_user(lines: list[str]):
    """``_agent_user`` with *lines* inserted, compiled in ``deps``' globals."""
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(deps._agent_user))
    assert source.count(_AGENT_ANCHOR) == 1
    block = "".join(f"    {line}\n" for line in lines)
    source = source.replace(_AGENT_ANCHOR, block + _AGENT_ANCHOR)
    namespace: dict = {}
    exec(compile(source, "<mutant _agent_user>", "exec"), vars(deps), namespace)
    return namespace["_agent_user"]


def test_the_tripwire_passes_the_real_branch_zero(monkeypatch):
    from fastapi.testclient import TestClient

    from tests.unit import _branch_zero_tripwire as tripwire

    app, seen = _signed_app(monkeypatch, row_org=ORG, member_org=ORG)
    tripwire.arm(monkeypatch, deps)
    with TestClient(app) as client:
        answer = client.get(_TASK, headers=_signed_headers(ORG))
    assert answer.status_code == 200, answer.text
    assert answer.json()["org"] == ORG
    assert seen == [ORG]


@pytest.mark.parametrize("label", sorted(_TRIPWIRE_MUTANTS))
def test_the_tripwire_bites_a_mutated_branch_zero(monkeypatch, label):
    """The real file never changes: the mutant is compiled from a string."""
    from fastapi.testclient import TestClient

    from tests.unit import _branch_zero_tripwire as tripwire

    app, _ = _signed_app(monkeypatch, row_org=ORG, member_org=ORG)
    tripwire.arm(monkeypatch, deps,
                 agent_user=_mutant_agent_user(_TRIPWIRE_MUTANTS[label]))
    with TestClient(app) as client, pytest.raises(tripwire.TripwireError):
        client.get(_TASK, headers=_signed_headers(ORG))


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
