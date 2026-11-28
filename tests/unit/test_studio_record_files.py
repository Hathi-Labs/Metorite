"""WS-STU D13b — the file serve route of a Studio record, the hermetic half.

Spec: ``project-docs/specs/ai_studio.md`` §14 D13b. Code:
``gateway/routes/studio/files.py`` (the route), ``gateway/studio/files_store.py``
(the one module that names the table), ``gateway/studio/reach.py`` (the shared
resolver) and the migration that adds ``studio_record_files`` (442 at build, 518 at merge),
found by its CONTENT, never by number (R1). The R8 half is
``tests/unit/test_studio_record_files_r8.py``.

**Acceptance (hermetic).**

a. The switch is the first line of the handler. Off gives 404 and no session.
b. A bad id or a bad object name gives 404 before a session.
c. The order of the checks: package, install, object, record, file.
d. The headers: ``inline`` for a PDF only, ``nosniff``, ``private, no-store``.
e. A viewer takes a reader slot. An owner takes none.
f. The migration is expand-only and tenant-scoped. One module runs SQL on the
   table. The route runs none.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from fastapi import HTTPException
from gateway.package_host.viewer import InstalledApp
from gateway.routes.studio import files
from gateway.studio import files_store, reach
from gateway.studio.objects_store import Record

_ROOT = Path(__file__).resolve().parents[2]
_ROUTE = _ROOT / "apps/services/gateway/gateway/routes/studio/files.py"
_MIGRATION = next(
    p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
    if "CREATE TABLE IF NOT EXISTS studio_record_files" in p.read_text(encoding="utf-8"))

ORG = "00000000-0000-0000-0000-0000000000aa"
PID = "00000000-0000-0000-0000-0000000000bb"
RID = "00000000-0000-0000-0000-0000000000cc"
FID = "00000000-0000-0000-0000-0000000000dd"
OID = "00000000-0000-0000-0000-0000000000ee"
OTHER = "00000000-0000-0000-0000-0000000000ff"
ANA = "ana@example.test"

_MANIFEST = {
    "studio": 1, "id": "docs", "name": "Docs", "purpose": "Hold documents.",
    "objects": [{"name": "invoice", "fields": [
        {"key": "title", "type": "text"}, {"key": "pdf", "type": "file"}]}],
    "actions": [{"id": "show", "uses": "object:invoice.get"}],
}
_NO_GET = {**_MANIFEST, "actions": [{"id": "find", "uses": "object:invoice.list"}]}


def _user() -> UserContext:
    return UserContext(email=ANA, role=UserRole.EMPLOYEE, organization_id=ORG,
                       access=build_access(["feature:studio"]))


def _obj() -> SimpleNamespace:
    return SimpleNamespace(id=OID, name="invoice", fields=(
        SimpleNamespace(key="title", type="text"), SimpleNamespace(key="pdf", type="file")))


def _record(data: dict[str, Any] | None = None) -> Record:
    data = {"title": "A", "pdf": {"id": FID}} if data is None else data
    return Record(id=RID, object_id=OID, data=data, created_by="x@y.z", updated_by="x@y.z",
                  created_at=None, updated_at=None)  # type: ignore[arg-type]


def _file(mime: str = "application/pdf", name: str = "Invoice 1.pdf") -> files_store.RecordFile:
    return files_store.RecordFile(id=FID, name=name, mime=mime, size_bytes=3,
                                  content=b"abc", created_at=None)  # type: ignore[arg-type]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Studio on. Each store call is a stand-in that records its name."""
    w = SimpleNamespace(trace=[], reach=None, record=_record(), obj=_obj(), file=_file(),
                        slots=[], slot_ok=True)
    monkeypatch.setattr(get_settings(), "studio_orgs", ORG)

    @contextlib.asynccontextmanager
    async def _session() -> AsyncIterator[object]:
        w.trace.append("session")
        yield object()

    async def _reach(db: Any, member: str, org: Any, pid: str) -> Any:
        w.trace.append("reach")
        assert (member, org, pid) == (ANA, ORG, PID)
        return w.reach

    async def _get_object(db: Any, name: str, *, package_id: str) -> Any:
        w.trace.append("object")
        return w.obj

    async def _get_record(db: Any, oid: str, rid: str, *, package_id: str, viewer: Any) -> Any:
        w.trace.append("record")
        assert viewer.member == ANA if hasattr(viewer, "member") else True
        return w.record

    async def _read(db: Any, **kw: Any) -> Any:
        w.trace.append("file")
        w.read_kw = kw
        return w.file

    @contextlib.contextmanager
    def _slot(org: str, wanted: bool) -> Any:
        w.slots.append((org, wanted))
        yield wanted and w.slot_ok

    monkeypatch.setattr(files, "_tenant_session", _session)
    monkeypatch.setattr(files, "reach_package", _reach)
    monkeypatch.setattr(files.objects_store, "get_object_by_name", _get_object)
    monkeypatch.setattr(files.objects_store, "get_record", _get_record)
    monkeypatch.setattr(files.files_store, "read_file", _read)
    monkeypatch.setattr(files, "_reader_slot", _slot)
    return w


def _owner(paused: bool = False) -> reach.Reach:
    return reach.Reach(owned=SimpleNamespace(
        paused_at="now" if paused else None, manifest=_MANIFEST))  # type: ignore[arg-type]


def _viewer(manifest: dict[str, Any] = _MANIFEST) -> reach.Reach:
    return reach.Reach(app=InstalledApp(id=PID, version=1, manifest=manifest))


def _call(pid: str = PID, name: str = "invoice", rid: str = RID, fid: str = FID) -> Any:
    try:
        return 200, asyncio.run(files.get_record_file(pid, name, rid, fid, user=_user()))
    except HTTPException as exc:
        return exc.status_code, exc.detail


# ── a and b. The switch and the ids ────────────────────────────────────────


def test_the_switch_is_the_first_line_of_the_handler() -> None:
    tree = ast.parse(_ROUTE.read_text(encoding="utf-8"))
    [fn] = [n for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_record_file"]
    body = [n for n in fn.body if not isinstance(n, ast.Expr)]
    first = body[1]  # body[0] is `org = user.organization_id`
    assert isinstance(first, ast.If)
    assert ast.unparse(first.test) == "not flags.studio_allowed(org)"


def test_the_switch_off_is_404_with_no_session(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    world.reach = _owner()
    assert _call() == (404, "Not found")
    assert world.trace == []


@pytest.mark.parametrize("kw", [{"pid": "x"}, {"rid": "x"}, {"fid": "x"}, {"name": "Bad-Name"},
                                {"name": ""}])
def test_a_bad_id_or_name_is_404_before_a_session(world, kw) -> None:
    world.reach = _owner()
    assert _call(**kw) == (404, "Not found")
    assert world.trace == []


# ── c. The order ───────────────────────────────────────────────────────────


def test_the_owner_reads_in_the_door_order(world) -> None:
    world.reach = _owner()
    status, got = _call()
    assert status == 200 and got.body == b"abc"
    assert world.trace == ["session", "reach", "object", "record", "file"]
    assert world.read_kw == {"package_id": PID, "object_id": OID, "record_id": RID,
                             "field_key": "pdf", "file_id": FID}
    assert world.slots == [(ORG, False)]


def test_no_package_stops_at_the_package(world) -> None:
    world.reach = None
    assert _call() == (404, "Not found")
    assert world.trace == ["session", "reach"]


def test_a_paused_package_of_the_owner_is_423_before_the_object(world) -> None:
    world.reach = _owner(paused=True)
    status, detail = _call()
    assert status == 423 and detail["code"] == "package_paused"
    assert world.trace == ["session", "reach"]


def test_a_viewer_without_a_declared_get_stops_before_the_record(world) -> None:
    world.reach = _viewer(_NO_GET)
    assert _call() == (404, "Not found")
    assert world.trace == ["session", "reach"]


def test_a_viewer_of_an_object_the_version_lacks_stops_before_the_record(world) -> None:
    world.reach = _viewer()
    assert _call(name="other") == (404, "Not found")
    assert "record" not in world.trace


def test_no_record_stops_before_the_file(world) -> None:
    world.reach, world.record = _owner(), None
    assert _call() == (404, "Not found")
    assert world.trace == ["session", "reach", "object", "record"]


def test_no_object_stops_before_the_record(world) -> None:
    world.reach, world.obj = _owner(), None
    assert _call() == (404, "Not found")
    assert world.trace == ["session", "reach", "object"]


def test_a_file_id_that_the_record_does_not_hold_is_404_before_the_store(world) -> None:
    world.reach = _owner()
    world.record = _record({"title": "A", "pdf": {"id": OTHER}})
    assert _call() == (404, "Not found")
    assert "file" not in world.trace


def test_a_file_id_in_a_text_field_is_not_a_file(world) -> None:
    world.reach = _owner()
    world.record = _record({"title": FID})
    assert _call() == (404, "Not found")
    assert "file" not in world.trace


def test_a_key_the_version_does_not_declare_is_dropped_for_a_viewer(world) -> None:
    """A newer field is not in the manifest of the install, so the projected
    record has no value of it."""
    older = {**_MANIFEST, "objects": [{"name": "invoice", "fields": [
        {"key": "title", "type": "text"}]}]}
    world.reach = _viewer(older)
    assert _call() == (404, "Not found")
    assert "file" not in world.trace


def test_a_viewer_reads_when_the_install_declares_get_and_the_key(world) -> None:
    world.reach = _viewer()
    status, _ = _call()
    assert status == 200
    assert world.trace == ["session", "reach", "object", "record", "file"]


def test_a_many_field_holds_a_list_of_ids(world) -> None:
    world.reach = _owner()
    world.record = _record({"pdf": [{"id": OTHER}, {"id": FID}]})
    assert _call()[0] == 200


def test_a_file_the_store_does_not_find_is_404(world) -> None:
    world.reach, world.file = _owner(), None
    assert _call() == (404, "Not found")


# ── e. The slot ────────────────────────────────────────────────────────────


def test_a_viewer_takes_a_reader_slot(world) -> None:
    world.reach = _viewer()
    _call()
    assert world.slots == [(ORG, True)]


def test_a_viewer_past_the_cap_gets_503_and_reads_nothing(world) -> None:
    world.reach, world.slot_ok = _viewer(), False
    status, detail = _call()
    assert status == 503 and detail["code"] == "store_busy"
    assert world.trace == ["session", "reach"]


# ── d. The headers ─────────────────────────────────────────────────────────


def test_a_pdf_is_inline_with_the_three_headers(world) -> None:
    world.reach = _owner()
    _, got = _call()
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith('inline; filename="Invoice_1.pdf"')
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "text/html"])
def test_each_other_type_is_an_attachment(world, mime) -> None:
    world.reach, world.file = _owner(), _file(mime=mime, name='a"\r\nb.html')
    _, got = _call()
    disp = got.headers["content-disposition"]
    assert disp.startswith("attachment;")
    assert "\r" not in disp and "\n" not in disp and disp.count('"') == 2
    assert got.headers["x-content-type-options"] == "nosniff"


# ── The resolver ───────────────────────────────────────────────────────────


def test_the_resolver_reads_the_owner_first_and_the_install_second(monkeypatch) -> None:
    calls: list[str] = []

    async def _get(db, member, pid):
        calls.append("owner")
        return None

    async def _app(db, *, viewer, app_id):
        calls.append("install")
        return "APP"

    monkeypatch.setattr(reach.packages_store, "get_package", _get)
    monkeypatch.setattr(reach._viewer, "installed_app", _app)
    monkeypatch.setattr(get_settings(), "package_host_orgs", ORG)
    got = asyncio.run(reach.reach_package(object(), ANA, ORG, PID))
    assert got is not None and got.app == "APP" and calls == ["owner", "install"]
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    calls.clear()
    assert asyncio.run(reach.reach_package(object(), ANA, ORG, PID)) is None
    assert calls == ["owner"]


def test_views_uses_the_shared_resolver() -> None:
    src = (_ROOT / "apps/services/gateway/gateway/routes/studio/views.py").read_text(
        encoding="utf-8")
    assert "reach_package(" in src and "installed_app(" not in src
    assert "get_package(" not in src


# ── f. The migration and the one SQL module ────────────────────────────────


def test_the_migration_is_tenant_scoped_and_expand_only() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    assert re.search(r"REFERENCES organization \(id\) ON DELETE CASCADE\s+"
                     r"DEFAULT current_setting\('app.tenant_id', true\)::uuid", body)
    assert "ARRAY['studio_record_files']" in body
    assert "'ALTER TABLE %I FORCE  ROW LEVEL SECURITY'" in body
    assert "AND relforcerowsecurity" in body
    assert "'WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)'" in body
    assert ("FOREIGN KEY (organization_id, package_id)\n"
            "        REFERENCES apps (organization_id, id) ON DELETE CASCADE") in body
    assert ("FOREIGN KEY (organization_id, object_id, record_id)\n"
            "        REFERENCES studio_records (organization_id, object_id, id) "
            "ON DELETE CASCADE") in body
    assert "size_bytes BETWEEN 0 AND 10000000 AND size_bytes = octet_length(content)" in body
    assert "mime IN ('application/pdf', 'image/png', 'image/jpeg')" in body
    assert "ON studio_record_files (organization_id, record_id)" in body
    for word in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER COLUMN", "DROP INDEX",
                 "attachments"):
        assert word not in body.upper() if word.isupper() else word not in body, word


def test_the_cap_of_the_store_is_the_cap_of_the_table_and_the_renderer() -> None:
    from gateway.pdf_render import MAX_DOCUMENT_PDF_BYTES
    assert files_store.MAX_FILE_BYTES == MAX_DOCUMENT_PDF_BYTES == 10_000_000


_TABLE_SQL = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+studio_record_files\b",
                        re.IGNORECASE)


def test_one_module_runs_sql_on_the_table() -> None:
    hits = sorted(
        p.relative_to(_ROOT).as_posix()
        for top in ("apps", "packages") for p in (_ROOT / top).rglob("*.py")
        if "node_modules" not in p.parts and ".venv" not in p.parts
        and _TABLE_SQL.search(p.read_text(encoding="utf-8", errors="replace")))
    assert hits == ["apps/services/gateway/gateway/studio/files_store.py"], hits


def test_the_route_module_runs_no_sql() -> None:
    src = _ROUTE.read_text(encoding="utf-8")
    for word in (r"\btext\(", r"\.execute\(", r"\bsqlalchemy\b", r"\bstudio_record_files\b"):
        assert re.search(word, src) is None, word


def test_each_read_statement_names_the_bound_org() -> None:
    assert f"organization_id = {files_store._BOUND_ORG}" in files_store._READ_SQL
    assert "organization_id" not in files_store._INSERT_SQL


def test_put_file_refuses_a_bad_file_before_a_statement() -> None:
    class _Db:
        async def execute(self, *a: Any, **k: Any) -> Any:  # pragma: no cover
            raise AssertionError("no statement")

    base = dict(package_id=PID, object_id=OID, record_id=RID, field_key="pdf",
                name="a.pdf", mime="application/pdf", content=b"x")
    for change in ({"mime": "text/html"}, {"content": b"x" * 10_000_001}, {"name": ""},
                   {"field_key": "Bad"}, {"record_id": "x"}):
        with pytest.raises(files_store.FileStoreError):
            asyncio.run(files_store.put_file(_Db(), ANA, **{**base, **change}))


# ── The binds of the read, the I1a gate, a busy store and the viewer type ──


def test_the_read_sql_binds_the_record_and_the_field_key_and_the_call_passes_them() -> None:
    sql = files_store._READ_SQL
    assert "record_id = CAST(:rid AS uuid)" in sql and "field_key = :key" in sql
    seen: dict[str, Any] = {}

    class _Db:
        async def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
            seen.update(params)
            return SimpleNamespace(first=lambda: None)

    got = asyncio.run(files_store.read_file(
        _Db(), package_id=PID, object_id=OID, record_id=RID, field_key="pdf", file_id=FID))
    assert got is None
    assert seen["rid"] == RID and seen["key"] == "pdf" and seen["fid"] == FID


def test_the_i1a_gate_refusal_is_404_and_the_route_calls_the_door_helper(
        world, monkeypatch) -> None:
    calls: list[Any] = []

    async def _needs(db: Any, member: str, manifest: dict[str, Any], action: str,
                     preview: bool = False, *, org: Any) -> bool:
        calls.append((member, action, org))
        return True

    assert files._needs_connection.__module__ == "gateway.routes.studio.invoke"
    monkeypatch.setattr(files, "_needs_connection", _needs)
    world.reach = _viewer()
    assert _call() == (404, "Not found")
    assert calls == [(ANA, "show", ORG)]
    assert "record" not in world.trace and "file" not in world.trace


def test_the_i1a_gate_off_serves_a_viewer_whose_manifest_names_a_connection(world) -> None:
    """With the gate off, the connections of the manifest change nothing."""
    world.reach = _viewer({**_MANIFEST, "connections": ["mcp:none"]})
    assert _call()[0] == 200


def test_a_busy_store_is_503_with_retry_after_and_not_404(world, monkeypatch) -> None:
    async def _busy(*a: Any, **k: Any) -> Any:
        raise files.objects_store.StoreBusy("busy")

    monkeypatch.setattr(files.objects_store, "get_record", _busy)
    world.reach = _owner()
    try:
        asyncio.run(files.get_record_file(PID, "invoice", RID, FID, user=_user()))
    except HTTPException as exc:
        assert exc.status_code == 503 and exc.detail["code"] == "store_busy"
        assert "Retry-After" in exc.headers
    else:  # pragma: no cover
        raise AssertionError("no 503")


def test_a_viewer_takes_the_field_type_of_the_installed_version(world) -> None:
    """The draft row calls ``pdf`` a file. The installed version calls it text."""
    retyped = {**_MANIFEST, "objects": [{"name": "invoice", "fields": [
        {"key": "title", "type": "text"}, {"key": "pdf", "type": "text"}]}]}
    world.reach = _viewer(retyped)
    assert _call() == (404, "Not found")
    assert "file" not in world.trace
    world.reach = _owner()
    assert _call()[0] == 200
