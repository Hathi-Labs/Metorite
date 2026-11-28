"""The gate of each /observability and /agent route (task 4110, HIGH, auth).

Before task 4110, the six live views of ``/observability``, the avatar writes,
``GET /agent/config`` and ``GET /agent/{name}/skills`` checked only that the
caller was signed in. ``GET /agent`` gave every member the server path and
the install health of each agent. Spec: ``project-docs/specs/org_access_control.md``
§5, the two rows of the agent registry and of observability.

**Acceptance.**

1. A member without ``feature:observability`` gets 403 on each of the six
   live views. A holder gets 200. An internal token with no user headers
   passes.
2. A guest that holds only ``feature:chat`` gets 200 on
   ``GET /observability/avatars`` and on ``GET /agent``.
3. A member without ``feature:agents`` gets 403 on ``GET /agent/{name}/skills``
   and on the avatar PUT, DELETE and generate.
4. A member without ``agents:manage``, or with an org outside
   ``AGENT_REGISTRY_ORGS``, gets 403 on ``GET /agent/config``, also for an
   absolute path. The handler does not run.
5. ``GET /agent`` for a caller without ``agents:manage`` has no
   ``local_path`` key and no ``dep_status`` key. A holder gets both.
6. ``ROUTE_GATES`` names each (method, path) that the gateway serves under
   ``/observability`` and ``/agent``, with its gate or a KEEP OPEN reason.
   A route that is not in the map fails, and a gate that differs fails.

**The mutations (R7).** Each ran on 2026-11-27, and each test named here
went red.

* M1: remove the ``dependencies=[require_feature_router(...)]`` of the
  observability router. 19 tests went red:
  ``test_each_route_has_the_gate_of_the_map`` (6 routes),
  ``test_a_member_without_observability_gets_403`` (12) and
  ``test_the_exempt_list_names_only_the_avatar_templates``.
* M2: remove ``dependencies=[require_permission("agents:manage"),
  Depends(_require_registry_operator)]`` from ``GET /agent/config``. Red:
  ``test_each_route_has_the_gate_of_the_map`` and the 6 cases of
  ``test_agent_config_refuses_before_the_handler``.
* M3: remove the trim of ``MANAGER_ONLY_AGENT_KEYS`` in ``list_agents``. Red:
  ``test_list_agents_drops_the_manager_keys_for_a_member``.
* M4: add ``GET /observability/ping`` with no row here. Red:
  ``test_every_served_route_is_in_the_map``.
"""

from __future__ import annotations

import inspect
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import agent as agent_routes
from gateway.routes import agent_skills
from gateway.routes import observability as obs

from tests.unit._routes import served_routes

ORG = str(uuid.uuid4())
OTHER_ORG = str(uuid.uuid4())

#: The KEEP OPEN reason of each run route of the chat transport. Each one
#: checks the member in its handler (``assert_can_run_agent_in_session`` or
#: the owner of the thread).
_RUN = "KEEP OPEN: the chat transport. The handler checks the agent or the thread owner."
#: The KEEP OPEN reason of each workspace route. Each one checks that the
#: session belongs to the member in its handler (``routes/workspace.py``).
_WS = "KEEP OPEN: the chat workspace. The handler checks the owner of the session."

#: Each (method, path) under /observability and /agent, with its gate. A gate
#: is the sorted tuple of the permissions and the named guards that the
#: dependant walk finds. A string that starts with "KEEP OPEN" means that the
#: walk finds no gate, on purpose, for the reason that the string gives.
ROUTE_GATES: dict[tuple[str, str], tuple[str, ...] | str] = {
    # ── /observability (task 4110) ──
    ("GET", "/observability/activity/recent"): ("feature:observability",),
    ("GET", "/observability/activity/stream"): ("feature:observability",),
    ("GET", "/observability/active"): ("feature:observability",),
    ("GET", "/observability/roster"): ("feature:observability",),
    ("GET", "/observability/cost"): ("feature:observability",),
    ("GET", "/observability/runs"): ("feature:observability",),
    ("GET", "/observability/avatars"): (
        "KEEP OPEN: the chat draws each agent with its avatar, for every member."
    ),
    ("PUT", "/observability/avatars/{name}"): ("feature:agents",),
    ("DELETE", "/observability/avatars/{name}"): ("feature:agents",),
    ("POST", "/observability/avatars/generate"): ("feature:agents",),
    # ── /agent, the registry ──
    ("GET", "/agent"): (
        "KEEP OPEN: the chat agent picker. The handler lists only the agents "
        "that the member may run, and drops MANAGER_ONLY_AGENT_KEYS."
    ),
    ("GET", "/agent/config"): ("_require_registry_operator", "agents:manage"),
    ("POST", "/agent"): ("_require_registry_operator", "agents:manage"),
    ("POST", "/agent/{name}/pull"): ("_require_registry_operator", "agents:manage"),
    ("DELETE", "/agent/{name}"): ("_require_registry_operator", "agents:manage"),
    ("PATCH", "/agent/{name}"): ("_require_registry_operator", "agents:manage"),
    ("GET", "/agent/{name}/skills"): ("feature:agents",),
    ("PUT", "/agent/{name}/skills"): ("admin:access:manage",),
    # ── /agent, the chat transport ──
    ("POST", "/agent/run/stream"): _RUN,
    ("POST", "/agent/respond-input"): _RUN,
    ("GET", "/agent/run/{thread_id}/reconnect"): _RUN,
    ("POST", "/agent/run/{thread_id}/cancel"): _RUN,
    ("POST", "/agent/run"): _RUN,
    ("POST", "/agent/run/async"): _RUN,
    ("GET", "/agent/run/{run_id}/status"): (
        "KEEP OPEN: the status of a run. The read binds the tenant of the member (RLS)."
    ),
    ("POST", "/agent/webhook/{source}"): (
        "KEEP OPEN: in PUBLIC_ROUTES. The HMAC signature of the body is the authorization."
    ),
    # ── /agent/mutations, the self-mutation queue ──
    ("GET", "/agent/mutations"): ("_require_mutation_reviewer", "agents:manage"),
    ("GET", "/agent/mutations/pending"): ("_require_mutation_reviewer", "agents:manage"),
    ("GET", "/agent/mutations/pending/{commit_id}/diff"): (
        "_require_mutation_reviewer", "agents:manage",
    ),
    ("POST", "/agent/mutations/pending/{commit_id}/approve"): (
        "_require_mutation_reviewer", "agents:manage", "require_internal_auth",
    ),
    ("POST", "/agent/mutations/pending/{commit_id}/reject"): (
        "_require_mutation_reviewer", "agents:manage", "require_internal_auth",
    ),
    ("POST", "/agent/mutations/pending/{commit_id}/remutate"): (
        "_require_mutation_reviewer", "agents:manage", "require_internal_auth",
    ),
    ("DELETE", "/agent/mutations/pending/{commit_id}"): (
        "_require_mutation_reviewer", "agents:manage", "require_internal_auth",
    ),
    ("DELETE", "/agent/mutations/audit/{run_id}"): (
        "_require_mutation_reviewer", "agents:manage", "require_internal_auth",
    ),
    # ── /agent/workspace and /agent/artifacts (routes/workspace.py) ──
    ("GET", "/agent/workspace/{session_id}"): _WS,
    ("PATCH", "/agent/workspace/{session_id}"): _WS,
    ("GET", "/agent/workspace/{session_id}/file"): _WS,
    ("PUT", "/agent/workspace/{session_id}/file"): _WS,
    ("DELETE", "/agent/workspace/{session_id}/file"): _WS,
    ("GET", "/agent/workspace/{session_id}/events"): _WS,
    ("POST", "/agent/workspace/{session_id}/events"): _WS,
    ("POST", "/agent/workspace/{session_id}/upload"): _WS,
    ("POST", "/agent/workspace/{session_id}/promote"): _WS,
    ("GET", "/agent/workspace/{session_id}/history"): _WS,
    ("POST", "/agent/artifacts/upload"): _WS,
    ("GET", "/agent/artifacts"): _WS,
    ("GET", "/agent/artifacts/file"): _WS,
    ("PUT", "/agent/artifacts/file"): _WS,
}

# ── The dependant walk ─────────────────────────────────────────────────────


def _gates_of_call(call: Any, path: str) -> list[str]:
    """The gates that one dependency holds, for the route at ``path``.

    ``permission`` is the closure of ``require_feature_router``. Its
    ``exempt_paths`` skip it, so an exempt template carries no gate.
    ``required`` is the closure of ``require_permission``. A function whose
    name starts with ``require`` or ``_require`` is a named guard. A role
    check (``require_role``) admits every role here, so it is not a gate.
    """
    found: list[str] = []
    try:
        names = inspect.getclosurevars(call).nonlocals
    except (TypeError, ValueError):
        names = {}
    exempt = names.get("exempt_paths") or frozenset()
    if isinstance(names.get("permission"), str) and path not in exempt:
        found.append(names["permission"])
    if isinstance(names.get("required"), tuple):
        found += [p for p in names["required"] if isinstance(p, str)]
    name = getattr(call, "__name__", "")
    if name.startswith(("_require", "require")) and name != "_check":
        found.append(name)
    return found


def _gates_of(context: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    stack = list(context.original_route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            seen.update(_gates_of_call(dep.call, context.path))
        stack.extend(dep.dependencies)
    return tuple(sorted(seen))


def _in_scope(path: str) -> bool:
    return path.startswith("/observability") or path == "/agent" or path.startswith("/agent/")


@pytest.fixture(scope="module")
def served() -> dict[tuple[str, str], Any]:
    from gateway import main

    out: dict[tuple[str, str], Any] = {}
    for context in served_routes(main.app.routes):
        path = getattr(context, "path", "")
        if not _in_scope(path):
            continue
        for method in getattr(context, "methods", None) or ():
            out[(method, path)] = context
    return out


def test_every_served_route_is_in_the_map(served: dict[tuple[str, str], Any]) -> None:
    missing = sorted(set(served) - set(ROUTE_GATES))
    assert not missing, (
        "A route under /observability or /agent has no row in ROUTE_GATES. "
        f"Add it, with its gate or a KEEP OPEN reason: {missing}"
    )
    stale = sorted(set(ROUTE_GATES) - set(served))
    assert not stale, f"ROUTE_GATES names a route that the gateway does not serve: {stale}"


@pytest.mark.parametrize("key", sorted(ROUTE_GATES), ids=lambda k: f"{k[0]} {k[1]}")
def test_each_route_has_the_gate_of_the_map(
    served: dict[tuple[str, str], Any], key: tuple[str, str],
) -> None:
    expected = ROUTE_GATES[key]
    got = _gates_of(served[key])
    if isinstance(expected, str):
        assert expected.startswith("KEEP OPEN: ") and len(expected) > 20, key
        assert got == (), (key, got)
    else:
        assert got == expected, (key, got)


# ── The behaviour, through the real routers ────────────────────────────────

LIVE_VIEWS = (
    "/observability/activity/recent",
    "/observability/activity/stream",
    "/observability/active",
    "/observability/roster",
    "/observability/cost",
    "/observability/runs",
)


def _member(grants: list[str], org: str | None = ORG, email: str = "ana@example.test") -> UserContext:
    return UserContext(
        email=email,
        role=UserRole.EMPLOYEE,
        organization_id=org,
        access=build_access(grants, []),
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(obs.router)
    app.include_router(agent_routes.router)
    app.include_router(agent_skills.router)
    return app


def _client(user: UserContext | None) -> TestClient:
    app = _app()
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def reached(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each reader of the routes under test records its name and answers empty."""
    calls: list[str] = []

    def _async(name: str, value: Any) -> Any:
        async def _fn(*_a: Any, **_k: Any) -> Any:
            calls.append(name)
            return value

        return _fn

    async def _sse(*_a: Any, **_k: Any) -> Any:
        calls.append("sse")
        yield b": connected\n\n"

    class _NoDb:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            calls.append("runs.db")

        def __enter__(self) -> Any:
            raise RuntimeError("no database here")

        def __exit__(self, *_a: Any) -> bool:
            return False

    import acb_graph

    monkeypatch.setattr(obs, "recent_activity", _async("recent", []))
    monkeypatch.setattr(obs, "active_runs", _async("active", []))
    monkeypatch.setattr(obs, "cost_summary", _async("cost", {"totals": {}}))
    monkeypatch.setattr(obs, "_sse_activity", _sse)
    monkeypatch.setattr(obs, "_load_avatars", lambda *_a, **_k: calls.append("avatars") or {})
    monkeypatch.setattr(obs, "_save_avatar", lambda *_a, **_k: calls.append("save"))
    monkeypatch.setattr(obs, "_delete_avatar", lambda *_a, **_k: calls.append("delete"))
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDb)
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda *_a, **_k: [])
    monkeypatch.setattr(agent_routes, "_load_agent_aliases", lambda *_a, **_k: {})
    monkeypatch.setattr(agent_skills, "_require_known_agent", lambda *_a: calls.append("skills"))
    return calls


@pytest.mark.parametrize("path", LIVE_VIEWS)
@pytest.mark.parametrize(
    "grants", [["feature:chat"], ["feature:agents", "feature:chat"]], ids=["guest", "agents"],
)
def test_a_member_without_observability_gets_403(
    reached: list[str], path: str, grants: list[str],
) -> None:
    r = _client(_member(grants)).get(path)
    assert r.status_code == 403, (path, r.status_code)
    assert "feature:observability" in r.json()["detail"]
    assert reached == []


@pytest.mark.parametrize("path", LIVE_VIEWS)
def test_a_holder_of_observability_gets_200(reached: list[str], path: str) -> None:
    r = _client(_member(["feature:observability"])).get(path)
    assert r.status_code == 200, (path, r.status_code, r.text)
    assert reached, path


@pytest.mark.parametrize("path", LIVE_VIEWS)
def test_an_internal_token_with_no_user_headers_passes(
    monkeypatch: pytest.MonkeyPatch, reached: list[str], path: str,
) -> None:
    token = "t4110-internal-token"
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", token)
    r = _client(None).get(path, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, (path, r.status_code, r.text)


def test_a_guest_with_chat_only_reads_the_avatars_and_the_agent_list(
    reached: list[str],
) -> None:
    client = _client(_member(["feature:chat"]))
    r = client.get("/observability/avatars")
    assert r.status_code == 200, r.text
    assert r.json() == {"avatars": {}}
    r = client.get("/agent")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


_AGENTS_ROUTES = (
    ("GET", "/agent/task-manager/skills", None),
    ("PUT", "/observability/avatars/task-manager", {"config": {"hue": 1}}),
    ("DELETE", "/observability/avatars/task-manager", None),
    ("POST", "/observability/avatars/generate", {"description": "a robot"}),
)


@pytest.mark.parametrize(("method", "path", "body"), _AGENTS_ROUTES)
def test_a_member_without_agents_gets_403(
    reached: list[str], method: str, path: str, body: Any,
) -> None:
    client = _client(_member(["feature:chat", "feature:observability"]))
    r = client.request(method, path, json=body)
    assert r.status_code == 403, (method, path, r.status_code)
    assert "feature:agents" in r.json()["detail"]
    assert reached == []


@pytest.mark.parametrize(("method", "path", "body"), _AGENTS_ROUTES)
def test_a_holder_of_agents_passes_the_gate(
    monkeypatch: pytest.MonkeyPatch,
    reached: list[str], method: str, path: str, body: Any,
) -> None:
    monkeypatch.delenv("PIXELLAB_API_KEY", raising=False)

    class _Db:
        async def __aenter__(self) -> Any:
            reached.append("skills.db")
            raise RuntimeError("no database here")

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(agent_skills, "_tenant_session", _Db)
    client = _client(_member(["feature:agents"]))
    r = client.request(method, path, json=body)
    assert r.status_code != 403, (method, path, r.text)
    if path.endswith("/generate"):
        assert r.status_code == 503  # the handler ran, and Pixel Lab is not set
    else:
        assert reached, (method, path)


# ── GET /agent/config ──────────────────────────────────────────────────────


@pytest.fixture
def config_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[list[str], str]:
    """A repo directory on disk, and a trip on each read that the handler makes."""
    (tmp_path / "config.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    calls: list[str] = []
    real_path = agent_routes.Path

    def _path(*a: Any, **k: Any) -> Any:
        calls.append("Path")
        return real_path(*a, **k)

    async def _token() -> str:
        calls.append("github_token")
        raise RuntimeError("the handler ran, and no GitHub call may follow")

    monkeypatch.setattr(agent_routes, "Path", _path)
    monkeypatch.setattr(agent_routes, "_org_github_token", _token)
    return calls, str(tmp_path.resolve())


@pytest.mark.parametrize(
    ("grants", "org", "registry"),
    [
        (["feature:agents"], ORG, ORG),          # no agents:manage
        (["agents:manage"], OTHER_ORG, ORG),     # outside AGENT_REGISTRY_ORGS
        (["agents:manage"], ORG, ""),            # the empty list admits no org
    ],
    ids=["no-manage", "other-org", "empty-list"],
)
@pytest.mark.parametrize("repo", ["absolute", "owner/repo"])
def test_agent_config_refuses_before_the_handler(
    monkeypatch: pytest.MonkeyPatch,
    config_trip: tuple[list[str], str],
    grants: list[str], org: str, registry: str, repo: str,
) -> None:
    calls, repo_dir = config_trip
    monkeypatch.setattr(get_settings(), "agent_registry_orgs", registry, raising=False)
    value = repo_dir if repo == "absolute" else "octo/agent"
    r = _client(_member(grants, org=org)).get("/agent/config", params={"repo": value})
    assert r.status_code == 403, (r.status_code, r.text)
    assert calls == []


def test_an_operator_reads_the_config(
    monkeypatch: pytest.MonkeyPatch, config_trip: tuple[list[str], str],
) -> None:
    calls, repo_dir = config_trip
    monkeypatch.setattr(get_settings(), "agent_registry_orgs", ORG, raising=False)
    r = _client(_member(["agents:manage"])).get("/agent/config", params={"repo": repo_dir})
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "x"}
    assert calls == ["Path"]


# ── GET /agent, the trim ───────────────────────────────────────────────────


@pytest.fixture
def agent_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """One static and one dynamic agent, each with both manager keys."""
    static = {"name": "agent-alpha", "description": "a", "agent_runtime": "maf",
              "local_path": "apps/agents/agent-alpha", "integrations": [],
              "optional_integrations": [], "tags": [], "status": "active"}
    dynamic = {"name": "agent-beta", "description": "b", "agent_runtime": "maf",
               "local_path": "/srv/secret/agent-beta", "repo_url": "https://x/y",
               "repo_name": "y", "integrations": [], "optional_integrations": [],
               "tags": [], "status": "active"}
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [static])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda *_a, **_k: [dynamic])
    monkeypatch.setattr(agent_routes, "_load_agent_aliases", lambda *_a, **_k: {})
    monkeypatch.setattr(agent_routes, "_declared_runtime", lambda *_a, **_k: None)

    import acb_skills.loader as loader
    from gateway.routes import workspace

    monkeypatch.setattr(workspace, "_agent_workspace_dir", lambda name: Path("/tmp") / name)
    monkeypatch.setattr(loader, "read_dep_status", lambda _ws: {"ok": False, "missing": ["x"]})


_CHAT_KEYS = ("name", "display_name", "description", "tags", "status", "agent_runtime",
              "integrations", "optional_integrations", "tier_routed")


def test_list_agents_drops_the_manager_keys_for_a_member(agent_list: None) -> None:
    member = _member(["feature:chat", "agents:run:*"])
    r = _client(member).get("/agent")
    assert r.status_code == 200, r.text
    items = r.json()
    assert {a["name"] for a in items} == {"agent-alpha", "agent-beta"}
    for item in items:
        assert "local_path" not in item, item
        assert "dep_status" not in item, item
        for key in _CHAT_KEYS:
            assert key in item, (key, item)
    beta = next(a for a in items if a["name"] == "agent-beta")
    assert beta["repo_url"] == "https://x/y" and beta["repo_name"] == "y"
    # The trim made new dicts. The shared registry entry keeps its path.
    assert agent_routes._AGENT_REGISTRY[0]["local_path"] == "apps/agents/agent-alpha"


def test_list_agents_keeps_the_manager_keys_for_a_manager(agent_list: None) -> None:
    r = _client(_member(["agents:manage"])).get("/agent")
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 2
    for item in items:
        assert item["local_path"], item
        assert item["dep_status"] == {"ok": False, "missing": ["x"]}, item


def test_the_manager_keys_are_the_two_of_the_spec() -> None:
    expected = frozenset({"local_path", "dep_status"})
    assert expected == agent_routes.MANAGER_ONLY_AGENT_KEYS


def test_the_exempt_list_names_only_the_avatar_templates() -> None:
    """The router gate skips the three avatar templates and nothing else."""
    dep = obs.router.dependencies[0].dependency
    exempt = inspect.getclosurevars(dep).nonlocals["exempt_paths"]
    assert exempt == frozenset(obs.AVATAR_TEMPLATES)
    assert all(re.fullmatch(r"/observability/avatars(/\{name\}|/generate)?", p) for p in exempt)
