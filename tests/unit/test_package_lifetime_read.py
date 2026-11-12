"""WS-PKG PKG-10e-1 — the lifetime read of the private install, with no database.

Spec: ``project-docs/specs/package_host.md`` §6.2 and the PKG-10e-1 row of
§10. Code: the route ``GET /studio/packages/{id}/lifetime``
(``routes/studio/packages.py`` ``package_lifetime`` and
``_owned_private_install``) and ``package_host/store.py``
(``read_lifetime``). The R8 twin is
``tests/unit/test_package_lifetime_read_r8.py``.

The checks here:

1. The shape. The route is a GET on the one Studio router. The handler
   takes the id and the session user only, so it reads no body and no
   query. A query such as ``now`` or ``member`` changes nothing (R5).
2. The two switches come first. Each answers 404 and opens no session.
3. The owner rule is the one of Keep and Restore. A package of another
   member or org is 404 ``Not found`` and reads no install. An owner with no
   private install gets 200 ``{"install": null}``, never 409.
4. The order of one session: the ``apps`` row lock, the find of the private
   install, then the read by the id that the find gave.
5. The answers for ``active``, ``paused`` and ``archived``. ``restore_until``
   is ``archived_at`` plus 30 days for an archived install only. ``now`` is
   the server clock. The read writes no audit row.
6. The SQL names the bound org and takes no lock.
7. No chat tool and no MCP pair reaches the route.
8. The R8 twin is in the ``--must-run`` lists of both workflows.

**R7.** M1: drop the ``package_host_allowed`` check. Check 2 goes red. M2:
call ``_own_private_install`` in the handler. Check 3 goes red, because an
owner with no private install gets 409. M3: add ``FOR UPDATE`` to
``_LIFETIME_READ_SQL``. Check 6 goes red.
"""

from __future__ import annotations

import ast
import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_auth.agent_oauth import MCP_ROUTES, MCP_WRITE_ROUTES
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from gateway.package_host import store as host_store
from gateway.routes.studio import actions, packages
from gateway.studio import packages_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "owen@pkg10e.test"
BEN = "ben@pkg10e.test"
PID = str(uuid.uuid4())
INSTALL = str(uuid.uuid4())
STUDIO = ["feature:studio"]
NOT_FOUND = {"detail": "Not found"}
PATH = "/studio/packages/{package_id}/lifetime"
ENDS = datetime(2026, 12, 1, 9, 30, tzinfo=UTC)
ARCHIVED = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)
CLOCK = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


# ── The fake world ─────────────────────────────────────────────────────────


class _World:
    """The calls that the route makes, in order. ``owned`` makes the lock
    find the package, ``has_install`` makes the find give the private
    install, and ``life`` is the answer of the read."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sessions = 0
        self.owned = True
        self.has_install = True
        self.life: host_store.Lifetime | None = host_store.Lifetime(
            state="active", ends_at=ENDS, archived_at=None)
        self.members: list[str] = []
        self.audits = 0


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield object()

    async def lock_for_install(db: Any, member: str, pid: str) -> Any:
        w.calls.append("lock")
        if w.owned and member == OWEN and pid == PID:
            return store.InstallTarget(package_id=pid, live_version=1)
        return None

    async def private_install_id(db: Any, *, app_id: str, member: str) -> str | None:
        w.calls.append("find")
        w.members.append(member)
        return INSTALL if w.has_install and app_id == PID else None

    async def read_lifetime(db: Any, *, install_id: str) -> host_store.Lifetime | None:
        w.calls.append(f"read:{install_id}")
        return w.life

    async def audit(*_a: Any, **_k: Any) -> None:
        w.audits += 1

    monkeypatch.setattr(packages, "_tenant_session", session)
    monkeypatch.setattr(packages, "record_app_audit_in", audit)
    monkeypatch.setattr(packages, "record_app_audit_bound", audit)
    monkeypatch.setattr(store, "lock_for_install", lock_for_install)
    monkeypatch.setattr(host_store, "private_install_id", private_install_id)
    monkeypatch.setattr(host_store, "read_lifetime", read_lifetime)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return w


def _client(email: str = OWEN, org: str = ORG) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org, access=build_access(STUDIO))
    return TestClient(app)


def _get(email: str = OWEN, pid: str = PID, query: str = "") -> Any:
    return _client(email).get(f"/studio/packages/{pid}/lifetime{query}")


def _tree() -> ast.Module:
    return ast.parse(Path(packages.__file__).read_text(encoding="utf-8"))


def _handler(name: str) -> ast.AsyncFunctionDef:
    return next(n for n in ast.walk(_tree())
                if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


# ── 1. The shape ───────────────────────────────────────────────────────────


def test_the_route_is_a_get_on_the_one_studio_router() -> None:
    found = [r for r in actions.router.routes if isinstance(r, APIRoute) and r.path == PATH]
    assert [sorted(r.methods) for r in found] == [["GET"]]
    assert found[0].endpoint.__name__ == "package_lifetime"


def test_the_handler_takes_the_id_and_the_session_user_only() -> None:
    """No ``request`` argument, so the handler cannot read a body, a query,
    a date or an org. The org and the member are the session (R5)."""
    handler = _handler("package_lifetime")
    assert [a.arg for a in handler.args.args] == ["package_id", "user"]
    names = {n.id for n in ast.walk(handler) if isinstance(n, ast.Name)}
    for name in ("request", "body", "_raw_body", "_parse_json", "_json_body", "Query"):
        assert name not in names, name


def test_a_query_with_a_date_an_org_or_a_member_changes_nothing(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(packages, "datetime", _Clock)
    r = _get(query=f"?now=2000-01-01T00:00:00Z&member={BEN}&organization_id={uuid.uuid4()}"
                   f"&install_id={uuid.uuid4()}")
    assert r.status_code == 200, r.text
    assert world.members == [OWEN]
    assert world.calls == ["lock", "find", f"read:{INSTALL}"]
    assert r.json()["install"]["now"] == CLOCK.isoformat()


# ── 2. The switches ────────────────────────────────────────────────────────


@pytest.mark.parametrize("off", ["studio_orgs", "package_host_orgs"])
def test_each_switch_off_is_404_and_opens_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch, off: str,
) -> None:
    monkeypatch.setattr(get_settings(), off, "")
    r = _get()
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


def test_the_switches_come_first_in_the_handler() -> None:
    body = _handler("package_lifetime").body[1:]  # the docstring
    tests = [ast.unparse(s.test) for s in body[:2] if isinstance(s, ast.If)]
    assert tests == ["not flags.studio_allowed(user.organization_id)",
                     "not package_host_allowed(user.organization_id)"]
    assert ast.unparse(body[2]) == "pid = _package_id(package_id)"


def test_a_bad_id_is_404_and_opens_no_session(world: _World) -> None:
    r = _get(pid="not-a-uuid")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


# ── 3. The owner rule ──────────────────────────────────────────────────────


def test_a_package_of_another_member_is_404_and_reads_no_install(world: _World) -> None:
    """Ben is a member of the org and does not own the package. The lock
    answers None, as it does for another org, an App Workshop app and an
    archived package."""
    r = _get(email=BEN)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.calls == ["lock"]


def test_an_owner_with_no_private_install_gets_200_null(world: _World) -> None:
    """The calm answer: the page shows no bar. An org or a group install is
    never the answer, because the find reads the private scope only."""
    world.has_install = False
    r = _get()
    assert (r.status_code, r.json()) == (200, {"install": None})
    assert world.calls == ["lock", "find"]


def test_an_install_that_went_between_the_find_and_the_read_is_null(world: _World) -> None:
    world.life = None
    r = _get()
    assert (r.status_code, r.json()) == (200, {"install": None})


def test_the_route_uses_the_one_owner_rule_and_not_the_409_wrapper() -> None:
    calls = {ast.unparse(c.func) for c in ast.walk(_handler("package_lifetime"))
             if isinstance(c, ast.Call)}
    assert "_owned_private_install" in calls
    assert "_own_private_install" not in calls
    assert "store.lock_for_install" not in calls  # no second copy of the rule


# ── 4. One session, in order ───────────────────────────────────────────────


def test_one_session_locks_then_finds_then_reads(world: _World) -> None:
    assert _get().status_code == 200
    assert world.sessions == 1
    assert world.calls == ["lock", "find", f"read:{INSTALL}"]


def test_the_find_comes_before_the_read_in_the_source() -> None:
    handler = _handler("package_lifetime")
    lines = {ast.unparse(c.func): c.lineno for c in ast.walk(handler) if isinstance(c, ast.Call)}
    assert lines["_owned_private_install"] < lines["host_store.read_lifetime"]
    call = next(c for c in ast.walk(handler)
                if isinstance(c, ast.Call) and ast.unparse(c.func) == "host_store.read_lifetime")
    assert [(k.arg, ast.unparse(k.value)) for k in call.keywords] == [
        ("install_id", "install_id")]
    rule = {ast.unparse(c.func): c.lineno for c in ast.walk(_handler("_owned_private_install"))
            if isinstance(c, ast.Call)}
    assert rule["store.lock_for_install"] < rule["host_store.private_install_id"]


# ── 5. The answers ─────────────────────────────────────────────────────────


class _Clock(datetime):
    """A ``datetime`` whose ``now`` is :data:`CLOCK`."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        assert tz is UTC
        return CLOCK


def test_an_active_install_with_an_end_date(world: _World, monkeypatch) -> None:
    monkeypatch.setattr(packages, "datetime", _Clock)
    r = _get()
    assert r.status_code == 200, r.text
    assert r.json() == {"install": {
        "state": "active", "ends_at": ENDS.isoformat(), "archived_at": None,
        "restore_until": None, "now": CLOCK.isoformat()}}


def test_an_active_install_with_no_end_date(world: _World) -> None:
    world.life = host_store.Lifetime(state="active", ends_at=None, archived_at=None)
    got = _get().json()["install"]
    assert (got["state"], got["ends_at"], got["restore_until"]) == ("active", None, None)


def test_a_paused_install(world: _World) -> None:
    world.life = host_store.Lifetime(state="paused", ends_at=ENDS, archived_at=None)
    got = _get().json()["install"]
    assert (got["state"], got["ends_at"], got["archived_at"], got["restore_until"]) == (
        "paused", ENDS.isoformat(), None, None)


def test_an_archived_install_can_be_restored_until_30_days_after_archived_at(
    world: _World,
) -> None:
    world.life = host_store.Lifetime(state="archived", ends_at=ENDS, archived_at=ARCHIVED)
    got = _get().json()["install"]
    assert got["state"] == "archived"
    assert got["archived_at"] == ARCHIVED.isoformat()
    assert got["restore_until"] == (ARCHIVED + timedelta(days=30)).isoformat()
    assert timedelta(days=30) == host_store.RESTORE_DAYS


def test_now_is_the_server_clock_with_a_time_zone(world: _World) -> None:
    before = datetime.now(UTC)
    got = _get().json()["install"]
    after = datetime.now(UTC)
    now = datetime.fromisoformat(got["now"])
    assert now.utcoffset() == timedelta(0)
    assert before <= now <= after


def test_the_read_writes_no_audit_row(world: _World) -> None:
    assert _get().status_code == 200
    assert world.audits == 0
    src = ast.unparse(_handler("package_lifetime"))
    assert "record_app_audit" not in src


# ── 6. The read of the store ───────────────────────────────────────────────


class _Db:
    def __init__(self, row: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.row = row

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.calls.append((str(stmt), dict(params or {})))
        return SimpleNamespace(first=lambda: self.row)


def test_the_sql_names_the_bound_org_and_the_id_and_takes_no_lock() -> None:
    sql = host_store._LIFETIME_READ_SQL
    assert sql.startswith("SELECT state, ends_at, archived_at FROM package_install ")
    for part in (f"organization_id = {host_store._BOUND_ORG}", "id = CAST(:install AS uuid)"):
        assert part in sql, part
    for lock in ("FOR UPDATE", "FOR SHARE", "FOR KEY SHARE", "FOR NO KEY UPDATE",
                 "pg_advisory"):
        assert lock not in sql, lock


def test_the_store_read_runs_one_statement_and_answers_the_lifetime() -> None:
    db = _Db(row=("archived", ENDS, ARCHIVED))
    got = asyncio.run(host_store.read_lifetime(db, install_id=INSTALL))
    assert got == host_store.Lifetime(state="archived", ends_at=ENDS, archived_at=ARCHIVED)
    assert db.calls == [(host_store._LIFETIME_READ_SQL, {"install": INSTALL})]
    assert asyncio.run(host_store.read_lifetime(_Db(), install_id=INSTALL)) is None


def test_a_bad_install_id_is_refused_before_the_database() -> None:
    db = _Db()
    with pytest.raises(host_store.InstallError):
        asyncio.run(host_store.read_lifetime(db, install_id="not-a-uuid"))
    assert db.calls == []


def test_the_store_read_takes_no_org_lock() -> None:
    tree = ast.parse(Path(host_store.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "read_lifetime")
    calls = {ast.unparse(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "_lock_org" not in calls and "_life" not in calls


# ── 7. No chat tool and no MCP pair ────────────────────────────────────────


def test_no_chat_tool_and_no_mcp_pair_reaches_the_route() -> None:
    """The bar of PKG-10e-2 is the first reader. The chat parity of the bar
    waits on the owner (task 2971)."""
    pair = ("GET", PATH)
    assert pair not in MCP_ROUTES and pair not in MCP_WRITE_ROUTES
    tools = (ROOT / "packages/acb_skills/acb_skills/studio_tools.py").read_text(encoding="utf-8")
    assert "/lifetime" not in tools


# ── 8. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_package_lifetime_read_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
