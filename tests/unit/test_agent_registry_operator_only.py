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


def _requires(dep_call: object, permission: str) -> bool:
    """True when a ``require_permission(...)`` checker holds *permission*.

    The checker keeps its permissions in the closure cell ``required``.
    """
    for cell in getattr(dep_call, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:  # an empty cell
            continue
        if isinstance(value, tuple) and permission in value:
            return True
    return False


def test_every_route_that_needs_agents_manage_to_write_is_gated() -> None:
    """A new registry writer that skips the gate fails here."""
    writers, bare = [], []
    for route in agent_routes.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (route.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        if not any(_requires(dep.call, "agents:manage") for dep in route.dependant.dependencies):
            continue
        writers.append(route.path)
        if not _gated(route):
            bare.append(f"{sorted(route.methods)} {route.path}")
    # Never vacuous: the walk must see the four registry writers and the five
    # writers of the self-mutation queue.
    assert len(writers) >= 9, writers
    assert not bare, bare


#: Every write route on the agent router that is NOT a registry writer, with
#: why. Anything else that writes must carry the operator gate, whatever
#: permission it names. A new writer fails here until a reviewer places it.
_NOT_REGISTRY_WRITERS = {
    # Run, stream, cancel and answer a running agent. No repo, no path.
    ("POST", "/agent/run"), ("POST", "/agent/run/async"),
    ("POST", "/agent/run/stream"), ("POST", "/agent/run/{thread_id}/cancel"),
    ("POST", "/agent/respond-input"),
    # ⚠️ The self-mutation review queue WAS listed here until 2026-10-19, as
    # "require_internal_auth, a fixed repo". Every BFF call carries that token,
    # so any member could approve a push. The queue now carries the operator
    # gate. Fence: tests/unit/test_mutation_queue_operator_only.py.
    # An inbound event that starts a run of a registered agent.
    ("POST", "/agent/webhook/{source}"),
}


def test_every_other_writer_is_gated_or_named_here() -> None:
    """Closes the gap of a writer gated by another permission name."""
    bare = []
    for route in agent_routes.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            if (method, route.path) in _NOT_REGISTRY_WRITERS or _gated(route):
                continue
            bare.append(f"{method} {route.path}")
    assert not bare, bare


def test_the_user_comes_from_the_server_session() -> None:
    sig = agent_routes._require_registry_operator.__defaults__ or ()
    assert any(getattr(d, "dependency", None) is get_current_user for d in sig)
