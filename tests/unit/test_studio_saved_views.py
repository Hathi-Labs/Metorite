"""WS-STU AF-10a — the saved views of a Studio app, the hermetic half.

Spec: ``project-docs/specs/ai_studio.md`` §14 AF-10a. Code:
``gateway/routes/studio/views.py`` (the four routes),
``gateway/studio/views_store.py`` (the one module that names the table) and
the migration that adds ``studio_saved_views`` (441 at build), found by its
CONTENT, never by number (R1). The R8 half is
``tests/unit/test_studio_saved_views_r8.py``.

**Acceptance (hermetic).**

a. The switch is the first line of each handler. With the switch off, each
   route answers 404 and opens no session.
b. A bad body stops before any session: a body over the cap (413), a key
   that a view does not take, such as ``owner_email`` (422), and a spec that
   the shape check of C2a refuses (422).
c. The member is ``user.email``. No body key names the owner.
d. A viewer of an install cannot save a shared view (403 ``read_only``).
e. The audit row holds the op and the share flag only.
f. A viewer's read drops each part that names a key the viewer may not read,
   counts it, and never returns a dropped value.
g. The migration is expand-only and tenant-scoped with FORCE RLS. One module
   runs SQL on the table. The route module runs no SQL.
h. The R8 half is in both ``--must-run`` lists.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from fastapi import HTTPException
from gateway.routes.studio import views
from gateway.studio import views_store

_ROOT = Path(__file__).resolve().parents[2]
_ROUTES = _ROOT / "apps/services/gateway/gateway/routes/studio/views.py"
_STORE = _ROOT / "apps/services/gateway/gateway/studio/views_store.py"
_MIGRATION = next(
    p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
    if "CREATE TABLE IF NOT EXISTS studio_saved_views" in p.read_text(encoding="utf-8"))

ORG = "00000000-0000-0000-0000-0000000000aa"
PID = "00000000-0000-0000-0000-0000000000bb"
VID = "00000000-0000-0000-0000-0000000000cc"
ANA = "Ana@Example.test"
_GOOD = {"object": "visit", "name": "Open visits",
         "spec": {"filter": [{"field": "status", "op": "eq", "value": "open"}],
                  "sort": {"field": "due", "dir": "asc"}, "layout": "table",
                  "columns": ["site", "status"]}}


def _user(email: str = ANA) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
                       access=build_access(["feature:studio"]))


class _Request:
    def __init__(self, payload: Any = None, raw: bytes | None = None) -> None:
        self._raw = raw if raw is not None else (
            b"" if payload is None else json.dumps(payload).encode("utf-8"))

    async def stream(self) -> Any:
        yield self._raw


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Studio is on for :data:`ORG`. Each session that a handler opens is
    recorded, and yields a stand-in that runs no SQL."""
    opened: list[str] = []
    monkeypatch.setattr(get_settings(), "studio_orgs", ORG)

    @contextlib.asynccontextmanager
    async def _session() -> AsyncIterator[object]:
        opened.append("session")
        yield object()

    monkeypatch.setattr(views, "_tenant_session", _session)
    return opened


@pytest.fixture
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    async def _record(db: Any, **kwargs: Any) -> None:
        assert db is not None, "the audit row needs the open session of the write"
        rows.append(kwargs)

    monkeypatch.setattr(views, "record_app_audit_in", _record)
    return rows


def _status(coro: Any) -> tuple[int, Any]:
    try:
        return 200, asyncio.run(coro)
    except HTTPException as exc:
        return exc.status_code, exc.detail


# ── a. The switch first ────────────────────────────────────────────────────


_HANDLERS = ("list_views", "create_view", "update_view", "delete_view")


@pytest.mark.parametrize("name", _HANDLERS)
def test_the_switch_is_the_first_line_of_each_handler(name: str) -> None:
    tree = ast.parse(_ROUTES.read_text(encoding="utf-8"))
    [fn] = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
    first = fn.body[1] if isinstance(fn.body[0], ast.Expr) else fn.body[0]
    assert isinstance(first, ast.If), ast.unparse(first)
    assert ast.unparse(first.test) == "not flags.studio_allowed(user.organization_id)"


def test_the_switch_off_is_404_with_no_session(sessions: list[str],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    calls = [
        views.list_views(PID, user=_user(), object_name="visit"),
        views.create_view(PID, request=_Request(_GOOD), user=_user()),
        views.update_view(PID, VID, request=_Request({"name": "x"}), user=_user()),
        views.delete_view(PID, VID, user=_user()),
    ]
    for call in calls:
        assert _status(call) == (404, "Not found")
    assert sessions == []


def test_a_bad_id_is_404_with_no_session(sessions: list[str]) -> None:
    assert _status(views.list_views("nope", user=_user(), object_name="visit"))[0] == 404
    assert _status(views.delete_view(PID, "nope", user=_user()))[0] == 404
    assert sessions == []


# ── b. A bad body stops before any session ────────────────────────────────


def test_a_body_over_the_cap_is_413_with_no_session(sessions: list[str]) -> None:
    raw = b"{" + b" " * views.MAX_BODY_BYTES + b"}"
    status, detail = _status(views.create_view(PID, request=_Request(raw=raw), user=_user()))
    assert status == 413 and detail["code"] == "body_too_large"
    assert sessions == []


@pytest.mark.parametrize("body,code", [
    ({**_GOOD, "owner_email": "eve@example.test"}, "view_invalid"),
    ({**_GOOD, "organization_id": ORG}, "view_invalid"),
    ({**_GOOD, "object": "Visit"}, "view_invalid"),
    ({**_GOOD, "name": ""}, "view_invalid"),
    ({**_GOOD, "name": "x" * 121}, "view_invalid"),
    ({**_GOOD, "shared": "yes"}, "view_invalid"),
    ({**_GOOD, "position": True}, "view_invalid"),
    ({**_GOOD, "spec": {"me": True}}, "view_invalid"),
    ({**_GOOD, "spec": {"layout": "gallery"}}, "view_invalid"),
    ({**_GOOD, "spec": {"columns": ["a"] * 2}}, "view_invalid"),
    ({**_GOOD, "spec": {"columns": [f"c{i}" for i in range(51)]}}, "view_invalid"),
    ({**_GOOD, "spec": {"filter": [{"field": "a", "op": "near", "value": 1}]}},
     "query_op_not_allowed"),
    ({**_GOOD, "spec": {"filter": [{"field": "a", "op": "eq"}] * 9}}, "query_too_big"),
    ({**_GOOD, "spec": {"sort": {"field": "a", "dir": "up"}}}, "query_value_invalid"),
    ({**_GOOD, "spec": {"filter": [{"field": "a", "op": "eq", "value": "x" * 8000}]}},
     "view_invalid"),
])
def test_a_refused_body_is_422_with_no_session(sessions: list[str], body: dict[str, Any],
                                               code: str) -> None:
    status, detail = _status(views.create_view(PID, request=_Request(body), user=_user()))
    assert status == 422 and detail["code"] == code, detail
    assert sessions == []


def test_a_spec_with_a_nul_or_a_nan_is_refused_before_a_session(sessions: list[str]) -> None:
    for value in ("a\x00b", float("nan")):
        body = {**_GOOD, "spec": {"filter": [{"field": "a", "op": "eq", "value": value}]}}
        raw = json.dumps(body).encode("utf-8")
        status, detail = _status(views.create_view(PID, request=_Request(raw=raw),
                                                   user=_user()))
        assert status == 422 and detail["code"] == "view_invalid", detail
    assert sessions == []


def test_a_patch_takes_no_object_and_no_empty_body(sessions: list[str]) -> None:
    for body in ({}, {"object": "visit"}, {"owner_email": "eve@example.test"}):
        status, _ = _status(views.update_view(PID, VID, request=_Request(body), user=_user()))
        assert status == 422
    assert sessions == []


# ── c, d, e. The member, the share rule and the audit row ─────────────────


def _saved(**over: Any) -> views_store.SavedView:
    base: dict[str, Any] = {
        "id": VID, "package_id": PID, "object_name": "visit", "owner_email": ANA.lower(),
        "name": "Open visits", "shared": False, "spec": _GOOD["spec"], "position": 0,
        "created_at": None, "updated_at": None}
    return views_store.SavedView(**{**base, **over})


def test_the_member_comes_from_the_session(sessions: list[str], audits: list[dict[str, Any]],
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def _reach(db: Any, user: UserContext, pid: str) -> None:
        return None

    async def _create(db: Any, member: str, **kwargs: Any) -> views_store.SavedView:
        seen.append(member)
        return _saved(name=kwargs["name"])

    async def _holds(*_a: Any) -> bool:
        return True

    monkeypatch.setattr(views, "_reach", _reach)
    monkeypatch.setattr(views, "_holds_object", _holds)
    monkeypatch.setattr(views.store, "create_view", _create)
    status, got = _status(views.create_view(PID, request=_Request(_GOOD), user=_user()))
    assert status == 200 and got["view"]["mine"] is True
    assert "owner_email" not in got["view"]
    assert seen == [ANA] and sessions == ["session"]
    assert audits == [{"app_id": PID, "user_email": ANA, "kind": "action",
                       "detail": {"op": "view_create", "shared": False}}]


def test_a_viewer_cannot_save_a_shared_view(sessions: list[str], audits: list[dict[str, Any]],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.package_host.viewer import InstalledApp

    async def _reach(db: Any, user: UserContext, pid: str) -> InstalledApp:
        return InstalledApp(id=PID, version=1, manifest={})

    async def _create(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the store ran")

    monkeypatch.setattr(views, "_reach", _reach)
    monkeypatch.setattr(views.store, "create_view", _create)
    status, detail = _status(views.create_view(
        PID, request=_Request({**_GOOD, "shared": True}), user=_user()))
    assert status == 403 and detail["code"] == "read_only"
    assert audits == []


@pytest.mark.parametrize("before,after,op", [
    (False, False, "view_update"), (False, True, "view_share"),
    (True, False, "view_unshare"), (True, True, "view_update")])
def test_the_op_of_a_change(before: bool, after: bool, op: str) -> None:
    assert views._op(before, after) == op


def test_the_audit_row_is_value_free() -> None:
    tree = ast.parse(_ROUTES.read_text(encoding="utf-8"))
    [fn] = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_audit"]
    body = ast.unparse(ast.Module(body=fn.body[1:], type_ignores=[]))
    assert "detail={'op': op, 'shared': shared}" in body
    for word in ("name", "spec", "filter", "value", "columns"):
        assert re.search(rf"\b{word}\b", body) is None, word


# ── f. The read of a viewer ───────────────────────────────────────────────


def test_a_viewer_read_drops_each_part_that_names_a_hidden_key() -> None:
    spec = {"filter": [{"field": "site", "op": "eq", "value": "Plant 4"},
                       {"field": "salary", "op": "gt", "value": 98765}],
            "sort": {"field": "salary", "dir": "desc"}, "layout": "board",
            "columns": ["site", "salary", "status"]}
    out, dropped = views.project_spec(spec, frozenset({"site", "status"}))
    assert dropped == 3
    assert out == {"filter": [{"field": "site", "op": "eq", "value": "Plant 4"}],
                   "sort": None, "layout": "board", "columns": ["site", "status"]}
    assert "98765" not in json.dumps(out) and "salary" not in json.dumps(out)


def test_an_object_that_the_viewer_cannot_read_drops_everything() -> None:
    out, dropped = views.project_spec(_GOOD["spec"], frozenset())
    assert dropped == 4 and out["filter"] == [] and out["sort"] is None
    assert out["columns"] == [] and out["layout"] == "table"


def test_a_spec_with_no_parts_drops_nothing() -> None:
    assert views.project_spec({}, frozenset()) == ({}, 0)


def test_a_view_of_an_object_that_the_app_lacks_is_422(
        sessions: list[str], audits: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """P1 of the review: a made-up object name would open a new cap of 50."""
    async def _reach(db: Any, user: UserContext, pid: str) -> None:
        return None

    async def _no_object(db: Any, name: str, *, package_id: str) -> None:
        assert (name, package_id) == ("visit", PID)

    async def _create(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the store ran")

    monkeypatch.setattr(views, "_reach", _reach)
    monkeypatch.setattr(views.objects_store, "get_object_by_name", _no_object)
    monkeypatch.setattr(views.store, "create_view", _create)
    status, detail = _status(views.create_view(PID, request=_Request(_GOOD), user=_user()))
    assert status == 422 and detail == {"code": "view_invalid", "message": views.NO_OBJECT}
    assert audits == []


def test_a_viewer_names_only_an_object_of_the_installed_version() -> None:
    from gateway.package_host.viewer import InstalledApp

    async def go() -> bool:
        app = InstalledApp(id=PID, version=1, manifest={"objects": [{"name": "visit"}]})
        # The object row is never read, because the version lacks the name.
        return await views._holds_object(None, app, PID, "invoice")

    assert asyncio.run(go()) is False


def test_a_failed_audit_row_fails_the_write(sessions: list[str],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """The audit row runs in the session of the write. A failed row raises out
    of the session, which rolls the write back."""
    async def _reach(db: Any, user: UserContext, pid: str) -> None:
        return None

    async def _holds(*_a: Any) -> bool:
        return True

    async def _create(db: Any, member: str, **kwargs: Any) -> views_store.SavedView:
        return _saved(shared=True)

    async def _broken(db: Any, **_k: Any) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(views, "_reach", _reach)
    monkeypatch.setattr(views, "_holds_object", _holds)
    monkeypatch.setattr(views.store, "create_view", _create)
    monkeypatch.setattr(views, "record_app_audit_in", _broken)
    with pytest.raises(RuntimeError, match="audit down"):
        asyncio.run(views.create_view(PID, request=_Request({**_GOOD, "shared": True}),
                                      user=_user()))


@pytest.mark.parametrize("name", ["create_view", "update_view", "delete_view"])
def test_each_write_audits_inside_its_session(name: str) -> None:
    """The audit call sits inside the ``async with _tenant_session()`` block,
    so a share, an unshare and a delete of a shared view commit with it."""
    tree = ast.parse(_ROUTES.read_text(encoding="utf-8"))
    [fn] = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
    [block] = [n for n in ast.walk(fn) if isinstance(n, ast.AsyncWith)]
    inside = [n for n in ast.walk(block) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name) and n.func.id == "_audit"]
    every = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_audit"]
    assert len(inside) == len(every) == 1
    assert isinstance(inside[0].args[0], ast.Name) and inside[0].args[0].id == "db"
    assert "record_app_audit_bound" not in _ROUTES.read_text(encoding="utf-8")


# ── f2. The allowlist of the viewer read ──────────────────────────────────


def test_the_projection_names_each_key_of_a_spec() -> None:
    """``project_spec`` is an allowlist. A key that a later slice adds to
    ``SPEC_KEYS`` (AF-4 adds ``group``) fails here until the projection reads
    it."""
    assert views.SPEC_KEYS == views.PROJECTED_KEYS
    full = {"filter": [{"field": "a", "op": "eq", "value": 1}], "sort": {"field": "a"},
            "layout": "board", "columns": ["a"]}
    assert set(full) == set(views.SPEC_KEYS)
    out, dropped = views.project_spec(full, frozenset({"a"}))
    assert out == full and dropped == 0


def test_the_projection_drops_a_key_that_it_does_not_name() -> None:
    spec = {"filter": [{"field": "a", "op": "eq", "value": 1, "note": "x"}],
            "sort": {"field": "a", "dir": "asc", "why": "x"}, "group": "salary",
            "q": "SECRET", "layout": "gallery"}
    out, _ = views.project_spec(spec, frozenset({"a"}))
    assert out == {"filter": [{"field": "a", "op": "eq", "value": 1}],
                   "sort": {"field": "a", "dir": "asc"}}
    assert "SECRET" not in json.dumps(out) and "salary" not in json.dumps(out)


# ── f3. The hidden keys of the object store ───────────────────────────────


class _HiddenDB:
    """The two statements of ``query_hidden_keys``: the object and its
    package, then the fields when the viewer reads no record."""

    def __init__(self, row: Any, fields: list[tuple[str, str, Any, bool]]) -> None:
        self.row, self.fields = row, fields

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        from gateway.studio import objects_store as os_

        sql = str(stmt)
        rows = ([self.row] if self.row is not None else []) if sql == os_._VIEW_SQL else (
            self.fields if sql == os_._FIELDS_SQL else None)
        assert rows is not None, sql
        return type("R", (), {"first": lambda _s: rows[0] if rows else None,
                              "all": lambda _s: rows})()


_FIELDS = [("site", "text", {}, False), ("status", "text", {}, False)]
_ACCESS = [{"role": "boss", "read": "all", "hide": ["status"]}]


@pytest.mark.parametrize("row,viewer,want", [
    (None, ("ana@x.test", ()), None),
    ([None, _ACCESS, "owen@x.test"], ("owen@x.test", ()), frozenset()),
    ([None, None, "owen@x.test"], ("ana@x.test", ()), frozenset()),
    ([None, _ACCESS, "owen@x.test"], ("ana@x.test", ("boss",)), frozenset({"status"})),
    ([None, _ACCESS, "owen@x.test"], ("ana@x.test", ()), frozenset({"site", "status"})),
])
def test_the_hidden_keys_of_a_viewer(row: Any, viewer: tuple[str, tuple[str, ...]],
                                     want: Any) -> None:
    """None for a missing object. Nothing for the owner and for an open
    object. ``maybe_hidden`` for a viewer with a role. Each key for a viewer
    whose roles read no record."""
    from acb_common.studio_access import Viewer
    from gateway.studio import objects_store

    db = _HiddenDB(row, _FIELDS)
    got = asyncio.run(objects_store.query_hidden_keys(
        db, package_id=PID, object_id=VID, viewer=Viewer(viewer[0], frozenset(viewer[1]))))
    assert got == want


# ── g. The migration and the fences ───────────────────────────────────────


def test_the_migration_is_tenant_scoped_and_expand_only() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    assert re.search(r"REFERENCES organization \(id\) ON DELETE CASCADE\s+"
                     r"DEFAULT current_setting\('app.tenant_id', true\)::uuid", body)
    assert "ARRAY['studio_saved_views']" in body
    assert "'ALTER TABLE %I FORCE  ROW LEVEL SECURITY'" in body
    assert "AND relforcerowsecurity" in body
    assert "'USING      (organization_id = current_setting(''app.tenant_id'', true)::uuid) '" in body
    assert "'WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)'" in body
    assert ("FOREIGN KEY (organization_id, package_id)\n"
            "        REFERENCES apps (organization_id, id) ON DELETE CASCADE") in body
    assert "octet_length(spec::text) <= 8000" in body
    assert "char_length(name) BETWEEN 1 AND 120" in body
    assert "owner_email = lower(owner_email)" in body
    assert "lower(name)" in body and "CREATE UNIQUE INDEX IF NOT EXISTS" in body
    for word in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER COLUMN", "DROP INDEX"):
        assert word not in body.upper(), word
    assert "CREATE TABLE IF NOT EXISTS" in body and "CREATE INDEX IF NOT EXISTS" in body


def test_the_caps_of_the_store_match_the_checks_of_the_table() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert f"<= {views_store.MAX_SPEC_BYTES})" in sql
    assert f"BETWEEN 1 AND {views_store.MAX_NAME})" in sql
    assert views_store.MAX_PRIVATE == 50 and views_store.MAX_SHARED == 50
    assert views_store.MAX_PACKAGE_PRIVATE == 200 and views_store.MAX_PACKAGE_SHARED == 200


#: SQL that names the table: a statement verb before it.
_TABLE_SQL = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+studio_saved_views\b",
                        re.IGNORECASE)


def test_one_module_runs_sql_on_the_table() -> None:
    """Only ``views_store.py`` runs SQL on ``studio_saved_views``. The member
    purge names the table in a clause list, which builds its statement, so the
    regex does not see it there."""
    hits = sorted(
        p.relative_to(_ROOT).as_posix()
        for top in ("apps", "packages") for p in (_ROOT / top).rglob("*.py")
        if "node_modules" not in p.parts and ".venv" not in p.parts
        and _TABLE_SQL.search(p.read_text(encoding="utf-8", errors="replace")))
    assert hits == ["apps/services/gateway/gateway/studio/views_store.py"], hits


def test_the_route_module_runs_no_sql() -> None:
    src = _ROUTES.read_text(encoding="utf-8")
    for word in (r"\btext\(", r"\.execute\(", r"\bsqlalchemy\b", r"\bstudio_saved_views\b"):
        assert re.search(word, src) is None, word


def test_each_statement_of_the_store_names_the_bound_org() -> None:
    for name in ("_LIST_SQL", "_GET_SQL", "_COUNT_PRIVATE_SQL", "_COUNT_SHARED_SQL",
                 "_COUNT_PACKAGE_PRIVATE_SQL", "_COUNT_PACKAGE_SHARED_SQL",
                 "_UPDATE_SQL", "_DELETE_SQL"):
        sql = getattr(views_store, name)
        assert f"organization_id = {views_store._BOUND_ORG}" in sql, name
    assert "organization_id" not in views_store._INSERT_SQL
    assert "(shared OR owner_email = :me)" in views_store._LIST_SQL
    assert "(shared OR owner_email = :me)" in views_store._GET_SQL


def test_the_routes_read_neither_the_share_verb_nor_the_admin_right() -> None:
    src = _ROUTES.read_text(encoding="utf-8")
    for word in ("SHARE_RIGHT", "ADMIN_RIGHT", "studio:publish", "admin:members",
                 "has_permission"):
        assert word not in src, word


# ── h. The R8 half runs ───────────────────────────────────────────────────


def test_the_r8_suite_is_named_in_both_ci_skip_guards() -> None:
    """A skip is not a pass. The R8 guard reads its must-run list from
    tests/fences.json (CIV-33c-2), so the R8 half must be named there."""
    from tests.unit import _r8_lists as r8_lists

    assert "tests/unit/test_studio_saved_views_r8.py" in r8_lists.must_run()
