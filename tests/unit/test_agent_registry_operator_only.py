"""Only the platform operator changes the agent registry (2026-10-11).

Found in the WS-54 IN-7 review: every org's owner and admin hold
``agents:manage``. ``POST /agent`` then cloned a repo the caller named with the
operator's ``GITHUB_TOKEN``, or read a path on the server, and the code ran in
the shared gateway. ``AGENT_REGISTRY_ORGS`` now names the orgs that may
register, pull, delete or patch. Empty means none.
"""

from __future__ import annotations

import asyncio

import pytest
from acb_auth import get_current_user
from acb_common import get_settings
from fastapi import HTTPException
from fastapi.routing import APIRoute
from gateway.routes import agent as agent_routes

OPERATOR = "11111111-1111-1111-1111-111111111111"
CUSTOMER = "22222222-2222-2222-2222-222222222222"


class _User:
    def __init__(self, org: str | None) -> None:
        self.organization_id = org


def _gate(org: str | None) -> None:
    asyncio.run(agent_routes._require_registry_operator(_User(org)))  # type: ignore[arg-type]


@pytest.fixture
def registry_orgs(monkeypatch: pytest.MonkeyPatch):
    def _set(value: str) -> None:
        monkeypatch.setattr(get_settings(), "agent_registry_orgs", value, raising=False)

    return _set


def test_the_default_admits_no_organization(registry_orgs) -> None:
    registry_orgs("")
    for org in (OPERATOR, CUSTOMER, None):
        with pytest.raises(HTTPException) as caught:
            _gate(org)
        assert caught.value.status_code == 403


def test_only_a_listed_organization_passes(registry_orgs) -> None:
    registry_orgs(f" {OPERATOR.upper()} ")
    _gate(OPERATOR)
    with pytest.raises(HTTPException):
        _gate(CUSTOMER)
    with pytest.raises(HTTPException):
        _gate(None)


def test_a_star_admits_every_organization_but_not_a_missing_one(registry_orgs) -> None:
    registry_orgs("*")
    _gate(CUSTOMER)
    with pytest.raises(HTTPException):
        _gate(None)


def _gated(route: APIRoute) -> bool:
    return any(
        getattr(dep, "call", None) is agent_routes._require_registry_operator
        for dep in route.dependant.dependencies
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", ""),
        ("POST", "/{name}/pull"),
        ("DELETE", "/{name}"),
        ("PATCH", "/{name}"),
    ],
)
def test_each_registry_writer_carries_the_operator_gate(method: str, path: str) -> None:
    routes = [
        r for r in agent_routes.router.routes
        if isinstance(r, APIRoute) and method in r.methods
        and r.path.endswith(path) and r.path.count("/") == (agent_routes.router.prefix + path).count("/")
    ]
    assert routes, (method, path)
    assert all(_gated(r) for r in routes), (method, path)


def test_every_route_that_needs_agents_manage_to_write_is_gated() -> None:
    """A new registry writer that skips the gate fails here."""
    bare = []
    for route in agent_routes.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (route.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        needs_manage = any(
            "agents:manage" in repr(getattr(dep.call, "__closure__", None) or "")
            or "agents:manage" in repr(getattr(dep.call, "__defaults__", None) or "")
            or "agents:manage" in str(getattr(dep.call, "permission", ""))
            for dep in route.dependant.dependencies
        )
        if needs_manage and not _gated(route):
            bare.append(f"{sorted(route.methods)} {route.path}")
    assert not bare, bare


def test_the_user_comes_from_the_server_session() -> None:
    sig = agent_routes._require_registry_operator.__defaults__ or ()
    assert any(getattr(d, "dependency", None) is get_current_user for d in sig)
