"""WS-MCP MCP-P1b-1 — the shared approval store and card, with no spend on branch 0.

Spec: ``project-docs/specs/external_agent_access.md`` §19.5, AD-5, AD-6,
AD-13, AD-16 and AD-17 of §19.12, and row MCP-P1b-1 of §19.13. The audit of
task 1056 holds the decisions D1 to D9 and the cases below.

Two halves.

**Hermetic.** The fences of R7: each reader of ``pm_agent_runs`` names its
subject, only ``acb_auth.app_requests`` spends, the lines of the digest are
pinned, the secret words of the store are the words of the edge, and the
migration is found by CONTENT (R1).

**R8.** A real Postgres, through the real routes, as the non-privileged role
of the phase-4 rehearsal, on a database with the whole ladder and two
organizations. The 15 cases of the audit:

1. Ask, approve, spend: one run, one elicitation, one spend, the stored call.
2. Two spends at once: one winner.
3. A spend or an approve after the expiry is refused. A decline closes it.
4. An approve and a decline at once: one winner.
5. A wrong digest changes nothing, and the right one then spends.
6. Another connection, another member and org B with the same email: 404
   on a read, and no spend.
7. A Studio request spends only with its member, app and package.
8. The Studio spend gives the stored call, never the caller's.
9. The cap: 20 live requests, and an expired one does not count.
10. Idempotency: the same key and call give the first request. Another
    call under the key is 409.
11. A call over 32 KiB is 413. A nested secret key is 422. Each stores
    nothing.
12. Class A and X are refused.
13. An app run never shows in the task-run readers.
14. The migration on a database with old task runs.
15. The decision route refuses a signed agent.

Run (a private ladder database, ONE R8 suite for each pytest process)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<db> \\
      uv run pytest tests/unit/test_app_requests_r8.py -q -rs

**The mutations (R7).** Each one turned the named tests red. Each was run on
a committed tree and restored with ``git checkout``.

* M1. ``SPEND_SQL``: drop ``AND r.status = 'in_progress'``.
  ``test_02_two_spends_at_once_have_one_winner``,
  ``test_01_ask_approve_spend_once``.
* M2. ``SPEND_SQL``: drop the digest predicate.
  ``test_05_a_wrong_digest_changes_nothing``.
* M3. ``SPEND_SQL``: drop ``r.connection_id IS NOT DISTINCT FROM``.
  ``test_06_another_connection_member_or_org_gets_nothing``,
  ``test_07_a_studio_request_spends_only_under_its_binding``.
* M4. ``SPEND_SQL``: drop ``AND r.member_email = :member``.
  ``test_06_another_connection_member_or_org_gets_nothing``.
* M5. ``SPEND_SQL``: drop the expiry predicate.
  ``test_03_expiry_refuses_a_spend_and_an_approve``.
* M6. ``gateway.app_requests._CAP_SQL``: drop the expiry predicate.
  ``test_09_the_cap_counts_live_requests_only``.
* M7. ``acb_auth.app_requests.spend``: return a call other than the stored
  one. ``test_08_the_studio_spend_gives_the_stored_call``.
* M8. ``gateway.app_requests.open_request``: drop the secret-key check.
  ``test_11_a_large_call_or_a_secret_key_is_refused``.
* M9. ``gateway.app_requests.open_request``: drop the size check.
  ``test_11_a_large_call_or_a_secret_key_is_refused``.
* M10. ``routes/projects/filters.agent_state_sql``: drop
  ``r.task_id IS NOT NULL``. ``test_13_an_app_run_never_shows_as_a_task_run``
  and ``test_every_pm_agent_runs_query_names_its_subject``.
* M11. ``routes/agent_requests.decide_app_request``: let an agent caller
  decide. ``test_15_the_decision_route_refuses_a_signed_agent``.
* M12. ``acb_auth.app_requests.digest_lines``: drop the ``if-match`` line.
  ``test_digest_lines_are_pinned``.
* M13. ``routes/agent_requests.AppRequestIn``: take ``organization_id`` from
  the body. ``tests/unit/test_tenant_request_binding.py::
  test_the_app_request_routes_take_no_tenant_from_input``.

The record of each red run is in the report of the MCP-P1b-1 build.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.agent_requests", reason="gateway not installed")

from acb_auth import app_requests as store
from acb_common import app_manifests as am
from acb_common.app_manifests import base
from fastapi import HTTPException, Request
from gateway import app_requests
from gateway.routes.projects import agent_runs
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_agent_runs_r8 import _seed_task
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"

ORIGIN = "https://claude.ai"
CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
VIA = "mcp:claude.ai:claude-code"
APP_ID = "testreq"
PKG1 = "11111111-2222-4333-8444-555555555555"
PKG2 = "66666666-7777-4888-9999-000000000000"

#: A door app with each kind of op, for the tests only.
TEST_APP = base.App(
    id=APP_ID, name="Test requests", reach="The rows of a test.", pane="/testreq",
    feature="projects", router="tests.unit.test_app_requests_r8",
    finish_in="Test requests, Drafts",
    routes=(
        base.Route("GET", "/testreq/items/{item_id}", "", "A", planned="test",
                   op="get_item", title="Read one item."),
        base.Route("POST", "/testreq/items", "", "B", planned="test",
                   op="make_item", title="Make an item."),
        base.Route("DELETE", "/testreq/items/{item_id}", "", "C", planned="test",
                   op="drop_item", title="Delete an item."),
        base.Route("POST", "/testreq/drafts", "", "B", planned="test",
                   op="file_draft", title="File a draft.", proposal=True),
    ),
)


# ══ Hermetic ═════════════════════════════════════════════════════════════════

# ── The reader fence (AD-16): each read of pm_agent_runs names its subject ──

#: A statement that reads the runs table.
_READS_RUNS = re.compile(r"\b(?:FROM|JOIN)\s+pm_agent_runs\b")
#: The three marks of a subject. One of them is in each statement.
SUBJECT_MARKS: tuple[str, ...] = ("task_id IS NOT NULL", "app IS NULL", "app IS NOT NULL")

#: The statements that read the runs table with no mark, each with its
#: reason. ``(path, owner)``: the owner is the module-level name or the
#: function that holds the statement. A new entry needs a reason that a
#: reviewer accepts.
SUBJECT_ALLOWED: dict[tuple[str, str], str] = {
    ("apps/services/gateway/gateway/routes/projects/agent_runs.py", "_RUN_SELECT"): (
        "A select prefix with no WHERE. Each statement that starts with it adds"
        " its subject, and the fence renders each of them with the prefix in"
        " place, so each use is held."
    ),
}


def _is_text(node: Any) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_text(node.left) and _is_text(node.right)
    return False


def _render(node: Any, names: dict[str, str]) -> str:
    """The text of a string expression. A ``{name}`` of a module-level text
    becomes that text, so a statement built from a prefix reads whole."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.BinOp):
        return _render(node.left, names) + _render(node.right, names)
    out = []
    for part in node.values:
        if isinstance(part, ast.Constant):
            out.append(str(part.value))
        elif isinstance(part.value, ast.Name) and part.value.id in names:
            out.append(names[part.value.id])
        else:
            out.append("{" + ast.unparse(part.value) + "}")
    return "".join(out)


def statements_of(source: str) -> list[tuple[str, int, str]]:
    """``(owner, line, text)`` of each SQL text of *source* that reads the
    runs table. A docstring is not a statement."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    names: dict[str, str] = {}
    for _ in range(3):
        for st in tree.body:
            if (isinstance(st, ast.Assign) and len(st.targets) == 1
                    and isinstance(st.targets[0], ast.Name) and _is_text(st.value)):
                names[st.targets[0].id] = _render(st.value, names)
    found = []
    for node in ast.walk(tree):
        parent = parents.get(node)
        if not _is_text(node) or _is_text(parent) or isinstance(parent, (ast.FormattedValue, ast.Expr)):
            continue
        body = _render(node, names)
        if not _READS_RUNS.search(body):
            continue
        owner, up = "<module>", node
        while up in parents:
            up = parents[up]
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = up.name
                break
            if (isinstance(up, ast.Assign) and parents.get(up) is tree
                    and isinstance(up.targets[0], ast.Name)):
                owner = up.targets[0].id
                break
        found.append((owner, node.lineno, body))
    return found


def _sources() -> list[Path]:
    out = []
    for top in ("apps", "packages"):
        for path in sorted((_REPO / top).rglob("*.py")):
            if "tests" in path.parts or ".venv" in path.parts or "node_modules" in path.parts:
                continue
            if "pm_agent_runs" in path.read_text(encoding="utf-8"):
                out.append(path)
    return out


def test_every_pm_agent_runs_query_names_its_subject() -> None:
    """AD-16. Each SQL text in ``apps/`` and ``packages/`` that reads
    ``pm_agent_runs`` holds ``task_id IS NOT NULL``, ``app IS NULL`` or
    ``app IS NOT NULL``, or sits on :data:`SUBJECT_ALLOWED` with a reason.
    So no app run shows in the runs list, the pill, the board, ``my_work``,
    Needs attention, the analytics or the sweep. Mutation M10 turns it red."""
    missing, used = [], set()
    for path in _sources():
        rel = path.relative_to(_REPO).as_posix()
        for owner, line, body in statements_of(path.read_text(encoding="utf-8")):
            if any(mark in body for mark in SUBJECT_MARKS):
                continue
            if (rel, owner) in SUBJECT_ALLOWED:
                used.add((rel, owner))
                continue
            missing.append(f"{rel}:{line} ({owner})")
    assert not missing, (
        "These statements read pm_agent_runs and name no subject. Add"
        " `task_id IS NOT NULL` for a task run:\n" + "\n".join(missing))
    assert used == set(SUBJECT_ALLOWED), sorted(set(SUBJECT_ALLOWED) - used)


def test_the_reader_fence_sees_a_statement_with_no_subject() -> None:
    """The control of the fence: a reader with no mark is found, a prefix
    renders into its use, and a docstring is not a statement."""
    source = (
        '"""SELECT * FROM pm_agent_runs in a docstring."""\n'
        'PREFIX = "SELECT r.* FROM pm_agent_runs r"\n'
        'GOOD = f"{PREFIX} WHERE r.task_id IS NOT NULL"\n'
        'def bad():\n'
        '    return f"{PREFIX} WHERE r.id = :rid"\n'
    )
    found = {owner: body for owner, _, body in statements_of(source)}
    assert set(found) == {"PREFIX", "GOOD", "bad"}
    assert "task_id IS NOT NULL" in found["GOOD"]
    assert not any(mark in found["bad"] for mark in SUBJECT_MARKS)


def test_the_task_run_readers_hold_the_task_subject() -> None:
    """The two loads of ``agent_runs`` each name one subject, so an app run
    id on a task-run route is 404, never a 500."""
    assert "r.task_id IS NOT NULL" in agent_runs._LOAD_TASK_RUN_SQL
    assert "r.app IS NOT NULL" in agent_runs._LOAD_APP_RUN_SQL
    assert "task_id IS NOT NULL" in agent_runs._LOCK_TASK_RUN_SQL
    assert "app IS NOT NULL" in agent_runs._LOCK_APP_RUN_SQL


# ── Only acb_auth.app_requests spends ───────────────────────────────────────

_SPEND_SHAPE = re.compile(
    r"UPDATE\s+pm_agent_runs\b.*status\s*=\s*'completed'.*\bapp\b", re.DOTALL)


def test_only_app_requests_spends() -> None:
    """No module but ``acb_auth/app_requests.py`` holds an UPDATE of
    ``pm_agent_runs`` to ``completed`` with an ``app`` predicate. A second
    spend path would be a second winner."""
    holders = set()
    for top in ("apps", "packages"):
        for path in sorted((_REPO / top).rglob("*.py")):
            if "tests" in path.parts or ".venv" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "pm_agent_runs" not in source:
                continue
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if _is_text(node) and _SPEND_SHAPE.search(_render(node, {})):
                    holders.add(path.relative_to(_REPO).as_posix())
    assert holders == {"packages/acb_auth/acb_auth/app_requests.py"}, holders


def test_the_spend_is_one_statement_with_each_guard() -> None:
    """D5. The guards of the spend sit in the WHERE of ONE statement."""
    sql = store.SPEND_SQL
    assert sql.count("UPDATE pm_agent_runs") == 1 and "RETURNING" in sql
    for guard in (
        "r.status = 'in_progress'",
        "->>'binding' = :binding",
        "r.member_email = :member",
        "r.connection_id IS NOT DISTINCT FROM CAST(:conn AS uuid)",
        "->>'digest' = :digest",
        "> clock_timestamp()",
        "r.app IS NOT NULL AND r.app = :app",
        "r.organization_id = CAST(:org AS uuid)",
    ):
        assert guard in sql, guard


# ── The digest (AD-17 as amended) ───────────────────────────────────────────

PINNED_INPUT: dict[str, Any] = {
    "binding": "conn:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "method": "patch",
    "template": "/knowledge/pages/{page_id}",
    "path": "/knowledge/pages/0b5c6c1e-1111-4222-8333-444444444444",
    "query": [("b", "2"), ("a", "x y")],
    "body_sha256": hashlib.sha256(b'{"title":"New"}').hexdigest(),
    "if_match": "7",
}

#: The lines of :data:`PINNED_INPUT`, written out by hand. A change of the
#: rule of the digest changes this list ON PURPOSE, with a review.
PINNED_LINES: tuple[str, ...] = (
    "v1",
    "PATCH",
    "/knowledge/pages/{page_id}",
    "/knowledge/pages/0b5c6c1e-1111-4222-8333-444444444444",
    "a=x+y&b=2",
    hashlib.sha256(b'{"title":"New"}').hexdigest(),
    "if-match:7",
    "conn:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
)
PINNED_DIGEST = hashlib.sha256("\n".join(PINNED_LINES).encode("utf-8")).hexdigest()


def test_digest_lines_are_pinned() -> None:
    """AD-17 plus ``if-match`` and the binding (D2.A, D3). Mutation M12
    (drop the ``if-match`` line) turns it red."""
    assert store.digest_lines(**PINNED_INPUT) == PINNED_LINES
    assert store.digest_of(**PINNED_INPUT) == PINNED_DIGEST
    other = store.digest_of(**{**PINNED_INPUT, "if_match": "8"})
    assert other != PINNED_DIGEST, "another If-Match is another change"
    unbound = store.digest_of(**{**PINNED_INPUT, "binding": "conn:" + str(uuid.uuid4())})
    assert unbound != PINNED_DIGEST, "another connection is another binding"


@pytest.mark.parametrize("field", ["path", "binding", "if_match", "template"])
def test_a_control_character_cannot_add_a_line(field: str) -> None:
    with pytest.raises(ValueError):
        store.digest_of(**{**PINNED_INPUT, field: "x\nconn:other"})


def test_the_stored_body_gives_the_bytes_of_build_call() -> None:
    """The edge digests the bytes of ``build_call``. The store digests the
    body that it parsed from them. Both give one hash."""
    call = base.build_call(TEST_APP, "make_item",
                           {"name": "Zoë", "n": 3, "f": 1e5, "tags": ["a", "b"], "x": None})
    assert store.body_bytes(json.loads(call.body)) == call.body
    stored = {"method": call.method, "template": call.template, "path": call.path,
              "query": list(call.query), "body": json.loads(call.body),
              "if_match": call.if_match}
    binding = store.mcp_binding(str(uuid.uuid4()))
    assert store.call_digest(stored, binding) == store.digest_of(
        binding=binding, method=call.method, template=call.template, path=call.path,
        query=call.query, body_sha256=hashlib.sha256(call.body).hexdigest(),
        if_match=call.if_match)


def test_the_bindings() -> None:
    conn = str(uuid.uuid4())
    assert store.binding_connection(store.mcp_binding(conn)) == conn
    assert store.binding_connection(store.studio_binding("A@x.io", "studio", PKG1)) is None
    assert store.studio_binding("A@x.io", "studio", PKG1) == f"session:a@x.io:studio:{PKG1}"
    with pytest.raises(ValueError):
        store.binding_connection("member:a@x.io")


def test_the_secret_words_are_the_edge_words() -> None:
    """D8 rule 4. The store refuses the keys that the edge drops."""
    tools = pytest.importorskip("agent_edge.tools")
    assert app_requests.SECRET_WORDS == tools.SECRET_WORDS


def test_the_status_machine_holds_the_two_app_events() -> None:
    assert agent_runs.transition("created", "app_ask") == "awaiting"
    assert agent_runs.transition("awaiting", "app_close") == "completed"
    assert agent_runs.transition("in_progress", "app_close") == "completed"
    for status in ("in_progress", "awaiting", "completed", "stopping"):
        with pytest.raises(HTTPException):
            agent_runs.transition(status, "app_ask")


# ── Dark by org (fix round 1, review P2-1) ──────────────────────────────────

_HERMETIC_ORG = "11111111-1111-4111-8111-111111111111"
_HERMETIC_CONN = "22222222-2222-4222-8222-222222222222"


def _request_routes_app(user: Any) -> Any:
    """The router of the request routes alone, with *user* as the caller.
    No database: a route that passes the gates fails at the session."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes import agent_requests

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(agent_requests.router)
    app.dependency_overrides[get_current_user] = _caller
    return app


@pytest.mark.parametrize("orgs", ["", "33333333-3333-4333-8333-333333333333"])
async def test_the_request_routes_are_dark_without_the_org_flag(monkeypatch, orgs: str) -> None:
    """Review P2-1. Agent access is ON in production (WS-55 is live), so it
    is not the dark switch of these routes. ``APP_REQUESTS_ORGS`` is: while
    it does not hold the caller's org, each of the four routes answers 404
    before any read. Mutation: drop the org check, and each route reaches
    the session, so this fails."""
    from acb_auth import AgentScope, UserContext, UserRole, build_access
    from acb_common import get_settings
    from acb_common.db import clear_tenant, release_tenant

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    monkeypatch.setattr(settings, "agent_access_writes_enabled", True)
    monkeypatch.setattr(settings, "agent_door_apps", APP_ID)
    monkeypatch.setattr(settings, "app_requests_orgs", orgs, raising=False)
    monkeypatch.setattr(am, "APPS", (*am.APPS, TEST_APP))
    access = build_access(["feature:projects"])
    member = UserContext(email="m@dark.test", role=UserRole.EMPLOYEE, access=access,
                         organization_id=_HERMETIC_ORG)
    agent = UserContext(
        email="m@dark.test", role=UserRole.EMPLOYEE, access=access,
        organization_id=_HERMETIC_ORG,
        agent=AgentScope(connection_id=_HERMETIC_CONN, scopes=(f"{APP_ID}:write",),
                         include_personal=False, client_origin=ORIGIN, actor_via=VIA))
    rid = str(uuid.uuid4())
    calls = [
        (agent, "POST", f"/agent-access/apps/{APP_ID}/requests",
         {"op": "make_item", "args": {"name": "x"}, "idempotency_key": "k"}),
        (member, "GET", "/agent-access/requests", None),
        (member, "GET", f"/agent-access/requests/{rid}", None),
        (member, "POST", f"/agent-access/requests/{rid}/decision",
         {"decision": "approve", "idempotency_key": "k"}),
    ]
    scope = clear_tenant()
    try:
        for user, method, path, body in calls:
            transport = httpx.ASGITransport(app=_request_routes_app(user))
            async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
                got = await c.request(method, path, json=body)
            assert got.status_code == 404, (path, got.text)
    finally:
        release_tenant(scope)


def test_the_org_flag_has_one_reader_and_ships_empty() -> None:
    """``APP_REQUESTS_ORGS`` ships empty, a tenant cannot write it, and
    ``app_requests.requests_allowed`` is its one reader."""
    from acb_common.env_guard import EnvWriteRefused, check_env_write
    from acb_common.settings import Settings

    assert Settings.model_fields["app_requests_orgs"].default == ""
    with pytest.raises(EnvWriteRefused):
        check_env_write("APP_REQUESTS_ORGS", "*")
    readers = [p.relative_to(_REPO).as_posix() for top in ("apps", "packages")
               for p in (_REPO / top).rglob("*.py")
               if "tests" not in p.parts and ".venv" not in p.parts
               and "app_requests_orgs" in p.read_text(encoding="utf-8")]
    assert sorted(readers) == ["apps/services/gateway/gateway/app_requests.py",
                               "packages/acb_common/acb_common/settings.py"], readers
    assert app_requests.requests_allowed(None) is False


# ── The migration, found by content (R1) ────────────────────────────────────


def _migration() -> Path:
    found = [p for p in sorted(_MIGRATIONS.glob("*.sql"))
             if "pm_agent_runs_one_subject" in p.read_text(encoding="utf-8")]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def test_the_migration_is_expand_only_and_holds_the_six_changes() -> None:
    sql = _migration().read_text(encoding="utf-8")
    for change in (
        "ALTER TABLE pm_agent_runs ALTER COLUMN task_id DROP NOT NULL",
        "ALTER TABLE pm_agent_run_activities ALTER COLUMN task_id DROP NOT NULL",
        "ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS app TEXT NULL",
        "ALTER TABLE pm_agent_run_activities ADD COLUMN IF NOT EXISTS app TEXT NULL",
        "CHECK ((task_id IS NULL) <> (app IS NULL)) NOT VALID",
        "VALIDATE CONSTRAINT pm_agent_runs_one_subject",
        "VALIDATE CONSTRAINT pm_agent_run_activities_one_subject",
        "CHECK (client_via ~ '^(mcp|studio):[a-z0-9:_.-]{1,60}$')",
        "actor_via ~ '^(mcp|studio):[a-z0-9:_.-]{1,60}$'",
        "WHERE app IS NOT NULL AND status IN ('awaiting', 'in_progress')",
        "ON pm_agent_runs (organization_id, member_email, created_at DESC)",
        "SET LOCAL lock_timeout",
    ):
        assert change in sql, change
    for word in ("DROP COLUMN", "DROP TABLE", "RENAME", "SET NOT NULL"):
        assert word not in sql, word


# ══ R8 ═══════════════════════════════════════════════════════════════════════


def _principal(email: str, org: str, *, connection: str | None = None,
               scopes: tuple[str, ...] = (f"{APP_ID}:read", f"{APP_ID}:write")) -> Any:
    from acb_auth import AgentScope, UserContext, UserRole, build_access

    agent = None
    if connection is not None:
        agent = AgentScope(connection_id=connection, scopes=scopes, include_personal=False,
                           client_origin=ORIGIN, actor_via=VIA)
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects", "data:org:read"]),
                       organization_id=org, agent=agent)


def _app(users: dict[str, Any]) -> Any:
    """The real gateway routers. The caller is the principal that the test
    header names, and the override binds the tenant from it."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes import agent_requests
    from gateway.routes import projects as projects_package

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(agent_requests.router)
    app.include_router(projects_package.router)
    app.dependency_overrides[get_current_user] = _caller
    return app


class _Ctx:
    """One engine scope and one ASGI client for a whole test."""

    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client = client
        self.users = users

    async def __call__(self, user: Any, method: str, path: str,
                       body: dict | None = None) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await self.client.request(method, path, json=body, headers={"x-test-as": key})


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(users)),
                              base_url="http://gw.test") as client,
        ):
            yield _Ctx(client, users)
    finally:
        release_tenant(scope)


@contextlib.asynccontextmanager
async def _db(org: str):
    """A tenant session of *org*, as the app role (inside :func:`_session`)."""
    from acb_common.db import tenant_session

    async with tenant_session(org) as db:
        yield db


def _seed_connection(p: Any, org: str, email: str) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO agent_connection (organization_id, member_email, client_id,"
            " client_name, client_origin, scopes, pinned_projects, include_personal,"
            " expires_at) VALUES (CAST(:o AS uuid), :m, :client, 'Claude Code', :origin,"
            " ARRAY['projects:read'], CAST('{}' AS uuid[]), false,"
            " now() + interval '1 day') RETURNING id"),
            {"o": org, "m": email, "origin": ORIGIN, "client": CLIENT_ID}).scalar())


def _seed_member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org})


def _rows(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params))


def _count(p: Any, sql: str, **params: Any) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(sql), params).scalar())


def _key() -> str:
    return uuid.uuid4().hex


def _expire(p: Any, request_id: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_agent_run_activities SET content_metadata = jsonb_set("
            " content_metadata, '{request,expires_at}',"
            " to_jsonb(CAST(now() - interval '1 minute' AS text)))"
            " WHERE id = CAST(:i AS uuid)"), {"i": request_id})


def _call_of(args: dict[str, Any], op: str = "make_item") -> dict[str, Any]:
    """The stored shape of a call, from the one builder."""
    call = base.build_call(TEST_APP, op, args)
    return {"method": call.method, "template": call.template, "path": call.path,
            "query": [list(q) for q in call.query],
            "body": json.loads(call.body) if call.body else None,
            "if_match": call.if_match}


def _edge_digest(args: dict[str, Any], connection: str, op: str = "make_item") -> str:
    """The digest that the edge computes from the bytes it sends (AD-17)."""
    call = base.build_call(TEST_APP, op, args)
    return store.digest_of(
        binding=store.mcp_binding(connection), method=call.method, template=call.template,
        path=call.path, query=call.query, body_sha256=hashlib.sha256(call.body).hexdigest(),
        if_match=call.if_match)


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    a_email, c_email = "p1b1-a@requests-r8.test", "p1b1-c@requests-r8.test"
    _seed_member(promoted, promoted.org_a, a_email)
    _seed_member(promoted, promoted.org_a, c_email)
    ns = SimpleNamespace(p=promoted, a_email=a_email, c_email=c_email)
    ns.conn_a = _seed_connection(promoted, promoted.org_a, a_email)
    ns.conn_a2 = _seed_connection(promoted, promoted.org_a, a_email)
    ns.conn_c = _seed_connection(promoted, promoted.org_a, c_email)
    ns.task_a = _seed_task(promoted, promoted.org_a, a_email, grant=a_email)
    ns.a = _principal(a_email, promoted.org_a)
    ns.agent_a = _principal(a_email, promoted.org_a, connection=ns.conn_a)
    ns.agent_a2 = _principal(a_email, promoted.org_a, connection=ns.conn_a2)
    # The same connection with the Projects scopes, for the task-run routes.
    ns.agent_a_projects = _principal(
        a_email, promoted.org_a, connection=ns.conn_a,
        scopes=("projects:read", "projects:write", f"{APP_ID}:read", f"{APP_ID}:write"))
    ns.c = _principal(c_email, promoted.org_a)
    # Org B, with the SAME email as member a. RLS and the org of the session
    # keep the two apart.
    ns.b_same = _principal(a_email, promoted.org_b)
    ns.agent_b_same = _principal(a_email, promoted.org_b, connection=ns.conn_a)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The flags on, the test app served, and no run left from the last test."""
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    monkeypatch.setattr(settings, "agent_access_writes_enabled", True)
    monkeypatch.setattr(settings, "agent_runs_enabled", True)
    monkeypatch.setattr(settings, "agent_door_apps", APP_ID)
    monkeypatch.setattr(settings, "app_requests_orgs", "*")
    monkeypatch.setattr(am, "APPS", (*am.APPS, TEST_APP))
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        c.execute(text("DELETE FROM pm_agent_run_strands"))
    return world


async def _ask(ctx: _Ctx, w: Any, args: dict[str, Any] | None = None, *,
               user: Any = None, op: str = "make_item", key: str | None = None) -> httpx.Response:
    return await ctx(user or w.agent_a, "POST", f"/agent-access/apps/{APP_ID}/requests",
                     {"op": op, "args": args if args is not None else {"name": "x"},
                      "idempotency_key": key or _key()})


async def _decide(ctx: _Ctx, user: Any, request_id: str, decision: str,
                  key: str | None = None) -> httpx.Response:
    return await ctx(user, "POST", f"/agent-access/requests/{request_id}/decision",
                     {"decision": decision, "idempotency_key": key or _key()})


async def _spend(w: Any, request_id: str, digest: str, *, org: str | None = None,
                 member: str | None = None, binding: str | None = None,
                 app: str = APP_ID) -> tuple[Any, str | None]:
    """One spend in its own tenant session, and the class of a refusal."""
    org = org or w.p.org_a
    member = member or w.a_email
    binding = binding or store.mcp_binding(w.conn_a)
    async with _db(org) as db:
        spent = await store.spend(db, org=org, request_id=request_id, app=app,
                                  member=member, binding=binding, digest=digest)
    if spent is not None:
        return spent, None
    async with _db(org) as db:
        why = await store.classify_refusal(db, org=org, request_id=request_id, app=app,
                                           member=member, binding=binding, digest=digest)
    return None, why


def _run_status(w: Any, run_id: str) -> str:
    return _rows(w.p, "SELECT status FROM pm_agent_runs WHERE id = CAST(:r AS uuid)",
                 r=run_id)[0].status


@_DB_GATE
class TestAppRequestsOnARealDatabase:

    async def test_00_the_routes_run_as_a_role_that_cannot_bypass_rls(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )).first()
        assert role is not None and not role[0] and not role[1]

    async def test_01_ask_approve_spend_once(self, w):
        """Case 1. One run, one elicitation, one answer and one spend. The
        spend gives the stored call."""
        args = {"name": "Extruder fan"}
        async with _session(w.p) as ctx:
            asked = await _ask(ctx, w, args)
            assert asked.status_code == 201, asked.text
            opened = asked.json()
            assert opened["state"] == "awaiting" and opened["created"] is True
            rid = opened["request_id"]
            listed = await ctx(w.a, "GET", "/agent-access/requests")
            assert listed.status_code == 200, listed.text
            [row] = listed.json()["rows"]
            assert row["request_id"] == rid and row["state"] == "awaiting"
            assert row["card"] == {
                "app_name": "Test requests", "title": "Make an item.",
                "class_line": "You can undo this in Test requests.",
                "client": "Claude Code"}
            assert row["call"]["body"] == args and row["class"] == "B"
            decided = await _decide(ctx, w.a, rid, "approve")
            assert decided.status_code == 200, decided.text
            assert decided.json()["state"] == "approved"
            assert (await ctx(w.a, "GET", "/agent-access/requests")).json()["rows"] == []
            spent, why = await _spend(w, rid, _edge_digest(args, w.conn_a))
            assert why is None and spent is not None
            assert spent.call == _call_of(args) and spent.op == "make_item" and spent.cls == "B"
            again, why = await _spend(w, rid, _edge_digest(args, w.conn_a))
            assert again is None and why == "spent"
            read = await ctx(w.a, "GET", f"/agent-access/requests/{rid}")
            assert read.json()["state"] == "done"
        assert _count(w.p, "SELECT count(*) FROM pm_agent_runs") == 1
        assert _run_status(w, opened["run_id"]) == "completed"
        kinds = [r.type for r in _rows(
            w.p, "SELECT type FROM pm_agent_run_activities ORDER BY seq")]
        assert kinds == ["elicitation", "prompt", "action"]
        [action] = _rows(w.p, "SELECT content, task_id, app FROM pm_agent_run_activities"
                              " WHERE type = 'action'")
        params = action.content["parameters"]
        assert action.task_id is None and action.app == APP_ID
        assert params["request_id"] == rid and params["op"] == "make_item"
        assert params["class"] == "B" and len(params["digest"]) == store.DIGEST_SHOWN
        assert "Extruder" not in json.dumps(action.content), "the audit holds no value"
        # No bell and no task comment for an app run (D1).
        assert _count(w.p, "SELECT count(*) FROM pm_notifications") == 0

    async def test_02_two_spends_at_once_have_one_winner(self, w):
        """Case 2. Two sessions spend one request at once: one winner."""
        async with _session(w.p) as ctx:
            rid = (await _ask(ctx, w)).json()["request_id"]
            assert (await _decide(ctx, w.a, rid, "approve")).status_code == 200
            digest = _edge_digest({"name": "x"}, w.conn_a)
            results = await asyncio.gather(_spend(w, rid, digest), _spend(w, rid, digest))
        won = [s for s, _ in results if s is not None]
        assert len(won) == 1, results
        assert [why for _, why in results if why is not None] == ["spent"]
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities"
                           " WHERE type = 'action'") == 1

    async def test_03_expiry_refuses_a_spend_and_an_approve(self, w):
        """Case 3. A spend after the expiry is refused. An approve after it
        is 409. A decline after it closes the run."""
        async with _session(w.p) as ctx:
            rid = (await _ask(ctx, w)).json()["request_id"]
            assert (await _decide(ctx, w.a, rid, "approve")).status_code == 200
            _expire(w.p, rid)
            spent, why = await _spend(w, rid, _edge_digest({"name": "x"}, w.conn_a))
            assert spent is None and why == "expired"
            later = await _ask(ctx, w)
            rid2, run2 = later.json()["request_id"], later.json()["run_id"]
            _expire(w.p, rid2)
            approve = await _decide(ctx, w.a, rid2, "approve")
            assert approve.status_code == 409, approve.text
            assert approve.json()["detail"]["error"] == "request_expired"
            assert _run_status(w, run2) == "awaiting"
            decline = await _decide(ctx, w.a, rid2, "decline")
            assert decline.status_code == 200, decline.text
            assert decline.json()["state"] == "declined"
        assert _run_status(w, run2) == "completed"

    async def test_04_an_approve_and_a_decline_race_to_one_winner(self, w):
        """Case 4. ``_lock_run`` serialises the two. The second is 409."""
        async with _session(w.p) as ctx:
            asked = (await _ask(ctx, w)).json()
            got = await asyncio.gather(_decide(ctx, w.a, asked["request_id"], "approve"),
                                       _decide(ctx, w.a, asked["request_id"], "decline"))
        assert sorted(r.status_code for r in got) == [200, 409], [r.text for r in got]
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities"
                           " WHERE type = 'prompt'") == 1

    async def test_05_a_wrong_digest_changes_nothing(self, w):
        """Case 5. Another body gives another digest: no spend, and the
        request stays approved, so the right call still spends it."""
        async with _session(w.p) as ctx:
            asked = (await _ask(ctx, w, {"name": "right"})).json()
            assert (await _decide(ctx, w.a, asked["request_id"], "approve")).status_code == 200
            spent, why = await _spend(w, asked["request_id"],
                                      _edge_digest({"name": "wrong"}, w.conn_a))
            assert spent is None and why == "mismatch"
            assert _run_status(w, asked["run_id"]) == "in_progress"
            spent, why = await _spend(w, asked["request_id"],
                                      _edge_digest({"name": "right"}, w.conn_a))
            assert spent is not None and spent.call["body"] == {"name": "right"}

    async def test_06_another_connection_member_or_org_gets_nothing(self, w):
        """Case 6. Another connection of the member, another member, and org
        B with the same email: 404 on a read and a decision, and no spend."""
        args = {"name": "x"}
        async with _session(w.p) as ctx:
            asked = (await _ask(ctx, w, args)).json()
            rid = asked["request_id"]
            own = await ctx(w.agent_a, "GET", f"/agent-access/requests/{rid}")
            assert own.status_code == 200, own.text
            for who in (w.agent_a2, w.c, w.b_same, w.agent_b_same):
                got = await ctx(who, "GET", f"/agent-access/requests/{rid}")
                assert got.status_code == 404, (who.email, got.text)
            for who in (w.c, w.b_same):
                got = await _decide(ctx, who, rid, "approve")
                assert got.status_code == 404, got.text
                assert (await ctx(who, "GET", "/agent-access/requests")).json()["rows"] == []
            assert (await _decide(ctx, w.a, rid, "approve")).status_code == 200
            # Another connection: its own binding, and the digest of the request.
            for digest in (_edge_digest(args, w.conn_a2), _edge_digest(args, w.conn_a)):
                spent, why = await _spend(w, rid, digest, binding=store.mcp_binding(w.conn_a2))
                assert spent is None and why == "not_found"
            # Another member of the org.
            spent, why = await _spend(w, rid, _edge_digest(args, w.conn_a), member=w.c_email)
            assert spent is None and why == "not_found"
            # Org B, with the same email and the same connection id.
            spent, why = await _spend(w, rid, _edge_digest(args, w.conn_a), org=w.p.org_b)
            assert spent is None and why == "not_found"
            assert _run_status(w, asked["run_id"]) == "in_progress"
            spent, _ = await _spend(w, rid, _edge_digest(args, w.conn_a))
            assert spent is not None

    async def _studio(self, w: Any, subject: str = PKG1) -> tuple[str, dict[str, Any]]:
        """Open one Studio request through the store, as B2c will, and
        approve it."""
        call = {"method": "POST", "template": "/studio/packages/{package}/actions/{action}",
                "path": f"/studio/packages/{subject}/actions/approve_order",
                "query": [], "body": {"order": "PO-7", "amount": 120}, "if_match": None}
        async with _session(w.p) as ctx:
            async with _db(w.p.org_a) as db:
                opened = await app_requests.open_request(
                    db, org=w.p.org_a, member=w.a_email, source="studio", app="studio",
                    subject=subject, op="approve_order", cls="C", call=call,
                    idempotency_key=_key(), connection_id=None, via="studio:approve-order",
                    title="Approve an order.", app_name="Orders")
            assert (await _decide(ctx, w.a, opened.request_id, "approve")).status_code == 200
        return opened.request_id, call

    async def test_07_a_studio_request_spends_only_under_its_binding(self, w):
        """Case 7. A Studio request spends with its member, app and package
        only. Another package is a mismatch, and a connection is no match."""
        rid, call = await self._studio(w)
        own = store.studio_binding(w.a_email, "studio", PKG1)
        other = store.studio_binding(w.a_email, "studio", PKG2)
        async with _session(w.p):
            # Fix round 1 (review P3-1): the spend and the classes hold the
            # whole binding, so another package reads `not_found`.
            spent, why = await _spend(w, rid, store.call_digest(call, other),
                                      binding=other, app="studio")
            assert spent is None and why == "not_found"
            spent, why = await _spend(w, rid, store.call_digest(call, own),
                                      binding=store.mcp_binding(w.conn_a), app="studio")
            assert spent is None and why == "not_found"
            spent, why = await _spend(w, rid, store.call_digest(call, own),
                                      binding=own, app="studio", member=w.c_email)
            assert spent is None and why == "not_found"
            spent, why = await _spend(w, rid, store.call_digest(call, own),
                                      binding=own, app="studio")
            assert spent is not None and spent.source == "studio" and spent.subject == PKG1

    async def test_08_the_studio_spend_gives_the_stored_call(self, w):
        """Case 8. The Studio door runs the STORED call from the spend, and
        a changed body of the second call is never in it."""
        rid, call = await self._studio(w)
        changed = {**call, "body": {"order": "PO-7", "amount": 999_999}}
        own = store.studio_binding(w.a_email, "studio", PKG1)
        async with _session(w.p):
            spent, why = await _spend(w, rid, store.call_digest(call, own),
                                      binding=own, app="studio")
        assert spent is not None, why
        assert spent.call == call and spent.call != changed
        assert spent.call["body"]["amount"] == 120

    async def test_09_the_cap_counts_live_requests_only(self, w):
        """Case 9 (AD-13, D6). 20 live requests of one connection, then 429.
        An expired request does not count."""
        async with _session(w.p) as ctx:
            first = None
            for i in range(app_requests.OPEN_CAP):
                got = await _ask(ctx, w, {"name": f"n{i}"})
                assert got.status_code == 201, got.text
                first = first or got.json()["request_id"]
            full = await _ask(ctx, w, {"name": "one more"})
            assert full.status_code == 429, full.text
            assert full.json()["detail"]["error"] == "too_many_requests"
            # Another connection has a cap of its own.
            assert (await _ask(ctx, w, {"name": "n"}, user=w.agent_a2)).status_code == 201
            _expire(w.p, first)
            assert (await _ask(ctx, w, {"name": "one more"})).status_code == 201

    async def test_10_idempotency(self, w):
        """Case 10. The same key and call give the first request (200). The
        same key with another call is 409, and it writes nothing."""
        key = _key()
        async with _session(w.p) as ctx:
            first = await _ask(ctx, w, {"name": "x"}, key=key)
            again = await _ask(ctx, w, {"name": "x"}, key=key)
            assert first.status_code == 201 and again.status_code == 200, again.text
            assert again.json()["request_id"] == first.json()["request_id"]
            other = await _ask(ctx, w, {"name": "y"}, key=key)
            assert other.status_code == 409, other.text
            assert other.json()["detail"]["error"] == "idempotency_key_reused"
            # The key of another connection is its own.
            mine = await _ask(ctx, w, {"name": "x"}, key=key, user=w.agent_a2)
            assert mine.status_code == 201
            assert mine.json()["request_id"] != first.json()["request_id"]
        assert _count(w.p, "SELECT count(*) FROM pm_agent_runs") == 2

    async def test_11_a_large_call_or_a_secret_key_is_refused(self, w):
        """Case 11 (D8 rules 1 and 4). 32 KiB and one byte of call is 413.
        A nested key that names a secret is 422. Each stores nothing."""

        def size(n: int) -> int:
            stored = _call_of({"name": "x" * n})
            return len(json.dumps(stored, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8"))

        n = app_requests.MAX_CALL_BYTES - size(0)
        assert size(n) == app_requests.MAX_CALL_BYTES
        async with _session(w.p) as ctx:
            big = await _ask(ctx, w, {"name": "x" * (n + 1)})
            assert big.status_code == 413, big.text
            assert big.json()["detail"]["error"] == "payload_too_large"
            for args in ({"name": "x", "meta": {"inner": [{"Stripe_API_Key": "v"}]}},
                         {"name": "x", "refresh_token": "v"},
                         {"name": "x", "deep": [[{"password": 1}]]}):
                secret = await _ask(ctx, w, args)
                assert secret.status_code == 422, secret.text
                assert secret.json()["detail"]["error"] == "secret_in_payload"
            assert _count(w.p, "SELECT count(*) FROM pm_agent_runs") == 0
            fits = await _ask(ctx, w, {"name": "x" * n})
            assert fits.status_code == 201, fits.text

    async def test_12_class_a_and_x_are_refused(self, w):
        """Case 12. A read or an X class never waits for the member. The
        route also refuses a read op and a proposal."""
        async with _session(w.p) as ctx:
            for cls in ("A", "X"):
                async with _db(w.p.org_a) as db:
                    with pytest.raises(HTTPException) as refused:
                        await app_requests.open_request(
                            db, org=w.p.org_a, member=w.a_email, source="mcp", app=APP_ID,
                            subject="", op="get_item", cls=cls, call=_call_of({"name": "x"}),
                            idempotency_key=_key(), connection_id=w.conn_a, via=VIA)
                assert refused.value.status_code == 422
                assert refused.value.detail["error"] == "class_refused"
            for op, args in (("get_item", {"item_id": PKG1}), ("file_draft", {"name": "x"})):
                got = await _ask(ctx, w, args, op=op)
                assert got.status_code == 422, got.text
            assert (await _ask(ctx, w, op="no_such_op")).status_code == 422
            # The ask is an agent's, and it needs the write scope.
            assert (await _ask(ctx, w, user=w.a)).status_code == 404
            read_only = _principal(w.a_email, w.p.org_a, connection=w.conn_a,
                                   scopes=(f"{APP_ID}:read",))
            assert (await _ask(ctx, w, user=read_only)).status_code == 403
        assert _count(w.p, "SELECT count(*) FROM pm_agent_runs") == 0

    async def test_13_an_app_run_never_shows_as_a_task_run(self, w):
        """Case 13 (AD-16). An approved app request, quiet for three hours,
        with its connection: the runs list, a read by id, ``my_work``
        (``needs_agent``), the pill, Needs attention, the analytics and the
        stranded sweep each pass it by."""
        from gateway import agent_run_sweep
        from gateway.routes.projects import analytics_agents, analytics_waiting, filters

        async with _session(w.p) as ctx:
            asked = (await _ask(ctx, w)).json()
            run_id = asked["run_id"]
            assert (await _decide(ctx, w.a, asked["request_id"], "approve")).status_code == 200
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_agent_runs SET last_activity_at = now() - interval '3 hours',"
                    " last_agent_activity_at = now() - interval '3 hours'"
                    " WHERE id = CAST(:r AS uuid)"), {"r": run_id})
                # A failed app run too, for the readers that count failures.
                c.execute(text(
                    "INSERT INTO pm_agent_runs (organization_id, app, member_email,"
                    " connection_id, client_via, status, started_by, ended_at)"
                    " VALUES (CAST(:o AS uuid), :app, :m, CAST(:c AS uuid), :via, 'failed',"
                    " :via, now())"),
                    {"o": w.p.org_a, "app": APP_ID, "m": w.a_email, "c": w.conn_a,
                     "via": VIA})
            for path in ("/projects/agent-runs", "/projects/agent-runs?needs_agent=true",
                         "/projects/agent-runs?status=in_progress"):
                for who in (w.a, w.agent_a_projects):
                    got = await ctx(who, "GET", path)
                    assert got.status_code == 200, got.text
                    assert got.json()["rows"] == [], path
            for who in (w.a, w.agent_a_projects):
                got = await ctx(who, "GET", f"/projects/agent-runs/{run_id}")
                assert got.status_code == 404, got.text
            posted = await ctx(w.agent_a_projects, "POST",
                               f"/projects/agent-runs/{run_id}/activities",
                               {"type": "thought", "body": "x", "idempotency_key": _key()})
            assert posted.status_code == 404, posted.text
            attention = await ctx(w.a, "GET", "/projects/needs-attention")
            assert attention.status_code == 200, attention.text
            assert run_id not in attention.text
            async with _db(w.p.org_a) as db:
                pills = (await db.execute(text(filters.agent_state_sql("TRUE")))).fetchall()
                assert pills == [], "the pill reads task runs only"
                failed = (await db.execute(text(
                    analytics_agents.failed_today_sql("TRUE")))).scalar()
                assert failed == 0
                seen = (await db.execute(text(
                    analytics_waiting.agent_runs_seen_sql("TRUE")))).scalar()
                assert seen is False
            result = agent_run_sweep.TickResult()
            await agent_run_sweep.run_org(w.p.org_a, result)
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_strands") == 0
        assert _run_status(w, run_id) == "in_progress", "the sweep never closes an app run"

    async def test_14_the_migration_on_a_database_with_old_task_runs(self, w):
        """Case 14 (R6). The schema of before the migration, with an old task
        run and its activity. The migration keeps each row valid, and the
        old write still lands. Then it rolls back, so nothing stays."""
        sql = _migration().read_text(encoding="utf-8")
        body = sql.replace("BEGIN;", "").replace("COMMIT;", "")
        with w.p.admin_engine.connect() as c:
            trans = c.begin()
            try:
                for stmt in (
                    "ALTER TABLE pm_agent_runs DROP CONSTRAINT pm_agent_runs_one_subject",
                    "ALTER TABLE pm_agent_runs DROP CONSTRAINT pm_agent_runs_app_shape",
                    "ALTER TABLE pm_agent_run_activities"
                    " DROP CONSTRAINT pm_agent_run_activities_one_subject",
                    "ALTER TABLE pm_agent_run_activities"
                    " DROP CONSTRAINT pm_agent_run_activities_app_shape",
                    "DROP INDEX pm_agent_runs_app_member",
                    "DROP INDEX pm_agent_runs_app_open_connection",
                    "ALTER TABLE pm_agent_runs DROP COLUMN app",
                    "ALTER TABLE pm_agent_run_activities DROP COLUMN app",
                    "ALTER TABLE pm_agent_runs ALTER COLUMN task_id SET NOT NULL",
                    "ALTER TABLE pm_agent_run_activities ALTER COLUMN task_id SET NOT NULL",
                    "ALTER TABLE pm_agent_runs DROP CONSTRAINT pm_agent_runs_client_via_check",
                    "ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_client_via_check"
                    " CHECK (client_via ~ '^mcp:[a-z0-9:_.-]{1,60}$')",
                    "ALTER TABLE pm_agent_run_activities"
                    " DROP CONSTRAINT pm_agent_run_activities_actor_via_check",
                    "ALTER TABLE pm_agent_run_activities"
                    " ADD CONSTRAINT pm_agent_run_activities_actor_via_check"
                    " CHECK (actor_via IS NULL OR actor_via ~ '^mcp:[a-z0-9:_.-]{1,60}$')",
                ):
                    c.execute(text(stmt))
                old_run = c.execute(text(
                    "INSERT INTO pm_agent_runs (organization_id, task_id, member_email,"
                    " connection_id, client_via, status, started_by)"
                    " VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m, CAST(:c AS uuid),"
                    " :via, 'in_progress', :via) RETURNING id"),
                    {"o": w.p.org_a, "t": w.task_a, "m": w.a_email, "c": w.conn_a,
                     "via": VIA}).scalar()
                c.execute(text(
                    "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id,"
                    " type, content, actor, actor_via) VALUES (CAST(:o AS uuid),"
                    " CAST(:r AS uuid), CAST(:t AS uuid), 'thought', '{}', :m, :via)"),
                    {"o": w.p.org_a, "r": old_run, "t": w.task_a, "m": w.a_email, "via": VIA})
                with c.connection.dbapi_connection.cursor() as cur:
                    cur.execute(body)
                valid = c.execute(text(
                    "SELECT conname, convalidated FROM pg_constraint WHERE conname IN"
                    " ('pm_agent_runs_one_subject', 'pm_agent_run_activities_one_subject',"
                    "  'pm_agent_runs_client_via_check',"
                    "  'pm_agent_run_activities_actor_via_check')")).fetchall()
                assert len(valid) == 4 and all(v.convalidated for v in valid), valid
                [kept] = c.execute(text(
                    "SELECT task_id, app FROM pm_agent_runs WHERE id = :r"), {"r": old_run})
                assert str(kept.task_id) == w.task_a and kept.app is None
                # The old write: a task, no app, an `mcp:` stamp.
                c.execute(text(
                    "INSERT INTO pm_agent_runs (organization_id, task_id, member_email,"
                    " client_via, status, started_by) VALUES (CAST(:o AS uuid),"
                    " CAST(:t AS uuid), :m, :via, 'completed', :via)"),
                    {"o": w.p.org_a, "t": w.task_a, "m": w.a_email, "via": VIA})
                # A Studio app run is legal now. No subject, or two, is not.
                c.execute(text(
                    "INSERT INTO pm_agent_runs (organization_id, app, member_email,"
                    " client_via, status, started_by) VALUES (CAST(:o AS uuid), 'studio',"
                    " :m, 'studio:approve-order', 'created', 'studio:approve-order')"),
                    {"o": w.p.org_a, "m": w.a_email})
                for task, app in ((None, None), (w.task_a, "studio")):
                    nested = c.begin_nested()
                    with pytest.raises(Exception, match="one_subject"):
                        c.execute(text(
                            "INSERT INTO pm_agent_runs (organization_id, task_id, app,"
                            " member_email, client_via, status, started_by)"
                            " VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :app, :m, :via,"
                            " 'completed', :via)"),
                            {"o": w.p.org_a, "t": task, "app": app, "m": w.a_email,
                             "via": VIA})
                    nested.rollback()
            finally:
                trans.rollback()
        # A second run of the whole file changes nothing (idempotent replay).
        with w.p.admin_engine.connect() as c:
            with c.connection.dbapi_connection.cursor() as cur:
                cur.execute(sql)
            c.commit()

    async def test_15_the_decision_route_refuses_a_signed_agent(self, w):
        """Case 15. A signed agent cannot decide, even on its own request,
        and nothing changes. The list is the member's too."""
        async with _session(w.p) as ctx:
            asked = (await _ask(ctx, w)).json()
            got = await _decide(ctx, w.agent_a, asked["request_id"], "approve")
            assert got.status_code == 403, got.text
            assert (await ctx(w.agent_a, "GET", "/agent-access/requests")).status_code == 404
        assert _run_status(w, asked["run_id"]) == "awaiting"
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities"
                           " WHERE type = 'prompt'") == 0

    async def test_16_the_decision_key_replays_the_first_answer(self, w):
        """The same key gives the first answer and writes nothing. A second
        answer with a new key is 409."""
        key = _key()
        async with _session(w.p) as ctx:
            rid = (await _ask(ctx, w)).json()["request_id"]
            first = await _decide(ctx, w.a, rid, "approve", key=key)
            again = await _decide(ctx, w.a, rid, "approve", key=key)
            assert first.status_code == again.status_code == 200, again.text
            assert again.json()["created"] is False
            other = await _decide(ctx, w.a, rid, "decline")
            assert other.status_code == 409, other.text
            wrong = await _decide(ctx, w.a, rid, "decline", key=key)
            assert wrong.status_code == 409, wrong.text
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities"
                           " WHERE type = 'prompt'") == 1

    async def test_17_the_routes_are_dark(self, w, monkeypatch):
        """Each route is 404 while agent access is off, and the ask is 404
        while the door serves no app."""
        from acb_common import get_settings

        async with _session(w.p) as ctx:
            # Review P2-1: agent access on, the org flag on another org.
            monkeypatch.setattr(get_settings(), "app_requests_orgs", w.p.org_b)
            assert (await ctx(w.a, "GET", "/agent-access/requests")).status_code == 404
            assert (await _ask(ctx, w)).status_code == 404
            monkeypatch.setattr(get_settings(), "app_requests_orgs", w.p.org_a)
            assert (await ctx(w.a, "GET", "/agent-access/requests")).status_code == 200
            monkeypatch.setattr(get_settings(), "agent_door_apps", "")
            assert (await _ask(ctx, w)).status_code == 404
            monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
            assert (await ctx(w.a, "GET", "/agent-access/requests")).status_code == 404
            got = await _decide(ctx, w.a, str(uuid.uuid4()), "approve")
            assert got.status_code == 404

    # ── Fix round 1 ─────────────────────────────────────────────────────────

    async def test_18_a_value_the_card_cannot_show_exactly_is_refused(self, w):
        """Review P2-2 and P3-2. The browser parses an integer past 2**53 - 1
        to another number, so the card would show a number other than the
        one that runs. U+0000 breaks the JSONB insert, and NaN and Infinity
        are no JSON at all. Each is 422 ``bad_args`` at any depth, and
        nothing is stored."""
        safe = 2**53 - 1
        bad: list[dict[str, Any]] = [
            {"name": "x", "qty": 2**53 + 1},
            {"name": "x", "qty": -(2**53 + 1)},
            {"name": "x", "meta": {"lines": [{"qty": 2**53 + 1}]}},
            {"name": "a\u0000b"},
            {"name": "x", "meta": {"k\u0000": 1}},
        ]
        # httpx refuses to write NaN, so these bodies go as raw text. The
        # gateway's JSON parse takes NaN and Infinity.
        raw = ['{"name": "x", "ratio": NaN}', '{"name": "x", "ratio": Infinity}',
               '{"name": "x", "meta": [-Infinity]}']
        async with _session(w.p) as ctx:
            for args in bad:
                got = await _ask(ctx, w, args)
                assert got.status_code == 422, (repr(args)[:60], got.text)
                assert got.json()["detail"]["error"] == "bad_args", got.text
            ctx.users[str(id(w.agent_a))] = w.agent_a
            for text_args in raw:
                body = f'{{"op": "make_item", "idempotency_key": "{_key()}", "args": {text_args}}}'
                got = await ctx.client.post(
                    f"/agent-access/apps/{APP_ID}/requests", content=body,
                    headers={"x-test-as": str(id(w.agent_a)),
                             "content-type": "application/json"})
                assert got.status_code == 422, (text_args, got.text)
                assert got.json()["detail"]["error"] == "bad_args", got.text
            assert _count(w.p, "SELECT count(*) FROM pm_agent_runs") == 0
            fits = await _ask(ctx, w, {"name": "x", "qty": safe, "neg": -safe, "f": 1.5})
            assert fits.status_code == 201, fits.text
            listed = (await ctx(w.a, "GET", "/agent-access/requests")).json()["rows"]
            # The decision key is stored as it came, so U+0000 in it is a 422.
            nul_key = await _decide(ctx, w.a, fits.json()["request_id"], "approve",
                                    key="k\u0000k")
            assert nul_key.status_code == 422, nul_key.text
        assert listed[0]["call"]["body"]["qty"] == safe

    async def test_19_a_studio_spend_holds_the_binding_of_its_request(self, w):
        """Review P3-1. A Studio request of package A never spends under the
        binding of package B, even with the stored digest. Before the fix
        the spend read only the connection part of the binding."""
        rid, call = await self._studio(w, PKG1)
        stored = store.call_digest(call, store.studio_binding(w.a_email, "studio", PKG1))
        other = store.studio_binding(w.a_email, "studio", PKG2)
        async with _session(w.p):
            spent, why = await _spend(w, rid, stored, binding=other, app="studio")
            assert spent is None and why == "not_found"
            spent, _ = await _spend(w, rid, stored,
                                    binding=store.studio_binding(w.a_email, "studio", PKG1),
                                    app="studio")
        assert spent is not None

    async def test_20_the_cap_holds_under_a_race(self, w):
        """The probe of the review, kept. Four asks at once at 19 live
        requests: one gets in, three get 429."""
        async with _session(w.p) as ctx:
            for i in range(app_requests.OPEN_CAP - 1):
                assert (await _ask(ctx, w, {"name": f"n{i}"})).status_code == 201
            got = await asyncio.gather(*[_ask(ctx, w, {"name": f"r{i}"}) for i in range(4)])
        assert sorted(r.status_code for r in got) == [201, 429, 429, 429]

    async def test_21_an_approve_and_many_spends_at_once_spend_once(self, w):
        """The probe of the review, kept. An approve races four spends, and
        four more follow: one spend wins in all."""
        async with _session(w.p) as ctx:
            rid = (await _ask(ctx, w)).json()["request_id"]
            digest = _edge_digest({"name": "x"}, w.conn_a)
            first = await asyncio.gather(_decide(ctx, w.a, rid, "approve"),
                                         *[_spend(w, rid, digest) for _ in range(4)])
            later = await asyncio.gather(*[_spend(w, rid, digest) for _ in range(4)])
        wins = [s for s, _ in [*first[1:], *later] if s is not None]
        assert len(wins) == 1
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities"
                           " WHERE type = 'action'") == 1

    async def test_22_a_replay_gives_the_state_of_the_request(self, w):
        """Verifier P3-8. The same key and call after an approve give the
        first request in its real state, not ``awaiting``."""
        key = _key()
        async with _session(w.p) as ctx:
            first = (await _ask(ctx, w, key=key)).json()
            assert (await _decide(ctx, w.a, first["request_id"], "approve")).status_code == 200
            again = await _ask(ctx, w, key=key)
            assert again.status_code == 200, again.text
            assert again.json()["state"] == "approved"
            assert again.json()["expires_at"] == first["expires_at"]

    async def test_23_the_class_never_comes_from_the_body(self, w):
        """Verifier P3-7. A body key ``cls`` is 422, because the body forbids
        extra keys. An ``args`` key ``cls`` is a plain field of the call: the
        class stays the manifest's."""
        async with _session(w.p) as ctx:
            top = await ctx(w.agent_a, "POST", f"/agent-access/apps/{APP_ID}/requests",
                            {"op": "make_item", "args": {"name": "x"}, "cls": "A",
                             "idempotency_key": _key()})
            assert top.status_code == 422, top.text
            inner = await _ask(ctx, w, {"name": "x", "cls": "A"})
            assert inner.status_code == 201, inner.text
            [row] = (await ctx(w.a, "GET", "/agent-access/requests")).json()["rows"]
        assert row["class"] == "B" and row["call"]["body"]["cls"] == "A"
