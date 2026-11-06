"""The gate of every AI Studio route (STU-J1-1), the hermetic half.

Spec: ``project-docs/specs/ai_studio.md`` §9 and §14 J1, and the owner's
answers STU-Q4 and STU-Q4b (§12). Code: the one router of
``gateway/routes/studio/actions.py``, which carries
``require_feature_router("studio")``. ``invoke.py``, ``packages.py`` and
``skills.py`` register their routes on it. The R8 half, with the real role
bundles of ``provision_org_roles`` and the real ``resolve_access``, is
``test_studio_route_gate_r8.py``.

**Acceptance (J1-1).**

a. A member without ``feature:studio`` gets 403 on each Studio route.
b. The interim gate of B1 is gone. A member with only ``feature:workflows``
   gets 403 on each route, when ``STUDIO_ORGS`` holds the org and when it
   does not. So the 403 of the gate comes before the 404 of the switch.
c. A holder of ``feature:studio`` passes the gate on each route. With the
   org outside ``STUDIO_ORGS``, each route answers 404, and no handler reads
   a store.
d. The router carries one feature gate, ``feature:studio``. No route adds a
   second gate, and no route reads ``studio:publish`` (J2 owns that check).
e. ``EXPECTED_ROUTES`` pins the routes. A new route fails here until a person
   adds it, and so adds its 403 case.
f. No file of ``routes/studio`` names the ``workflows`` gate.
g. In a chat, a Studio tool that a member without the feature runs gets the
   refusal text, and the handler never runs.
h. The R8 half is in the ``--must-run`` list of the CI skip guard.

**The mutations (R7).** Each ran on 2026-11-05, and each test named here
went red.

* M1: in ``actions.py``, restore ``require_feature_router("workflows")``.
  88 tests went red: ``test_a_workflows_member_gets_403_on_each_route`` (28),
  ``test_a_member_without_studio_gets_403_on_each_route`` (28, the two
  override cases), ``test_a_studio_holder_passes_the_gate_on_each_route``
  (14), ``test_no_route_adds_a_second_gate_or_the_share_verb`` (14),
  ``test_the_router_carries_one_feature_gate_and_it_is_studio``,
  ``test_no_studio_file_names_the_workflows_gate`` and both chat tests. The
  R8 half went red too (its docstring names the tests).
* M2: add ``GET /studio/ping`` to the router with no case here. Red:
  ``test_expected_routes_are_the_routes_of_the_router``.
* M3: add ``dependencies=[require_permission("studio:publish")]`` to the
  publish route. Red: ``test_no_route_adds_a_second_gate_or_the_share_verb``
  and ``test_a_studio_holder_passes_the_gate_on_each_route`` (the
  ``feature:studio`` and ``feature:*`` holders) on that route, and
  ``test_the_share_verb_is_a_capability_that_no_studio_route_reads``.
* M5: remove ``"studio"`` from ``FEATURES`` and ``"studio:publish"`` from
  ``CAPABILITIES``. Red: ``test_the_share_verb_is_a_capability_that_no_studio_route_reads``,
  and the pins in ``test_org_access_control.py`` and
  ``test_auth_me_features.py``.
"""

from __future__ import annotations

import inspect
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_auth.permissions import CAPABILITIES, FEATURES
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from gateway.routes import studio as studio_pkg
from gateway.routes.studio import actions

_ROOT = Path(__file__).resolve().parents[2]
_STUDIO_DIR = _ROOT / "apps/services/gateway/gateway/routes/studio"

ORG = str(uuid.uuid4())
ANA = "ana@example.test"

#: Every route of the Studio router, as ``(method, path template)``.
#: ⚠️ A route that you add to the router fails
#: ``test_expected_routes_are_the_routes_of_the_router`` until you add it
#: here. The parametrized 403 tests then cover it.
EXPECTED_ROUTES: tuple[tuple[str, str], ...] = (
    # B1, the action catalog.
    ("GET", "/studio/actions"),
    # F2a, the skill routes.
    ("GET", "/studio/skills"),
    ("POST", "/studio/skills"),
    ("GET", "/studio/skills/{skill_id}"),
    ("GET", "/studio/skills/{skill_id}/versions/{version}"),
    ("POST", "/studio/skills/{skill_id}/versions"),
    ("PATCH", "/studio/skills/{skill_id}/state"),
    # A5, the package routes.
    ("POST", "/studio/packages"),
    ("GET", "/studio/packages"),
    ("GET", "/studio/packages/{package_id}"),
    ("PUT", "/studio/packages/{package_id}/draft"),
    ("POST", "/studio/packages/{package_id}/versions"),
    ("GET", "/studio/packages/{package_id}/versions"),
    # A5c-1, the package delete.
    ("GET", "/studio/packages/{package_id}/delete-preview"),
    ("DELETE", "/studio/packages/{package_id}"),
    # B2a to B2c, the action door.
    ("POST", "/studio/apps/{app_id}/actions/{action}"),
)

_PARAMS = {
    "skill_id": str(uuid.uuid4()),
    "package_id": str(uuid.uuid4()),
    "app_id": str(uuid.uuid4()),
    "version": "1",
    "action": "peek",
}

STUDIO = ["feature:studio"]
WORKFLOWS_ONLY = ["feature:workflows"]
GUEST = ["feature:chat", "apps:use:*"]


def _router_routes() -> list[tuple[str, str]]:
    return sorted((m, r.path) for r in actions.router.routes
                  if isinstance(r, APIRoute) for m in r.methods)


def _url(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: _PARAMS[m.group(1)], path)


def _client(grants: list[str], overrides: list[tuple[str, str]] | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)

    def _user() -> UserContext:
        return UserContext(email=ANA, role=UserRole.EMPLOYEE, organization_id=ORG,
                           access=build_access(grants, overrides or []))

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app, raise_server_exceptions=False)


def _call(client: TestClient, method: str, path: str) -> httpx.Response:
    body = {} if method in ("POST", "PUT", "PATCH") else None
    return client.request(method, _url(path), json=body)


@pytest.fixture
def no_store(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each store and session that a handler can reach records a call. A
    test that expects no handler asserts that the list stays empty."""
    reached: list[str] = []

    def _trip(name: str) -> Any:
        def _fn(*_a: Any, **_k: Any) -> Any:
            reached.append(name)
            raise RuntimeError(f"{name} ran")
        return _fn

    from gateway.routes.studio import invoke, packages, skills

    for module in (actions, invoke, packages, skills):
        if hasattr(module, "_tenant_session"):
            monkeypatch.setattr(module, "_tenant_session", _trip(f"{module.__name__}.session"))
    monkeypatch.setattr(actions, "catalog_rows", _trip("catalog_rows"))
    monkeypatch.setattr(actions, "package_rows", _trip("package_rows"))
    return reached


def _open(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", value)


# ── e. The pinned routes ───────────────────────────────────────────────────


def test_expected_routes_are_the_routes_of_the_router() -> None:
    assert _router_routes() == sorted(EXPECTED_ROUTES), (
        "The Studio router changed. Add the new route to EXPECTED_ROUTES, so "
        "the 403 tests of J1 cover it.")


def test_the_package_exports_the_one_router() -> None:
    assert studio_pkg.router is actions.router
    sources = {p.name: p.read_text(encoding="utf-8") for p in _STUDIO_DIR.glob("*.py")}
    assert [n for n, s in sources.items() if "APIRouter(" in s] == ["actions.py"]


# ── d. One gate, and it is `feature:studio` ───────────────────────────────


def _permissions_of(call: Any) -> list[str]:
    """The permission strings that a gate closure of ``acb_auth.deps`` holds:
    ``permission`` (``require_feature_router``) or ``required``
    (``require_permission`` and ``require_any_permission``)."""
    try:
        names = inspect.getclosurevars(call).nonlocals
    except (TypeError, ValueError):
        return []
    found: list[str] = []
    if isinstance(names.get("permission"), str):
        found.append(names["permission"])
    if isinstance(names.get("required"), tuple):
        found += [p for p in names["required"] if isinstance(p, str)]
    return found


def _gates_of(route: APIRoute) -> list[str]:
    seen: list[str] = []
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            seen += _permissions_of(dep.call)
        stack.extend(dep.dependencies)
    return seen


def test_the_router_carries_one_feature_gate_and_it_is_studio() -> None:
    assert len(actions.router.dependencies) == 1
    assert _permissions_of(actions.router.dependencies[0].dependency) == ["feature:studio"]


@pytest.mark.parametrize("method,path", EXPECTED_ROUTES)
def test_no_route_adds_a_second_gate_or_the_share_verb(method: str, path: str) -> None:
    route = next(r for r in actions.router.routes
                 if isinstance(r, APIRoute) and r.path == path and method in r.methods)
    assert _gates_of(route) == ["feature:studio"], (method, path)


# ── a, b. 403 before the handler ──────────────────────────────────────────


@pytest.mark.parametrize("listed", ["", "*"])
@pytest.mark.parametrize("method,path", EXPECTED_ROUTES)
def test_a_workflows_member_gets_403_on_each_route(
    monkeypatch: pytest.MonkeyPatch, no_store: list[str], method: str, path: str, listed: str,
) -> None:
    """B1's interim gate is gone. The 403 comes before the 404 of the switch."""
    _open(monkeypatch, listed)
    r = _call(_client(WORKFLOWS_ONLY), method, path)
    assert r.status_code == 403, (method, path, r.status_code)
    assert "feature:studio" in r.json()["detail"]
    assert no_store == []


@pytest.mark.parametrize("who,grants,overrides", [
    ("guest", GUEST, []),
    ("denied member", ["feature:*"], [("feature:studio", "deny")]),
    ("workflows yes, studio no", ["feature:*"],
     [("feature:workflows", "allow"), ("feature:studio", "deny")]),
])
@pytest.mark.parametrize("method,path", EXPECTED_ROUTES)
def test_a_member_without_studio_gets_403_on_each_route(
    monkeypatch: pytest.MonkeyPatch, no_store: list[str], method: str, path: str,
    who: str, grants: list[str], overrides: list[tuple[str, str]],
) -> None:
    _open(monkeypatch, "*")
    r = _call(_client(grants, overrides), method, path)
    assert r.status_code == 403, (who, method, path, r.status_code)
    assert no_store == []


# ── c. A holder passes the gate ───────────────────────────────────────────


@pytest.mark.parametrize("grants", [STUDIO, ["feature:*"], ["*"]])
@pytest.mark.parametrize("method,path", EXPECTED_ROUTES)
def test_a_studio_holder_passes_the_gate_on_each_route(
    monkeypatch: pytest.MonkeyPatch, no_store: list[str], method: str, path: str,
    grants: list[str],
) -> None:
    """Past the gate, the switch of each handler answers 404 for an org
    outside ``STUDIO_ORGS``. That proves the gate let the call through and
    that no handler reached a store."""
    _open(monkeypatch, "")
    r = _call(_client(grants), method, path)
    assert r.status_code == 404, (method, path, r.status_code)
    assert r.json() == {"detail": "Not found"}
    assert no_store == []


@pytest.mark.parametrize("method,path", EXPECTED_ROUTES)
def test_a_workflows_deny_override_no_longer_closes_studio(
    monkeypatch: pytest.MonkeyPatch, no_store: list[str], method: str, path: str,
) -> None:
    """The edge that the owner accepted on 2026-11-06 (task 1199, D180). Before
    J1-1, a member with a ``feature:workflows`` deny override got 403 here.
    Now ``feature:*`` decides ``feature:studio``, so that member passes the
    gate. An admin denies ``feature:studio`` by hand to close Studio for them."""
    _open(monkeypatch, "")
    r = _call(_client(["feature:*"], [("feature:workflows", "deny")]), method, path)
    assert r.status_code == 404, (method, path, r.status_code)
    assert no_store == []


def test_an_anonymous_caller_gets_401() -> None:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=None, role=UserRole.EMPLOYEE, access=build_access(STUDIO))
    assert TestClient(app).get("/studio/actions").status_code == 401


# ── f. The source names no `workflows` gate ───────────────────────────────


def test_no_studio_file_names_the_workflows_gate() -> None:
    gate = re.compile(
        r"""(?:require_feature(?:_router)?|require_permission|require_any_permission)"""
        r"""\(\s*["'](?:feature:)?workflows["']""")
    for path in sorted(_STUDIO_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not gate.search(text), path.name
        assert "feature:workflows" not in text, path.name
    actions_src = (_STUDIO_DIR / "actions.py").read_text(encoding="utf-8")
    assert 'dependencies=[require_feature_router("studio")]' in actions_src


def test_the_share_verb_is_a_capability_that_no_studio_route_reads() -> None:
    """J1-1 adds `studio:publish` to the vocabulary only. Version publish of
    a member's own app needs `feature:studio` and ownership (STU-Q4b)."""
    assert "studio:publish" in CAPABILITIES
    assert "studio" in FEATURES
    for path in sorted(_STUDIO_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "studio:publish" not in text, path.name
        assert "has_permission(" not in text, path.name


# ── g. The chat gets the refusal ──────────────────────────────────────────


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The Studio tools call the REAL router over an ASGI transport, as the
    member of the run. ``STUDIO_ORGS`` holds every org."""
    from acb_skills import studio_tools as st

    _open(monkeypatch, "*")
    box: dict[str, Any] = {"grants": WORKFLOWS_ONLY}
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=ANA, role=UserRole.EMPLOYEE, organization_id=ORG,
        access=build_access(box["grants"]))
    real = httpx.AsyncClient
    monkeypatch.setattr(st.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.ASGITransport(app=app), **kw))
    monkeypatch.setattr(st, "_current_user_email", lambda: ANA)
    box["st"] = st
    return box


def test_a_chat_tool_of_a_member_without_the_feature_gets_the_refusal(
    chat: Any, no_store: list[str],
) -> None:
    import asyncio

    st = chat["st"]
    listed = asyncio.run(st.studio_list_actions())
    assert listed["ok"] is False
    assert [e["message"] for e in listed["errors"]] == ["The action catalog is not open to you."]
    skills = asyncio.run(st.studio_skill_list())
    assert skills["ok"] is False
    assert [e["message"] for e in skills["errors"]] == ["AI Studio is not open to you."]
    assert no_store == []


def test_a_chat_tool_of_a_holder_reaches_the_handler(
    chat: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    async def _rows(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(actions, "catalog_rows", _rows)
    monkeypatch.setattr(actions, "package_rows", _rows)
    chat["grants"] = STUDIO
    listed = asyncio.run(chat["st"].studio_list_actions())
    assert listed["ok"] is True, listed


# ── h. The R8 half runs in CI ─────────────────────────────────────────────


def test_the_r8_suite_is_named_in_the_ci_skip_guard() -> None:
    """A fence that never runs is no fence. The ``--must-run`` list names the
    pytest ARGUMENT, so a mention in a comment does not count."""
    workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
    assert block, "the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_route_gate_r8\.py(?: \\)?$",
                     block.group(1), re.M)
