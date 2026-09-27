"""BO-2 residual #1 — authentication is the default posture, not an opt-in.

``get_current_user`` never rejects; it *labels*. So before this, every route
was open unless it remembered to add a guard, and the failure mode of opt-in
security is the route nobody opted in. Two of them were found this way:
``/agent/workspace/{session_id}/history`` (anonymous read of an agent's file
history) and ``/promote`` (anonymous write).

The coverage test at the bottom is the point of this file: it fails when a new
route is added without a guard, so the class of bug cannot recur.
"""
from __future__ import annotations

import pytest
from acb_auth import get_current_user, require_authenticated
from acb_auth.access import _degraded, legacy_fallback_enabled
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.unit._routes import served_routes

PUBLIC = frozenset({"/health", "/hooks/{source}"})


def _app(user: UserContext) -> TestClient:
    app = FastAPI(dependencies=[require_authenticated(public=PUBLIC)])

    @app.get("/health")
    async def _health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/hooks/{source}")
    async def _hook(source: str) -> dict[str, str]:
        return {"source": source}

    @app.get("/private")
    async def _private() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/items/{item_id}")
    async def _item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


ANON = UserContext(email=None, role=UserRole.EMPLOYEE)
MEMBER = UserContext(email="a@fracktal.in", role=UserRole.EMPLOYEE)
SERVICE = UserContext(email="system:internal", role=UserRole.AGENT)


# ── The guard ───────────────────────────────────────────────────────────────

def test_anonymous_is_refused_by_default() -> None:
    assert _app(ANON).get("/private").status_code == 401


def test_a_member_passes() -> None:
    assert _app(MEMBER).get("/private").status_code == 200


def test_the_service_principal_passes() -> None:
    assert _app(SERVICE).get("/private").status_code == 200


def test_public_routes_stay_open_to_anonymous() -> None:
    client = _app(ANON)
    assert client.get("/health").status_code == 200
    assert client.post("/hooks/clickup").status_code == 200


def test_a_new_route_is_covered_without_opting_in() -> None:
    """The whole reason the guard is app-level rather than per-route."""
    assert _app(ANON).get("/items/anything").status_code == 401


def test_path_parameters_cannot_impersonate_a_public_route() -> None:
    """Matching is on the route TEMPLATE, never the concrete URL."""
    assert _app(ANON).get("/items/health").status_code == 401


def test_authentication_is_not_authorization() -> None:
    """The guard only asks "who are you", never "may you".

    A member with an empty permission set still passes it — the permission
    checks are separate and run after.
    """
    bare = UserContext(
        email="a@fracktal.in", role=UserRole.EMPLOYEE,
        access=EffectiveAccess(is_active=True),
    )
    assert _app(bare).get("/private").status_code == 200


# ── Degraded resolution ─────────────────────────────────────────────────────

def test_degraded_access_fails_closed_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    assert legacy_fallback_enabled() is False
    degraded = _degraded("executive")
    assert not degraded.is_active
    assert degraded.allowed_features() == ()


def test_degraded_access_can_be_reopened_for_recovery(monkeypatch) -> None:
    """The hatch for a failed migration — on, fix, off."""
    monkeypatch.setenv("ACCESS_LEGACY_FALLBACK", "1")
    assert legacy_fallback_enabled() is True
    assert "chat" in _degraded("executive").allowed_features()


# ── Coverage over the real app ──────────────────────────────────────────────

def _main():
    """The already-assembled gateway app.

    Deliberately does NOT re-import under a different ACB_ENV: the module is
    cached by the time the suite reaches here, so a re-import would silently
    return whatever env the first importer used. The env-dependent bit is
    tested through `docs_enabled` instead.
    """
    import gateway.main as main

    return main


#: FastAPI's own docs endpoints. They carry no dependency chain by
#: construction, so they cannot be guarded — they are disabled outside dev
#: instead, which `test_docs_are_dev_only` covers.
_BUILTIN_DOCS = {"swagger_ui_html", "redoc_html", "openapi", "swagger_ui_redirect"}


def _is_guarded(route) -> bool:
    for dep in getattr(route, "dependencies", []):
        fn = getattr(dep, "dependency", None)
        if (
            getattr(fn, "__module__", "") == "acb_auth.deps"
            and getattr(fn, "__qualname__", "").startswith("require_authenticated")
        ):
            return True
    return False


def test_every_gateway_route_is_behind_the_default_deny_guard() -> None:
    """No route escapes authentication — including ones added later.

    If this fails, a router was included in a way that bypasses the app-level
    dependency. Fix the wiring; do not add an exception here. Genuinely public
    endpoints belong in ``gateway.main.PUBLIC_ROUTES``, which this guard reads.
    """
    main = _main()
    routes = [
        r for r in served_routes(main.app.routes)
        if getattr(r, "methods", None)
        and getattr(getattr(r, "endpoint", None), "__name__", "") not in _BUILTIN_DOCS
    ]
    assert routes, "no routes discovered — the app failed to assemble"

    unguarded = [
        (sorted(r.methods)[0], r.path) for r in routes if not _is_guarded(r)
    ]
    assert not unguarded, (
        f"{len(unguarded)} route(s) bypass require_authenticated: {unguarded}"
    )


def test_docs_are_dev_only() -> None:
    """Swagger/ReDoc publish the whole API surface and cannot carry a guard."""
    main = _main()
    assert main.docs_enabled("dev") is True
    assert main.docs_enabled("staging") is False
    assert main.docs_enabled("prod") is False


def test_public_routes_all_exist_and_are_deliberate() -> None:
    """A typo'd public route is dead text; a stale one is a hole left open."""
    main = _main()
    live = {getattr(r, "path", "") for r in served_routes(main.app.routes)}
    missing = main.PUBLIC_ROUTES - live
    assert not missing, f"PUBLIC_ROUTES entries match no live route: {missing}"


@pytest.mark.parametrize(
    "path",
    [
        "/agent/workspace/{session_id}/history",
        "/agent/workspace/{session_id}/promote",
        "/health/runtime",
        "/copilot/models",
    ],
)
def test_previously_anonymous_routes_are_no_longer_public(path: str) -> None:
    """The holes this change closed. They must not drift back into PUBLIC_ROUTES.

    The workspace pair is the sharp one: `history` read an agent's full file
    version history and `promote` wrote to the workspace, both with no caller
    identity at all.
    """
    main = _main()
    assert path not in main.PUBLIC_ROUTES
