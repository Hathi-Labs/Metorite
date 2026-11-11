"""WS-STU J2a — a member reads an app that another member owns, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 J2a and §19.7. Code:
``gateway/package_host/viewer.py``, ``gateway/package_host/resolve.py``
(``visible_installs``), ``GET /studio/packages/{id}`` in
``gateway/routes/studio/packages.py``, and ``_check``, ``refusal_for`` and
``_reader_slot`` in ``gateway/routes/studio/invoke.py``. The R8 twin is
``tests/unit/test_studio_package_viewer_r8.py``.

The checks here:

1. ``viewer.installed`` gives the installed version of one app, and None for
   a draft, a manifest that is not an object and another app. A viewer that
   is not an address reads nothing and runs no statement.
2. One rule of who sees an install: ``resolve_for`` and the viewer read both
   call ``visible_installs``, and ``viewer.py`` holds no SQL.
3. ``GET /studio/packages/{id}``: the owner answer does not change. A viewer
   gets the installed version, and the objects that it names. With
   ``PACKAGE_HOST_ORGS`` off, or with no install, the answer is 404, and the
   viewer read does not run while the switch is off.
4. The door: a viewer's get and list give 200 on the declarations of the
   installed version. A declaration of the draft only is 403
   ``not_declared``. A viewer's write is 403 ``read_only`` at the first check,
   also with a proof or ``ask=1``, and the door opens no second session. An
   install that goes during the read gives 404 at the second check.
5. The cap of the reads of a viewer: past it the answer is 503
   ``store_busy`` with ``Retry-After``, and no second session opens. The
   owner's read takes no slot. The shape of the slot is the shape of the
   writers.
6. The audit row of a viewer holds ``viewer`` and the installed version.
7. The R8 twin is in the ``--must-run`` lists of both workflows.

**R7: red first, then mutate.** Each mutation below ran on 2026-11-11 and
turns a test of this file red.

* V1. ``viewer.installed``: drop the ``version is None`` check. Check 1 goes
  red.
* V2. ``invoke._check``: drop the ``package_host_allowed`` test. The switch
  test of check 4 and the order test of check 2 go red.
* V3. ``invoke.refusal_for``: drop the ``read_only`` branch. Check 4 goes red
  (each write runs, or asks).
* V4. ``invoke._run_executor``: take no reader slot. Check 5 goes red.
* V5. ``packages.get_package``: show a viewer each object row, also one that
  the installed version does not name. Check 3 goes red.
* V6 (fix round 1). ``viewer.InstalledApp.project``: keep every field of the
  row. The field and record tests of section 6b go red.
* V7 (fix round 1). ``invoke._run_object_list``: drop the ``parse_query`` of
  a viewer. ``test_a_viewer_cannot_filter_or_sort_on_a_newer_field`` goes red.

Section 6b (fix round 1) holds the fields of the installed version: a field
and a rule that a newer version added never reach a viewer, a record names
no writer, and the chat read tells a viewer that only the owner changes it.
"""

from __future__ import annotations

import ast
import inspect
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
from acb_common.studio_fields import stored_field
from acb_common.studio_manifest import parse_uses, validate_manifest
from acb_common.studio_rules import parse_rules
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway import app_requests
from gateway.package_host import resolve, viewer
from gateway.package_host.resolve import InstallRow
from gateway.routes.studio import actions, invoke, packages
from gateway.studio import objects_store, packages_store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
PID = str(uuid.uuid4())
OWEN = "owen@j2a.test"
ANA = "ana@j2a.test"
NOW = datetime(2026, 11, 11, tzinfo=UTC)
NOT_FOUND = {"detail": "Not found"}

#: The manifest of the installed version 1.
INSTALLED: dict[str, Any] = {
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

#: The draft of the owner: a new name, a new object and an action that only
#: the draft declares.
DRAFT: dict[str, Any] = {
    **INSTALLED, "name": "Draft name",
    "objects": [*INSTALLED["objects"],
                {"name": "secret", "fields": [{"key": "note", "type": "text"}]}],
    "actions": [*INSTALLED["actions"], {"id": "draft_only", "uses": "object:visit.list"}],
}


#: The rule that a newer version (v2) adds on its new field ``cost``.
COST_RULE = {"name": "cost_ok", "kind": "range", "field": "cost", "min": 0}


def _object(name: str) -> objects_store.StudioObject:
    """The object row after v2: ``visit`` holds ``site`` (v1) and ``cost`` with a
    rule (v2 added both), because a publish only adds to the rows."""
    if name != "visit":
        return objects_store.StudioObject(
            id=f"obj-{name}", package_id=PID, name=name, label=None, created_by=OWEN,
            created_at=NOW, updated_at=NOW, fields=())
    return objects_store.StudioObject(
        id="obj-visit", package_id=PID, name=name, label=None, created_by=OWEN,
        created_at=NOW, updated_at=NOW,
        fields=(stored_field("site", "text", {}, False), stored_field("cost", "number", {}, True)),
        constraints=parse_rules([COST_RULE]))


#: The values of a record after v2. A viewer of v1 reads ``site`` only.
ROW_DATA = {"site": "Hosur", "cost": 42}


def _package() -> packages_store.Package:
    return packages_store.Package(
        id=PID, slug=f"visits-{PID[:8]}", name="Draft name", description="", owner_email=OWEN,
        visibility="private", status="live", manifest=DRAFT, live_version=2,
        created_at=NOW, updated_at=NOW)


def _app(version: int = 1) -> viewer.InstalledApp:
    return viewer.InstalledApp(id=PID, version=version, manifest=INSTALLED)


def _row(app: str = PID, version: int | None = 1, manifest: Any = None,
         kind: str = "org") -> InstallRow:
    return InstallRow(id=str(uuid.uuid4()), app_id=app, scope_kind=kind, scope_subject=kind,
                      created_at=NOW, manifest=INSTALLED if manifest is None else manifest,
                      version=version)


# ── 1. The pure read ───────────────────────────────────────────────────────


def test_the_manifests_of_this_file_are_valid() -> None:
    assert validate_manifest(INSTALLED) == []
    assert validate_manifest(DRAFT) == []


def test_the_read_gives_the_installed_version_of_the_app() -> None:
    other = str(uuid.uuid4())
    got = viewer.installed([_row(app=other, version=7), _row(version=1)], PID)
    assert got == viewer.InstalledApp(id=PID, version=1, manifest=INSTALLED)
    assert (got.name, got.description) == ("Site visits", "Track the visits.")
    assert got.object_names == frozenset({"visit"})


def test_a_draft_install_never_reaches_a_viewer() -> None:
    """V1. The resolve shows a draft to the member that it names. The viewer
    read refuses it, so a draft never reaches a member who is not the owner."""
    assert viewer.installed([_row(version=None, kind="private")], PID) is None


@pytest.mark.parametrize("manifest", [[], "not json", 7, b"[1]"])
def test_a_manifest_that_is_not_an_object_reads_nothing(manifest: Any) -> None:
    assert viewer.installed([_row(manifest=manifest)], PID) is None


def test_a_manifest_in_json_text_is_read() -> None:
    import json

    got = viewer.installed([_row(manifest=json.dumps(INSTALLED))], PID)
    assert got is not None and got.manifest == INSTALLED


def test_another_app_reads_nothing() -> None:
    assert viewer.installed([_row(app=str(uuid.uuid4()))], PID) is None
    assert viewer.installed([], PID) is None


@pytest.mark.parametrize("who", ["", "   ", "system:door", None])
def test_a_viewer_that_is_not_an_address_runs_no_statement(
        monkeypatch: pytest.MonkeyPatch, who: Any) -> None:
    """An org install shows to every caller, so a caller with no address must
    not reach the resolve at all."""
    import asyncio

    async def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("the resolve ran for a caller with no address")

    monkeypatch.setattr(resolve, "visible_installs", boom)
    assert asyncio.run(viewer.installed_app(object(), viewer=who, app_id=PID)) is None


def test_the_viewer_is_folded_to_lower_case(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    seen: list[str] = []

    async def visible(db: Any, *, viewer: str) -> list[InstallRow]:
        seen.append(viewer)
        return [_row()]

    monkeypatch.setattr(resolve, "visible_installs", visible)
    got = asyncio.run(viewer.installed_app(object(), viewer=" Ana@J2A.test ", app_id=PID))
    assert seen == ["ana@j2a.test"] and got is not None


# ── 2. One rule of who sees an install ─────────────────────────────────────


def test_the_sidebar_and_the_viewer_read_share_one_install_read() -> None:
    """No second install query: ``resolve_for`` and ``viewer.installed_app``
    both go through ``visible_installs``, which reads ``INSTALLS_SQL`` through
    ``read_installs`` and keeps one install of each app."""
    vis = inspect.getsource(resolve.visible_installs)
    assert "await read_installs(" in vis and "widest_per_app(" in vis
    res = inspect.getsource(resolve.resolve_for)
    assert "await visible_installs(" in res and "read_installs(" not in res
    assert "resolve.visible_installs(" in inspect.getsource(viewer.installed_app)
    assert "package_install.version" in resolve.INSTALLS_SQL


def test_the_viewer_module_holds_no_sql() -> None:
    src = Path(viewer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            assert not any(n.startswith("sqlalchemy") for n in names), ast.unparse(node)
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("execute", "executemany", "scalars"), ast.unparse(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not re.search(r"\bSELECT\s.+\sFROM\b", node.value, re.S | re.I)


def test_each_caller_reads_the_owner_first() -> None:
    """The owner path does not change: each caller asks the package store for
    the owner before the viewer read, and the door holds one call of each."""
    check = inspect.getsource(invoke._check)
    assert check.index("packages_store.get_package(") < check.index(
        "package_host_allowed(org)") < check.index("_viewer.installed_app(")
    whole = Path(invoke.__file__).read_text(encoding="utf-8")
    assert whole.count("_viewer.installed_app(") == 1
    route = inspect.getsource(packages.get_package)
    assert route.index("store.get_package(") < route.index(
        "package_host_allowed(user.organization_id)") < route.index("viewer.installed_app(")
    assert Path(packages.__file__).read_text(encoding="utf-8").count(
        "viewer.installed_app(") == 1


# ── 3. GET /studio/packages/{id} ───────────────────────────────────────────


def _client(email: str) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
        access=build_access(["feature:studio"]))
    return TestClient(app)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """The fakes of both routes. ``answers`` holds the answer of each viewer
    read in turn, and the last one repeats."""
    w = SimpleNamespace(sessions=0, calls=[], answers=[_app()], audits=[], reads=[])

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield SimpleNamespace(n=w.sessions)

    async def get_package(db: Any, member: str, pid: str, **_: Any) -> Any:
        w.calls.append("owner")
        return _package() if member == OWEN and pid == PID else None

    async def installed_app(db: Any, *, viewer: str, app_id: str) -> Any:
        w.calls.append(f"viewer:{viewer}")
        i = sum(1 for c in w.calls if c.startswith("viewer:")) - 1
        return w.answers[min(i, len(w.answers) - 1)] if app_id == PID else None

    async def list_objects(db: Any, pid: str) -> list[Any]:
        w.calls.append("objects")
        return [_object("visit"), _object("secret")]

    async def by_name(db: Any, name: str, *, package_id: str) -> Any:
        return _object(name) if package_id == PID and name in ("visit", "secret") else None

    async def get_record(db: Any, oid: str, rid: str, *, package_id: str) -> Any:
        w.reads.append(("get", oid, rid))
        return objects_store.Record(rid, oid, dict(ROW_DATA), OWEN, OWEN, NOW, NOW)

    async def query(db: Any, *, package_id: str, object_id: str, **_: Any) -> Any:
        w.reads.append(("list", object_id))
        rec = objects_store.Record("r1", object_id, dict(ROW_DATA), OWEN, OWEN, NOW, NOW)
        return objects_store.RecordPage((rec,), 1, None, 0, 50, "")

    async def audit(**kw: Any) -> None:
        w.audits.append(kw)

    async def no_write(*_: Any, **__: Any) -> Any:
        raise AssertionError("a viewer reached a write")

    for module in (packages, invoke):
        monkeypatch.setattr(module, "_tenant_session", session)
    monkeypatch.setattr(packages_store, "get_package", get_package)
    monkeypatch.setattr(viewer, "installed_app", installed_app)
    monkeypatch.setattr(objects_store, "list_objects", list_objects)
    monkeypatch.setattr(objects_store, "get_object_by_name", by_name)
    monkeypatch.setattr(objects_store, "get_record", get_record)
    monkeypatch.setattr(objects_store, "query_records", query)
    monkeypatch.setattr(objects_store, "apply_writes", no_write)
    monkeypatch.setattr(invoke, "record_app_audit_bound", audit)
    monkeypatch.setattr(invoke, "record_app_audit_in", no_write)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    invoke._READS_IN_FLIGHT.clear()
    yield w
    invoke._READS_IN_FLIGHT.clear()


_OWNER_KEYS = {"id", "slug", "name", "description", "visibility", "status", "live_version",
               "created_at", "updated_at", "manifest", "manifest_hash"}


def test_the_owner_answer_does_not_change(world: SimpleNamespace) -> None:
    r = _client(OWEN).get(f"/studio/packages/{PID}")
    assert r.status_code == 200, r.text
    assert set(r.json()["package"]) == _OWNER_KEYS
    assert r.json()["package"]["manifest"] == DRAFT
    assert [o["name"] for o in r.json()["objects"]] == ["visit", "secret"]
    assert world.calls == ["owner", "objects"]


def test_a_viewer_reads_the_installed_version_and_never_the_draft(world: SimpleNamespace) -> None:
    r = _client(ANA).get(f"/studio/packages/{PID}")
    assert r.status_code == 200, r.text
    pkg = r.json()["package"]
    assert (pkg["access"], pkg["version"], pkg["name"]) == ("viewer", 1, "Site visits")
    assert pkg["manifest"] == INSTALLED
    # The objects that the installed version names, and not the new one of v2.
    assert [o["name"] for o in r.json()["objects"]] == ["visit"]
    # Fix round 1 (P2): the fields and the rules of v1 only, not the field that v2 added.
    [visit] = r.json()["objects"]
    assert [f["key"] for f in visit["fields"]] == ["site"] and visit["rules"] == []
    text = r.text
    for secret in ("Draft name", "draft_only", OWEN, f"visits-{PID[:8]}", "cost"):
        assert secret not in text, secret
    assert world.calls == ["owner", f"viewer:{ANA}", "objects"]


def test_a_member_with_no_install_gets_404(world: SimpleNamespace) -> None:
    world.answers = [None]
    r = _client(ANA).get(f"/studio/packages/{PID}")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert "objects" not in world.calls


def test_the_switch_off_is_404_and_runs_no_viewer_read(
        world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    r = _client(ANA).get(f"/studio/packages/{PID}")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.calls == ["owner"]
    # The owner still reads the package with the switch off.
    assert _client(OWEN).get(f"/studio/packages/{PID}").status_code == 200


def test_the_other_routes_stay_the_owners(world: SimpleNamespace) -> None:
    """J2a opens one read. The versions and each write stay 404 for a viewer."""
    c = _client(ANA)
    for path in (f"/studio/packages/{PID}/versions", f"/studio/packages/{PID}/versions/1"):
        assert c.get(path).status_code == 404, path
    assert c.put(f"/studio/packages/{PID}/draft", json=INSTALLED).status_code == 404
    assert c.post(f"/studio/packages/{PID}/versions", json={}).status_code == 404
    assert not any(c.startswith("viewer:") for c in world.calls)


# ── 4. The door ────────────────────────────────────────────────────────────


def _door(email: str, action: str, body: Any = None, query: str = "") -> Any:
    return _client(email).post(f"/studio/apps/{PID}/actions/{action}{query}",
                               json={} if body is None else body)


def test_a_viewer_gets_and_lists_on_the_installed_declarations(world: SimpleNamespace) -> None:
    r = _door(ANA, "peek", {"id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    r = _door(ANA, "find", {})
    assert r.status_code == 200, r.text
    assert r.json()["records"][0]["data"] == {"site": "Hosur"}
    assert [k for k, *_ in world.reads] == ["get", "list"]
    # Each call ran the viewer read at both checks: four reads in all.
    assert sum(1 for c in world.calls if c.startswith("viewer:")) == 4


def test_a_declaration_of_the_draft_does_not_reach_a_viewer(world: SimpleNamespace) -> None:
    r = _door(ANA, "draft_only", {})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_declared"
    assert world.reads == []


@pytest.mark.parametrize("action", ["add", "change", "remove"])
@pytest.mark.parametrize("how", ["plain", "ask", "direct"])
def test_a_viewer_write_is_403_read_only_at_the_first_check(
        world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, action: str, how: str) -> None:
    """V3. A write of a viewer runs nowhere: not as a direct write, and not
    as an ask for a card. The door opens no second session, so it reads no
    byte of the body."""
    monkeypatch.setattr(app_requests, "requests_allowed", lambda org: True)
    if how == "direct":
        monkeypatch.setattr(invoke, "_is_direct", lambda request, user: True)
    query = "?ask=1" if how == "ask" else ""
    body = {"data": {"site": "x"}} if action == "add" else {"id": str(uuid.uuid4())}
    r = _door(ANA, action, body, query)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "read_only", "message": invoke.REFUSALS["read_only"]}
    assert world.sessions == 1 and world.reads == []
    [row] = world.audits
    assert row["detail"]["outcome"] == "refused" and row["detail"]["reason"] == "read_only"


def test_a_viewer_never_reaches_the_write_branch(world: SimpleNamespace) -> None:
    """The second guard: in the session of the executor, a viewer's app never
    reaches a write, whatever the class says."""
    src = inspect.getsource(invoke._run_executor)
    guard = src.index("if not isinstance(package, packages_store.Package):")
    assert src.index("in DIRECT_EXECUTORS:") < guard < src.index('if mode == "ask":')
    assert guard < src.index("await _run_object_write(")


@pytest.mark.parametrize("ref_text", ["object:visit.create", "object:visit.update",
                                      "object:visit.delete"])
def test_refusal_for_a_viewer_is_read_only_for_each_write(ref_text: str) -> None:
    ref = parse_uses(ref_text)
    assert ref is not None
    for direct, delegated in ((False, False), (True, False), (False, True)):
        cls = "B" if ref.op != "delete" else "C"
        assert invoke.refusal_for(ref, cls, direct, delegated, True) == "read_only"
    assert invoke.refusal_for(parse_uses("object:visit.list"), "A", viewer=True) is None
    assert invoke.refusal_for(parse_uses("object:visit.list"), "X", viewer=True) == "class_x"


def test_an_install_that_goes_during_the_read_is_404_at_the_second_check(
        world: SimpleNamespace) -> None:
    world.answers = [_app(), None]
    r = _door(ANA, "find", {})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.reads == [] and world.audits == []


def test_the_switch_off_gives_a_viewer_404_at_the_door(
        world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """V2. With ``PACKAGE_HOST_ORGS`` off, the door reads no install."""
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    r = _door(ANA, "find", {})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert not any(c.startswith("viewer:") for c in world.calls)


# ── 5. The cap of the reads of a viewer ────────────────────────────────────


def test_a_viewer_read_past_the_cap_is_503_and_opens_no_second_session(
        world: SimpleNamespace) -> None:
    """V4."""
    invoke._READS_IN_FLIGHT[ORG] = invoke.READERS_PER_ORG
    r = _door(ANA, "find", {})
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == {"code": "store_busy", "message": invoke.READ_BUSY_MESSAGE}
    assert r.headers["retry-after"] == invoke.BUSY_RETRY_AFTER
    assert world.sessions == 1 and world.reads == []
    assert invoke._READS_IN_FLIGHT == {ORG: invoke.READERS_PER_ORG}


def test_the_owner_read_takes_no_slot(world: SimpleNamespace) -> None:
    """The answers to the owner do not change: a full cap leaves the owner
    read as it was."""
    invoke._READS_IN_FLIGHT[ORG] = invoke.READERS_PER_ORG
    r = _door(OWEN, "find", {})
    assert r.status_code == 200, r.text
    assert invoke._READS_IN_FLIGHT == {ORG: invoke.READERS_PER_ORG}


def test_a_viewer_read_gives_its_slot_back(world: SimpleNamespace) -> None:
    assert _door(ANA, "find", {}).status_code == 200
    assert invoke._READS_IN_FLIGHT == {}


def test_the_reader_slot_has_the_shape_of_the_writer_slot() -> None:
    from contextlib import ExitStack

    invoke._READS_IN_FLIGHT.clear()
    try:
        with ExitStack() as stack:
            held = [stack.enter_context(invoke._reader_slot(f"o{i // 3}", True))
                    for i in range(6)]
            assert held == [True] * 6 and invoke._READS_IN_FLIGHT == {"o0": 3, "o1": 3}
            # The first reader of an org always gets a slot.
            with invoke._reader_slot("o9", True) as first:
                assert first is True
                with invoke._reader_slot("o9", True) as second:
                    assert second is False
            with invoke._reader_slot("o0", True) as fourth:
                assert fourth is False
            with invoke._reader_slot("o0", False) as not_wanted:
                assert not_wanted is False
            # The two counters are separate.
            assert invoke._IN_FLIGHT == {}
        assert invoke._READS_IN_FLIGHT == {}
        with pytest.raises(RuntimeError), invoke._reader_slot("o", True) as got:
            assert got and invoke._READS_IN_FLIGHT == {"o": 1}
            raise RuntimeError("the read failed")
        assert invoke._READS_IN_FLIGHT == {}
    finally:
        invoke._READS_IN_FLIGHT.clear()
    assert (invoke.READERS_PER_ORG, invoke.READERS_TOTAL) == (3, 6)


# ── 6. The audit row ───────────────────────────────────────────────────────


def test_the_audit_row_of_a_viewer_names_the_installed_version(world: SimpleNamespace) -> None:
    assert _door(ANA, "find", {}).status_code == 200
    assert _door(OWEN, "find", {}).status_code == 200
    viewer_row, owner_row = world.audits
    assert viewer_row["user_email"] == ANA and viewer_row["app_id"] == PID
    assert viewer_row["detail"]["viewer"] is True and viewer_row["detail"]["version"] == 1
    assert "viewer" not in owner_row["detail"] and "version" not in owner_row["detail"]


# ── 6b. Fix round 1: the fields of the installed version ───────────────────


def test_project_keeps_the_fields_and_the_rules_of_the_installed_version() -> None:
    """V6. A publish only adds fields to the rows. The view of a viewer keeps
    the fields and the rules that the installed version declares."""
    shown = _app().project(_object("visit"))
    assert [f.key for f in shown.fields] == ["site"] and shown.constraints == ()
    v2 = {**INSTALLED, "objects": [{"name": "visit", "fields": [
        {"key": "site", "type": "text"}, {"key": "cost", "type": "number", "hot": True}],
        "rules": [COST_RULE]}]}
    pinned_v2 = viewer.InstalledApp(id=PID, version=2, manifest=v2)
    shown = pinned_v2.project(_object("visit"))
    assert [f.key for f in shown.fields] == ["site", "cost"]
    assert [r.name for r in shown.constraints] == ["cost_ok"]
    # An object that the version does not declare keeps no field.
    assert _app().project(_object("secret")).fields == ()


def test_project_record_keeps_the_installed_values_and_no_writer() -> None:
    rec = objects_store.Record("r1", "obj-visit", dict(ROW_DATA), OWEN, OWEN, NOW, NOW)
    shown = _app().project_record("visit", rec)
    assert shown.data == {"site": "Hosur"}
    assert (shown.created_by, shown.updated_by) == ("", "")
    assert (shown.id, shown.created_at) == ("r1", NOW)


def test_a_viewer_record_holds_no_newer_value_and_no_writer(world: SimpleNamespace) -> None:
    for action, body in (("peek", {"id": str(uuid.uuid4())}), ("find", {})):
        r = _door(ANA, action, body)
        assert r.status_code == 200, r.text
        rec = r.json()["record"] if action == "peek" else r.json()["records"][0]
        assert rec["data"] == {"site": "Hosur"}, action
        assert (rec["created_by"], rec["updated_by"]) == ("", ""), action
    # The owner reads the newest version, as before.
    mine = _door(OWEN, "find", {}).json()["records"][0]
    assert mine["data"] == ROW_DATA and mine["created_by"] == OWEN


@pytest.mark.parametrize("body", [
    {"filter": [{"field": "cost", "op": "gt", "value": 1}]},
    {"sort": {"field": "cost", "dir": "desc"}},
], ids=["filter", "sort"])
def test_a_viewer_cannot_filter_or_sort_on_a_newer_field(world: SimpleNamespace,
                                                         body: dict[str, Any]) -> None:
    """V7. A filter on a field of a newer version would tell a viewer its
    values. The one grammar refuses the key before any read."""
    r = _door(ANA, "find", body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "query_field_unknown"
    assert world.reads == []
    # The owner may filter on it.
    assert _door(OWEN, "find", body).status_code == 200


def test_the_chat_read_of_a_viewer_says_who_changes_the_app(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat tool reads the same answer. A viewer is told that only the
    owner changes the app, and never to publish it."""
    import asyncio

    from acb_skills import studio_tools as st

    answers: dict[str, Any] = {}

    async def fake_get(path: str) -> Any:
        return st._Reply(200, answers["body"])

    monkeypatch.setattr(st, "_get", fake_get)
    pkg = {"id": PID, "name": "Site visits", "access": "viewer", "version": 1,
           "manifest": INSTALLED}
    for objects, note in (([], st._VIEWER_EMPTY), ([{"name": "visit", "fields": [], "rules": []}],
                                                   st._VIEWER_NOTE)):
        answers["body"] = {"package": pkg, "objects": objects}
        out = asyncio.run(st.studio_package_get(PID))
        assert (out["access"], out["installed_version"], out["note"]) == ("viewer", 1, note)
        assert "Publish" not in out["note"]
    answers["body"] = {"package": {"id": PID, "name": "Site visits", "status": "live",
                                   "manifest": INSTALLED}, "objects": []}
    owner = asyncio.run(st.studio_package_get(PID))
    assert "access" not in owner and owner["note"].startswith("This package has no object yet.")


# ── 7. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_viewer_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
