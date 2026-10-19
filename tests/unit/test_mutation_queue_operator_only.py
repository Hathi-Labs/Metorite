"""Only an operator admin reviews the self-mutation queue (2026-10-19).

The eight routes under ``/agent/mutations`` checked only that a caller was
signed in. ``require_internal_auth`` proves that the Next.js server sent the
call, and every BFF call carries that token. So any member of the org that
owned a row could approve it. An approve pushes to the agent's remote, or
opens a monorepo PR, with the operator's credentials.

The routes now take the gate of ``/{name}/pull``: ``agents:manage`` (owner and
admin only) and ``_require_registry_operator`` (``AGENT_REGISTRY_ORGS``).

This file is the fence. It asserts the gate on every route, and it drives each
write route as a plain member, as an admin of a customer org and as an operator
admin. A refused call must reach no git process and no database session.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, get_current_user
from acb_auth.permissions import EffectiveAccess
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from gateway.routes import agent as agent_routes

OPERATOR = "11111111-1111-1111-1111-111111111111"
CUSTOMER = "22222222-2222-2222-2222-222222222222"
TOKEN = "test-internal-token"
COMMIT = "33333333-3333-3333-3333-333333333333"

_MEMBER_GRANTS = frozenset({"feature:chat", "feature:tasks", "agents:run:*"})
_ADMIN_GRANTS = frozenset({"admin:members:manage", "feature:*", "agents:run:*", "agents:manage"})

#: Every route of the queue: method, path, and whether it changes state.
_QUEUE_ROUTES = [
    ("GET", "/agent/mutations", False),
    ("GET", "/agent/mutations/pending", False),
    ("GET", "/agent/mutations/pending/{commit_id}/diff", False),
    ("POST", "/agent/mutations/pending/{commit_id}/approve", True),
    ("POST", "/agent/mutations/pending/{commit_id}/reject", True),
    ("POST", "/agent/mutations/pending/{commit_id}/remutate", True),
    ("DELETE", "/agent/mutations/pending/{commit_id}", True),
    ("DELETE", "/agent/mutations/audit/{run_id}", True),
]

_WRITES = [
    ("POST", f"/agent/mutations/pending/{COMMIT}/approve"),
    ("POST", f"/agent/mutations/pending/{COMMIT}/reject"),
    ("POST", f"/agent/mutations/pending/{COMMIT}/remutate"),
    ("DELETE", f"/agent/mutations/pending/{COMMIT}"),
    ("DELETE", "/agent/mutations/audit/run-1"),
]

_READS = [
    ("GET", "/agent/mutations"),
    ("GET", "/agent/mutations/pending"),
    ("GET", f"/agent/mutations/pending/{COMMIT}/diff"),
]


def _user(org: str | None, grants: frozenset[str], role: str) -> UserContext:
    return UserContext(
        email=f"{role}@example.com",
        role=UserRole.EMPLOYEE,
        organization_id=org,
        access=EffectiveAccess(roles=frozenset({role}), role_granted=grants),
    )


MEMBER_OF_OPERATOR = _user(OPERATOR, _MEMBER_GRANTS, "member")
ADMIN_OF_CUSTOMER = _user(CUSTOMER, _ADMIN_GRANTS, "admin")
ADMIN_OF_OPERATOR = _user(OPERATOR, _ADMIN_GRANTS, "admin")


# ── The structural fence. It reads the router, so it cannot skip. ───────────


def _calls(route: APIRoute) -> list[Any]:
    return [dep.call for dep in route.dependant.dependencies]


def _requires(dep_call: object, permission: str) -> bool:
    for cell in getattr(dep_call, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if isinstance(value, tuple) and permission in value:
            return True
    return False


def _route(method: str, path: str) -> APIRoute:
    found = [
        r for r in agent_routes.router.routes
        if isinstance(r, APIRoute) and r.path == path and method in r.methods
    ]
    assert len(found) == 1, (method, path, found)
    return found[0]


@pytest.mark.parametrize(("method", "path", "writes"), _QUEUE_ROUTES)
def test_each_queue_route_carries_the_operator_admin_gate(
    method: str, path: str, writes: bool,
) -> None:
    calls = _calls(_route(method, path))
    assert agent_routes._require_registry_operator in calls, (method, path)
    assert any(_requires(c, "agents:manage") for c in calls), (method, path)
    if writes:
        from acb_auth import require_internal_auth

        assert require_internal_auth in calls, (method, path)


def test_a_new_queue_route_fails_until_it_is_gated_and_listed() -> None:
    """Never vacuous, and a ninth route under the prefix must join the list."""
    listed = {(m, p) for m, p, _ in _QUEUE_ROUTES}
    seen = set()
    for route in agent_routes.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/agent/mutations"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            seen.add((method, route.path))
    assert seen == listed, sorted(seen ^ listed)


# ── The behaviour, through the real dependency chain. ───────────────────────


class _Result:
    def __init__(self, row: Any = None, rows: list[Any] | None = None, rowcount: int = 0):
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _Session:
    """The tenant session. *row* is what RLS lets this caller see."""

    def __init__(self, row: Any) -> None:
        self._row = row

    def execute(self, stmt: Any, params: dict | None = None) -> _Result:
        sql = " ".join(str(stmt).split())
        if sql.startswith("SELECT") and "WHERE id = :id" in sql:
            return _Result(row=self._row)
        if sql.startswith("SELECT"):
            return _Result(rows=[])
        hit = 1 if self._row is not None else 0
        return _Result(rowcount=hit)

    def commit(self) -> None:
        return None


class _Proc:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"main\n", b""


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch):
    """A test app on the agent router, with every side effect recorded."""
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setattr(get_settings(), "agent_registry_orgs", OPERATOR, raising=False)

    state: dict[str, Any] = {"git": [], "sessions": 0, "audit": [], "row": None}

    async def _fake_exec(*args: Any, **_kw: Any) -> _Proc:
        state["git"].append(args)
        return _Proc()

    async def _fake_git_exec(clone_dir: str, args: list[str]) -> int:
        state["git"].append(("git", *args))
        return 0

    async def _fake_push(clone_dir: str, sha: str) -> tuple[bool, str | None]:
        state["git"].append(("git", "push", sha))
        return True, None

    @contextlib.contextmanager
    def _fake_session(_user: UserContext):
        state["sessions"] += 1
        yield _Session(state["row"])

    import asyncio

    import acb_audit

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(agent_routes, "_git_exec", _fake_git_exec)
    monkeypatch.setattr(agent_routes, "_git_push_with_rebase", _fake_push)
    monkeypatch.setattr(agent_routes, "_member_graph_session", _fake_session)
    monkeypatch.setattr(acb_audit, "record", lambda ev: state["audit"].append(ev))

    app = FastAPI()
    app.include_router(agent_routes.router)

    def _as(user: UserContext) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    state["as"] = _as
    return state


def _row(status: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(
        id=COMMIT, agent_name="agent-x", run_id="run-1",
        local_clone_dir="/nonexistent/clone", commit_sha="a" * 40,
        commit_message="fix", status=status, diff_text="",
        test_summary="ok",
    )


def _call(client: TestClient, method: str, path: str):
    return client.request(method, path, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.mark.parametrize(("method", "path"), _WRITES + _READS)
@pytest.mark.parametrize(
    "caller", [MEMBER_OF_OPERATOR, ADMIN_OF_CUSTOMER], ids=["member", "customer-admin"],
)
def test_a_caller_without_the_gate_gets_403_and_touches_nothing(
    rig, caller: UserContext, method: str, path: str,
) -> None:
    rig["row"] = _row()
    res = _call(rig["as"](caller), method, path)
    assert res.status_code == 403, res.text
    assert "agents:manage" in res.text or "platform operator" in res.text
    assert rig["git"] == []
    assert rig["sessions"] == 0
    assert rig["audit"] == []


def test_the_member_is_told_which_permission_is_missing(rig) -> None:
    res = _call(rig["as"](MEMBER_OF_OPERATOR), *_WRITES[0])
    assert res.json()["detail"] == "Forbidden: missing permission(s) ['agents:manage']."


def test_the_customer_admin_is_told_only_the_operator_reviews(rig) -> None:
    res = _call(rig["as"](ADMIN_OF_CUSTOMER), *_WRITES[0])
    assert "platform operator" in res.json()["detail"]


def test_an_operator_admin_approves_and_the_commit_is_pushed(rig) -> None:
    rig["row"] = _row()
    res = _call(rig["as"](ADMIN_OF_OPERATOR), *_WRITES[0])
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    assert ("git", "push", "a" * 40) in rig["git"]
    assert [e.action for e in rig["audit"]] == ["mutation_commit_approved"]
    assert rig["audit"][0].actor == "human:admin@example.com"


@pytest.mark.parametrize(
    ("method", "path", "action"),
    [
        (*_WRITES[1], "mutation_commit_rejected"),
        (*_WRITES[2], "mutation_commit_remutate_requested"),
        (*_WRITES[3], "mutation_commit_deleted"),
        (*_WRITES[4], "mutation_audit_dismissed"),
    ],
)
def test_an_operator_admin_runs_each_other_write_and_it_is_audited(
    rig, method: str, path: str, action: str,
) -> None:
    rig["row"] = _row()
    res = _call(rig["as"](ADMIN_OF_OPERATOR), method, path)
    assert res.status_code == 200, res.text
    assert [e.action for e in rig["audit"]] == [action]
    assert rig["audit"][0].actor == "human:admin@example.com"


@pytest.mark.parametrize(("method", "path"), _READS)
def test_an_operator_admin_reads_the_queue(rig, method: str, path: str) -> None:
    rig["row"] = _row()
    assert _call(rig["as"](ADMIN_OF_OPERATOR), method, path).status_code == 200


@pytest.mark.parametrize(("method", "path"), _WRITES[:3])
def test_a_row_of_another_org_is_404_and_nothing_is_pushed(
    rig, method: str, path: str,
) -> None:
    """RLS hides another org's row, so the lookup finds none.

    Hermetic: the fake session returns no row. The real RLS on
    ``pending_commit`` is proven against Postgres by
    ``test_h201_readers_under_rls.py::test_pending_commits_read_in_the_members_tenant``.
    """
    rig["row"] = None
    res = _call(rig["as"](ADMIN_OF_OPERATOR), method, path)
    assert res.status_code == 404, res.text
    assert rig["git"] == []
    assert rig["audit"] == []


def test_a_delete_of_another_orgs_row_removes_nothing_and_writes_no_audit(rig) -> None:
    rig["row"] = None
    res = _call(rig["as"](ADMIN_OF_OPERATOR), *_WRITES[3])
    assert res.status_code == 200
    assert res.json()["rows_deleted"] == 0
    assert rig["audit"] == []
