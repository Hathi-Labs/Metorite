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
# Spec ``external_agent_access.md`` §10.1. Since the parameter test above was
# widened, these tests are the automatic H2 checks for branch 0. They are a
# BACKSTOP and not a proof. Neither layer stops deliberate code. The guard
# against deliberate code is the digest test at the end of this section and
# the review rule of §10.1 (process control).
#
# 1. A STATIC scan. In each scanned function it refuses a list of construct
#    CLASSES that branch 0 does not need: nested functions, lambdas, `match`,
#    `global`, frame and dunder attributes, the introspection modules and
#    builtins, chained comparisons. It refuses every use of `request` that is
#    not one of a few exact shapes, and every rebind of a trusted name.
# 2. Module checks: each scanned function is bound once, by its own plain
#    def, in its own file, with no wrapper.
# 3. A RUNTIME tripwire (`tests/unit/_branch_zero_tripwire.py`). It records
#    the reads it can see through a wrapped request, on the paths the tests
#    run.
#
# Known residual gaps, named so nobody claims more: a caller-frame walk on a
# path outside the refused constructs (for example through an exception that
# a helper outside the scan builds), a closure cell reached through a helper
# that the scan does not cover, and `gc`. The digest test makes any edit to
# the scanned code visible, and the review rule decides it.

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
    ("body",),
})
#: ``route`` and ``app`` find the matched route template. ``path`` and
#: ``query_string`` are the signed path and query (§10.1).
_SCOPE_KEYS = frozenset({"route", "app", "path", "query_string"})
#: The four signed headers, the two that tell branch 0 to refuse a body on a
#: GET, and the nonce of a write (EA-6a, EA3-28).
_HEADERS = frozenset({
    "x-agent-connection", "x-agent-timestamp", "x-agent-signature", "x-actor-via",
    "content-length", "transfer-encoding", "x-agent-nonce",
})
#: EA-6a (EA3-28): the ONE function that may read the body, and only as
#: ``await request.body()`` with no argument. It hashes the body of a write
#: for the signature. The runtime tripwire refuses the read on a GET.
_BODY_READER = "_agent_user"
#: A header or scope key that names where the request came from.
_HOST_NAMES = frozenset({
    "host", "x-forwarded-host", "x-forwarded-for", "x-forwarded-proto",
    "x-real-ip", "forwarded", "origin", "referer", "server", "client",
})

#: RULE 1c, the ONE list of refused names (it absorbed the old list of
#: dynamic calls). A scanned function may not name one of these, call it,
#: import it, or import from one of the modules among them. Annotations are
#: not checked, because both modules start with ``from __future__ import
#: annotations`` and so never evaluate them (a test pins that import).
_REFUSED_NAMES = frozenset({
    "sys", "inspect", "gc", "traceback", "operator", "functools", "builtins",
    "importlib", "ctypes", "object", "type", "vars", "globals", "locals",
    "setattr", "delattr", "getattr", "__import__", "eval", "exec", "compile",
    "dict", "breakpoint", "import_module", "_getframe", "currentframe",
    "getframe", "string",
})
#: The modules among :data:`_REFUSED_NAMES`. Importing from one is refused.
_REFUSED_MODULES = frozenset({
    "sys", "inspect", "gc", "traceback", "operator", "functools", "builtins",
    "importlib", "ctypes", "string",
})

#: The allow-list of rule 1c: each ``getattr`` call the real code makes, by
#: function and exact source, with its reason. Each one reads ONE fixed,
#: literal attribute, with a default, from an object that is not the request.
_ALLOWED_GETATTR: dict[tuple[str, str], str] = {
    ("_matched_template", "getattr(route, 'path', None)"):
        "the template of the route object Starlette matched; a route may lack it",
    ("_effective_template", "getattr(request.scope.get('app'), 'routes', None)"):
        "the app's route table, to find the prefixed template; the app may lack it",
    ("_effective_template", "getattr(c, 'original_route', None)"):
        "FastAPI's route context; an older context type lacks the field",
    ("_effective_template", "getattr(contexts[0], 'path', None)"):
        "the prefixed path of the one context that serves the route",
    ("_effective_template", "getattr(context, 'path', None)"):
        "the prefixed path of the context that matched this request",
    ("agent_signing:_platform_secrets", "getattr(settings, 'gateway_internal_token', '')"):
        "a Settings field read with a default, so a test stand-in may omit it",
    ("agent_signing:_platform_secrets", "getattr(settings, 'litellm_master_key', '')"):
        "a Settings field read with a default, so a test stand-in may omit it",
    ("agent_signing:usable_keys", "getattr(settings, 'agent_edge_signing_key', '')"):
        "a Settings field read with a default, so a test stand-in may omit it",
    ("agent_signing:usable_keys",
     "getattr(settings, 'agent_edge_signing_key_previous', '')"):
        "a Settings field read with a default, so a test stand-in may omit it",
    ("agent_signing:signing_key",
     "getattr(get_settings(), 'agent_edge_signing_key', '')"):
        "a Settings field read with a default, so a test stand-in may omit it",
}

#: RULE 1b: refused attribute names. The allow-list is EMPTY: no scanned
#: function reads a dunder, a frame, a traceback, a generator or coroutine
#: frame, or a closure cell.
_REFUSED_ATTR_PREFIXES = ("__", "tb_", "f_", "gi_", "cr_")
_REFUSED_ATTRS = frozenset({"cell_contents"})
#: Round 5: a format string reaches attributes and items by name, past the
#: attribute rules. Any attribute with one of these names is refused. The
#: last three close `string.Formatter`, which reads fields the same way.
_FORMAT_ATTRS = frozenset({
    "format", "format_map", "vformat", "get_field", "format_field",
})
_ALLOWED_ATTRS: dict[str, str] = {}

#: RULE 2d: the ONLY stores of these names in ``_effective_template``, as
#: (name, line after the def line). A spy that rebinds ``context`` before
#: ``context.matches(request.scope)`` adds a store, and the scan refuses it.
_PINNED_STORES = frozenset({
    ("cached", 13), ("contexts", 15), ("cached", 19), ("contexts", 21),
    ("context", 26),
})

#: The one call outside branch 0 that may receive the request's scope:
#: ``<receiver>.matches(request.scope)`` in ``_effective_template``, where the
#: receiver is the loop variable over FastAPI's route contexts. Starlette's
#: ``Route.matches`` answers which route matched, from the scope's type, path
#: and method. Starlette's own router already read the same scope to send the
#: request here. It returns a match, never a value from the scope.
_MATCHES_RECEIVER = "context"

#: The trigger's loop variable may only be used as
#: ``<var>.lower().startswith(<literal that starts with this>)``.
_TRIGGER_PREFIX = "x-agent-"

#: Scanned functions of ``agent_signing.py`` carry this prefix in the source
#: map, so their names never collide with a branch-0 name.
_SIGNING = "agent_signing:"


def _module_text(module) -> str:
    """The source FILE of *module* (never ``inspect``, which follows
    ``__wrapped__``). ``deps.py`` starts with a BOM, so read utf-8-sig."""
    with open(module.__file__, encoding="utf-8-sig") as fh:
        return fh.read()


def _top_level_defs(module) -> dict[str, object]:
    import ast

    tree = ast.parse(_module_text(module))
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, node)
    return out


def _signing_names() -> tuple[str, ...]:
    from acb_auth import agent_signing

    return tuple(_top_level_defs(agent_signing))


def _sources() -> dict[str, str]:
    """Each scanned function's source, cut from its module FILE by the AST."""
    import ast

    from acb_auth import agent_signing

    out: dict[str, str] = {}
    for module, names, prefix in ((deps, _BRANCH_ZERO, ""),
                                  (agent_signing, _signing_names(), _SIGNING)):
        text = _module_text(module)
        defs = _top_level_defs(module)
        for name in names:
            out[prefix + name] = ast.get_source_segment(text, defs[name])  # type: ignore[arg-type]
    return out


def _protected(name: str) -> frozenset[str]:
    """Names a scanned function may never rebind: the branch-0 functions and
    every module global of its own module."""
    from acb_auth import agent_signing

    module = agent_signing if name.startswith(_SIGNING) else deps
    return frozenset({*_BRANCH_ZERO, *(n for n in vars(module) if not n.startswith("__"))})


def _docstring_ids(tree) -> set[int]:
    import ast

    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _annotation_ids(tree) -> set[int]:
    import ast

    out: set[int] = set()
    for node in ast.walk(tree):
        parts = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            parts.append(node.returns)
        if isinstance(node, ast.arg) and node.annotation:
            parts.append(node.annotation)
        if isinstance(node, ast.AnnAssign):
            parts.append(node.annotation)
        for part in parts:
            out |= {id(n) for n in ast.walk(part)}
    return out


def _fold(node) -> str | None:
    """The text a str or bytes literal, a ``+`` of literals, an f-string of
    literals or an ``agent_signing.<NAME>`` constant spells. None when it is
    computed."""
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


def _root_name(node):
    import ast

    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def ast_name(func) -> str:
    return getattr(func, "id", None) or getattr(func, "attr", None) or "?"


class _InvertedScan:
    """Every `request` must be an allowed shape, and every refused construct
    class is a finding."""

    def __init__(self, name: str, source: str, sources: dict[str, str]) -> None:
        import ast

        self.name = name
        self.sources = sources
        self.tree = ast.parse(source)
        self.fn = self.tree.body[0]
        self.docstrings = _docstring_ids(self.tree)
        self.annotations = _annotation_ids(self.tree)
        self.parents = {
            child: parent
            for parent in ast.walk(self.tree)
            for child in ast.iter_child_nodes(parent)
        }
        self.protected = _protected(name)
        self.problems: list[str] = []

    def flag(self, node, what: str) -> None:
        self.problems.append(f"{self.name}:{getattr(node, 'lineno', '?')} {what}")

    def run(self) -> list[str]:
        import ast

        if len(self.tree.body) != 1 or not isinstance(
                self.fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.flag(self.tree.body[0], "is not one plain function")
            return self.problems
        for node in ast.walk(self.tree):
            self.strings(node)
            self.rebinding(node)
            self.constructs(node)
            self.refused(node)
            if isinstance(node, ast.Name) and node.id == "request":
                self.occurrence(node)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_one_header":
                key = node.args[1] if len(node.args) > 1 else None
                if key is None or (_fold(key) or "\0").lower() not in _HEADERS:
                    self.flag(node, "_one_header without an allowed literal header")
        if self.name == "_one_header":
            self.one_header_name()
        if self.name == "_is_agent_request":
            self.trigger_loop()
        if self.name == "_effective_template":
            self.pinned_stores()
        return self.problems

    # ── literals ────────────────────────────────────────────────────────────

    def strings(self, node) -> None:
        """No str or bytes literal, ``+`` or f-string of them names a host."""
        import ast

        if isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr)) and (
                id(node) not in self.docstrings):
            text = _fold(node)
            if text is not None and text.strip().lower() in _HOST_NAMES:
                self.flag(node, f"names {text!r}")

    # ── RULE 1a and 1d: construct classes ─────────────────────────────────

    def constructs(self, node) -> None:
        import ast

        if isinstance(node, ast.Match):
            self.flag(node, "uses a match statement")
        if isinstance(node, ast.Lambda):
            self.flag(node, "uses a lambda")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
                node is not self.fn):
            self.flag(node, f"defines a nested {type(node).__name__}")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            self.flag(node, "declares global or nonlocal")
        if isinstance(node, ast.Compare) and len(node.ops) > 1:
            self.flag(node, "uses a chained comparison")

    # ── RULE 1b and 1c: frames, dunders, introspection ────────────────────

    def refused(self, node) -> None:
        import ast

        if isinstance(node, ast.Attribute) and (
                node.attr.startswith(_REFUSED_ATTR_PREFIXES) or node.attr in _REFUSED_ATTRS
        ) and node.attr not in _ALLOWED_ATTRS:
            self.flag(node, f"reads the attribute {node.attr}")
        if isinstance(node, ast.Attribute) and node.attr in _REFUSED_NAMES:
            self.flag(node, f"reaches {node.attr}")
        # Round 5: a format string walks attributes and items by name, so
        # `'{0.__traceback__...}'.format(e)` reads a frame with no attribute
        # node the scan could see. No scanned function formats a string.
        if isinstance(node, ast.Attribute) and node.attr in _FORMAT_ATTRS:
            self.flag(node, f"calls {node.attr}")
        # Round 5: no name that starts with "__", so no `__builtins__`.
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            self.flag(node, f"names the dunder {node.id}")
        if isinstance(node, ast.Name) and node.id in _REFUSED_NAMES and (
                id(node) not in self.annotations) and not self._allowed_getattr(node):
            self.flag(node, f"names {node.id}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [getattr(node, "module", None) or ""]
            modules += [alias.name for alias in node.names]
            for module in modules:
                if module.split(".")[0] in _REFUSED_MODULES:
                    self.flag(node, f"imports from {module}")

    def _allowed_getattr(self, node) -> bool:
        import ast

        call = self.parents.get(node)
        if node.id != "getattr" or not isinstance(call, ast.Call) or call.func is not node:
            return False
        return (self.name, ast.unparse(call)) in _ALLOWED_GETATTR

    # ── RULE 2d: the pinned stores of _effective_template ─────────────────

    def pinned_stores(self) -> None:
        import ast

        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Name) and node.id in {n for n, _ in _PINNED_STORES}
                    and not isinstance(node.ctx, ast.Load)
                    and (node.id, node.lineno - self.fn.lineno) not in _PINNED_STORES):
                self.flag(node, f"stores {node.id} outside its pinned lines")

    # ── trusted names are never rebound ──────────────────────────────────

    def rebinding(self, node) -> None:
        import ast

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                self.flag(decorator, "has a decorator")
            if node is not self.fn and node.name in self.protected:
                self.flag(node, f"defines the trusted name {node.name}")
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and (
                node.id in self.protected):
            self.flag(node, f"rebinds the trusted name {node.id}")
        if isinstance(node, (ast.Attribute, ast.Subscript)) and not isinstance(
                node.ctx, ast.Load) and _root_name(node) in self.protected:
            # The one write the real code makes: `_effective_template` fills
            # its own cache of route contexts, keyed by `id(route)`.
            cache_fill = (self.name == "_effective_template"
                          and isinstance(node, ast.Subscript)
                          and isinstance(node.value, ast.Name)
                          and node.value.id == "_TEMPLATES")
            if not cache_fill:
                self.flag(node, f"writes into the trusted name {_root_name(node)}")
        if isinstance(node, ast.ExceptHandler) and node.name in self.protected:
            self.flag(node, f"binds the trusted name {node.name} in except")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if alias.asname:
                    self.flag(node, f"imports {alias.name} as {alias.asname}")
                if bound in self.protected:
                    self.flag(node, f"imports over the trusted name {bound}")
                if "_route_template" in (alias.name, module):
                    self.flag(node, "imports _route_template")
        if (isinstance(node, ast.Name) and node.id == "_route_template") or (
                isinstance(node, ast.Attribute) and node.attr == "_route_template"):
            self.flag(node, "reaches _route_template, which falls back to request.url")

    # ── `_one_header` reads its parameter `name`, and nothing else ─────────

    def one_header_name(self) -> None:
        import ast

        names = set(_params(self.fn)) - {"request"}
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Name) and node.id in names):
                continue
            parent = self.parents.get(node)
            ok = (isinstance(node.ctx, ast.Load) and isinstance(parent, ast.Call)
                  and parent.args == [node] and not parent.keywords
                  and isinstance(parent.func, ast.Attribute)
                  and parent.func.attr in ("get", "getlist")
                  and isinstance(parent.func.value, ast.Attribute)
                  and parent.func.value.attr == "headers"
                  and isinstance(parent.func.value.value, ast.Name)
                  and parent.func.value.value.id == "request")
            if not ok:
                self.flag(node, f"uses the parameter {node.id} outside request.headers.getlist")

    # ── the trigger's loop variable ──────────────────────────────────────

    def trigger_loop(self) -> None:
        import ast

        loops = [n for n in ast.walk(self.tree) if isinstance(n, ast.For)
                 and isinstance(n.iter, ast.Attribute) and n.iter.attr == "headers"
                 and isinstance(n.iter.value, ast.Name) and n.iter.value.id == "request"]
        for loop in loops:
            if not isinstance(loop.target, ast.Name):
                self.flag(loop, "unpacks the header names")
                continue
            var = loop.target.id
            for node in ast.walk(self.tree):
                if not (isinstance(node, ast.Name) and node.id == var) or node is loop.target:
                    continue
                if not self._prefix_test(node):
                    self.flag(node, f"uses the header name {var} beyond a prefix test")

    def _prefix_test(self, node) -> bool:
        import ast

        lower = self.parents.get(node)
        call1 = self.parents.get(lower)
        start = self.parents.get(call1)
        call2 = self.parents.get(start)
        return (isinstance(node.ctx, ast.Load)
                and isinstance(lower, ast.Attribute) and lower.attr == "lower"
                and isinstance(call1, ast.Call) and call1.func is lower
                and not call1.args and not call1.keywords
                and isinstance(start, ast.Attribute) and start.attr == "startswith"
                and isinstance(call2, ast.Call) and call2.func is start
                and len(call2.args) == 1 and not call2.keywords
                and (_fold(call2.args[0]) or "").lower().startswith(_TRIGGER_PREFIX))

    # ── every occurrence of `request` ──────────────────────────────────────

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
              and len(parent.ops) == 1
              and isinstance(parent.ops[0], (ast.Is, ast.IsNot))
              and isinstance(parent.comparators[0], ast.Constant)
              and parent.comparators[0].value is None):
            return  # `request is not None`, one comparison only
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
                return  # the trigger reads header NAMES (see `trigger_loop`)
            if isinstance(use, ast.Compare) and len(use.ops) == 1 and top in use.comparators \
                    and isinstance(use.ops[0], (ast.In, ast.NotIn)):
                self.literal(use.left, _HEADERS, "header")
                return
            if isinstance(use, ast.Subscript) and use.value is top:
                self.literal(use.slice, _HEADERS, "header")
                return
        # EA-6a: `await request.body()`, no argument, in `_BODY_READER` only.
        if (key == ("body",) and self.name == _BODY_READER
                and isinstance(use, ast.Call) and use.func is top
                and not use.args and not use.keywords
                and isinstance(self.parents.get(use), ast.Await)):
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
                return  # `one_header_name` and `run` check this parameter
            self.literal(given, _HEADERS, "header")
        else:
            self.literal(given, _SCOPE_KEYS, "scope key")

    def literal(self, node, allowed: frozenset[str], what: str) -> None:
        text = _fold(node)
        if text is None:
            self.flag(node, f"reads a computed {what}")
        elif text.lower() not in allowed:
            self.flag(node, f"reads {what} {text!r}")


def _scan(sources: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name, source in sources.items():
        problems += _InvertedScan(name, source, sources).run()
    return problems


def test_branch_zero_reads_no_host_derived_input():
    """The STATIC scan of the branch-0 functions of ``deps.py`` and of every
    function of ``agent_signing.py``. It refuses, in each one:

    * the construct classes of rule 1: a nested function or class, a
      lambda, a ``match`` statement, ``global``, ``nonlocal``, a chained
      comparison, an attribute that starts with ``__``, ``tb_``, ``f_``,
      ``gi_`` or ``cr_`` or is ``cell_contents``, and every name of
      ``_REFUSED_NAMES`` (the introspection modules, the frame and dynamic
      calls, ``getattr``, ``type``, ``object`` and ``dict``) outside the
      allow-list ``_ALLOWED_GETATTR``, and any import from those modules;
    * a name that starts with ``__``, and an attribute named ``format``,
      ``format_map``, ``vformat``, ``get_field`` or ``format_field``, because
      a format string reads attributes by name (``str.format`` and
      ``string.Formatter``);
    * every rebind of a trusted name (a branch-0 name or a global of its own
      module), any decorator, any use of ``_route_template``, and any store
      of ``contexts``, ``cached`` or ``context`` off its pinned line;
    * every use of ``request`` that is not one of the exact shapes of
      ``_ALLOWED_CHAINS`` and the hand-off rules, every header or scope key
      that is not a literal of the allow-lists, and every literal that names
      a host-derived input.

    What it cannot stop: deliberate code that reaches the request on a path
    outside these construct classes, for example through a helper outside
    the scan. The digest test and the review rule of §10.1 are the guard
    against deliberate code.
    """
    assert _scan(_sources()) == []


def test_both_modules_never_evaluate_annotations():
    """Rule 1c skips annotations. That is safe only while both modules start
    with ``from __future__ import annotations``."""
    import ast

    from acb_auth import agent_signing

    for module in (deps, agent_signing):
        tree = ast.parse(_module_text(module))
        assert any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                   and any(a.name == "annotations" for a in n.names)
                   for n in tree.body), module.__name__


def test_every_allow_list_entry_is_used():
    """An allow-list entry the code no longer needs is removed, so the list
    stays the list of real uses."""
    import ast

    used = set()
    for name, source in _sources().items():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr":
                used.add((name, ast.unparse(node)))
    assert set(_ALLOWED_GETATTR) == used
    assert _ALLOWED_ATTRS == {}


def test_the_body_is_read_once_and_in_one_function():
    """EA-6a. Exactly one ``request.body`` in all the scanned code, in
    ``_BODY_READER``. The scan checks the shape, and this pins the count."""
    import ast

    found = []
    for name, source in _sources().items():
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Attribute) and node.attr == "body"
                    and isinstance(node.value, ast.Name) and node.value.id == "request"):
                found.append(name)
    assert found == [_BODY_READER]


def test_every_scanned_branch_zero_function_reads_the_request():
    """The scan is not vacuous: each branch-0 name is a real function that
    reads the request."""
    import ast

    for name in _BRANCH_ZERO:
        tree = ast.parse(_sources()[name])
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
    """``_route_template`` falls back to ``request.url``. No scanned function
    names it, calls it, reaches it as an attribute or imports it, by any
    alias, and ``_agent_user`` calls ``_matched_template`` (AST, not text)."""
    import ast

    for name, source in _sources().items():
        for node in ast.walk(ast.parse(source)):
            assert not (isinstance(node, ast.Name) and node.id == "_route_template"), name
            assert not (isinstance(node, ast.Attribute)
                        and node.attr == "_route_template"), name
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name != "_route_template", name
    calls = [n for n in ast.walk(ast.parse(_sources()["_agent_user"]))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_matched_template"]
    assert len(calls) == 1


# ── RULE 2: module level ─────────────────────────────────────────────────────


def _binds(node, name: str) -> bool:
    """True when *node* binds, rebinds, wraps or writes into *name*."""
    import ast

    return (
        (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
         and node.name == name)
        or (isinstance(node, ast.Name) and node.id == name
            and not isinstance(node.ctx, ast.Load))
        or (isinstance(node, (ast.Attribute, ast.Subscript))
            and not isinstance(node.ctx, ast.Load) and _root_name(node) == name)
        or (isinstance(node, (ast.Import, ast.ImportFrom))
            and any((a.asname or a.name.split(".")[0]) == name for a in node.names))
        or (isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names)
    )


def _module_findings(module, names, text: str | None = None) -> list[str]:
    """Rule 2a and 2b for one module, on its file or on *text*."""
    import ast

    text = _module_text(module) if text is None else text
    tree = ast.parse(text)
    found: list[str] = []
    for name in names:
        bindings = [node for node in ast.walk(tree) if _binds(node, name)]
        own = [b for b in bindings if b in tree.body
               and isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
               and not b.decorator_list]
        if len(bindings) != 1 or len(own) != 1:
            found.append(f"{module.__name__}.{name} is bound {len(bindings)} times")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
                "__getattr__", "__dir__"):
            found.append(f"{module.__name__} defines a module {node.name}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "modules" and (
                isinstance(node.value, ast.Name) and node.value.id == "sys"):
            found.append(f"{module.__name__} reaches sys.modules")
    return found


def _agent_signing_bindings(text: str | None = None) -> list[str]:
    """Rule 2b: ``agent_signing`` is bound once in ``deps.py``, by its import."""
    import ast

    tree = ast.parse(_module_text(deps) if text is None else text)
    bindings = [node for node in ast.walk(tree) if _binds(node, "agent_signing")]
    ok = (len(bindings) == 1 and isinstance(bindings[0], ast.ImportFrom)
          and bindings[0] in tree.body and bindings[0].module == "acb_auth")
    return [] if ok else [f"agent_signing is bound {len(bindings)} times in deps.py"]


def test_each_scanned_function_is_bound_once_by_its_own_def():
    """Rule 2a and 2b. No other statement in either module assigns, deletes,
    decorates, imports over or wraps a scanned function. Neither module has a
    module ``__getattr__`` or touches ``sys.modules``. ``agent_signing`` is
    bound once in ``deps.py``, by its import."""
    from acb_auth import agent_signing

    assert _module_findings(deps, _BRANCH_ZERO) == []
    assert _module_findings(agent_signing, _signing_names()) == []
    assert _agent_signing_bindings() == []


_MODULE_MUTANTS = {
    "a module-level rewrap": "\n_agent_user = _agent_user\n",
    "a module-level attribute write": "\n_agent_user.__qualname__ = 'x'\n",
    "a module __getattr__": "\ndef __getattr__(name):\n    return None\n",
    "a sys.modules write": "\nimport sys\nsys.modules['acb_auth.agent_signing'] = None\n",
    "an agent_signing rebind": "\nagent_signing = None\n",
}


@pytest.mark.parametrize("label", sorted(_MODULE_MUTANTS))
def test_the_module_checks_turn_red(label):
    """Each mutant is appended to a COPY of the ``deps.py`` text."""
    text = _module_text(deps) + _MODULE_MUTANTS[label]
    found = _module_findings(deps, _BRANCH_ZERO, text) + _agent_signing_bindings(text)
    assert found, label


def test_each_scanned_function_is_its_own_code():
    """Rule 2c, at runtime. The object on the module is not a wrapper, its
    code comes from the module's own file, its qualified name is its own,
    and its first line is the line of its def."""
    import os

    from acb_auth import agent_signing

    for module, names in ((deps, _BRANCH_ZERO), (agent_signing, _signing_names())):
        defs = _top_level_defs(module)
        here = os.path.normcase(os.path.abspath(module.__file__))
        for name in names:
            obj = getattr(module, name)
            assert not hasattr(obj, "__wrapped__"), name
            assert os.path.normcase(os.path.abspath(obj.__code__.co_filename)) == here, name
            assert obj.__qualname__ == name, name
            assert obj.__code__.co_firstlineno == defs[name].lineno, name  # type: ignore[attr-defined]


def test_the_runtime_identity_check_turns_red(monkeypatch):
    """Wrap one function on the module, and the identity check fails."""
    import functools

    original = deps._one_header

    @functools.wraps(original)
    def _wrapper(*args, **kwargs):
        return original(*args, **kwargs)

    monkeypatch.setattr(deps, "_one_header", _wrapper)
    with pytest.raises(AssertionError):
        test_each_scanned_function_is_its_own_code()


# ── RULE 5: the digest fence of the process control (R7) ────────────────────

#: SHA-256 of the normalised AST of every scanned function, of the helpers of
#: :data:`_BRANCH_ZERO_HELPERS`, of ``agent_signing.py`` and of
#: ``_branch_zero_tripwire.py``, keyed by the Python minor version, because
#: ``ast.dump`` differs between versions. Update it ONLY in the commit that
#: carries the review and the verification of §10.1.
#:
#: EA-6a (2026-10-14) changed branch 0 for the writes. The pins before it were
#: 3.12 ``1eded904…db93e0`` and 3.13 ``2ac62c16…a40f22``.
_BRANCH_ZERO_DIGESTS: dict[tuple[int, int], str] = {
    (3, 12): "8de38247cccac7431f87a478656904e1e2b3c666e971c45a5bd13cbe99cafdb2",
    (3, 13): "0cc35491d3670faf1d95d56f5165a7b80c4ea6a9219578286cecb033159e002c",
}

#: EA-6a: two helpers of ``deps.py`` that branch 0 calls and that never see
#: the request, so the scan does not read them. The connection read and the
#: write guard (nonce and cap) are part of the boundary all the same, so the
#: digest covers them, and an edit to either one fails the digest test.
_BRANCH_ZERO_HELPERS = ("_read_connection", "_claim_write")


def _branch_zero_digest() -> str:
    import ast
    import hashlib
    import os

    from acb_auth import agent_signing

    text = _module_text(deps)
    defs = _top_level_defs(deps)
    sources = dict(_sources())
    for name in _BRANCH_ZERO_HELPERS:
        sources["helper:" + name] = ast.get_source_segment(text, defs[name])  # type: ignore[arg-type]
    digest = hashlib.sha256()
    for name, source in sorted(sources.items()):
        digest.update(name.encode())
        digest.update(ast.dump(ast.parse(source)).encode())
    tripwire = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_branch_zero_tripwire.py")
    for path in (agent_signing.__file__, tripwire):
        with open(path, encoding="utf-8-sig") as fh:
            digest.update(ast.dump(ast.parse(fh.read())).encode())
    return digest.hexdigest()


def test_branch_zero_digest_is_pinned():
    import sys

    version = sys.version_info[:2]
    assert version in _BRANCH_ZERO_DIGESTS, (
        f"No digest is pinned for Python {version[0]}.{version[1]}. "
        "Add one after review."
    )
    assert _branch_zero_digest() == _BRANCH_ZERO_DIGESTS[version], (
        "branch 0 changed. Get a diff-reviewer review and an independent "
        "verification, then update this digest in the same commit."
    )


def test_the_monitor_never_takes_the_optimizer_tool_id(monkeypatch):
    """Round 5. The RAISE hook uses tool id 3 or 4 only. Python reserves 5
    for the optimizer. With 3 and 4 both taken, arming fails loudly."""
    import sys

    from tests.unit import _branch_zero_tripwire as tripwire

    monitoring = sys.monitoring
    taken = [t for t in (3, 4) if monitoring.get_tool(t) is None]
    for tool in taken:
        monitoring.use_tool_id(tool, "test_occupies")
    try:
        with pytest.raises(RuntimeError, match="tool ids 3 and 4 are both taken"):
            monkeypatch.setattr(tripwire.MONITOR, "armed", True)
        assert tripwire.MONITOR.tool is None
    finally:
        for tool in taken:
            monitoring.free_tool_id(tool)


#: Each bypass: the function it goes into, the anchor line it goes BEFORE (at
#: that line's indentation), the inserted lines, and any helper added to the
#: scanned set. Each must give a finding ON an inserted line, or in a helper.
_A_AGENT = "    keys = agent_signing.usable_keys()\n"
_A_TEMPLATE = '    routes = getattr(request.scope.get("app"), "routes", None)\n'
_A_ONE = "    values = request.headers.getlist(name)\n"
_A_TRIGGER = "        if name.lower().startswith(agent_signing.AGENT_HEADER_PREFIX):\n"
_A_VERIFY = "    connection_header = _one_header(request, agent_signing.HEADER_CONNECTION)\n"
_A_DEF = "async def _agent_user(request: Request) -> UserContext:\n"
_A_KEYS = "    settings = get_settings()\n    forbidden = _platform_secrets()\n"
_PEER_CLIENT = "def _peer(request):\n    return request.client\n"
_BYPASSES: dict[str, tuple[str, str, list[str], dict[str, str]]] = {
    "B01 request.headers['host']": ("_agent_user", _A_AGENT, ["_h = request.headers['host']"], {}),
    "B02 request.url.hostname": ("_agent_user", _A_AGENT, ["_h = request.url.hostname"], {}),
    "B03 a scanned helper reads request.client": (
        "_agent_user", _A_AGENT, ["_h = _peer(request)"], {"_peer": _PEER_CLIENT}),
    "B03b the helper is not scanned": ("_agent_user", _A_AGENT, ["_h = _peer(request)"], {}),
    "B04 _peer(request.scope)": (
        "_agent_user", _A_AGENT, ["_h = _peer(request.scope)"],
        {"_peer": "def _peer(scope):\n    return scope.get('client')\n"}),
    "B05 getattr(request, 'url')": ("_agent_user", _A_AGENT, ["_h = getattr(request, 'url')"], {}),
    "B06 list(request.headers.raw)": (
        "_agent_user", _A_AGENT, ["_h = list(request.headers.raw)"], {}),
    "B06b request.headers.items()": (
        "_agent_user", _A_AGENT, ["_h = list(request.headers.items())"], {}),
    "B07 request.scope['server']": ("_agent_user", _A_AGENT, ["_h = request.scope['server']"], {}),
    "B08 helper(req=request)": ("_agent_user", _A_AGENT, ["_h = _helper(req=request)"], {}),
    "B09 a bytes literal": ("_agent_user", _A_AGENT, ["_h = b'x-forwarded-host'"], {}),
    "B10 a built header name": (
        "_agent_user", _A_AGENT, ["_h = request.headers.get('x-forwarded-' + 'host')"], {}),
    "B11 a plain alias": ("_agent_user", _A_AGENT, ["_r = request", "_h = _r.url"], {}),
    "B12 request.scope.get('client')": (
        "_agent_user", _A_AGENT, ["_h = request.scope.get('client')"], {}),
    "B13 a walrus alias": (
        "_agent_user", _A_AGENT, ["if (_r := request):", "    _h = _r.url"], {}),
    "B14 an inline walrus": ("_agent_user", _A_AGENT, ["_h = (_r := request).url"], {}),
    "B15 a tuple-unpack alias": (
        "_agent_user", _A_AGENT, ["_r, _s = request, None", "_h = _r.url"], {}),
    "B16 an annotated alias": (
        "_agent_user", _A_AGENT, ["_r: str = request", "_h = _r.url"], {}),
    "B17 a for-loop alias": (
        "_agent_user", _A_AGENT, ["for _r in [request]:", "    _h = _r.url"], {}),
    "B18 a comprehension": (
        "_agent_user", _A_AGENT, ["_h = [r.url for r in (request,)]"], {}),
    "B19 a nested def with default r=request": (
        "_agent_user", _A_AGENT,
        ["def _f(r=request):", "    return r.client", "_h = _f()"], {}),
    "B20 a lambda r=request": (
        "_agent_user", _A_AGENT, ["_h = (lambda r=request: r.url)()"], {}),
    "B21 headers.get(key=chr(104)+'ost')": (
        "_agent_user", _A_AGENT, ["_h = request.headers.get(key=chr(104) + 'ost')"], {}),
    "B22 scope.get(key=chr(99)+'lient')": (
        "_agent_user", _A_AGENT, ["_h = request.scope.get(key=chr(99) + 'lient')"], {}),
    "B23 _peer(*[request])": (
        "_agent_user", _A_AGENT, ["_h = _peer(*[request])"], {"_peer": _PEER_CLIENT}),
    "B24 _peer(**{'r': request})": (
        "_agent_user", _A_AGENT, ["_h = _peer(**{'r': request})"],
        {"_peer": "def _peer(r):\n    return r.client\n"}),
    "B25 _BOX[0] = request": ("_agent_user", _A_AGENT, ["_BOX[0] = request"], {}),
    "B26 a decorator with a default argument": (
        "_agent_user", _A_AGENT,
        ["@_deco", "def _f(r=request):", "    return r.client", "_h = _f()"], {}),
    "B27 an if-expression alias": (
        "_agent_user", _A_AGENT, ["_r = request if True else None", "_h = _r.url"], {}),
    "B28 _peer(request, None) into def _peer(x, request=None)": (
        "_agent_user", _A_AGENT, ["_h = _peer(request, None)"],
        {"_peer": "def _peer(x, request=None):\n    return x.client\n"}),
    "B29 a local function named matches": (
        "_agent_user", _A_AGENT,
        ["def matches(s):", "    return s.get('client')", "_h = matches(request.scope)"], {}),
    "B30 req: str = request in _effective_template": (
        "_effective_template", _A_TEMPLATE, ["req: str = request", "_h = req.client"], {}),
    "B31 return request": ("_agent_user", _A_AGENT, ["return request"], {}),
    "B32 request.headers.keys()": (
        "_agent_user", _A_AGENT, ["_h = list(request.headers.keys())"], {}),
    "B33 _is_agent_request = _peer_unscanned": (
        "_agent_user", _A_AGENT,
        ["_is_agent_request = _peer_unscanned", "_h = _is_agent_request(request)"], {}),
    "B34 import _route_template as _matched_template": (
        "_agent_user", _A_AGENT,
        ["from acb_auth.deps import _route_template as _matched_template",
         "_h = _matched_template(request)"], {}),
    "B35 _one_header reassigns name to chr(104)+'ost'": (
        "_one_header", _A_ONE,
        ["name = chr(104) + 'ost' if name == agent_signing.HEADER_CONNECTION else name"], {}),
    "B36 the trigger loop binds a tenant from a header name": (
        "_is_agent_request", _A_TRIGGER, ["bind_tenant(name[8:])"], {}),
    "B37 a @_traced decorator on _agent_user": ("_agent_user", _A_DEF, ["@_traced"], {}),
    "B38 a local shadow of agent_signing": (
        "_verify_signed_request", _A_VERIFY, ["agent_signing = _fake_signing"], {}),
    "B38b import json as agent_signing": (
        "_verify_signed_request", _A_VERIFY, ["import json as agent_signing"], {}),
    "B39 setattr on agent_signing": (
        "_agent_user", _A_AGENT,
        ["setattr(agent_signing, 'HEADER_' + 'CONNECTION', 'ho' + 'st')"], {}),
    "B39b an attribute write on agent_signing": (
        "_agent_user", _A_AGENT, ["agent_signing.HEADER_CONNECTION = _x"], {}),
    "B40 _one_header swaps name and falls back on an error": (
        "_one_header", _A_ONE,
        ["_orig = name", "name = 'ho' + 'st'", "try:",
         "    values = request.headers.getlist(name)",
         "except Exception:", "    name = _orig"], {}),
    "B41 repr(request.scope)": ("_agent_user", _A_AGENT, ["_h = repr(request.scope)"], {}),
    "B42 dict.get(request.scope, 'client')": (
        "_agent_user", _A_AGENT, ["_h = dict.get(request.scope, 'client')"], {}),
    "B43 dict.get(scope, 'headers') through an alias": (
        "_agent_user", _A_AGENT, ["_s = request.scope", "_h = dict.get(_s, 'headers')"], {}),
    "B44 dict.get(scope, 'server') through an alias": (
        "_agent_user", _A_AGENT, ["_s = request.scope", "_h = dict.get(_s, 'server')"], {}),
    "B45 a host read in try/except that returns SWALLOWED": (
        "_agent_user", _A_AGENT,
        ["try:", "    _h = request.url.hostname", "except Exception:",
         "    _h = 'SWALLOWED'"], {}),
    "B46 eval of a built attribute": (
        "_agent_user", _A_AGENT, ["_h = eval('request.' + 'url')"], {}),
    "B47 a global declaration": ("_agent_user", _A_AGENT, ["global _is_agent_request"], {}),
    "B48 del of a trusted name": ("_agent_user", _A_AGENT, ["del db_busy"], {}),
    "B49 a bare reference to _route_template": (
        "_agent_user", _A_AGENT, ["_fallback = _route_template"], {}),
    # ── fix round 4: construct classes ──
    "B50 sys._getframe(1)": ("_agent_user", _A_AGENT, ["_f = sys._getframe(1)"], {}),
    "B51 a traceback frame walk": (
        "_agent_user", _A_AGENT,
        ["try:", "    raise ValueError", "except ValueError as _e:",
         "    _f = _e.__traceback__.tb_frame.f_back"], {}),
    "B52 inspect.stack()": ("_agent_user", _A_AGENT, ["_f = inspect.stack()"], {}),
    "B53 dict.get on a value that holds the scope": (
        "_agent_user", _A_AGENT, ["_h = dict.get(_x, 'x-tenant-id')"], {}),
    "B54 operator.attrgetter": (
        "_agent_user", _A_AGENT, ["_g = operator.attrgetter('url')"], {}),
    "B55 type(x).__mro__": ("_agent_user", _A_AGENT, ["_t = type(_x).__mro__"], {}),
    "B56 a chained comparison on request": (
        "_agent_user", _A_AGENT, ["if request is not None is not None:", "    pass"], {}),
    "B57 a match statement": (
        "_agent_user", _A_AGENT, ["match _x:", "    case _:", "        pass"], {}),
    "B58 functools.partial": ("_agent_user", _A_AGENT, ["_p = functools.partial(_x)"], {}),
    "B59 getattr outside the allow-list": (
        "_agent_user", _A_AGENT, ["_g = getattr(_x, 'url', None)"], {}),
    "B60 from builtins import getattr as _ga": (
        "_agent_user", _A_AGENT, ["from builtins import getattr as _ga"], {}),
    "B61 a closure cell": ("_agent_user", _A_AGENT, ["_c = _x.cell_contents"], {}),
    "B62 a frame read in agent_signing.usable_keys": (
        _SIGNING + "usable_keys", _A_KEYS, ["import sys"], {}),
    "B63 a spy rebinds context in _effective_template": (
        "_effective_template", _A_TEMPLATE, ["context = _Spy()"], {}),
    "B64 gc.get_referrers": ("_agent_user", _A_AGENT, ["_r = gc.get_referrers(_x)"], {}),
    "B65 a coroutine frame": ("_agent_user", _A_AGENT, ["_f = _x.cr_frame"], {}),
    "B66 an object.__getattribute__ call": (
        "_agent_user", _A_AGENT, ["_u = object.__getattribute__(_x, 'url')"], {}),
    # Shapes that ONE rule alone catches, so each rule has its own red case.
    "B67 a bare lambda": ("_agent_user", _A_AGENT, ["_f = lambda: None"], {}),
    "B68 a bare nested def": ("_agent_user", _A_AGENT, ["def _f():", "    pass"], {}),
    "B69 a bare nested class": ("_agent_user", _A_AGENT, ["class _C:", "    pass"], {}),
    "B70 a bare chained comparison": ("_agent_user", _A_AGENT, ["_ok = 1 < _x < 3"], {}),
    # ── fix round 5: format strings and dunder names ──
    "B71 a format string walks a traceback in an except clause": (
        "_agent_user", _A_AGENT,
        ["try:", "    raise ValueError", "except ValueError as _e:",
         "    _h = '{0.__traceback__.tb_frame.f_locals[request].url.hostname}'.format(_e)"],
        {}),
    "B72 a format string reads __class__.__mro__": (
        "_agent_user", _A_AGENT, ["_h = '{0.__class__.__mro__}'.format(_x)"], {}),
    "B73 __builtins__['getattr']": (
        "_agent_user", _A_AGENT, ["_g = __builtins__['getattr']"], {}),
    # A string.Formatter reads fields by name, as str.format does.
    "B74 string.Formatter().vformat walks a traceback": (
        "_agent_user", _A_AGENT,
        ["import string", "try:", "    raise ValueError", "except ValueError as _e:",
         "    _h = string.Formatter().vformat("
         "'{0.__traceback__.tb_frame.f_back.f_locals[request].url.hostname}', (_e,), {})"],
        {}),
    "B75 a Formatter from a helper calls get_field": (
        "_agent_user", _A_AGENT, ["_h = _fmt.get_field('0.__class__', (_x,), {})"], {}),
    # ── EA-6a: the one body read stays one exact shape in one function ──
    "B76 the body read in another function": (
        "_verify_signed_request", _A_VERIFY, ["_b = await request.body()"], {}),
    "B77 the body method as a value": ("_agent_user", _A_AGENT, ["_b = request.body"], {}),
    "B78 the body read not awaited": ("_agent_user", _A_AGENT, ["_b = request.body()"], {}),
    "B79 the body read with an argument": (
        "_agent_user", _A_AGENT, ["_b = await request.body(1)"], {}),
    "B80 the request stream": ("_agent_user", _A_AGENT, ["_b = request.stream()"], {}),
}


def _mutated(target: str, anchor: str, lines: list[str], helpers: dict[str, str]):
    """The sources with the bypass inserted before *anchor*, and the line
    numbers it takes."""
    sources = _sources()
    source = sources[target]
    assert source.count(anchor) == 1, (target, anchor)
    indent = anchor[: len(anchor) - len(anchor.lstrip(" "))]
    first = source[: source.index(anchor)].count("\n") + 1
    block = "".join(f"{indent}{line}\n" for line in lines)
    sources[target] = source.replace(anchor, block + anchor)
    sources.update(helpers)
    return sources, set(range(first, first + len(lines)))


@pytest.mark.parametrize("label", sorted(_BYPASSES))
def test_the_scan_turns_red_for_each_bypass(label):
    """Each bypass goes into a COPY of the source, parsed with ``ast``. The real
    file never changes. Each must give a finding ON an inserted line, or in an
    added helper, so it cannot pass on a finding about something else."""
    target, anchor, lines, helpers = _BYPASSES[label]
    sources, inserted = _mutated(target, anchor, lines, helpers)
    problems = _scan(sources)
    hits = [p for p in problems
            if any(p.startswith(f"{target}:{n} ") for n in inserted)
            or any(p.startswith(f"{h}:") for h in helpers)]
    assert hits, (label, problems)


def test_the_bypass_list_has_the_count_the_spec_names():
    """79 until EA-6a, which added B76 to B80 for the body read."""
    assert len(_BYPASSES) == 84


# ── The runtime tripwire ─────────────────────────────────────────────────────

#: Each mutant: the function, its anchor, and the lines inserted (compiled
#: from a string; the real file never changes).
_TRIPWIRE_MUTANTS: dict[str, tuple[str, str, list[str]]] = {
    "request.url.hostname": ("_agent_user", _A_AGENT, ["_h = request.url.hostname"]),
    "request.client": ("_agent_user", _A_AGENT, ["_h = request.client"]),
    "headers.get('host')": ("_agent_user", _A_AGENT, ["_h = request.headers.get('host')"]),
    "headers.get(chr(104)+'ost')": (
        "_agent_user", _A_AGENT, ["_h = request.headers.get(chr(104) + 'ost')"]),
    "an annotated alias": ("_agent_user", _A_AGENT, ["_r: object = request", "_h = _r.url"]),
    "repr(request.scope)": ("_agent_user", _A_AGENT, ["_h = repr(request.scope)"]),
    "dict.get(scope, 'headers')": (
        "_agent_user", _A_AGENT, ["_h = dict.get(request.scope, 'headers')"]),
    "dict.get(scope, 'server')": (
        "_agent_user", _A_AGENT, ["_h = dict.get(request.scope, 'server')"]),
    "request._real": ("_agent_user", _A_AGENT, ["_h = request._real.headers.get('host')"]),
    "request.headers._real": (
        "_agent_user", _A_AGENT, ["_h = request.headers._real.get('host')"]),
    "a host read swallowed by except Exception": (
        "_agent_user", _A_AGENT,
        ["try:", "    _h = request.url.hostname", "except Exception:",
         "    _h = 'SWALLOWED'"]),
    "a host read swallowed by a bare except": (
        "_agent_user", _A_AGENT,
        ["try:", "    _h = request.headers.get('host')", "except:  # noqa: E722",
         "    _h = 'SWALLOWED'"]),
    "_one_header swaps name and falls back": (
        "_one_header", _A_ONE,
        ["_orig = name", "name = 'ho' + 'st'", "try:",
         "    _v = request.headers.getlist(name)",
         "except BaseException:", "    name = _orig"]),
    "scope.get('type') outside matches": (
        "_agent_user", _A_AGENT, ["_h = request.scope.get('type')"]),
    # ── fix round 4 ──
    "3a a str subclass whose lower() changes": (
        "_agent_user", _A_AGENT,
        ["class _Sly(str):",
         "    calls = [0]",
         "    def lower(self):",
         "        self.calls[0] += 1",
         "        return 'x-agent-connection' if self.calls[0] == 1 else 'host'",
         "_h = request.headers.get(_Sly('Host'))"]),
    "3b dict.get on the scope, TypeError caught": (
        "_agent_user", _A_AGENT,
        ["try:", "    _h = dict.get(request.scope, 'server')",
         "except Exception:", "    _h = None"]),
    "3c matches() on a fake receiver, error caught": (
        "_agent_user", _A_AGENT,
        ["import fastapi.routing as _fr",
         "class _Fake:",
         "    pass",
         "try:",
         "    _fr.APIRoute.matches(_Fake(), request.scope)",
         "except Exception:",
         "    pass"]),
    # ── EA-6a: a GET never reads its body, even when the error is caught ──
    "a GET reads the body, error caught": (
        "_agent_user", _A_AGENT,
        ["try:", "    _b = await request.body()", "except BaseException:",
         "    _b = b''"]),
}


def _mutant(target: str, anchor: str, lines: list[str]):
    """*target* with *lines* inserted, compiled in ``deps``' globals. The
    source comes from the module FILE (``_sources``), never ``inspect``."""
    source = _sources()[target]
    assert source.count(anchor) == 1
    indent = anchor[: len(anchor) - len(anchor.lstrip(" "))]
    block = "".join(f"{indent}{line}\n" for line in lines)
    namespace: dict = {}
    exec(compile(source.replace(anchor, block + anchor), f"<mutant {target}>", "exec"),
         vars(deps), namespace)
    return namespace[target]


@pytest.fixture
def tripwire_armed(monkeypatch):
    """Arm the tripwire, and assert at teardown that nothing tripped."""
    from tests.unit import _branch_zero_tripwire as tripwire

    yield tripwire
    tripwire.assert_clean()


def test_the_tripwire_passes_the_real_branch_zero(monkeypatch, tripwire_armed):
    from fastapi.testclient import TestClient

    app, seen = _signed_app(monkeypatch, row_org=ORG, member_org=ORG)
    armed = tripwire_armed.arm(monkeypatch, deps)
    app.dependency_overrides[deps.get_current_user] = (
        tripwire_armed.current_user_override(armed))
    with TestClient(app) as client:
        answer = client.get(_TASK, headers=_signed_headers(ORG))
    assert answer.status_code == 200, answer.text
    assert answer.json()["org"] == ORG
    assert seen == [ORG]


@pytest.mark.parametrize("label", sorted(_TRIPWIRE_MUTANTS))
def test_the_tripwire_catches_a_mutated_branch_zero(monkeypatch, label):
    """The mutant is compiled from a string; the real file never changes. It
    must trip DURING the request or leave a violation for TEARDOWN, even when
    it catches the error and answers normally."""
    from fastapi.testclient import TestClient

    from tests.unit import _branch_zero_tripwire as tripwire

    target, anchor, lines = _TRIPWIRE_MUTANTS[label]
    app, _ = _signed_app(monkeypatch, row_org=ORG, member_org=ORG)
    mutant = _mutant(target, anchor, lines)
    if target == "_agent_user":
        armed = tripwire.arm(monkeypatch, deps, agent_user=mutant)
    else:
        monkeypatch.setattr(deps, target, mutant)
        armed = tripwire.arm(monkeypatch, deps)
    app.dependency_overrides[deps.get_current_user] = tripwire.current_user_override(armed)
    raised: BaseException | None = None
    try:
        with TestClient(app) as client:
            client.get(_TASK, headers=_signed_headers(ORG))
    except BaseException as exc:  # the point: nothing swallows it
        raised = exc
    found = list(tripwire.VIOLATIONS)
    tripwire.VIOLATIONS.clear()
    assert found or isinstance(raised, (TypeError, tripwire.TripwireError)), (
        label, raised)
    if not isinstance(raised, TypeError):
        assert found, (label, "no violation recorded for the teardown check")


def test_a_swallowed_violation_still_fails_the_teardown(monkeypatch):
    """The verifier's mutant: it catches the tripwire error and falls back, so
    the request answers 200. The violation is recorded anyway, and the
    teardown check (``assert_clean``) fails on it."""
    from fastapi.testclient import TestClient

    from tests.unit import _branch_zero_tripwire as tripwire

    app, _ = _signed_app(monkeypatch, row_org=ORG, member_org=ORG)
    mutant = _mutant("_agent_user", _A_AGENT,
                     ["try:", "    _h = request.url.hostname",
                      "except BaseException:", "    _h = 'SWALLOWED'"])
    armed = tripwire.arm(monkeypatch, deps, agent_user=mutant)
    app.dependency_overrides[deps.get_current_user] = tripwire.current_user_override(armed)
    with TestClient(app) as client:
        answer = client.get(_TASK, headers=_signed_headers(ORG))
    assert answer.status_code == 200, "the mutant swallowed the error"
    with pytest.raises(AssertionError, match=r"request.url"):
        tripwire.assert_clean()


def test_the_tripwire_error_is_not_an_exception():
    from tests.unit import _branch_zero_tripwire as tripwire

    assert issubclass(tripwire.TripwireError, BaseException)
    assert not issubclass(tripwire.TripwireError, Exception)


def test_the_real_request_is_not_an_attribute_of_the_wrapper(monkeypatch):
    from starlette.requests import Request

    from tests.unit import _branch_zero_tripwire as tripwire

    real = Request({"type": "http", "method": "GET", "path": "/x", "headers": [
        (b"host", b"other-org.metorite.com")], "query_string": b""})
    wrapped = tripwire.wrap(real)
    for reach in (lambda: wrapped._real, lambda: wrapped.headers._real,
                  lambda: wrapped.scope._real, lambda: vars(wrapped)):
        with pytest.raises((tripwire.TripwireError, TypeError)):
            reach()
    with pytest.raises(TypeError):
        dict.get(wrapped.scope, "client")
    tripwire.VIOLATIONS.clear()


def test_a_route_served_at_two_prefixes_runs_under_the_tripwire(monkeypatch, tripwire_armed):
    """With one route object at two prefixes, ``_effective_template`` asks
    ``context.matches(request.scope)``. That path runs under the tripwire, and
    the matcher reads only keys of ``MATCHES_SCOPE_KEYS``. A read of a
    matches key OUTSIDE the matcher still trips (the mutant
    ``scope.get('type') outside matches``)."""
    from acb_common.db import current_tenant
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.testclient import TestClient
    from gateway.main import TenantScopeMiddleware

    _signed_app(monkeypatch, row_org=ORG, member_org=ORG)  # arms branch 0
    router = APIRouter()

    @router.get("/tasks/{task_id}")
    async def task(task_id: str, user=Depends(deps.get_current_user)):
        return {"email": user.email, "tenant": current_tenant()}

    app = FastAPI()
    app.add_middleware(TenantScopeMiddleware)
    app.include_router(router, prefix="/projects")
    app.include_router(router, prefix="/alt")
    armed = tripwire_armed.arm(monkeypatch, deps)
    app.dependency_overrides[deps.get_current_user] = (
        tripwire_armed.current_user_override(armed))
    with TestClient(app) as client:
        answer = client.get(_TASK, headers=_signed_headers(ORG))
    assert answer.status_code == 200, answer.text
    assert answer.json()["tenant"] == ORG
    matched = {k.split(":", 1)[1] for k in armed.scope_reads if k.startswith("matches:")}
    assert matched, "the two-prefix path did not call matches"
    assert matched <= tripwire_armed.MATCHES_SCOPE_KEYS
    plain = {k for k in armed.scope_reads if not k.startswith("matches:")}
    assert plain == tripwire_armed.ALLOWED_SCOPE_KEYS


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


def test_branch_zero_binds_the_rows_organization_and_no_header_moves_it(
        monkeypatch, tripwire_armed):
    """Host, forwarded and organization headers beside a signed request do
    not move the tenant. It is the organization of the connection row, which
    the member's own resolve agrees with. Branch 0 runs under the tripwire."""
    from fastapi.testclient import TestClient

    app, seen = _signed_app(monkeypatch, row_org=ORG, member_org=ORG)
    armed = tripwire_armed.arm(monkeypatch, deps)
    app.dependency_overrides[deps.get_current_user] = (
        tripwire_armed.current_user_override(armed))
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


def test_a_signed_organization_that_differs_from_the_row_is_refused(
        monkeypatch, tripwire_armed):
    """The row and the member agree on ``_OTHER_ORG``, and the request signs
    ``ORG``. The signed organization must EQUAL the row's, so this is the
    anonymous context, and no route runs. Branch 0 runs under the tripwire."""
    from fastapi.testclient import TestClient

    app, seen = _signed_app(monkeypatch, row_org=_OTHER_ORG, member_org=_OTHER_ORG)
    armed = tripwire_armed.arm(monkeypatch, deps)
    app.dependency_overrides[deps.get_current_user] = (
        tripwire_armed.current_user_override(armed))
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
