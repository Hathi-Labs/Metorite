"""WS-STU J3a — the kill switch of a package, with no database (hermetic).

Spec: ``project-docs/specs/ai_studio.md`` §9 item 7 and §14 J3a, and
``package_host.md`` §5.3 (PKG-9). Code: ``pause_package`` and
``resume_package`` of ``gateway/studio/packages_store.py``, ``_check`` and
``_answer`` of ``gateway/routes/studio/invoke.py``, and ``INSTALLS_SQL`` of
``gateway/package_host/resolve.py``. Migration: the file that adds
``apps.paused_at`` (412 at build). The R8 twin is
``tests/unit/test_studio_package_pause_r8.py``.

The checks here:

1. The migration is expand only: two nullable columns, no default, no CHECK,
   no table, and ``lock_timeout`` before the ALTER. The CHECK of
   ``apps.status`` does not change.
2. The store: the ``apps`` row ``FOR UPDATE`` comes first, then the stamp,
   then each install move through the install store, then ONE audit row in
   the same session. The row read names the bound org and the format, and
   not the owner. A package of another org writes nothing. A second pause
   stamps nothing again. A resume of a running package writes nothing. An
   install that the expiry moved after the read is skipped.
3. The door: each action of a paused package of the caller is 423
   ``package_paused`` at the first check, before the declaration, with one
   audit row. No body is read, no record is read, and no second session
   opens. A resend of an approved card spends nothing. A pause during the
   body read is 423 at the second check.
4. The read side: the resolve shows no install of a paused app, so a viewer
   gets 404. A publish and a rollback name no pause column.
5. Only the package store writes a pause column.
6. The sidebar reads the manifest again within 10 seconds
   (``MANIFEST_TTL_MS``), and the R8 twin is in both ``--must-run`` lists.

**R7: red first, then mutate.** Each mutation below ran on 2026-11-11 and
turns a test of this file red.

* P1. ``invoke._check``: drop the ``paused_at`` test. Section 3 goes red.
* P2. ``packages_store.pause_package``: skip ``_move_installs``. Section 2
  goes red.
* P3. ``resolve.INSTALLS_SQL``: drop ``a.paused_at IS NULL``. Section 4 goes
  red.
"""

from __future__ import annotations

import ast
import asyncio
import re
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway import app_requests
from gateway.package_host import resolve, viewer
from gateway.package_host import store as install_store
from gateway.routes.studio import actions, invoke
from gateway.studio import objects_store, packages_store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
PID = str(uuid.uuid4())
OWEN = "owen@j3a.test"
ADA = "Ada@J3A.test"
NOW = datetime(2026, 11, 11, tzinfo=UTC)
PAUSED = {"code": "package_paused", "message": invoke.PAUSED_TEXT}

MANIFEST: dict[str, Any] = {
    "studio": 1, "id": "visits", "name": "Site visits", "purpose": "Track the visits.",
    "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
    "actions": [
        {"id": "peek", "uses": "object:visit.get"},
        {"id": "find", "uses": "object:visit.list"},
        {"id": "add", "uses": "object:visit.create"},
        {"id": "change", "uses": "object:visit.update"},
        {"id": "remove", "uses": "object:visit.delete"},
    ],
}

_MIGRATION = next(
    p for p in (ROOT / "infra" / "postgres").glob("*.sql")
    if "COMMENT ON COLUMN apps.paused_at IS" in p.read_text(encoding="utf-8"))


# ── 1. The migration ───────────────────────────────────────────────────────


def _sql_only(path: Path) -> str:
    """The statements of *path*, with each ``--`` comment line removed."""
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))


def test_the_migration_adds_two_nullable_columns_and_nothing_else() -> None:
    sql = _sql_only(_MIGRATION)
    assert "ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ," in sql
    assert "ADD COLUMN IF NOT EXISTS paused_by TEXT;" in sql
    for word in ("DEFAULT", "NOT NULL", "CHECK", "CREATE TABLE", "DROP", "UPDATE ",
                 "INSERT ", "RENAME", "status"):
        assert word not in sql, word
    assert sql.index("SET lock_timeout") < sql.index("ALTER TABLE apps")


def test_the_status_check_of_apps_does_not_change() -> None:
    """The pause is a column, not a status. 114 keeps its CHECK."""
    base = (ROOT / "infra" / "postgres" / "114_custom_apps.sql").read_text(encoding="utf-8")
    assert "CHECK (status IN ('draft', 'live', 'archived'))" in base


# ── 2. The store ───────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = list(rows or [])

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class _StoreDb:
    """Records each statement. The row read answers *row*, and the install
    read answers the installs of the state that it names."""

    def __init__(self, row: Any, installs: dict[str, list[str]] | None = None,
                 log: list[str] | None = None) -> None:
        self.row = row
        self.installs = installs or {}
        self.log = log if log is not None else []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql, p = str(stmt), dict(params or {})
        self.calls.append((sql, p))
        if sql == packages_store._STOP_ROW_SQL:
            self.log.append("row")
            return _Result([] if self.row is None else [self.row])
        if sql == packages_store._PAUSE_SQL:
            self.log.append("stamp")
            return _Result()
        if sql == packages_store._RESUME_SQL:
            self.log.append("clear")
            return _Result()
        if sql == packages_store._INSTALLS_IN_STATE_SQL:
            self.log.append(f"installs:{p['state']}")
            return _Result([(i,) for i in self.installs.get(p["state"], [])])
        if sql == packages_store._AUDIT_SQL:
            self.log.append("audit")
            return _Result()
        raise AssertionError(f"an unexpected statement: {sql[:80]}")


@pytest.fixture
def moves(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The install store, faked. ``refuse`` names the installs that the
    expiry moved after the read, with the refusal that the store raises."""
    m = SimpleNamespace(log=[], refuse={})

    def fake(verb: str) -> Any:
        async def move(db: Any, *, install_id: str) -> bool:
            m.log.append(f"{verb}:{install_id}")
            db.log.append(f"{verb}:{install_id}")
            if install_id in m.refuse:
                raise m.refuse[install_id]
            return True
        return move

    monkeypatch.setattr(install_store, "pause_install", fake("pause"))
    monkeypatch.setattr(install_store, "resume_install", fake("resume"))
    return m


def _audits(db: _StoreDb) -> list[dict[str, Any]]:
    import json

    return [{**p, "detail": json.loads(p["detail"])} for sql, p in db.calls
            if sql == packages_store._AUDIT_SQL]


def test_the_row_read_locks_the_package_of_the_bound_org_and_names_no_owner() -> None:
    sql = packages_store._STOP_ROW_SQL
    assert sql.endswith(" FOR UPDATE")
    assert "organization_id = CAST(NULLIF(current_setting('app.tenant_id'" in sql
    assert "format = 'manifest'" in sql and "owner_email" not in sql
    for write in (packages_store._PAUSE_SQL, packages_store._RESUME_SQL):
        assert "organization_id = CAST(NULLIF(current_setting('app.tenant_id'" in write
        assert "format = 'manifest'" in write
        sets = write.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        assert {c.split("=")[0].strip() for c in sets.split(",")} == {"paused_at", "paused_by"}
    reads = packages_store._INSTALLS_IN_STATE_SQL
    assert reads.startswith("SELECT ") and "organization_id = CAST(NULLIF(" in reads


def test_a_pause_locks_stamps_moves_each_install_then_writes_one_audit_row(
        moves: SimpleNamespace) -> None:
    """P2. The order of the one transaction, and the audit row of the actor."""
    db = _StoreDb((None, 3), {"active": ["i1", "i2"]})
    got = asyncio.run(packages_store.pause_package(db, ADA, PID))
    assert got == packages_store.PackageStop(package_id=PID, paused=True, changed=True,
                                             installs=2)
    assert db.log == ["row", "stamp", "installs:active", "pause:i1", "pause:i2", "audit"]
    stamp = next(p for sql, p in db.calls if sql == packages_store._PAUSE_SQL)
    assert stamp == {"id": PID, "by": "ada@j3a.test"}
    [row] = _audits(db)
    assert (row["m"], row["kind"], row["v"]) == ("ada@j3a.test", "action", 3)
    assert row["detail"] == {"op": "package.pause", "installs": 2}


def test_a_package_of_another_org_writes_nothing(moves: SimpleNamespace) -> None:
    for fn in (packages_store.pause_package, packages_store.resume_package):
        db = _StoreDb(None, {"active": ["i1"], "paused": ["i1"]})
        assert asyncio.run(fn(db, ADA, PID)) is None
        assert db.log == ["row"]
    assert moves.log == []


def test_a_second_pause_stamps_nothing_and_writes_no_row(moves: SimpleNamespace) -> None:
    db = _StoreDb((NOW, None), {})
    got = asyncio.run(packages_store.pause_package(db, ADA, PID))
    assert got == packages_store.PackageStop(PID, paused=True, changed=False, installs=0)
    assert db.log == ["row", "installs:active"]


def test_a_second_pause_still_pauses_an_active_install(moves: SimpleNamespace) -> None:
    """A first publish during the pause writes an active install. A second
    pause moves it, and its row names the one install."""
    db = _StoreDb((NOW, 1), {"active": ["late"]})
    got = asyncio.run(packages_store.pause_package(db, ADA, PID))
    assert got == packages_store.PackageStop(PID, paused=True, changed=True, installs=1)
    assert db.log == ["row", "installs:active", "pause:late", "audit"]


def test_an_install_that_the_expiry_moved_after_the_read_is_skipped(
        moves: SimpleNamespace) -> None:
    moves.refuse = {
        "gone": install_store.InstallNotFound("install_not_found", "No install has this id."),
        "archived": install_store.StateRefused("state_refused", "archived"),
    }
    db = _StoreDb((None, 1), {"active": ["gone", "archived", "kept"]})
    got = asyncio.run(packages_store.pause_package(db, ADA, PID))
    assert got is not None and got.installs == 1
    assert _audits(db)[0]["detail"] == {"op": "package.pause", "installs": 1}


def test_a_resume_clears_the_stamp_and_resumes_each_paused_install(
        moves: SimpleNamespace) -> None:
    db = _StoreDb((NOW, 2), {"paused": ["i1", "i2"]})
    got = asyncio.run(packages_store.resume_package(db, ADA, PID))
    assert got == packages_store.PackageStop(PID, paused=False, changed=True, installs=2)
    assert db.log == ["row", "clear", "installs:paused", "resume:i1", "resume:i2", "audit"]
    assert _audits(db)[0]["detail"] == {"op": "package.resume", "installs": 2}


def test_a_resume_of_a_running_package_writes_nothing(moves: SimpleNamespace) -> None:
    db = _StoreDb((None, 2), {"paused": ["i1"]})
    got = asyncio.run(packages_store.resume_package(db, ADA, PID))
    assert got == packages_store.PackageStop(PID, paused=False, changed=False, installs=0)
    assert db.log == ["row"] and moves.log == []


@pytest.mark.parametrize("actor,pid", [("", PID), ("   ", PID), (ADA, "not-a-uuid")])
def test_a_bad_actor_or_id_raises_before_any_statement(actor: str, pid: str) -> None:
    db = _StoreDb((None, 1))
    for fn in (packages_store.pause_package, packages_store.resume_package):
        with pytest.raises(packages_store.PackageStoreError):
            asyncio.run(fn(db, actor, pid))
    assert db.calls == []


def test_the_package_row_carries_the_pause() -> None:
    assert packages_store._PACKAGE_COLUMNS.endswith("paused_at, paused_by")
    row = (PID, "visits-1", "Site visits", "", OWEN, "private", "live", {"id": "v"}, 1,
           NOW, NOW, NOW, "ada@j3a.test")
    pkg = packages_store._package(row)
    assert (pkg.paused_at, pkg.paused_by) == (NOW, "ada@j3a.test")
    assert packages_store._package((*row[:11], None, None)).paused_at is None


# ── 3. The door ────────────────────────────────────────────────────────────


def _package(paused: bool) -> packages_store.Package:
    return packages_store.Package(
        id=PID, slug=f"visits-{PID[:8]}", name="Site visits", description="",
        owner_email=OWEN, visibility="private", status="live", manifest=MANIFEST,
        live_version=1, created_at=NOW, updated_at=NOW,
        paused_at=NOW if paused else None, paused_by="ada@j3a.test" if paused else None)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """The fakes of the door. ``paused`` holds the pause state of each read
    of the package in turn, and the last one repeats."""
    w = SimpleNamespace(sessions=0, paused=[True], reads=[], audits=[], viewer_reads=0)

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield SimpleNamespace(n=w.sessions)

    async def get_package(db: Any, member: str, pid: str, **_: Any) -> Any:
        if member != OWEN or pid != PID:
            return None
        i = min(db.n - 1, len(w.paused) - 1)
        return _package(w.paused[i])

    async def installed_app(db: Any, *, viewer: str, app_id: str) -> Any:
        w.viewer_reads += 1
        return None

    async def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("a paused package reached a read, a write or a spend")

    async def by_name(db: Any, name: str, *, package_id: str) -> Any:
        w.reads.append(name)
        return objects_store.StudioObject(
            id="obj-visit", package_id=PID, name=name, label=None, created_by=OWEN,
            created_at=NOW, updated_at=NOW, fields=())

    async def query(db: Any, **_: Any) -> Any:
        w.reads.append("list")
        return objects_store.RecordPage((), 0, None, 0, 50, "")

    async def audit(**kw: Any) -> None:
        w.audits.append(kw)

    monkeypatch.setattr(invoke, "_tenant_session", session)
    monkeypatch.setattr(packages_store, "get_package", get_package)
    monkeypatch.setattr(viewer, "installed_app", installed_app)
    monkeypatch.setattr(objects_store, "get_object_by_name", by_name)
    monkeypatch.setattr(objects_store, "query_records", query)
    monkeypatch.setattr(objects_store, "get_record", boom)
    monkeypatch.setattr(objects_store, "apply_writes", boom)
    monkeypatch.setattr(invoke.store, "spend", boom)
    monkeypatch.setattr(app_requests, "read_request", boom)
    monkeypatch.setattr(app_requests, "open_request", boom)
    monkeypatch.setattr(app_requests, "open_studio_request", boom)
    monkeypatch.setattr(invoke, "record_app_audit_bound", audit)
    monkeypatch.setattr(invoke, "record_app_audit_in", boom)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    yield w


def _door(email: str, action: str, body: Any = None, query: str = "") -> Any:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
        access=build_access(["feature:studio"]))
    return TestClient(app).post(f"/studio/apps/{PID}/actions/{action}{query}",
                                json={} if body is None else body)


@pytest.mark.parametrize("action", ["peek", "find", "add", "change", "remove", "not_there"])
@pytest.mark.parametrize("how", ["plain", "ask", "direct", "resend"])
def test_each_action_of_a_paused_package_is_423_at_the_first_check(
        world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, action: str, how: str) -> None:
    """P1. One code for each action and each class, also an action that the
    manifest does not declare, a proof, an ask and the resend of an approved
    card. Nothing is read, no second session opens, and one row says why."""
    monkeypatch.setattr(app_requests, "requests_allowed", lambda org: True)
    if how == "direct":
        monkeypatch.setattr(invoke, "_is_direct", lambda request, user: True)
    query = {"ask": "?ask=1", "resend": f"?request_id={uuid.uuid4()}"}.get(how, "")
    r = _door(OWEN, action, {"id": str(uuid.uuid4())}, query)
    assert r.status_code == 423, r.text
    assert r.json()["detail"] == PAUSED
    assert world.sessions == 1 and world.reads == [] and world.viewer_reads == 0
    [row] = world.audits
    assert row["app_id"] == PID and row["user_email"] == OWEN and row["kind"] == "action"
    assert row["detail"]["outcome"] == "refused"
    assert row["detail"]["reason"] == "package_paused"
    assert "viewer" not in row["detail"]


def test_a_pause_during_the_body_read_is_423_at_the_second_check(
        world: SimpleNamespace) -> None:
    world.paused = [False, True]
    r = _door(OWEN, "find", {})
    assert (r.status_code, r.json()["detail"]) == (423, PAUSED)
    assert world.sessions == 2 and world.reads == []
    [row] = world.audits
    assert row["detail"]["reason"] == "package_paused"


def test_a_running_package_still_runs(world: SimpleNamespace) -> None:
    world.paused = [False]
    r = _door(OWEN, "find", {})
    assert r.status_code == 200, r.text
    assert world.reads == ["visit", "list"]


def test_a_member_who_is_not_the_owner_still_gets_404(world: SimpleNamespace) -> None:
    """The owner read misses, and the resolve shows no install of a paused
    app. So the answer names nothing, and no 423 tells a stranger the app is
    there."""
    r = _door("ana@j3a.test", "find", {})
    assert (r.status_code, r.json()) == (404, {"detail": "Not found"})
    assert world.viewer_reads == 1 and world.audits == []


def test_the_pause_is_checked_first_and_once() -> None:
    check = ast.get_source_segment(Path(invoke.__file__).read_text(encoding="utf-8"),
                                   _function(invoke.__file__, "_check"))
    assert check is not None
    paused = check.index("owned.paused_at is not None")
    assert check.index("packages_store.get_package(") < paused
    assert paused < check.index("package_host_allowed(org)") < check.index("_declared(")
    whole = Path(invoke.__file__).read_text(encoding="utf-8")
    assert whole.count("out.refusal = PACKAGE_PAUSED") == 1
    assert invoke.PAUSED_STATUS == 423 and invoke.PACKAGE_PAUSED not in invoke.REFUSALS
    assert invoke.PAUSED_TEXT.endswith(".") and invoke.PAUSED_TEXT.count(". ") == 0


def _function(path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"no function {name}")


# ── 4. The read side and the publish ───────────────────────────────────────


def test_the_resolve_shows_no_install_of_a_paused_app() -> None:
    """P3. The sidebar and a viewer read through ``INSTALLS_SQL``. An install
    that a publish or a restore makes active during the pause stays hidden."""
    assert "AND a.paused_at IS NULL" in resolve.INSTALLS_SQL


def test_a_publish_and_a_rollback_keep_the_pause() -> None:
    """A draft save, a publish and a rollback set no pause column. The draft
    save reads the columns back in its RETURNING only."""
    for sql in (packages_store._PUBLISH_POINT_SQL, packages_store._ROLLBACK_POINT_SQL,
                packages_store._UPDATE_SQL):
        sets = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        assert "paused" not in sets, sql


# ── 5. One writer of the pause ─────────────────────────────────────────────


def _strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(v.value for v in node.values
                               if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return out


_WRITES_APPS = re.compile(r"\b(UPDATE|INSERT\s+INTO)\s+apps\b", re.I)
_NAMES_PAUSE = re.compile(r"\bpaused_(at|by)\b", re.I)


def test_only_the_package_store_writes_a_pause_column() -> None:
    """An INSERT or an UPDATE of ``apps`` that names a pause column, under
    ``apps/`` and ``packages/``, is in the package store only."""
    writers: set[str] = set()
    for top in ("apps", "packages"):
        for path in (ROOT / top).rglob("*.py"):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            if any(_WRITES_APPS.search(s) and _NAMES_PAUSE.search(s) for s in _strings(path)):
                writers.add(path.relative_to(ROOT).as_posix())
    assert writers == {"apps/services/gateway/gateway/studio/packages_store.py"}
    assert _WRITES_APPS.search(packages_store._PAUSE_SQL)
    assert _WRITES_APPS.search(packages_store._RESUME_SQL)


# ── 6. The bound of the sidebar, and the R8 twin ───────────────────────────


def test_the_sidebar_reads_the_manifest_again_within_ten_seconds() -> None:
    """§9 item 7: new actions stop within 10 seconds. The door reads the
    package on each call. The sidebar reads the resolve again at this TTL."""
    src = (ROOT / "workbench" / "control_plane" / "src" / "lib" / "shell"
           / "packageManifest.ts").read_text(encoding="utf-8")
    found = re.search(r"export const MANIFEST_TTL_MS = ([0-9_]+);", src)
    assert found and int(found.group(1).replace("_", "")) <= 10_000


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_pause_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
