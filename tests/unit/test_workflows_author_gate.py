"""The authoring gate of the Workflows API (STU-J1-3), the hermetic half.

Spec: ``project-docs/specs/ai_studio.md`` §14 J1-3, and the owner's rules of
2026-11-10 on task 1818. Code: ``AUTHOR_GATE``, ``check_author`` and
``author_gate_on`` in ``gateway/routes/workflows/core.py``. The R8 half, with
the real role bundles of ``provision_org_roles`` and the real
``resolve_access``, is ``test_workflows_author_gate_r8.py``.

**The seam shape.** The router keeps ``require_feature_router("workflows")``
on every route: the surface gate. A write route adds ``AUTHOR_GATE``, and the
draft branch of ``POST /{id}/run`` calls ``check_author`` in the handler.
Both call ``require_feature("studio")`` of ``acb_auth.deps``, the one
entitlement seam. So a write needs BOTH features, and the router gate answers
first. The check applies only where ``WORKFLOW_STUDIO_GATE_ORGS`` holds the
org. The default is empty, so the rule before J1-3 holds until the flip.

**Acceptance (J1-3).**

a. ``EXPECTED_ROUTES`` gives each route of the router one class. A new route
   fails here until a person gives it a class, and so a gate.
b. The gates of each route equal the gates of its class (``GATES``). A read,
   a run, a module route and the hook add nothing. A write adds
   ``feature:studio``. A live route adds ``workflows:publish`` and no Studio
   right (owner rule 2).
c. With the flag on, a member with a ``feature:studio`` deny gets 403 on each
   write and on a draft run, and no handler runs. That member still reads,
   runs the published version and uses the module routes.
d. A member with a ``feature:workflows`` deny gets 403 on each route but the
   hook, with the flag on and off. So the deny still blocks reads and
   published runs.
e. A guest with a ``feature:workflows`` allow reads and runs the published
   version. With the flag on, that guest gets 403 on each write.
f. With the flag off, a member with a ``feature:studio`` deny writes, as
   before J1-3.
g. A listed org is on and another org is off. ``*`` checks a caller with no
   org, so the gate fails closed.
h. The check is the seam's own ``require_feature("studio")``. Only
   ``run_workflow`` calls ``check_author`` in a handler.
i. The flag has one reader and an empty default. The R8 half is in the
   ``--must-run`` list of the CI skip guard.

**The mutations (R7).** Each ran on 2026-11-11, and each test named here
went red.

* M1: drop ``dependencies=[AUTHOR_GATE]`` from ``POST /workflows``. Red:
  ``test_the_gates_of_each_route_are_the_gates_of_its_class`` and
  ``test_a_studio_deny_refuses_each_write`` on that route, and the R8 half.
* M2: drop the ``check_author`` call of ``run_workflow``. Red:
  ``test_a_studio_deny_refuses_a_draft_run``,
  ``test_a_guest_with_workflows_reads_and_runs_but_does_not_write`` and
  ``test_only_run_workflow_calls_the_check_in_a_handler``.
* M3: make ``author_gate_on`` return False. Red:
  ``test_a_studio_deny_refuses_each_write``,
  ``test_a_studio_deny_refuses_a_draft_run``,
  ``test_a_refused_write_reads_no_body``,
  ``test_a_guest_with_workflows_reads_and_runs_but_does_not_write``,
  ``test_a_listed_org_is_on_and_another_org_is_off``,
  ``test_star_checks_a_caller_with_no_org`` and the R8 half.
* M4: add ``AUTHOR_GATE`` to the publish route. Red:
  ``test_the_gates_of_each_route_are_the_gates_of_its_class`` and
  ``test_a_studio_deny_keeps_the_live_routes_of_a_publisher``.
"""

from __future__ import annotations

import ast
import inspect
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.settings import Settings
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from gateway.routes.workflows import (
    catalog,
    copilot,
    core,
    crud,
    modules,
    publish,
    runs,
    search,
)
from gateway.routes.workflows import router as workflows_router

from tests.unit import _r8_lists as r8_lists

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _ROOT / "apps/services/gateway/gateway/routes/workflows"

ORG = str(uuid.uuid4())
OTHER_ORG = str(uuid.uuid4())
ANA = "ana@example.test"

READ, RUN, AUTHOR, LIVE, MODULE, HOOK = "read", "run", "author", "live", "module", "hook"

#: Every route of the Workflows router, as ``(method, path template)``, and its
#: class. ⚠️ A route that you add fails
#: ``test_expected_routes_are_the_routes_of_the_router`` until you give it a
#: class here. The class decides its gates (``GATES``) and its probes below.
EXPECTED_ROUTES: dict[tuple[str, str], str] = {
    # Reads keep `feature:workflows` (owner, task 1818).
    ("GET", "/workflows"): READ,
    ("GET", "/workflows/{workflow_id}"): READ,
    ("GET", "/workflows/catalog"): READ,
    ("GET", "/workflows/catalog/search"): READ,
    ("GET", "/workflows/{workflow_id}/runs"): READ,
    ("GET", "/workflows/runs/{run_id}"): READ,
    ("GET", "/workflows/runs/{run_id}/stream"): READ,
    # Validate writes nothing. It reads the draft and the module list, and
    # answers the issues of the graph. So it is a read.
    ("POST", "/workflows/{workflow_id}/validate"): READ,
    # A run of the published version is use (owner rule 1). A test run of the
    # draft (`draft: true`) is a write, and the handler checks it.
    ("POST", "/workflows/{workflow_id}/run"): RUN,
    # The writes of the edit-model, and the Copilot (it spends credits and
    # builds the graph for the editor).
    ("POST", "/workflows"): AUTHOR,
    ("POST", "/workflows/{workflow_id}/duplicate"): AUTHOR,
    ("PUT", "/workflows/{workflow_id}"): AUTHOR,
    ("DELETE", "/workflows/{workflow_id}"): AUTHOR,
    ("POST", "/workflows/{workflow_id}/copilot"): AUTHOR,
    # The four live routes keep `workflows:publish` and the org tier, with no
    # Studio right (owner rule 2). J2 owns a private tier.
    ("POST", "/workflows/{workflow_id}/publish"): LIVE,
    ("POST", "/workflows/{workflow_id}/versions/{version}/rollback"): LIVE,
    ("POST", "/workflows/{workflow_id}/disable"): LIVE,
    ("POST", "/workflows/{workflow_id}/enable"): LIVE,
    # Module Studio stays out of J1-3 until STU-Q15 has a decision.
    ("GET", "/workflows/modules"): MODULE,
    ("POST", "/workflows/modules"): MODULE,
    ("GET", "/workflows/modules/{module_id}"): MODULE,
    ("PUT", "/workflows/modules/{module_id}"): MODULE,
    ("DELETE", "/workflows/modules/{module_id}"): MODULE,
    ("POST", "/workflows/modules/test"): MODULE,
    ("POST", "/workflows/modules/{module_id}/test"): MODULE,
    ("POST", "/workflows/modules/generate"): MODULE,
    # The inbound webhook. The token is the credential, and it has no member.
    ("POST", "/workflows/hooks/{hook_token}"): HOOK,
}

#: The gates of each class, in the order that FastAPI runs them. The router
#: gate runs first. The draft branch of a run adds `feature:studio` in the
#: handler, so the route itself carries the router gate only.
GATES: dict[str, list[str]] = {
    READ: ["feature:workflows"],
    RUN: ["feature:workflows"],
    AUTHOR: ["feature:workflows", "feature:studio"],
    LIVE: ["feature:workflows", "workflows:publish"],
    MODULE: ["feature:workflows"],
    HOOK: [],
}

_PARAMS = {
    "workflow_id": str(uuid.uuid4()),
    "run_id": str(uuid.uuid4()),
    "module_id": str(uuid.uuid4()),
    "version": "1",
    "hook_token": "t" * 32,
}

GUEST = ["feature:chat", "apps:use:*"]
STUDIO_DENY = [("feature:studio", "deny")]
WORKFLOWS_DENY = [("feature:workflows", "deny")]


def _routes_of(cls: str) -> list[tuple[str, str]]:
    return sorted(k for k, v in EXPECTED_ROUTES.items() if v == cls)


def _url(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: _PARAMS[m.group(1)], path)


def _client(
    grants: list[str],
    overrides: list[tuple[str, str]] | None = None,
    *,
    org: str | None = ORG,
    email: str | None = ANA,
) -> TestClient:
    app = FastAPI()
    app.include_router(workflows_router)

    def _user() -> UserContext:
        return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                           access=build_access(grants, overrides or []))

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app, raise_server_exceptions=False)


def _call(client: TestClient, method: str, path: str, *, draft: bool = False) -> httpx.Response:
    """One request. A run sends ``draft``. Each other POST and PUT sends
    ``[]``, so a route with a body model answers 422 after the gates and its
    handler never runs. A route with no body runs its handler, and the first
    session call trips (``reached``)."""
    if path.endswith("/run"):
        body: Any = {"draft": draft}
    elif method in ("POST", "PUT"):
        body = []
    else:
        body = None
    return client.request(method, _url(path), json=body)


def _passed(r: httpx.Response) -> bool:
    return r.status_code not in (401, 403)


def _refused(r: httpx.Response, permission: str) -> bool:
    return r.status_code == 403 and permission in str(r.json().get("detail"))


@pytest.fixture
def reached(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each session that a handler opens answers 418 and records the module.
    A test that expects no handler asserts that the list stays empty."""
    seen: list[str] = []

    def _trip(name: str) -> Any:
        def _fn(*_a: Any, **_k: Any) -> Any:
            seen.append(name)
            raise HTTPException(status_code=418, detail=f"{name} reached")
        return _fn

    for module in (catalog, copilot, crud, modules, publish, runs, search):
        monkeypatch.setattr(module, "_tenant_session", _trip(module.__name__))
    return seen


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "workflow_studio_gate_orgs", value)


#: The routes that a pass probe calls. The stream answers 200 and then reads
#: in its body, so only the refusal probes call it.
_PROBED = [k for k in EXPECTED_ROUTES if EXPECTED_ROUTES[k] != HOOK]
_PASS_PROBED = [k for k in _PROBED if k[1] != "/workflows/runs/{run_id}/stream"]


# ── a, b. The route table and the gates of each class ─────────────────────


def _router_routes() -> list[tuple[str, str]]:
    return sorted((m, r.path) for r in workflows_router.routes
                  if isinstance(r, APIRoute) for m in r.methods)


def test_expected_routes_are_the_routes_of_the_router() -> None:
    assert _router_routes() == sorted(EXPECTED_ROUTES), (
        "The Workflows router changed. Give the new route a class in "
        "EXPECTED_ROUTES, so the gate tests of J1-3 cover it.")


def _permissions_of(call: Any) -> list[str]:
    """The permission strings of a gate closure of ``acb_auth.deps``:
    ``permission`` (``require_feature_router``) or ``required``
    (``require_permission``). ``core._require_author`` reads
    ``feature:studio`` through ``core._studio_check`` (test h)."""
    if call is core._require_author:
        return [f"feature:{core.AUTHOR_FEATURE}"]
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
    """The gates of a route in the order that FastAPI runs them. The router
    gate skips a template in ``core.EXEMPT_ROUTES``."""
    seen: list[str] = []

    def walk(dependant: Any) -> None:
        for dep in dependant.dependencies:
            if dep.call is not None:
                seen.extend(_permissions_of(dep.call))
            walk(dep)

    walk(route.dependant)
    if route.path in core.EXEMPT_ROUTES:
        seen = [g for g in seen if g != "feature:workflows"]
    return seen


def test_the_router_carries_one_gate_and_it_is_workflows() -> None:
    assert len(workflows_router.dependencies) == 1
    call = workflows_router.dependencies[0].dependency
    assert _permissions_of(call) == ["feature:workflows"]
    assert set(inspect.getclosurevars(call).nonlocals["exempt_paths"]) == {
        "/workflows/hooks/{hook_token}"}


@pytest.mark.parametrize("method,path", sorted(EXPECTED_ROUTES))
def test_the_gates_of_each_route_are_the_gates_of_its_class(method: str, path: str) -> None:
    route = next(r for r in workflows_router.routes
                 if isinstance(r, APIRoute) and r.path == path and method in r.methods)
    cls = EXPECTED_ROUTES[(method, path)]
    assert _gates_of(route) == GATES[cls], (method, path, cls)


# ── c. A `feature:studio` deny, with the flag on ──────────────────────────


@pytest.mark.parametrize("method,path", _routes_of(AUTHOR))
def test_a_studio_deny_refuses_each_write(
    monkeypatch: pytest.MonkeyPatch, reached: list[str], method: str, path: str,
) -> None:
    _flag(monkeypatch, "*")
    r = _call(_client(["feature:*"], STUDIO_DENY), method, path)
    assert _refused(r, "feature:studio"), (method, path, r.status_code, r.text)
    assert reached == []


def test_a_studio_deny_refuses_a_draft_run(
    monkeypatch: pytest.MonkeyPatch, reached: list[str],
) -> None:
    _flag(monkeypatch, "*")
    r = _call(_client(["feature:*"], STUDIO_DENY), "POST", "/workflows/{workflow_id}/run",
              draft=True)
    assert _refused(r, "feature:studio"), (r.status_code, r.text)
    assert reached == [], "the check runs before the session"


@pytest.mark.parametrize("method,path",
                         [k for k in _PASS_PROBED if EXPECTED_ROUTES[k] in (READ, RUN, MODULE)])
def test_a_studio_deny_keeps_reads_published_runs_and_modules(
    monkeypatch: pytest.MonkeyPatch, reached: list[str], method: str, path: str,
) -> None:
    _flag(monkeypatch, "*")
    r = _call(_client(["feature:*"], STUDIO_DENY), method, path, draft=False)
    assert _passed(r), (method, path, r.status_code, r.text)


@pytest.mark.parametrize("method,path", _routes_of(LIVE))
def test_a_studio_deny_keeps_the_live_routes_of_a_publisher(
    monkeypatch: pytest.MonkeyPatch, reached: list[str], method: str, path: str,
) -> None:
    """Owner rule 2: the live routes need `workflows:publish` and no Studio
    right. A member without the verb still gets the 403 of the verb."""
    _flag(monkeypatch, "*")
    publisher = _client(["feature:*", "workflows:publish"], STUDIO_DENY)
    assert _passed(_call(publisher, method, path)), (method, path)
    r = _call(_client(["feature:*"], STUDIO_DENY), method, path)
    assert _refused(r, "workflows:publish"), (method, path, r.status_code, r.text)


@pytest.mark.parametrize("grants", [["feature:*"], ["*"],
                                    ["feature:workflows", "feature:studio"]])
@pytest.mark.parametrize("method,path", _routes_of(AUTHOR))
def test_a_holder_of_both_features_writes(
    monkeypatch: pytest.MonkeyPatch, reached: list[str], method: str, path: str,
    grants: list[str],
) -> None:
    _flag(monkeypatch, "*")
    r = _call(_client(grants), method, path)
    assert _passed(r), (method, path, r.status_code, r.text)


def test_a_holder_of_both_features_runs_the_draft(
    monkeypatch: pytest.MonkeyPatch, reached: list[str],
) -> None:
    _flag(monkeypatch, "*")
    r = _call(_client(["feature:workflows", "feature:studio"]), "POST",
              "/workflows/{workflow_id}/run", draft=True)
    assert r.status_code == 418, (r.status_code, r.text)
    assert reached == [runs.__name__]


def test_a_refused_write_reads_no_body(
    monkeypatch: pytest.MonkeyPatch, reached: list[str],
) -> None:
    """The gate runs before the body check: a refused write gets 403, not the
    422 of a bad body."""
    _flag(monkeypatch, "*")
    client = _client(["feature:*"], STUDIO_DENY)
    for body in ([], {"name": ""}, {"graph": "not an object"}):
        assert client.post("/workflows", json=body).status_code == 403
        assert client.put(_url("/workflows/{workflow_id}"), json=body).status_code == 403
    assert reached == []


# ── d. A `feature:workflows` deny still blocks everything ─────────────────


@pytest.mark.parametrize("flag", ["", "*"])
@pytest.mark.parametrize("method,path", _PROBED)
def test_a_workflows_deny_refuses_each_route(
    monkeypatch: pytest.MonkeyPatch, reached: list[str], method: str, path: str, flag: str,
) -> None:
    _flag(monkeypatch, flag)
    client = _client(["feature:*"], WORKFLOWS_DENY)
    for draft in ((False, True) if path.endswith("/run") else (False,)):
        r = _call(client, method, path, draft=draft)
        assert _refused(r, "feature:workflows"), (method, path, draft, r.status_code)
    assert reached == []


# ── e. A guest with a `feature:workflows` allow ───────────────────────────


def test_a_guest_with_workflows_reads_and_runs_but_does_not_write(
    monkeypatch: pytest.MonkeyPatch, reached: list[str],
) -> None:
    _flag(monkeypatch, "*")
    guest = _client(GUEST, [("feature:workflows", "allow")])
    for method, path in _routes_of(READ):
        if path.endswith("/stream"):
            continue
        assert _passed(_call(guest, method, path)), (method, path)
    assert _passed(_call(guest, "POST", "/workflows/{workflow_id}/run", draft=False))
    reached.clear()
    for method, path in _routes_of(AUTHOR):
        assert _refused(_call(guest, method, path), "feature:studio"), (method, path)
    assert _refused(_call(guest, "POST", "/workflows/{workflow_id}/run", draft=True),
                    "feature:studio")
    assert reached == []


# ── f. The flag off keeps the rule before J1-3 ────────────────────────────


@pytest.mark.parametrize("method,path", _routes_of(AUTHOR))
def test_with_the_flag_off_a_studio_deny_still_writes(
    monkeypatch: pytest.MonkeyPatch, reached: list[str], method: str, path: str,
) -> None:
    _flag(monkeypatch, "")
    r = _call(_client(["feature:*"], STUDIO_DENY), method, path)
    assert _passed(r), (method, path, r.status_code, r.text)


def test_with_the_flag_off_a_studio_deny_still_runs_the_draft(
    monkeypatch: pytest.MonkeyPatch, reached: list[str],
) -> None:
    _flag(monkeypatch, "")
    r = _call(_client(["feature:*"], STUDIO_DENY), "POST", "/workflows/{workflow_id}/run",
              draft=True)
    assert r.status_code == 418, (r.status_code, r.text)


def test_the_flag_defaults_to_off() -> None:
    assert Settings.model_fields["workflow_studio_gate_orgs"].default == ""


# ── g. A list of orgs, and `*` ────────────────────────────────────────────


def test_a_listed_org_is_on_and_another_org_is_off(
    monkeypatch: pytest.MonkeyPatch, reached: list[str],
) -> None:
    _flag(monkeypatch, f" {ORG.upper()} , {uuid.uuid4()}")
    listed = _client(["feature:*"], STUDIO_DENY, org=ORG)
    other = _client(["feature:*"], STUDIO_DENY, org=OTHER_ORG)
    assert _refused(_call(listed, "POST", "/workflows"), "feature:studio")
    assert _passed(_call(other, "POST", "/workflows"))
    assert core.author_gate_on(ORG) and not core.author_gate_on(OTHER_ORG)
    assert not core.author_gate_on(None) and not core.author_gate_on("")


def test_star_checks_a_caller_with_no_org(
    monkeypatch: pytest.MonkeyPatch, reached: list[str],
) -> None:
    _flag(monkeypatch, "*")
    assert core.author_gate_on(None)
    r = _call(_client(["feature:*"], STUDIO_DENY, org=None), "POST", "/workflows")
    assert _refused(r, "feature:studio")
    assert reached == []


def test_an_anonymous_caller_gets_401(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    client = _client(["feature:*"], email=None)
    assert client.post("/workflows", json={"name": "x"}).status_code == 401
    assert client.post(_url("/workflows/{workflow_id}/run"),
                       json={"draft": True}).status_code == 401


# ── h. One seam, one check in a handler ───────────────────────────────────


def test_the_check_is_the_seam_of_acb_auth() -> None:
    from acb_auth import deps

    check = core._studio_check
    assert check.__module__ == deps.__name__
    assert check.__qualname__.startswith("require_permission")
    assert inspect.getclosurevars(check).nonlocals["required"] == ("feature:studio",)
    assert core.AUTHOR_FEATURE == "studio"


def test_only_run_workflow_calls_the_check_in_a_handler() -> None:
    """``check_author`` is called in ``core._require_author`` and in the
    draft branch of ``runs.run_workflow``, and nowhere else. Every other write
    takes ``AUTHOR_GATE``, which the route table pins."""
    calls: list[tuple[str, str]] = []
    for path in sorted(_WORKFLOWS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for top in tree.body:
            if not isinstance(top, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for node in ast.walk(top):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "check_author":
                    calls.append((path.name, top.name))
    assert sorted(calls) == [("core.py", "_require_author"), ("runs.py", "run_workflow")], calls
    tree = ast.parse((_WORKFLOWS_DIR / "runs.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_workflow")
    first = fn.body[0]
    assert isinstance(first, ast.If) and ast.unparse(first.test) == "body.draft", \
        "the draft check must be the first statement, before the session"
    assert ast.unparse(first.body[0]) == "await check_author(user)"


def _code_strings(tree: ast.AST) -> list[str]:
    """Each string constant of a module that is not a docstring."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_no_workflows_file_reads_studio_by_hand() -> None:
    """The one seam: no file of ``routes/workflows`` asks for a Studio right by
    itself. The code names the feature once, in ``core.AUTHOR_FEATURE``. The
    check reads the syntax tree, so the quote style does not matter."""
    studio: list[str] = []
    for path in sorted(_WORKFLOWS_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        strings = _code_strings(ast.parse(text))
        assert "feature:studio" not in strings, path.name
        assert not any("studio:publish" in s for s in strings), path.name
        assert ".access.has(" not in text, path.name
        assert not re.search(r"has_permission\([^)]*studio", text), path.name
        studio += [path.name for s in strings if s == "studio"]
    assert studio == ["core.py"], studio


# ── i. One reader, and the CI skip guard ──────────────────────────────────


def test_the_flag_has_one_reader() -> None:
    readers: list[str] = []
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            if "workflow_studio_gate_orgs" in path.read_text(encoding="utf-8", errors="ignore"):
                readers.append(path.relative_to(_ROOT).as_posix())
    assert sorted(readers) == [
        "apps/services/gateway/gateway/routes/workflows/core.py",
        "packages/acb_common/acb_common/settings.py",
    ], readers


def test_the_r8_suite_is_named_in_the_ci_skip_guard() -> None:
    """A fence that never runs is no fence. The ``--must-run`` list names the
    pytest ARGUMENT, so a mention in a comment does not count."""
    must_run = r8_lists.must_run()
    assert "tests/unit/test_workflows_author_gate_r8.py" in must_run
