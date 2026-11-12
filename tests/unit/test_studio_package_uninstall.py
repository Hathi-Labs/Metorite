"""WS-PKG PKG-8e — the org and group uninstall of a Studio app, with no database.

Spec: ``project-docs/specs/package_host.md`` §5.3 (disposal), §5.5, §6.1 and
the PKG-8e row of §10. Decision: D180.3 (``studio:publish`` gates the share
to others, so it gates the removal of a share too). Code: the route
``DELETE /studio/packages/{id}/install`` (``routes/studio/packages.py``
``uninstall_package``), ``package_host/studio_app.py``
(``remove_scope_install``) and ``package_host/store.py`` (``install_at_scope``
and ``remove_install``). The R8 twin is
``tests/unit/test_studio_package_uninstall_r8.py``.

The checks here:

1. The two switches answer 404 and open no session. They come first.
2. The owner read comes before the body parse. A bad scope is 422, and the
   route opens no second session.
3. Each scope needs ``studio:publish``, and the check fails closed. A
   refusal is 403, and the route opens no second session and writes no
   audit row. The route asks no ``group_exists``.
4. The removal and the audit row run in one session, in that order. No
   install at the scope is 404 ``install_not_found``, with no audit row.
5. ``remove_scope_install`` finds the install, then calls
   ``remove_install``, the one path of §5.3. ``install_at_scope`` takes the
   org lock before its read, and refuses a bad scope before any statement.
6. The cascade: the claims and the extension values go by the FK of their
   tables, and ``remove_install`` calls no ``delete_values``.
7. The R8 twin is in the ``--must-run`` lists of both workflows.

**R7.** M1: drop the ``studio:publish`` check. Check 3 goes red. M2: drop
the ``_lock_org`` call of ``install_at_scope``. Check 5 goes red. M3: move
the audit call above the removal. Check 4 goes red.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.package_host import store as host_store
from gateway.package_host import studio_app
from gateway.routes.studio import actions, packages
from gateway.studio import packages_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "owen@pkg8e.test"
PID = str(uuid.uuid4())
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]
NOT_FOUND = {"detail": "Not found"}


# ── The fake world ─────────────────────────────────────────────────────────


class _World:
    """The calls that the route makes, in order. ``found`` is False when the
    app has no install at the scope. ``audits`` holds each audit row."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sessions = 0
        self.owned = True
        self.found = True
        self.audits: list[dict[str, Any]] = []


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield object()

    async def get_package(db: Any, member: str, pid: str, **_: Any) -> Any:
        w.calls.append("owner")
        return object() if w.owned and member == OWEN and pid == PID else None

    async def group_exists(db: Any, *, group: str) -> bool:
        w.calls.append(f"group_exists:{group}")
        return True

    async def remove(db: Any, **kw: Any) -> host_store.Install:
        w.calls.append(f"remove:{kw['scope_kind']}:{kw['scope_subject']}")
        if not w.found:
            raise host_store.InstallNotFound(
                "install_not_found", studio_app.NO_INSTALL_AT_SCOPE)
        return host_store.Install(
            id="install-1", app_id=kw["app_id"], version=4, scope_kind=kw["scope_kind"],
            scope_subject=kw["scope_subject"], installed_by=OWEN, ends_at=None,
            contributions=())

    async def audit_in(db: Any, **kw: Any) -> None:
        w.calls.append("audit")
        w.audits.append(kw)

    monkeypatch.setattr(packages, "_tenant_session", session)
    monkeypatch.setattr(packages, "record_app_audit_in", audit_in)
    monkeypatch.setattr(store, "get_package", get_package)
    monkeypatch.setattr(host_store, "group_exists", group_exists)
    monkeypatch.setattr(studio_app, "remove_scope_install", remove)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return w


def _client(grants: list[str], email: str = OWEN) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG, access=build_access(grants))
    return TestClient(app)


def _uninstall(grants: list[str], body: Any, email: str = OWEN, pid: str = PID) -> Any:
    return _client(grants, email).request(
        "DELETE", f"/studio/packages/{pid}/install", json=body)


def _handler() -> ast.AsyncFunctionDef:
    tree = ast.parse(Path(packages.__file__).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "uninstall_package")


# ── 1. The switches ────────────────────────────────────────────────────────


@pytest.mark.parametrize("off", ["studio_orgs", "package_host_orgs"])
def test_each_switch_off_is_404_and_opens_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch, off: str,
) -> None:
    monkeypatch.setattr(get_settings(), off, "")
    r = _client(PUBLISHER).request("DELETE", f"/studio/packages/{PID}/install",
                                   content=b"{not json",
                                   headers={"content-type": "application/json"})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


def test_the_switches_come_first_in_the_handler() -> None:
    body = _handler().body[1:]  # the docstring
    tests = [ast.unparse(s.test) for s in body[:2] if isinstance(s, ast.If)]
    assert tests == ["not flags.studio_allowed(user.organization_id)",
                     "not package_host_allowed(user.organization_id)"]


# ── 2. The owner and the scope ─────────────────────────────────────────────


def test_a_package_of_another_member_is_404_before_the_body_is_read(world: _World) -> None:
    world.owned = False
    r = _uninstall(PUBLISHER, {"scope": "nope"})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.calls == ["owner"] and world.audits == []


@pytest.mark.parametrize("body", [{}, {"scope": OWEN}, {"scope": None}, {"scope": "private"}])
def test_a_bad_scope_is_422_and_removes_nothing(world: _World, body: dict[str, Any]) -> None:
    r = _uninstall(PUBLISHER, body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "bad_scope"
    assert world.calls == ["owner"] and world.sessions == 1


def test_a_body_that_is_not_a_json_object_is_422(world: _World) -> None:
    r = _client(PUBLISHER).request("DELETE", f"/studio/packages/{PID}/install",
                                   content=b"[1]",
                                   headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert world.calls == ["owner"]


# ── 3. Who removes (D180.3, §6.1) ──────────────────────────────────────────


@pytest.mark.parametrize("scope", ["org", "group:ops"])
def test_each_scope_needs_the_share_verb(world: _World, scope: str) -> None:
    r = _uninstall(STUDIO, {"scope": scope})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden", "message": packages.NO_REMOVE_RIGHT}
    assert world.calls == ["owner"]
    assert world.sessions == 1     # the owner read only
    assert world.audits == []


def test_a_group_uninstall_asks_no_group_exists(world: _World) -> None:
    """An install of a group that an admin deleted later can still go."""
    r = _uninstall(PUBLISHER, {"scope": "Group:OPS"})
    assert r.status_code == 200, r.text
    assert world.calls == ["owner", "remove:group:group:ops", "audit"]
    assert "group_exists" not in ast.unparse(_handler())


# ── 4. One session, in order ───────────────────────────────────────────────


def test_an_org_uninstall_is_200_with_the_install_that_went(world: _World) -> None:
    r = _uninstall(PUBLISHER, {"scope": "org"})
    assert r.status_code == 200, r.text
    assert r.json() == {"removed": True, "install": {
        "id": "install-1", "app_id": PID, "version": 4, "scope_kind": "org",
        "scope_subject": "org"}}
    assert world.calls == ["owner", "remove:org:org", "audit"]
    assert world.sessions == 2


def test_the_audit_row_names_who_the_scope_and_the_version(world: _World) -> None:
    assert _uninstall(PUBLISHER, {"scope": "group:ops"}).status_code == 200
    [row] = world.audits
    assert row == {"app_id": PID, "user_email": OWEN, "kind": "action", "app_version": 4,
                   "detail": {"op": "package.uninstall", "scope_kind": "group",
                              "scope": "group:ops", "version": 4}}


def test_no_install_at_the_scope_is_404_and_writes_no_audit_row(world: _World) -> None:
    world.found = False
    r = _uninstall(PUBLISHER, {"scope": "org"})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == {"code": "install_not_found",
                                  "message": studio_app.NO_INSTALL_AT_SCOPE}
    assert world.audits == []


def test_the_removal_comes_before_the_audit_and_takes_no_apps_lock() -> None:
    """No statement of the uninstall checks a foreign key to ``apps``, so it
    takes no row lock there and never waits for a publish."""
    lines = {ast.unparse(c.func): c.lineno for c in ast.walk(_handler())
             if isinstance(c, ast.Call)}
    assert lines["studio_app.remove_scope_install"] < lines["record_app_audit_in"]
    source = ast.unparse(_handler())
    assert "lock_for_install" not in source and "for_update" not in source


# ── 5. The store path ──────────────────────────────────────────────────────


def test_remove_scope_install_finds_the_install_then_removes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    found: list[host_store.Install | None] = [host_store.Install(
        id="install-7", app_id=PID, version=2, scope_kind="org", scope_subject="org",
        installed_by=OWEN, ends_at=None, contributions=())]

    async def at_scope(db: Any, **kw: Any) -> host_store.Install | None:
        calls.append(f"find:{kw['scope_kind']}:{kw['scope_subject']}")
        return found[0]

    async def remove_install(db: Any, *, install_id: str) -> bool:
        calls.append(f"remove:{install_id}")
        return True

    monkeypatch.setattr(host_store, "install_at_scope", at_scope)
    monkeypatch.setattr(host_store, "remove_install", remove_install)

    def go(kind: str = "org", subject: str = "org") -> host_store.Install:
        return asyncio.run(studio_app.remove_scope_install(
            object(), app_id=PID, scope_kind=kind, scope_subject=subject))

    assert go() == found[0]
    assert calls == ["find:org:org", "remove:install-7"]
    calls.clear()
    found[0] = None
    with pytest.raises(host_store.InstallNotFound) as exc:
        go()
    assert exc.value.status == 404 and exc.value.code == "install_not_found"
    assert calls == ["find:org:org"]
    calls.clear()
    with pytest.raises(host_store.ScopeRefused):
        go("private", OWEN)
    assert calls == []


class _Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeDb:
    """Answers the bound org, and one install row at the scope."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, params))
        if sql.startswith("SELECT CAST(CAST(NULLIF"):
            return _Result((ORG, "read committed"))
        if sql.startswith("SELECT CAST(id AS text), version"):
            return _Result(("install-3", 5, OWEN, None))
        return _Result(None)


def test_install_at_scope_takes_the_org_lock_before_its_read() -> None:
    db = _FakeDb()
    got = asyncio.run(host_store.install_at_scope(
        db, app_id=PID, scope_kind="group", scope_subject="group:Ops"))
    assert [sql for sql, _ in db.calls] == [
        host_store._ORG_SQL, host_store._LOCK_SQL, host_store._INSTALL_ROW_AT_SCOPE_SQL]
    assert db.calls[1][1] == {"key": f"package_install:{ORG}"}
    assert db.calls[2][1] == {"app": PID, "kind": "group", "subject": "group:ops"}
    assert (got.id, got.version, got.scope_kind, got.scope_subject) == (
        "install-3", 5, "group", "group:ops")


@pytest.mark.parametrize(("kind", "subject"), [
    ("org", "group:ops"), ("group", "org"), ("org", ""), ("org", "a\x00b"), ("org", 7)])
def test_install_at_scope_refuses_a_bad_scope_before_any_statement(
    kind: str, subject: Any,
) -> None:
    db = _FakeDb()
    with pytest.raises(host_store.ScopeRefused):
        asyncio.run(host_store.install_at_scope(
            db, app_id=PID, scope_kind=kind, scope_subject=subject))
    assert db.calls == []


def test_the_read_at_scope_names_the_bound_org() -> None:
    sql = host_store._INSTALL_ROW_AT_SCOPE_SQL
    assert f"organization_id = {host_store._BOUND_ORG}" in sql
    for part in ("app_id = CAST(:app AS uuid)", "scope_kind = :kind",
                 "scope_subject = :subject"):
        assert part in sql, part


# ── 6. The cascade ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("pattern", "constraint"), [
    ("*_package_registry.sql", "package_contribution_install_fk"),
    ("*_package_field_value.sql", "package_field_value_install_fk"),
])
def test_the_claims_and_the_values_go_by_the_cascade_of_the_install(
    pattern: str, constraint: str,
) -> None:
    """A later slice (MKT-7) reads both before the removal, to keep a copy.
    The removal itself deletes only the install row."""
    [migration] = sorted((ROOT / "infra" / "postgres").glob(pattern))
    text = migration.read_text(encoding="utf-8")
    assert re.search(
        rf"CONSTRAINT {constraint} FOREIGN KEY \(organization_id, install_id\)\s+"
        r"REFERENCES package_install \(organization_id, id\) ON DELETE CASCADE", text), constraint


def test_remove_install_deletes_the_install_row_only() -> None:
    src = inspect.getsource(host_store.remove_install)
    assert "_DELETE_INSTALL_SQL" in src
    assert "delete_values" not in src.split('"""')[-1]
    assert host_store._DELETE_INSTALL_SQL.startswith("DELETE FROM package_install ")


# ── 7. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_uninstall_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
