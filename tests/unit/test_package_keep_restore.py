"""WS-PKG PKG-10d — Keep and Restore of the private install, with no database.

Spec: ``project-docs/specs/package_host.md`` §6.2 (steps 1 and 2, restore
and deletion), PKG-Q7 and the PKG-10d row of §10. Code: the routes
``POST /studio/packages/{id}/keep`` and ``POST /studio/packages/{id}/restore``
(``routes/studio/packages.py`` ``keep_package``, ``restore_package`` and
``_own_private_install``), and ``package_host/store.py``
(``private_install_id``, ``keep_install`` and ``restore_install``). The R8
twin is ``tests/unit/test_package_keep_restore_r8.py``.

The checks here:

1. The shape. Each route is a POST on the one Studio router. Each handler
   takes the id and the session user only, so it reads no body. A body key
   such as ``ends_at``, ``now``, ``organization_id`` or ``owner_email``
   changes nothing (R5).
2. The two switches come first. Each answers 404 and opens no session.
3. The owner rule. A package of another member or org is 404 ``Not found``
   and reads no install. An owner with no private install is 409
   ``private_only``, and the message names the rule. The private install is
   the install of the session member, never of a body value.
4. The order of one session: the ``apps`` row lock, the read of the private
   install, then the store call.
5. The answers. A keep is 200, and a second keep is 200 with ``changed``
   False. A restore is 200 with the new ``ends_at``. The time of a restore
   is the server clock, with a time zone. Each store refusal is its
   ``detail_for`` with its own status.
6. The read of the store names the bound org, folds the member, and refuses
   a value that is not an address before the database.
7. No chat tool and no MCP pair reaches the two routes.
8. The R8 twin is in the ``--must-run`` lists of both workflows.

**R7.** M1: drop the ``package_host_allowed`` check of ``keep_package``.
Check 2 goes red. M2: call ``private_install_id`` with a body value. Check 1
goes red, because the handler takes no body. M3: pass ``datetime.now()``
with no zone. Check 5 goes red, and the store refuses it too.
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
OWEN = "owen@pkg10d.test"
BEN = "ben@pkg10d.test"
PID = str(uuid.uuid4())
INSTALL = str(uuid.uuid4())
STUDIO = ["feature:studio"]
NOT_FOUND = {"detail": "Not found"}
ROUTES = ("keep", "restore")
HANDLERS = {"keep": "keep_package", "restore": "restore_package"}


# ── The fake world ─────────────────────────────────────────────────────────


class _World:
    """The calls that the route makes, in order. ``owned`` makes the lock
    find the package, ``has_install`` makes the read find the private
    install, ``changed`` is the answer of a keep, and ``refuse`` is a store
    refusal that a keep or a restore raises. ``nows`` holds each time that a
    restore got."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sessions = 0
        self.owned = True
        self.has_install = True
        self.changed = True
        self.refuse: host_store.InstallError | None = None
        self.nows: list[datetime] = []
        self.members: list[str] = []


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

    async def keep_install(db: Any, *, install_id: str) -> bool:
        w.calls.append(f"keep:{install_id}")
        if w.refuse is not None:
            raise w.refuse
        return w.changed

    async def restore_install(db: Any, *, install_id: str, now: datetime) -> datetime:
        w.calls.append(f"restore:{install_id}")
        w.nows.append(now)
        if w.refuse is not None:
            raise w.refuse
        return now + host_store.RESTORED_ENDS

    monkeypatch.setattr(packages, "_tenant_session", session)
    monkeypatch.setattr(store, "lock_for_install", lock_for_install)
    monkeypatch.setattr(host_store, "private_install_id", private_install_id)
    monkeypatch.setattr(host_store, "keep_install", keep_install)
    monkeypatch.setattr(host_store, "restore_install", restore_install)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return w


def _client(email: str = OWEN, org: str = ORG) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org, access=build_access(STUDIO))
    return TestClient(app)


def _post(route: str, body: Any = None, email: str = OWEN, pid: str = PID) -> Any:
    return _client(email).post(f"/studio/packages/{pid}/{route}", json=body)


def _tree() -> ast.Module:
    return ast.parse(Path(packages.__file__).read_text(encoding="utf-8"))


def _handler(name: str) -> ast.AsyncFunctionDef:
    return next(n for n in ast.walk(_tree())
                if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


# ── 1. The shape ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_each_route_is_a_post_on_the_one_studio_router(route: str) -> None:
    path = f"/studio/packages/{{package_id}}/{route}"
    found = [r for r in actions.router.routes if isinstance(r, APIRoute) and r.path == path]
    assert [sorted(r.methods) for r in found] == [["POST"]], path
    assert found[0].endpoint.__name__ == HANDLERS[route]


@pytest.mark.parametrize("route", ROUTES)
def test_each_handler_takes_the_id_and_the_session_user_only(route: str) -> None:
    """No ``request`` argument, so the handler cannot read a body, a date or
    an org. The org and the member are the session (R5)."""
    args = [a.arg for a in _handler(HANDLERS[route]).args.args]
    assert args == ["package_id", "user"], args
    names = {n.id for n in ast.walk(_handler(HANDLERS[route])) if isinstance(n, ast.Name)}
    for name in ("request", "body", "_raw_body", "_parse_json", "_json_body"):
        assert name not in names, name


@pytest.mark.parametrize("route", ROUTES)
def test_a_body_with_a_date_an_org_or_a_member_changes_nothing(world: _World, route: str) -> None:
    body = {"ends_at": "2099-01-01T00:00:00Z", "now": "2000-01-01T00:00:00Z",
            "organization_id": str(uuid.uuid4()), "owner_email": BEN, "member": BEN,
            "scope": "org", "install_id": str(uuid.uuid4())}
    r = _post(route, body)
    assert r.status_code == 200, r.text
    assert world.members == [OWEN]
    assert world.calls == ["lock", "find", f"{route}:{INSTALL}"]
    if route == "restore":
        [now] = world.nows
        assert now.year != 2000 and now.year != 2099


def test_the_restore_passes_the_server_clock_with_a_time_zone(world: _World) -> None:
    before = datetime.now(UTC)
    r = _post("restore")
    after = datetime.now(UTC)
    assert r.status_code == 200, r.text
    [now] = world.nows
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    assert before <= now <= after
    got = r.json()["install"]
    assert got["id"] == INSTALL and got["app_id"] == PID and got["state"] == "active"
    assert datetime.fromisoformat(got["ends_at"]) == now + timedelta(days=30)


# ── 2. The switches ────────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("off", ["studio_orgs", "package_host_orgs"])
def test_each_switch_off_is_404_and_opens_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch, route: str, off: str,
) -> None:
    monkeypatch.setattr(get_settings(), off, "")
    r = _client().post(f"/studio/packages/{PID}/{route}", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


@pytest.mark.parametrize("route", ROUTES)
def test_the_switches_come_first_in_the_handler(route: str) -> None:
    body = _handler(HANDLERS[route]).body[1:]  # the docstring
    tests = [ast.unparse(s.test) for s in body[:2] if isinstance(s, ast.If)]
    assert tests == ["not flags.studio_allowed(user.organization_id)",
                     "not package_host_allowed(user.organization_id)"]


@pytest.mark.parametrize("route", ROUTES)
def test_a_bad_id_is_404_and_opens_no_session(world: _World, route: str) -> None:
    r = _post(route, pid="not-a-uuid")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


# ── 3. The owner rule ──────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_a_package_of_another_member_is_404_and_reads_no_install(
    world: _World, route: str,
) -> None:
    """Ben is a member of the org and does not own the package. The lock
    answers None, as it does for another org, an App Workshop app and an
    archived package. The answer does not show that the install exists."""
    r = _post(route, email=BEN)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.calls == ["lock"]


@pytest.mark.parametrize("route", ROUTES)
def test_an_owner_with_no_private_install_gets_409_that_names_the_rule(
    world: _World, route: str,
) -> None:
    """An org or a group install is never the target. It has no end date,
    and the uninstall of PKG-8e removes it."""
    world.has_install = False
    r = _post(route)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "private_only", "message": packages.NO_PRIVATE_INSTALL}
    assert "private install" in packages.NO_PRIVATE_INSTALL
    assert world.calls == ["lock", "find"]


# ── 4. One session, in order ───────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_one_session_locks_then_finds_then_calls_the_store(world: _World, route: str) -> None:
    assert _post(route).status_code == 200
    assert world.sessions == 1
    assert world.calls == ["lock", "find", f"{route}:{INSTALL}"]


def test_the_lock_comes_before_the_find_and_the_store_call_in_the_source() -> None:
    lines = {ast.unparse(c.func): c.lineno for c in ast.walk(_handler("_own_private_install"))
             if isinstance(c, ast.Call)}
    assert lines["store.lock_for_install"] < lines["host_store.private_install_id"]
    for name, call in (("keep_package", "host_store.keep_install"),
                       ("restore_package", "host_store.restore_install")):
        calls = {ast.unparse(c.func): c.lineno for c in ast.walk(_handler(name))
                 if isinstance(c, ast.Call)}
        assert calls["_own_private_install"] < calls[call], name
        kwargs = next(c for c in ast.walk(_handler(name))
                      if isinstance(c, ast.Call) and ast.unparse(c.func) == call).keywords
        assert ast.unparse(next(k.value for k in kwargs if k.arg == "install_id")) == "install_id"
    restore = ast.unparse(_handler("restore_package"))
    assert "now=datetime.now(UTC)" in restore


def test_the_private_install_is_the_install_of_the_session_member() -> None:
    call = next(c for c in ast.walk(_handler("_own_private_install"))
                if isinstance(c, ast.Call) and ast.unparse(c.func) == "host_store.private_install_id")
    member = next(k.value for k in call.keywords if k.arg == "member")
    assert ast.unparse(member) == "user.email or ''"


# ── 5. The answers ─────────────────────────────────────────────────────────


def test_a_keep_is_200_and_a_second_keep_is_200_with_no_change(world: _World) -> None:
    first = _post("keep")
    assert first.status_code == 200
    assert first.json() == {"changed": True,
                            "install": {"id": INSTALL, "app_id": PID, "ends_at": None}}
    world.changed = False
    again = _post("keep")
    assert again.status_code == 200
    assert again.json()["changed"] is False


@pytest.mark.parametrize(("route", "refusal", "status"), [
    ("keep", host_store.StateRefused(
        "state_refused", "An archived install cannot be kept. Restore it first."), 409),
    ("keep", host_store.InstallNotFound("install_not_found", "No install has this id."), 404),
    ("restore", host_store.StateRefused(
        "state_refused", "An install that is active cannot be restored."), 409),
    ("restore", host_store.StateRefused(
        "restore_closed", "The time to restore this install has ended."), 409),
    ("restore", host_store.AppNotInstallable(
        "app_not_installable", "No Studio package that is not archived has this id."), 404),
    ("restore", host_store.InstallNotFound("install_not_found", "No install has this id."), 404),
])
def test_each_store_refusal_is_its_detail_for_the_member(
    world: _World, route: str, refusal: host_store.InstallError, status: int,
) -> None:
    world.refuse = refusal
    r = _post(route)
    assert r.status_code == status, r.text
    assert r.json()["detail"] == {"code": refusal.code, "message": str(refusal)}


# ── 6. The read of the store ───────────────────────────────────────────────


class _Db:
    def __init__(self, row: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.row = row

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.calls.append((str(stmt), dict(params or {})))
        return SimpleNamespace(first=lambda: self.row)


def test_the_read_names_the_bound_org_the_app_and_a_private_scope() -> None:
    sql = host_store._PRIVATE_INSTALL_SQL
    for part in (f"organization_id = {host_store._BOUND_ORG}", "app_id = CAST(:app AS uuid)",
                 "scope_kind = 'private'", "scope_subject = :subject"):
        assert part in sql, part
    assert "FOR UPDATE" not in sql


def test_the_read_folds_the_member_and_answers_the_id() -> None:
    db = _Db(row=(INSTALL,))
    got = asyncio.run(host_store.private_install_id(db, app_id=PID, member="Owen@PKG10d.test"))
    assert got == INSTALL
    [(sql, params)] = db.calls
    assert sql == host_store._PRIVATE_INSTALL_SQL
    assert params == {"app": PID, "subject": OWEN}
    assert asyncio.run(host_store.private_install_id(_Db(), app_id=PID, member=OWEN)) is None


@pytest.mark.parametrize("member", ["org", "group:ops", "", "not an address"])
def test_a_member_that_is_not_an_address_is_refused_before_the_database(member: str) -> None:
    db = _Db()
    with pytest.raises(host_store.ScopeRefused):
        asyncio.run(host_store.private_install_id(db, app_id=PID, member=member))
    assert db.calls == []


# ── 7. No chat tool and no MCP pair ────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_no_chat_tool_and_no_mcp_pair_reaches_the_route(route: str) -> None:
    """The owner chooses the screen on task 2949. No chat tool exists, and
    the MCP door refuses each pair outside its list (branch 0)."""
    pair = ("POST", f"/studio/packages/{{package_id}}/{route}")
    assert pair not in MCP_ROUTES and pair not in MCP_WRITE_ROUTES
    tools = (ROOT / "packages/acb_skills/acb_skills/studio_tools.py").read_text(encoding="utf-8")
    assert not re.search(rf"/{route}[\"'/]", tools), route


# ── 8. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_package_keep_restore_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
