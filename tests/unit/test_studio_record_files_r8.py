"""WS-STU D13b — the file store of a Studio record and its serve route, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 D13b. Code:
``gateway/routes/studio/files.py``, ``gateway/studio/files_store.py``,
``gateway/studio/reach.py`` and the migration that adds ``studio_record_files``
(442 at build). The hermetic twin is ``tests/unit/test_studio_record_files.py``.

The real package, install, record and serve handlers run on a phase-4 promoted
scratch database, as the non-privileged app role, so FORCE RLS binds each
statement. Org A has Owen (the owner), Ana and Ben in ``ops``, and Cat in
``eng``. Org B has Zed. Owen installs the app for ``ops``. The app holds two
objects. ``invoice`` is open, with a file field ``pdf`` and a text field
``title``. ``ticket`` has the role ``staff``, who reads the tickets where
``assignee = $me`` and may not read the field ``secret``.

**Roles of a viewer.** C8b is not built, so the route reads a viewer with no
role (``Viewer(user.email)``). The row-rule cases give the viewer the role
``staff`` through one patch of ``files.Viewer``, so the real row rule runs in
the real SQL. The case with no patch shows that a viewer with no role reads
no record of ``ticket``.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_record_files_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**R7: red first, then mutate.** M1 to M7 were RUN by an independent verifier,
each restored after the run (2026-11-14). The test that goes red is named for each,
and M3 is the one that stays green:

* M1. ``files_store._READ_SQL``: drop ``AND record_id = ...``. R8:
  ``test_r8_a_file_id_written_into_another_record_is_404``. Hermetic:
  ``test_the_read_sql_binds_the_record_and_the_field_key_and_the_call_passes_them``.
* M2. ``files.get_record_file``: skip the ``reached.paused`` check. R8:
  ``test_r8_a_paused_package_is_404_for_a_viewer_and_423_for_the_owner``.
* M3. ``files._read``: pass ``rec`` before ``project_record``. NO case goes red
  (advisory, not a fence). Since a viewer takes its field types from the
  installed version (``InstalledApp.field_types``), a key of a newer version is
  not a file field for that viewer, so ``package.field_types()`` already gives
  404 for the case of
  ``test_r8_a_key_of_a_newer_version_is_404_for_a_pinned_viewer``. The
  ``project_record`` line is defence in depth. Remove it only with a test that
  also removes the type map.
* M4. ``files._read``: drop the ``_holds_file`` test. Hermetic:
  ``test_a_file_id_that_the_record_does_not_hold_is_404_before_the_store``. No
  R8 case goes red, because ``read_file`` binds the ids too, so the R8 answer
  stays 404.
* M5. The migration: ``NO FORCE`` on the table. No R8 case goes red, because
  ``generated/04_policies.sql`` forces RLS again in the promoted template.
  The hermetic source test
  ``test_the_migration_is_tenant_scoped_and_expand_only`` fences it.
* M6 (field key). ``files_store._READ_SQL``: drop ``AND field_key = ...``. R8:
  ``test_r8_a_file_id_of_a_hidden_key_written_into_a_visible_field_is_404``.
  Hermetic: the same test as M1.
* M7 (I1a). ``files._read``: drop the ``_needs_connection`` call. R8:
  ``test_r8_the_connection_gate_hides_the_file_and_gate_off_serves``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.studio_access import Viewer
from fastapi import HTTPException, Response
from gateway.routes.studio import files, invoke, packages
from gateway.studio import files_store, objects_store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@d13b.test"
#: Mixed case in ``app_user`` and in the session, so each read folds it.
ANA = f"Ana.{TAG}@d13b.test"
ANA_LOW = ANA.lower()
BEN = f"ben.{TAG}@d13b.test"
BEN_LOW = BEN.lower()
CAT = f"cat.{TAG}@d13b.test"
ZED = f"zed.{TAG}@d13b.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_EMAIL_LIKE = f"%.{TAG}@d13b.test"
_GROUP_LIKE = f"%{TAG}"
_SLUG_LIKE = f"d13b{TAG}-%"
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
NOT_FOUND = "Not found"
#: Bytes with NUL and high bytes, so a text path would break them.
PDF = b"%PDF-1.4\n\x00\xff\xfe binary \x80" + bytes(range(256)) * 20 + b"\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _manifest(mid: str, with_v2: bool = False) -> dict[str, Any]:
    ticket_fields = [{"key": "assignee", "type": "person", "hot": True},
                     {"key": "att", "type": "file"}, {"key": "secret", "type": "file"}]
    invoice_fields = [{"key": "title", "type": "text"}, {"key": "pdf", "type": "file"}]
    if with_v2:
        invoice_fields.append({"key": "newer", "type": "file"})
    return {
        "studio": 1, "id": mid, "name": "Documents", "purpose": "Hold the files of a record.",
        "roles": ["staff"],
        "objects": [
            {"name": "invoice", "fields": invoice_fields},
            {"name": "ticket", "fields": ticket_fields,
             "access": [{"role": "staff", "read": {"where": "assignee = $me"},
                         "hide": ["secret"]}]},
        ],
        "actions": [{"id": "show_invoice", "uses": "object:invoice.get"},
                    {"id": "show_ticket", "uses": "object:ticket.get"}],
    }


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def stream(self) -> Any:
        yield self._raw


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "save_draft", "publish", "install_package"):
        kwargs["request"] = _Request(payload)
    response = Response()
    response.status_code = None  # type: ignore[assignment]
    if route == "install_package":
        kwargs["response"] = response
    token = bind_tenant(org)
    try:
        got = await handler(**kwargs)
        return response.status_code or (201 if route == "install_package" else 200), got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _get(org: str, email: str, pid: str, obj: str, rid: str,
               fid: str) -> tuple[int, Any]:
    """Call the real serve route with *org* bound."""
    token = bind_tenant(org)
    try:
        return 200, await files.get_record_file(pid, obj, rid, fid, user=_user(org, email))
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _make(org: str, pid: str, obj_name: str, data: dict[str, Any],
                key: str, content: bytes = PDF, mime: str = "application/pdf",
                name: str = "Invoice 1.pdf") -> tuple[str, str]:
    """One record of *obj_name* with a file in the field *key*. Answers the
    record id and the file id. The record is written by Owen."""
    async with tenant_session(org) as db:
        obj = await objects_store.get_object_by_name(db, obj_name, package_id=pid)
        assert obj is not None
        rec = await objects_store.create_record(db, OWEN, obj.id, data, package_id=pid,
                                                viewer=Viewer(OWEN))
        assert rec is not None
        fid = await files_store.put_file(
            db, OWEN, package_id=pid, object_id=obj.id, record_id=rec.id, field_key=key,
            name=name, mime=mime, content=content)
        done = await objects_store.update_record(
            db, OWEN, obj.id, rec.id, {key: {"id": fid}}, package_id=pid, viewer=Viewer(OWEN))
        assert done is not None
    return rec.id, fid


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        users: dict[str, str] = {}
        for email, org in ((OWEN, p.org_a), (ANA, p.org_a), (BEN, p.org_a), (CAT, p.org_a),
                           (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        for group, org, members in ((OPS, p.org_a, (OWEN, ANA, BEN)), (ENG, p.org_a, (CAT,)),
                                    (f"group:ext{TAG}", p.org_b, (ZED,))):
            gid = str(c.execute(text(
                "INSERT INTO org_group (organization_id, slug, display_name) "
                "VALUES (:org, :s, :s) RETURNING id"),
                {"org": org, "s": group.removeprefix("group:")}).scalar_one())
            for email in members:
                c.execute(text(
                    "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                    "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": org})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, mid=f"d13b{TAG}-docs")

    async def setup() -> None:
        status, got = await _call("create_package", w.a, OWEN, payload=_manifest(w.mid))
        assert status == 200, got
        w.pid = str(got["package"]["id"])
        assert (await _call("publish", w.a, OWEN, w.pid, payload={}))[0] == 200
        status, got = await _call("install_package", w.a, OWEN, w.pid,
                                  payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 201, got
        w.inv_rid, w.inv_fid = await _make(w.a, w.pid, "invoice", {"title": "One"}, "pdf")
        w.inv2_rid, w.inv2_fid = await _make(
            w.a, w.pid, "invoice", {"title": "Two"}, "pdf", content=PNG, mime="image/png",
            name="Two.png")
        w.t_ana, w.t_ana_fid = await _make(w.a, w.pid, "ticket", {"assignee": ANA_LOW}, "att")
        w.t_ben, w.t_ben_fid = await _make(w.a, w.pid, "ticket", {"assignee": BEN_LOW}, "att")
        w.secret_rid, w.secret_fid = await _make(
            w.a, w.pid, "ticket", {"assignee": ANA_LOW}, "secret")

    _run(w, setup)
    yield w
    invoke._READS_IN_FLIGHT.clear()
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("studio_record_files", "package_id"), ("app_audit", "app_id"),
                           ("studio_objects", "package_id"), ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


@pytest.fixture
def staff(monkeypatch):
    """C8b gives a viewer its roles. Until then, this gives each viewer of the
    route the role ``staff``, so the real row rule runs."""
    monkeypatch.setattr(files, "Viewer",
                        lambda email: Viewer(email, roles=frozenset({"staff"})))


def _admin(world: Any, sql: str, **params: Any) -> Any:
    with world.p.admin_engine.begin() as c:
        res = c.execute(text(sql), params)
        return res.all() if res.returns_rows else []


def _count_files(world: Any, rid: str | None = None) -> int:
    sql = "SELECT count(*) FROM studio_record_files"
    if rid is not None:
        return int(_admin(world, sql + " WHERE record_id = CAST(:r AS uuid)", r=rid)[0][0])
    return int(_admin(world, sql)[0][0])


def _ok(got: tuple[int, Any], content: bytes, mime: str) -> None:
    status, resp = got
    assert status == 200, got
    assert resp.body == content and resp.media_type == mime
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "private, no-store"


# ── The table ──────────────────────────────────────────────────────────────


def test_r8_the_table_forces_rls_and_org_b_sees_no_row(world) -> None:
    rows = _admin(world, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                         "WHERE oid = 'studio_record_files'::regclass")
    assert tuple(rows[0]) == (True, True)
    assert _count_files(world) == 5
    orgs = _admin(world, "SELECT DISTINCT organization_id::text FROM studio_record_files")
    assert [r[0] for r in orgs] == [world.a]

    async def go() -> int:
        async with tenant_session(world.b) as db:
            return int((await db.execute(text(
                "SELECT count(*) FROM studio_record_files"))).scalar_one())

    assert _run(world, go) == 0


def test_r8_an_insert_with_another_org_id_fails_the_with_check(world) -> None:
    # Org B is bound. The row names org A, so the WITH CHECK must refuse it.
    # The object id is read as the admin.
    obj = _admin(world, "SELECT object_id::text FROM studio_record_files LIMIT 1")[0][0]

    async def insert() -> str:
        async with tenant_session(world.b) as db:
            try:
                await db.execute(text(
                    "INSERT INTO studio_record_files (organization_id, package_id, object_id, "
                    "record_id, field_key, name, mime, size_bytes, content, created_by) "
                    "VALUES (CAST(:a AS uuid), CAST(:p AS uuid), CAST(:o AS uuid), "
                    "CAST(:r AS uuid), 'pdf', 'x.pdf', 'application/pdf', 1, "
                    "decode('00', 'hex'), 'x@y.z')"),
                    {"a": world.a, "p": world.pid, "o": obj, "r": world.inv_rid})
            except Exception as exc:
                return str(exc)
        return ""

    assert "row-level security" in _run(world, insert)
    assert _count_files(world) == 5


def test_r8_the_checks_of_the_table_refuse_a_bad_row(world) -> None:
    obj = _admin(world, "SELECT object_id::text FROM studio_record_files LIMIT 1")[0][0]
    base = dict(a=world.a, p=world.pid, o=obj, r=world.inv_rid)
    sql = ("INSERT INTO studio_record_files (organization_id, package_id, object_id, "
           "record_id, field_key, name, mime, size_bytes, content, created_by) VALUES "
           "(CAST(:a AS uuid), CAST(:p AS uuid), CAST(:o AS uuid), CAST(:r AS uuid), "
           ":k, :n, :m, :s, decode(:c, 'hex'), 'x@y.z')")
    good = dict(base, k="pdf", n="x.pdf", m="application/pdf", s=1, c="00")
    for change, check in (({"m": "text/html"}, "studio_record_files_mime"),
                          ({"s": 2}, "studio_record_files_size"),
                          ({"k": "Bad Key"}, "studio_record_files_field_key"),
                          ({"n": ""}, "studio_record_files_name")):
        with pytest.raises(Exception, match=check), world.p.admin_engine.begin() as c:
            c.execute(text(sql), {**good, **change})
    with pytest.raises(Exception, match="studio_record_files_record_fk"), \
            world.p.admin_engine.begin() as c:
        c.execute(text(sql), {**good, "r": str(uuid.uuid4())})
    assert _count_files(world) == 5


# ── The owner and the viewer read the bytes ────────────────────────────────


def test_r8_the_owner_opens_the_file_and_the_bytes_match(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, world.inv_fid),
                await _get(world.a, OWEN, world.pid, "invoice", world.inv2_rid, world.inv2_fid)]

    pdf, png = _run(world, go)
    _ok(pdf, PDF, "application/pdf")
    assert pdf[1].headers["content-disposition"].startswith('inline; filename="Invoice_1.pdf"')
    _ok(png, PNG, "image/png")
    assert png[1].headers["content-disposition"].startswith("attachment;")
    assert _count_files(world, world.inv_rid) == 1


def test_r8_a_viewer_in_the_group_opens_the_file_and_the_bytes_match(world) -> None:
    got = _run(world, lambda: _get(world.a, ANA, world.pid, "invoice", world.inv_rid,
                                   world.inv_fid))
    _ok(got, PDF, "application/pdf")


def test_r8_org_b_gets_404_on_each_id_of_org_a(world) -> None:
    got = _run(world, lambda: _get(world.b, ZED, world.pid, "invoice", world.inv_rid,
                                   world.inv_fid))
    assert got == (404, NOT_FOUND)


def test_r8_a_viewer_outside_the_group_gets_404(world) -> None:
    got = _run(world, lambda: _get(world.a, CAT, world.pid, "invoice", world.inv_rid,
                                   world.inv_fid))
    assert got == (404, NOT_FOUND)


def test_r8_a_flag_off_gives_404(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    got = _run(world, lambda: _get(world.a, OWEN, world.pid, "invoice", world.inv_rid,
                                   world.inv_fid))
    assert got == (404, NOT_FOUND)


def test_r8_package_host_orgs_off_gives_404_to_the_viewer_and_200_to_the_owner(
        world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")

    async def go() -> list[tuple[int, Any]]:
        return [await _get(world.a, ANA, world.pid, "invoice", world.inv_rid, world.inv_fid),
                await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, world.inv_fid)]

    viewer, owner = _run(world, go)
    assert viewer == (404, NOT_FOUND)
    _ok(owner, PDF, "application/pdf")


def test_r8_a_paused_package_is_404_for_a_viewer_and_423_for_the_owner(world) -> None:
    _admin(world, "UPDATE apps SET paused_at = now(), paused_by = :o "
                  "WHERE id = CAST(:p AS uuid)", o=OWEN.lower(), p=world.pid)

    async def go() -> list[tuple[int, Any]]:
        return [await _get(world.a, ANA, world.pid, "invoice", world.inv_rid, world.inv_fid),
                await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, world.inv_fid)]

    viewer, owner = _run(world, go)
    assert viewer == (404, NOT_FOUND)
    assert owner[0] == 423 and owner[1]["code"] == "package_paused"


# ── The record and the key ─────────────────────────────────────────────────


def test_r8_a_file_id_of_another_record_is_404(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [
            # The file of record two, asked on record one.
            await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, world.inv2_fid),
            await _get(world.a, ANA, world.pid, "invoice", world.inv_rid, world.inv2_fid),
            # A made-up id and a bad id.
            await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, str(uuid.uuid4())),
            await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, "not-a-uuid"),
            # A record of another object, asked on this object.
            await _get(world.a, OWEN, world.pid, "ticket", world.inv_rid, world.inv_fid),
        ]

    assert _run(world, go) == [(404, NOT_FOUND)] * 5


def test_r8_a_record_delete_removes_the_file_and_the_route_gives_404(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        before = await _get(world.a, ANA, world.pid, "invoice", world.inv_rid, world.inv_fid)
        async with tenant_session(world.a) as db:
            obj = await objects_store.get_object_by_name(db, "invoice", package_id=world.pid)
            assert obj is not None
            assert await objects_store.delete_record(
                db, OWEN, obj.id, world.inv_rid, package_id=world.pid, viewer=Viewer(OWEN))
        return [before,
                await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, world.inv_fid),
                await _get(world.a, ANA, world.pid, "invoice", world.inv_rid, world.inv_fid)]

    before, owner, viewer = _run(world, go)
    assert before[0] == 200
    assert _count_files(world, world.inv_rid) == 0
    assert owner == viewer == (404, NOT_FOUND)
    assert _count_files(world) == 4


def test_r8_a_key_of_a_newer_version_is_404_for_a_pinned_viewer(world) -> None:
    """Owen adds the file field ``newer`` and publishes version 2. The group
    install stays at version 1. A file in ``newer`` reads for the owner, and
    404 for Ana."""
    async def go() -> list[tuple[int, Any]]:
        v2 = _manifest(world.mid, with_v2=True)
        assert (await _call("save_draft", world.a, OWEN, world.pid, payload=v2))[0] == 200
        assert (await _call("publish", world.a, OWEN, world.pid, payload={}))[0] == 200
        rid, fid = await _make(world.a, world.pid, "invoice", {"title": "Three"}, "newer")
        return [await _get(world.a, OWEN, world.pid, "invoice", rid, fid),
                await _get(world.a, ANA, world.pid, "invoice", rid, fid)]

    owner, viewer = _run(world, go)
    _ok(owner, PDF, "application/pdf")
    assert viewer == (404, NOT_FOUND)


# ── The rules of C8 ────────────────────────────────────────────────────────


def test_r8_a_viewer_with_no_role_reads_no_record_of_a_bound_object(world) -> None:
    got = _run(world, lambda: _get(world.a, ANA, world.pid, "ticket", world.t_ana,
                                   world.t_ana_fid))
    assert got == (404, NOT_FOUND)


def test_r8_a_row_rule_gives_the_own_record_and_404_on_another_persons(world, staff) -> None:
    async def go() -> dict[str, tuple[int, Any]]:
        return {
            "own": await _get(world.a, ANA, world.pid, "ticket", world.t_ana, world.t_ana_fid),
            "other": await _get(world.a, ANA, world.pid, "ticket", world.t_ben,
                                world.t_ben_fid),
            "ben": await _get(world.a, BEN, world.pid, "ticket", world.t_ben, world.t_ben_fid),
            "owner": await _get(world.a, OWEN, world.pid, "ticket", world.t_ben,
                                world.t_ben_fid),
        }

    got = _run(world, go)
    _ok(got["own"], PDF, "application/pdf")
    assert got["other"] == (404, NOT_FOUND)
    _ok(got["ben"], PDF, "application/pdf")
    _ok(got["owner"], PDF, "application/pdf")


def test_r8_a_hidden_key_gives_404(world, staff) -> None:
    """``secret`` is hidden from ``staff``. Ana reads her own ticket, but the
    file of the hidden key is 404 for her and 200 for the owner."""
    async def go() -> list[tuple[int, Any]]:
        return [await _get(world.a, ANA, world.pid, "ticket", world.secret_rid,
                           world.secret_fid),
                await _get(world.a, OWEN, world.pid, "ticket", world.secret_rid,
                           world.secret_fid)]

    viewer, owner = _run(world, go)
    assert viewer == (404, NOT_FOUND)
    _ok(owner, PDF, "application/pdf")


def test_r8_a_viewer_past_the_reader_cap_gets_503_and_the_owner_is_not_counted(
        world, monkeypatch) -> None:
    monkeypatch.setattr(invoke, "READERS_PER_ORG", 1)
    invoke._READS_IN_FLIGHT[world.a] = 1

    async def go() -> list[tuple[int, Any]]:
        return [await _get(world.a, ANA, world.pid, "invoice", world.inv_rid, world.inv_fid),
                await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, world.inv_fid)]

    viewer, owner = _run(world, go)
    assert viewer[0] == 503 and viewer[1]["code"] == "store_busy"
    assert owner[0] == 200


# ── The binds of the read, and the I1a gate ────────────────────────────────


def _put_field(world: Any, obj_name: str, rid: str, key: str, fid: str) -> None:
    """Write *fid* into the file field *key* as the owner. The ``file`` field
    accepts any UUID today (D13b-2 is open)."""
    async def go() -> None:
        async with tenant_session(world.a) as db:
            obj = await objects_store.get_object_by_name(db, obj_name, package_id=world.pid)
            assert obj is not None
            assert await objects_store.update_record(
                db, OWEN, obj.id, rid, {key: {"id": fid}}, package_id=world.pid,
                viewer=Viewer(OWEN)) is not None

    _run(world, go)


def test_r8_a_file_id_written_into_another_record_is_404(world) -> None:
    """Record one holds the id of the file of record two in its visible field.
    The bind on ``record_id`` must stop it."""
    _put_field(world, "invoice", world.inv_rid, "pdf", world.inv2_fid)

    async def go() -> list[tuple[int, Any]]:
        return [await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, world.inv2_fid),
                await _get(world.a, ANA, world.pid, "invoice", world.inv_rid, world.inv2_fid)]

    assert _run(world, go) == [(404, NOT_FOUND)] * 2


def test_r8_a_file_id_of_a_hidden_key_written_into_a_visible_field_is_404(
        world, staff) -> None:
    """The file of ``secret`` of the ticket is named in the visible field
    ``att`` of the same ticket. The bind on ``field_key`` must stop it."""
    _put_field(world, "ticket", world.secret_rid, "att", world.secret_fid)

    async def go() -> list[tuple[int, Any]]:
        return [await _get(world.a, ANA, world.pid, "ticket", world.secret_rid,
                           world.secret_fid),
                await _get(world.a, OWEN, world.pid, "ticket", world.secret_rid,
                           world.secret_fid)]

    assert _run(world, go) == [(404, NOT_FOUND)] * 2


def test_r8_the_connection_gate_hides_the_file_and_gate_off_serves(
        world, monkeypatch) -> None:
    """The installed version names a connection that nobody has. Gate on: 404 for
    the viewer and the owner. Gate off: 200."""
    manifest = json.dumps({"connections": [f"mcp:tally-{TAG}"]})
    _admin(world, "UPDATE apps SET manifest = manifest || CAST(:m AS jsonb) "
                  "WHERE id = CAST(:p AS uuid)", m=manifest, p=world.pid)
    _admin(world, "UPDATE app_versions SET manifest = manifest || CAST(:m AS jsonb) "
                  "WHERE app_id = CAST(:p AS uuid)", m=manifest, p=world.pid)

    async def both() -> list[tuple[int, Any]]:
        return [await _get(world.a, ANA, world.pid, "invoice", world.inv_rid, world.inv_fid),
                await _get(world.a, OWEN, world.pid, "invoice", world.inv_rid, world.inv_fid)]

    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", world.a)
    assert _run(world, both) == [(404, NOT_FOUND)] * 2
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "")
    viewer, owner = _run(world, both)
    _ok(viewer, PDF, "application/pdf")
    _ok(owner, PDF, "application/pdf")
