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

    ``get_current_user`` is the ONE place a request binds its tenant (H2). Its
    whole parameter list is three headers, none of them host-shaped: there is no
    ``Request``, no ``Host``, no ``X-Forwarded-Host``, so there is nothing for a
    future edit to *read* without first widening this signature — which reddens
    here, at the place the rule is written down.
    """
    import inspect

    params = set(inspect.signature(deps.get_current_user).parameters)
    assert params == {"x_user_email", "x_user_role", "authorization"}
    for banned in ("host", "x_forwarded_host", "request", "origin", "referer"):
        assert banned not in params


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
