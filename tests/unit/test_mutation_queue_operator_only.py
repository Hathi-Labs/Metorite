"""Only an admin of a reviewing org uses the self-mutation queue (2026-10-19).

The eight routes under ``/agent/mutations`` checked only that a caller was
signed in. ``require_internal_auth`` proves that the Next.js server sent the
call, and every BFF call carries that token. So any member of the org that
owned a row could approve it. An approve pushes to the agent's remote, or
opens a monorepo PR, with the operator's credentials.

The routes now take two checks, in order:

1. ``require_permission("agents:manage")``. Only the owner and admin roles
   hold it.
2. ``_require_mutation_reviewer``. The org is ``organization.first_party``, or
   it is in ``AGENT_REGISTRY_ORGS``.

Why ``first_party``: self-mutation writes rows only for a first-party org
(MT-0b), and RLS keeps each row in its org. On production the first-party org
is not in the registry list, so the registry check alone locked out everyone
who can see a row.

This file is the fence. It asserts the gate on every route. It drives each
route as a member, as a customer admin, as a first-party admin and as an
operator admin. A refused call must reach no git process and no database
session. The last test reads ``first_party`` from a real Postgres (R8).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, get_current_user
from acb_auth.permissions import EffectiveAccess
from acb_common import get_settings
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from gateway.routes import agent as agent_routes

# Resolved by name as a fixture (R8 test below). The import is load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

OPERATOR = "11111111-1111-1111-1111-111111111111"
CUSTOMER = "22222222-2222-2222-2222-222222222222"
FIRST_PARTY = "44444444-4444-4444-4444-444444444444"
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


MEMBER_OF_FIRST_PARTY = _user(FIRST_PARTY, _MEMBER_GRANTS, "member")
ADMIN_OF_CUSTOMER = _user(CUSTOMER, _ADMIN_GRANTS, "admin")
ADMIN_OF_FIRST_PARTY = _user(FIRST_PARTY, _ADMIN_GRANTS, "admin")
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
def test_each_queue_route_carries_the_gate_in_order(
    method: str, path: str, writes: bool,
) -> None:
    calls = _calls(_route(method, path))
    perm = [i for i, c in enumerate(calls) if _requires(c, "agents:manage")]
    assert perm, (method, path)
    assert agent_routes._require_mutation_reviewer in calls, (method, path)
    # The permission check runs first, so a member never causes the org read.
    assert perm[0] < calls.index(agent_routes._require_mutation_reviewer)
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


def test_the_registry_writers_keep_the_registry_gate_only() -> None:
    """The first-party path is for the queue. Pull and its siblings are untouched."""
    for method, path in [
        ("POST", "/agent/{name}/pull"), ("POST", "/agent"),
        ("DELETE", "/agent/{name}"), ("PATCH", "/agent/{name}"),
    ]:
        calls = _calls(_route(method, path))
        assert agent_routes._require_registry_operator in calls, (method, path)
        assert agent_routes._require_mutation_reviewer not in calls, (method, path)


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
        if sql.startswith("SELECT") and "FROM pending_commit ORDER BY" in sql:
            return _Result(rows=[self._row] if self._row is not None else [])
        if sql.startswith("SELECT"):
            return _Result(rows=[])
        return _Result(rowcount=1 if self._row is not None else 0)

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

    state: dict[str, Any] = {
        "git": [], "sessions": 0, "audit": [], "row": None, "org_reads": [],
        "first_party": {FIRST_PARTY: True, OPERATOR: False, CUSTOMER: False},
        "read_error": None,
    }

    async def _fake_exec(*args: Any, **_kw: Any) -> _Proc:
        state["git"].append(args)
        return _Proc()

    async def _fake_git_exec(clone_dir: str, args: list[str]) -> int:
        state["git"].append(("git", *args))
        return 0

    async def _fake_push(clone_dir: str, sha: str) -> tuple[bool, str | None]:
        state["git"].append(("git", "push", sha))
        return True, None

    async def _fake_first_party(org: str | None) -> bool | None:
        state["org_reads"].append(org)
        if state["read_error"] is not None:
            raise state["read_error"]
        return state["first_party"].get(org)

    @contextlib.contextmanager
    def _fake_session(_user: UserContext):
        state["sessions"] += 1
        yield _Session(state["row"])

    import acb_audit
    from acb_auth import agent_operator

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(agent_routes, "_git_exec", _fake_git_exec)
    monkeypatch.setattr(agent_routes, "_git_push_with_rebase", _fake_push)
    monkeypatch.setattr(agent_routes, "_member_graph_session", _fake_session)
    # The ONE read of first_party, which the shared rule calls. The queue's gate
    # reaches it through acb_auth.agent_operator.may_review_agent_commits.
    monkeypatch.setattr(agent_operator, "read_first_party", _fake_first_party)
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
        test_summary="ok", reviewed_by=None, reviewed_at=None,
        created_at="2026-10-19 00:00:00", mutation_mode=None, pr_url=None,
        target_path=None,
    )


def _call(client: TestClient, method: str, path: str):
    return client.request(method, path, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.mark.parametrize(("method", "path"), _WRITES + _READS)
@pytest.mark.parametrize(
    "caller", [MEMBER_OF_FIRST_PARTY, ADMIN_OF_CUSTOMER],
    ids=["first-party-member", "customer-admin"],
)
def test_a_caller_without_the_gate_gets_403_and_touches_nothing(
    rig, caller: UserContext, method: str, path: str,
) -> None:
    rig["row"] = _row()
    res = _call(rig["as"](caller), method, path)
    assert res.status_code == 403, res.text
    assert rig["git"] == []
    assert rig["sessions"] == 0
    assert rig["audit"] == []


def test_a_member_is_refused_before_the_org_is_read(rig) -> None:
    res = _call(rig["as"](MEMBER_OF_FIRST_PARTY), *_WRITES[0])
    assert res.json()["detail"] == "Forbidden: missing permission(s) ['agents:manage']."
    assert rig["org_reads"] == []


def test_a_customer_admin_is_told_who_reviews(rig) -> None:
    res = _call(rig["as"](ADMIN_OF_CUSTOMER), *_WRITES[0])
    assert res.json()["detail"] == agent_routes._MUTATION_REVIEWER_DENIED
    assert rig["org_reads"] == [CUSTOMER]


@pytest.mark.parametrize("admin", [ADMIN_OF_FIRST_PARTY, ADMIN_OF_OPERATOR],
                         ids=["first-party-admin", "operator-admin"])
def test_a_reviewing_admin_approves_and_the_commit_is_pushed(rig, admin) -> None:
    rig["row"] = _row()
    res = _call(rig["as"](admin), *_WRITES[0])
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    assert ("git", "push", "a" * 40) in rig["git"]
    assert [e.action for e in rig["audit"]] == ["mutation_commit_approved"]
    assert rig["audit"][0].actor == "human:admin@example.com"


def test_an_operator_org_passes_without_reading_first_party(rig) -> None:
    rig["row"] = _row()
    assert _call(rig["as"](ADMIN_OF_OPERATOR), *_READS[1]).status_code == 200
    assert rig["org_reads"] == []


def test_the_first_party_admin_reads_the_queue_and_sees_the_row(rig) -> None:
    rig["row"] = _row()
    client = rig["as"](ADMIN_OF_FIRST_PARTY)
    listed = _call(client, *_READS[0])
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json() if r.get("type") == "pending_commit"] == [COMMIT]
    pending = _call(client, *_READS[1])
    assert [r["id"] for r in pending.json()] == [COMMIT]
    assert _call(client, *_READS[2]).json()["id"] == COMMIT
    assert rig["org_reads"] == [FIRST_PARTY] * 3


@pytest.mark.parametrize(
    ("method", "path", "action"),
    [
        (*_WRITES[1], "mutation_commit_rejected"),
        (*_WRITES[2], "mutation_commit_remutate_requested"),
        (*_WRITES[3], "mutation_commit_deleted"),
        (*_WRITES[4], "mutation_audit_dismissed"),
    ],
)
def test_a_first_party_admin_runs_each_other_write_and_it_is_audited(
    rig, method: str, path: str, action: str,
) -> None:
    rig["row"] = _row()
    res = _call(rig["as"](ADMIN_OF_FIRST_PARTY), method, path)
    assert res.status_code == 200, res.text
    assert [e.action for e in rig["audit"]] == [action]
    assert rig["audit"][0].actor == "human:admin@example.com"


@pytest.mark.parametrize("failure", ["raises", "no-row"])
def test_the_org_read_fails_closed(rig, failure: str) -> None:
    rig["row"] = _row()
    if failure == "raises":
        rig["read_error"] = RuntimeError("db down")
    else:
        rig["first_party"] = {}
    res = _call(rig["as"](ADMIN_OF_FIRST_PARTY), *_WRITES[0])
    assert res.status_code == 403, res.text
    assert res.json()["detail"] == agent_routes._MUTATION_REVIEWER_DENIED
    assert rig["git"] == []
    assert rig["sessions"] == 0


def test_an_admin_with_no_org_is_refused_and_no_sole_org_read_runs(rig) -> None:
    """``_read_first_party(None)`` answers for a sole org. It must never run here."""
    res = _call(rig["as"](_user(None, _ADMIN_GRANTS, "admin")), *_WRITES[0])
    assert res.status_code == 403
    assert rig["org_reads"] == []


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
    res = _call(rig["as"](ADMIN_OF_FIRST_PARTY), method, path)
    assert res.status_code == 404, res.text
    assert rig["git"] == []
    assert rig["audit"] == []


def test_a_delete_of_another_orgs_row_removes_nothing_and_writes_no_audit(rig) -> None:
    rig["row"] = None
    res = _call(rig["as"](ADMIN_OF_FIRST_PARTY), *_WRITES[3])
    assert res.status_code == 200
    assert res.json()["rows_deleted"] == 0
    assert rig["audit"] == []


# ── R8: the org read against a real, RLS-promoted Postgres. ─────────────────


@_DB_GATE
def test_the_reviewer_check_reads_first_party_from_postgres(
    promoted, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The check reads ``organization.first_party`` as the non-privileged app role."""
    from acb_common import db as acb_db
    from sqlalchemy import text

    with promoted.admin_engine.begin() as c:
        c.execute(text("UPDATE organization SET first_party = (id = CAST(:a AS uuid)) "
                       "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"a": promoted.org_a, "b": promoted.org_b})

    monkeypatch.setenv("DATABASE_URL", promoted.app_url.render_as_string(hide_password=False))
    monkeypatch.setattr(acb_db, "_ENGINE", None)
    monkeypatch.setattr(acb_db, "_SESSION_FACTORY", None)
    monkeypatch.setattr(get_settings(), "agent_registry_orgs", "", raising=False)

    async def _outcomes() -> dict[str, int]:
        out: dict[str, int] = {}
        try:
            for name, org in (("first_party", promoted.org_a),
                              ("other", promoted.org_b),
                              ("unknown", str(uuid.uuid4()))):
                try:
                    await agent_routes._require_mutation_reviewer(
                        _user(org, _ADMIN_GRANTS, "admin"))
                    out[name] = 200
                except HTTPException as exc:
                    out[name] = exc.status_code
        finally:
            if acb_db._ENGINE is not None:
                await acb_db._ENGINE.dispose()
        return out

    assert asyncio.run(_outcomes()) == {"first_party": 200, "other": 403, "unknown": 403}
