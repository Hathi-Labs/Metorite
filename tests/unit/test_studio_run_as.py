"""WS-STU B6a — the reads of a run with no viewer, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 B6 and B6a, §19.2 and §19.10
row 10, and ``package_host.md`` §5.1. Code: ``package_host/store.py``
(``run_as_member``, ``set_run_as``, ``read_run_as``),
``resolve.install_seen_by``, the route ``PUT
/studio/packages/{id}/install/run-as`` (``routes/studio/packages.py``
``set_install_run_as``) and ``invoke.run_as_read``. The R8 twin is
``tests/unit/test_studio_run_as_r8.py``.

The checks here:

1. The store: one shape check of the address, each check before the first
   statement, the org lock before the row lock, and the row lock before the
   UPDATE. Each statement names the bound org.
2. The route: the two switches (404, no session), the owner read before the
   body parse, the scope and the address (422), ``studio:publish`` (403),
   then the member (one 422 for each miss). The change and its audit row run
   in one session, in that order. A clear asks no member check.
3. The run: no org, a switch that is off, a bad id or a bad action read
   nothing. The install names the member, never the caller. A refusal that
   reaches the install writes its row. Only the object get and list run. The
   whole run has no person present. The row names ``system:package:<id>``.
4. The fences: the run never reads the draft or the widest install, and the
   R8 twin is in both ``--must-run`` lists.

**R7.** The two mutations of the R8 twin also turn this suite red: M1 (skip
``may_run_as``) fails ``test_a_member_who_lost_access_is_refused_with_a_row``,
and M2 (the draft of the owner) fails ``test_the_run_never_reads_the_draft``.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.no_person import no_person_present
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.package_host import resolve
from gateway.package_host import store as host_store
from gateway.package_host.store import InstallError, InstallNotFound, ScopeRefused
from gateway.routes.studio import actions, invoke, packages
from gateway.studio import packages_store

_ROOT = Path(__file__).resolve().parents[2]
ORG = "00000000-0000-0000-0000-00000000b6a0"
OTHER_ORG = "00000000-0000-0000-0000-00000000b6a1"
OWEN = "owen@b6a.test"
MIA = "mia@b6a.test"
PID = str(uuid.uuid4())
INSTALL = str(uuid.uuid4())
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── 1. The store ───────────────────────────────────────────────────────────


class _Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeDb:
    """Records each statement. Answers the bound org and the isolation level,
    and the row of the install when *found*."""

    def __init__(self, found: bool = True, old: str | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.found = found
        self.old = old

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, params))
        if sql.startswith("SELECT CAST(CAST(NULLIF"):
            return _Result((ORG, "read committed"))
        if sql.startswith("SELECT run_as FROM package_install"):
            return _Result((self.old,) if self.found else None)
        if sql.startswith("SELECT CAST(app_id AS text), run_as, state"):
            return _Result((PID, self.old, "active") if self.found else None)
        return _Result(None)


@pytest.mark.parametrize(("value", "want"), [
    (MIA, MIA), ("Mia@B6a.Test", MIA), (" mia@b6a.test ", MIA)])
def test_the_shape_check_folds_an_address(value: str, want: str) -> None:
    assert host_store.run_as_member(value) == want


@pytest.mark.parametrize("value", [
    None, "", "org", "group:ops", "GROUP:ops@b6a.test", "system:package:x@b6a.test",
    "system:workflow:1", "no-at-sign", "a b@b6a.test", "x\x00@b6a.test", 7,
    "a" * 400 + "@b6a.test"])
def test_the_shape_check_refuses_each_value_that_is_no_member(value: Any) -> None:
    with pytest.raises(ScopeRefused) as exc:
        host_store.run_as_member(value)
    assert (exc.value.code, exc.value.status) == ("bad_run_as", 422)


@pytest.mark.parametrize(("kw", "code"), [
    ({"install_id": "nope"}, "bad_install_id"),
    ({"run_as": "group:ops"}, "bad_run_as"),
    ({"set_by": "org"}, "bad_member")])
def test_set_run_as_refuses_before_the_first_statement(kw: dict[str, Any], code: str) -> None:
    db = _FakeDb()
    args = {"install_id": INSTALL, "run_as": MIA, "set_by": OWEN, **kw}
    with pytest.raises(InstallError) as exc:
        _run(host_store.set_run_as(db, **args))
    assert exc.value.code == code
    assert db.calls == []


def test_set_run_as_locks_the_org_then_the_row_then_writes() -> None:
    db = _FakeDb(old="ned@b6a.test")
    was = _run(host_store.set_run_as(db, install_id=INSTALL, run_as="Mia@B6a.test",
                                     set_by="Owen@B6a.test"))
    assert was == "ned@b6a.test"
    sqls = [s for s, _ in db.calls]
    assert sqls[0].startswith("SELECT CAST(CAST(NULLIF")
    assert sqls[1] == host_store._LOCK_SQL
    assert db.calls[1][1] == {"key": f"package_install:{ORG}"}
    assert sqls[2] == host_store._RUN_AS_LOCK_SQL and "FOR UPDATE" in sqls[2]
    assert sqls[3] == host_store._SET_RUN_AS_SQL
    assert db.calls[3][1] == {"install": INSTALL, "run_as": MIA, "by": OWEN}
    for sql in sqls[2:]:
        assert host_store._BOUND_ORG in sql, sql


def test_a_clear_writes_null_and_stamps_the_member() -> None:
    db = _FakeDb(old=MIA)
    assert _run(host_store.set_run_as(db, install_id=INSTALL, run_as=None,
                                      set_by=OWEN)) == MIA
    assert db.calls[-1][1] == {"install": INSTALL, "run_as": None, "by": OWEN}


def test_set_run_as_of_an_unknown_install_writes_nothing() -> None:
    db = _FakeDb(found=False)
    with pytest.raises(InstallNotFound) as exc:
        _run(host_store.set_run_as(db, install_id=INSTALL, run_as=MIA, set_by=OWEN))
    assert exc.value.status == 404
    assert not any(s.startswith("UPDATE") for s, _ in db.calls)


def test_read_run_as_names_the_bound_org_and_takes_no_lock() -> None:
    db = _FakeDb(old=MIA)
    got = _run(host_store.read_run_as(db, install_id=INSTALL))
    assert got == host_store.RunAs(install_id=INSTALL, app_id=PID, run_as=MIA, state="active")
    assert len(db.calls) == 1
    sql = db.calls[0][0]
    assert host_store._BOUND_ORG in sql and "FOR UPDATE" not in sql
    assert _run(host_store.read_run_as(_FakeDb(found=False), install_id=INSTALL)) is None


# ── 2. The route ───────────────────────────────────────────────────────────


class _World:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sessions = 0
        self.owned = True
        self.may = True
        self.found = True
        self.seen = True
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

    async def may_run_as(member: str, org: str) -> bool:
        w.calls.append(f"may:{member}:{org}")
        return w.may

    async def install_at_scope(db: Any, **kw: Any) -> Any:
        w.calls.append(f"find:{kw['scope_kind']}:{kw['scope_subject']}")
        if not w.found:
            return None
        return host_store.Install(
            id=INSTALL, app_id=kw["app_id"], version=3, scope_kind=kw["scope_kind"],
            scope_subject=kw["scope_subject"], installed_by=OWEN, ends_at=None,
            contributions=())

    async def install_seen_by(db: Any, *, viewer: str, install_id: str) -> Any:
        w.calls.append(f"seen:{viewer}:{install_id}")
        return object() if w.seen else None

    async def set_run_as(db: Any, **kw: Any) -> str | None:
        w.calls.append(f"set:{kw['run_as']}:{kw['set_by']}")
        return "ned@b6a.test"

    async def audit_in(db: Any, **kw: Any) -> None:
        w.calls.append("audit")
        w.audits.append(kw)

    monkeypatch.setattr(packages, "_tenant_session", session)
    monkeypatch.setattr(packages, "record_app_audit_in", audit_in)
    monkeypatch.setattr(packages_store, "get_package", get_package)
    monkeypatch.setattr(invoke, "may_run_as", may_run_as)
    monkeypatch.setattr(host_store, "install_at_scope", install_at_scope)
    monkeypatch.setattr(host_store, "set_run_as", set_run_as)
    monkeypatch.setattr(resolve, "install_seen_by", install_seen_by)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return w


def _put(grants: list[str], body: Any, email: str = OWEN, pid: str = PID) -> Any:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG, access=build_access(grants))
    return TestClient(app).put(f"/studio/packages/{pid}/install/run-as", json=body)


@pytest.mark.parametrize("flag", ["studio_orgs", "package_host_orgs"])
def test_each_switch_answers_404_with_no_session(world: _World, monkeypatch, flag: str) -> None:
    monkeypatch.setattr(get_settings(), flag, "")
    r = _put(PUBLISHER, {"scope": "org", "run_as": MIA})
    assert r.status_code == 404
    assert (world.calls, world.sessions) == ([], 0)


def test_a_stranger_gets_404_before_the_body_is_parsed(world: _World) -> None:
    r = _put(PUBLISHER, {"scope": "nonsense"}, email="zed@b6a.test")
    assert r.status_code == 404
    assert (world.calls, world.sessions) == (["owner"], 1)


@pytest.mark.parametrize(("body", "code"), [
    ({"scope": "nonsense", "run_as": MIA}, "bad_scope"),
    ({"scope": MIA, "run_as": MIA}, "bad_scope"),
    ({"scope": "org", "run_as": "system:package:x@b6a.test"}, "bad_run_as"),
    ({"scope": "org", "run_as": "group:ops"}, "bad_run_as"),
    ({"scope": "org", "run_as": 7}, "bad_run_as")])
def test_a_bad_scope_or_address_is_422_and_opens_no_session(
        world: _World, body: dict[str, Any], code: str) -> None:
    r = _put(PUBLISHER, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == code
    assert (world.calls, world.sessions) == (["owner"], 1)


def test_a_body_with_no_run_as_is_422(world: _World) -> None:
    r = _put(PUBLISHER, {"scope": "org"})
    assert r.status_code == 422
    assert r.json()["detail"]["message"] == packages.NO_RUN_AS_KEY
    assert (world.calls, world.sessions) == (["owner"], 1)


def test_a_caller_without_the_share_right_gets_403_and_no_member_read(world: _World) -> None:
    r = _put(STUDIO, {"scope": "org", "run_as": MIA})
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": packages.FORBIDDEN,
                                  "message": packages.NO_RUN_AS_RIGHT}
    assert (world.calls, world.sessions) == (["owner"], 1)


def test_each_member_miss_is_the_same_422(world: _World) -> None:
    world.may = False
    lost = _put(PUBLISHER, {"scope": "group:ops", "run_as": MIA})
    world.may, world.seen = True, False
    unseen = _put(PUBLISHER, {"scope": "group:ops", "run_as": MIA})
    want = {"code": packages.RUN_AS_REFUSED, "message": packages.NO_RUN_AS_MEMBER}
    assert (lost.status_code, lost.json()["detail"]) == (422, want)
    assert (unseen.status_code, unseen.json()["detail"]) == (422, want)
    assert not any(c.startswith("set:") or c == "audit" for c in world.calls)
    # The member check runs before the session, with the org of the session.
    assert world.calls[:2] == ["owner", f"may:{MIA}:{ORG}"]


def test_no_install_at_the_scope_is_404(world: _World) -> None:
    world.found = False
    r = _put(PUBLISHER, {"scope": "org", "run_as": MIA})
    assert r.status_code == 404
    assert r.json()["detail"] == {"code": "install_not_found",
                                  "message": packages.NO_INSTALL_FOR_RUN_AS}
    assert world.audits == []


def test_a_change_writes_the_value_then_its_audit_row_in_one_session(world: _World) -> None:
    r = _put(PUBLISHER, {"scope": "GROUP:Ops", "run_as": "Mia@B6a.Test",
                         "organization_id": OTHER_ORG, "set_by": "zed@b6a.test"})
    assert r.status_code == 200, r.text
    assert r.json() == {"install": {"id": INSTALL, "app_id": PID, "version": 3,
                                    "scope_kind": "group", "scope_subject": "group:ops"},
                        "run_as": MIA}
    assert world.calls == ["owner", f"may:{MIA}:{ORG}", "find:group:group:ops",
                           f"seen:{MIA}:{INSTALL}", f"set:{MIA}:{OWEN}", "audit"]
    # The owner read, then ONE session for the change and its row.
    assert world.sessions == 2
    (row,) = world.audits
    assert (row["app_id"], row["user_email"], row["kind"], row["app_version"]) == (
        PID, OWEN, "action", 3)
    assert row["detail"] == {"op": "package.run_as", "scope_kind": "group",
                             "scope": "group:ops", "install_id": INSTALL, "run_as": MIA,
                             "was": "ned@b6a.test"}


def test_a_clear_asks_no_member_check(world: _World) -> None:
    r = _put(PUBLISHER, {"scope": "org", "run_as": None})
    assert r.status_code == 200, r.text
    assert r.json()["run_as"] is None
    assert world.calls == ["owner", "find:org:org", f"set:None:{OWEN}", "audit"]
    assert world.sessions == 2
    assert world.audits[0]["detail"]["run_as"] is None


# ── 3. The run ─────────────────────────────────────────────────────────────


def _row(manifest: dict[str, Any], version: int | None = 1) -> resolve.InstallRow:
    return resolve.InstallRow(id=INSTALL, app_id=PID, scope_kind="group",
                              scope_subject="group:ops", created_at=datetime.now(UTC),
                              manifest=manifest, version=version)


_PINNED = {
    "studio": 1, "id": "b6a-app", "name": "Site visits",
    "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
    "actions": [
        {"id": "find", "uses": "object:visit.list"},
        {"id": "peek", "uses": "object:visit.get"},
        {"id": "add", "uses": "object:visit.create"},
        {"id": "remove", "uses": "object:visit.delete"},
        {"id": "tasks", "uses": "projects.list_tasks"},
        {"id": "flow", "uses": "workflow:" + str(uuid.uuid4())},
    ],
}


class _Run:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.member: str | None = MIA
        self.again: str | None = MIA
        self.may = True
        self.row: resolve.InstallRow | None = _row(_PINNED)
        self.audits: list[dict[str, Any]] = []
        self.marks: list[bool] = []


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> _Run:
    r = _Run()
    reads = iter(range(100))

    @asynccontextmanager
    async def session(org: str | None = None) -> Any:
        r.calls.append(f"session:{org}")
        r.marks.append(no_person_present())
        yield object()

    async def read_run_as(db: Any, *, install_id: str) -> Any:
        first = next(reads) == 0
        r.calls.append("read_run_as")
        member = r.member if first else r.again
        return host_store.RunAs(install_id=install_id, app_id=PID, run_as=member,
                                state="active")

    async def may_run_as(member: str, org: str) -> bool:
        r.calls.append(f"may:{member}:{org}")
        r.marks.append(no_person_present())
        return r.may

    async def install_seen_by(db: Any, *, viewer: str, install_id: str) -> Any:
        r.calls.append(f"seen:{viewer}:{install_id}")
        return r.row

    async def object_list(db: Any, package: Any, ref: Any, raw: bytes, out: Any) -> None:
        r.calls.append(f"list:{package.id}:{package.version}:{raw.decode()}")
        r.marks.append(no_person_present())

    async def object_get(db: Any, package: Any, ref: Any, raw: bytes, out: Any) -> None:
        r.calls.append(f"get:{raw.decode()}")

    async def audit_in(db: Any, **kw: Any) -> None:
        r.calls.append("audit")
        r.audits.append(kw)

    async def catalog() -> list[Any]:
        r.calls.append("catalog")
        return []

    monkeypatch.setattr(invoke, "_tenant_session", session)
    monkeypatch.setattr(host_store, "read_run_as", read_run_as)
    monkeypatch.setattr(invoke, "may_run_as", may_run_as)
    monkeypatch.setattr(resolve, "install_seen_by", install_seen_by)
    monkeypatch.setattr(invoke, "_run_object_list", object_list)
    monkeypatch.setattr(invoke, "_run_object_get", object_get)
    monkeypatch.setattr(invoke, "record_app_audit_in", audit_in)
    monkeypatch.setattr(actions, "catalog_rows", catalog)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return r


def _read(action: str = "find", body: Any = None, org: str = ORG,
          install: str = INSTALL) -> dict[str, Any]:
    return _run(invoke.run_as_read(organization_id=org, install_id=install, action=action,
                                   body=body, run_id="run-7"))


def test_a_read_runs_as_the_member_of_the_install_with_no_person_present(run: _Run) -> None:
    got = _read("find", {"limit": 5})
    # The fake executor fills no page, so the run reads as one that found nothing.
    assert got["outcome"] == "ran" and got["code"] is None
    assert run.calls == [f"session:{ORG}", "read_run_as", f"may:{MIA}:{ORG}",
                         f"session:{ORG}", "read_run_as", f"seen:{MIA}:{INSTALL}",
                         f'list:{PID}:1:{{"limit": 5}}', "audit"]
    assert run.marks == [True, True, True, True]
    assert no_person_present() is False
    (row,) = run.audits
    assert (row["app_id"], row["user_email"], row["kind"], row["app_version"]) == (
        PID, f"system:package:{PID}", "action", 1)
    assert row["detail"] == {"action": "find", "uses": "object:visit.list", "class": "A",
                             "outcome": "ran", "via": "run_as", "run_as": MIA,
                             "install_id": INSTALL, "run_id": "run-7", "version": 1}


def test_a_get_reads_the_body_id(run: _Run) -> None:
    _read("peek", {"id": "r-1"})
    assert '|'.join(run.calls).count('get:{"id": "r-1"}') == 1


@pytest.mark.parametrize(("org", "install", "action", "code"), [
    ("", INSTALL, "find", invoke.RUN_AS_NO_ORG),
    ("  ", INSTALL, "find", invoke.RUN_AS_NO_ORG),
    (ORG, "not-a-uuid", "find", invoke.RUN_AS_NOT_FOUND),
    (ORG, INSTALL, "Bad Action!", invoke.RUN_AS_NOT_FOUND)])
def test_a_run_that_names_nothing_reads_nothing(run: _Run, org: str, install: str,
                                                action: str, code: str) -> None:
    got = _read(action, org=org, install=install)
    assert (got["ok"], got["code"], got["result"]) == (False, code, None)
    assert run.calls == []


@pytest.mark.parametrize("flag", ["studio_orgs", "package_host_orgs"])
def test_each_switch_stops_the_run_with_no_session(run: _Run, monkeypatch, flag: str) -> None:
    monkeypatch.setattr(get_settings(), flag, "")
    got = _read()
    assert (got["ok"], got["code"]) == (False, invoke.RUN_AS_OFF)
    assert run.calls == []


def test_an_install_with_no_member_is_refused_with_a_row(run: _Run) -> None:
    run.member = None
    got = _read()
    assert (got["ok"], got["outcome"], got["code"]) == (False, "refused", invoke.RUN_AS_NONE)
    assert run.calls == [f"session:{ORG}", "read_run_as", f"session:{ORG}", "audit"]
    assert run.audits[0]["detail"]["run_as"] is None
    assert run.audits[0]["user_email"] == f"system:package:{PID}"


def test_a_member_who_lost_access_is_refused_with_a_row(run: _Run) -> None:
    run.may = False
    got = _read()
    assert (got["ok"], got["code"]) == (False, invoke.RUN_AS_LOST)
    assert run.calls == [f"session:{ORG}", "read_run_as", f"may:{MIA}:{ORG}",
                         f"session:{ORG}", "audit"]
    assert run.audits[0]["detail"]["reason"] == invoke.RUN_AS_LOST


def test_a_member_who_does_not_see_the_install_is_refused(run: _Run) -> None:
    run.row = None
    got = _read()
    assert (got["ok"], got["code"]) == (False, invoke.RUN_AS_LOST)
    assert not any(c.startswith(("list:", "get:")) for c in run.calls)
    assert run.calls[-1] == "audit"


def test_a_draft_install_is_refused(run: _Run) -> None:
    run.row = _row(_PINNED, version=None)
    assert _read()["code"] == invoke.RUN_AS_LOST


def test_a_member_that_changed_during_the_run_is_refused(run: _Run) -> None:
    run.again = "ned@b6a.test"
    got = _read()
    assert got["code"] == invoke.RUN_AS_CHANGED
    assert not any(c.startswith("seen:") for c in run.calls)


@pytest.mark.parametrize(("action", "code", "cls"), [
    ("add", invoke.RUN_AS_READS_ONLY, "B"),
    ("remove", invoke.RUN_AS_READS_ONLY, "C"),
    ("tasks", invoke.SOURCE_NOT_OPEN, None),
    ("flow", invoke.SOURCE_NOT_OPEN, None),
    ("nope", invoke.NOT_DECLARED, None)])
def test_only_the_object_get_and_list_run(run: _Run, action: str, code: str,
                                          cls: str | None) -> None:
    got = _read(action, {"data": {"site": "x"}})
    assert (got["ok"], got["code"]) == (False, code)
    assert not any(c.startswith(("list:", "get:", "catalog")) for c in run.calls)
    assert run.audits[0]["detail"]["class"] == cls


def test_a_body_that_is_not_json_is_refused(run: _Run) -> None:
    got = _read("find", {"x": {1, 2}})
    assert (got["ok"], got["code"]) == (False, "bad_input")
    assert not any(c.startswith("list:") for c in run.calls)


def test_a_run_past_the_reader_cap_opens_no_read_session(run: _Run, monkeypatch) -> None:
    monkeypatch.setattr(invoke, "_READS_IN_FLIGHT", {ORG: invoke.READERS_PER_ORG})
    got = _read()
    assert (got["ok"], got["outcome"], got["code"]) == (False, "failed", invoke.STORE_BUSY)
    assert run.calls == [f"session:{ORG}", "read_run_as", f"may:{MIA}:{ORG}"]


# ── 4. The fences ──────────────────────────────────────────────────────────


def _names(fn: Any) -> set[str]:
    tree = ast.parse(inspect.getsource(fn).replace("\r\n", "\n"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_the_run_never_reads_the_draft() -> None:
    """The run reads the manifest of the version that THIS install pins. The
    door's ``_check`` and ``packages_store.get_package`` read the draft of the
    owner, and ``installed_app`` and ``visible_installs`` take the widest
    install of the app. None of them is in the run (M2)."""
    for fn in (invoke.run_as_read, invoke._run_as_session):
        named = _names(fn)
        for banned in ("_check", "get_package", "installed_app", "visible_installs",
                       "packages_store"):
            assert banned not in named, (fn.__name__, banned)
    named = _names(invoke._run_as_session)
    assert {"install_seen_by", "installed"} <= named
    assert "may_run_as" in _names(invoke.run_as_read)
    assert frozenset({("object", "get"), ("object", "list")}) == invoke.RUN_AS_EXECUTORS


def test_the_run_is_marked_and_names_the_package_as_its_actor() -> None:
    named = _names(invoke.run_as_read)
    assert "no_person_scope" in named
    assert invoke.run_as_actor(PID) == f"system:package:{PID}"


def test_the_route_reads_one_member_rule() -> None:
    """The route and the run ask the same two reads: ``may_run_as`` and
    ``install_seen_by``. The route holds no SQL (``test_studio_package_routes``)."""
    named = _names(packages.set_install_run_as)
    assert {"may_run_as", "install_seen_by", "set_run_as", "install_at_scope",
            "SHARE_RIGHT"} <= named


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, workflow
    assert re.search(r"^[ \t]+tests/unit/test_studio_run_as_r8\.py \\$", block.group(1), re.M)


def test_the_spec_names_the_fence() -> None:
    spec = (_ROOT / "project-docs/specs/ai_studio.md").read_text(encoding="utf-8")
    assert "| **B6a** | 🔨 **BUILT, dark" in spec
    assert "`tests/unit/test_studio_run_as_r8.py` for the reads (B6a)" in spec
