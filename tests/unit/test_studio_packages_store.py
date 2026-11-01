"""WS-STU A3 — the package store, with no database (hermetic).

Spec: ``project-docs/specs/ai_studio.md`` §3.6 and the §14 A3 row. Code:
``gateway/studio/packages_store.py``. The R8 twin is
``tests/unit/test_studio_packages_r8.py``.

What this suite holds:

a. The store validates a manifest before each write. A manifest that A2's
   ``validate_manifest`` refuses raises one ``PackageStoreError`` that
   carries A2's paths, and no statement runs.
b. The store checks the JSON text itself: an object, no NUL, no key that is
   not text, no float that Postgres writes longer, no lone surrogate, and at
   most 256 KiB. A manifest of exactly the limit passes.
c. The slug is ``<manifest id>-<8 hex digits of the new id>``. A taken slug
   makes the store try a new id, and the last refusal names no slug.
d. Each statement filters on the bound org, the format and the member, and no
   INSERT names an org.
e. Each of the five App Workshop readers skips a package. An empty
   `workspace_path` never means the working directory.
f. ``update_manifest`` refuses a new manifest id, and only the store writes
   a package (a source scan of ``apps/`` and ``packages/``).
g. A read of a row with no folder (``_to_detail``, ``_effective_manifest``)
   answers from the stored manifest, never 409 (fix round 2).
h. Publish, versions and rollback (A4): the order of the statements, the
   catalog check, the row lock, and a source scan that finds no UPDATE of
   ``app_versions``, no review proposal and no ``record_app_audit``.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_manifest
from gateway.studio import packages_store as store

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "COMMENT ON COLUMN apps.format IS" in p.read_text(encoding="utf-8"))

MANIFEST: dict[str, Any] = {
    "studio": 1,
    "id": "quotes",
    "name": "Quotes",
    "purpose": "Write quotes from the price list.",
    "objects": [{"name": "quote", "fields": [
        {"key": "customer", "type": "text"},
        {"key": "amount", "type": "money"},
    ]}],
    "actions": [{"id": "set_amount", "uses": "object:quote.update", "set": {"amount": 1}}],
    "views": [{"id": "list", "block": "table", "source": {"object": "quote"},
               "columns": ["customer", "amount"]}],
}


def _manifest(**patch: Any) -> dict[str, Any]:
    d = copy.deepcopy(MANIFEST)
    d.update(patch)
    return d


def _size(doc: dict[str, Any]) -> int:
    return len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))


def manifest_of_size(size: int, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """A valid manifest whose JSON text holds exactly *size* bytes. The
    markdown views carry the bulk, at most 20,000 characters each (A2).
    *base* is the start, ``MANIFEST`` by default."""
    doc = copy.deepcopy(base) if base is not None else _manifest()
    i = 0
    while _size(doc) < size:
        view = {"id": f"note-{i}", "block": "markdown", "text": ""}
        doc["views"].append(view)
        i += 1
        view["text"] = "x" * max(0, min(size - _size(doc), studio_manifest.MAX_STRING_CHARS))
    over = _size(doc) - size
    if over > 0:
        prev = doc["views"][-2]
        prev["text"] = prev["text"][:-over]
    assert _size(doc) == size
    return doc


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row

    def all(self) -> list[Any]:
        return [] if self._row is None else [self._row]


class _FakeDb:
    """Records each statement. Answers the queued rows in order, then None."""

    def __init__(self, *rows: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows = list(rows)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(stmt), dict(params or {})))
        return _Result(self._rows.pop(0) if self._rows else None)


def _row(params: dict[str, Any]) -> tuple[Any, ...]:
    now = datetime.now(UTC)
    return (params.get("id", str(uuid.uuid4())), params.get("slug", "quotes-00000000"),
            params["name"], params["desc"], params["m"], "private", "draft",
            params["manifest"], None, now, now)


class _EchoDb(_FakeDb):
    """Answers each INSERT with the row that it wrote, after *misses* Nones."""

    def __init__(self, misses: int = 0) -> None:
        super().__init__()
        self.misses = misses

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(stmt), dict(params or {})))
        if self.misses:
            self.misses -= 1
            return _Result(None)
        return _Result(_row(dict(params or {})))


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


# ── a. Validate before write ────────────────────────────────────────────────


def test_the_example_manifest_is_valid() -> None:
    assert studio_manifest.validate_manifest(MANIFEST) == []
    assert json.loads(store.check_manifest(MANIFEST)) == MANIFEST


@pytest.mark.parametrize(("patch", "path"), [
    ({"views": [{"id": "list", "block": "spreadsheet"}]}, "$.views[0].block"),
    ({"views": [{"id": "list", "block": "table", "color": "#ff0000"}]}, "$.views[0].color"),
    ({"studio": 2}, "$.studio"),
    ({"id": "Bad Id"}, "$.id"),
    ({"rules": []}, "$.rules"),
])
def test_an_invalid_manifest_raises_a2s_paths_and_writes_nothing(
        patch: dict[str, Any], path: str) -> None:
    bad = _manifest(**patch)
    a2_paths = [e.path for e in studio_manifest.validate_manifest(bad)]
    assert path in a2_paths
    for call in (lambda db: store.create_package(db, "ana@x.test", bad),
                 lambda db: store.update_manifest(db, "ana@x.test", str(uuid.uuid4()), bad)):
        db = _FakeDb()
        with pytest.raises(store.PackageStoreError) as exc:
            _run(call(db))
        assert exc.value.paths == a2_paths
        assert all(e.message for e in exc.value.errors)
        assert db.calls == []


@pytest.mark.parametrize("doc", [[], "quotes", None, 1, ("studio", 1)])
def test_a_manifest_that_is_not_an_object_is_refused(doc: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.create_package(db, "ana@x.test", doc))
    assert exc.value.paths == ["$"]
    assert db.calls == []


def test_a_missing_studio_key_is_refused() -> None:
    """The CHECK tests `studio` too. The store refuses it first, through A2."""
    doc = _manifest()
    del doc["studio"]
    with pytest.raises(store.PackageStoreError) as exc:
        store.check_manifest(doc)
    assert "$.studio" in exc.value.paths


# ── b. The JSON text ────────────────────────────────────────────────────────


def test_the_size_cap_is_the_number_of_a2_and_of_the_check() -> None:
    assert store.MAX_MANIFEST_BYTES == studio_manifest.MAX_DOC_BYTES == 262144
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert f"octet_length(manifest::text) <= {store.MAX_MANIFEST_BYTES}" in sql


def test_a_manifest_of_exactly_the_limit_passes_and_one_byte_more_is_refused() -> None:
    at = manifest_of_size(store.MAX_MANIFEST_BYTES)
    assert len(store.check_manifest(at).encode("utf-8")) == store.MAX_MANIFEST_BYTES
    over = manifest_of_size(store.MAX_MANIFEST_BYTES + 1)
    # A2's estimate passes it, so only the store's own count refuses it.
    assert studio_manifest.validate_manifest(over) == []
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.create_package(db, "ana@x.test", over))
    assert exc.value.paths == ["$"]
    assert "256 KiB" in exc.value.errors[0].message
    assert db.calls == []


def _with_set(value: Any) -> dict[str, Any]:
    return _manifest(actions=[{"id": "set_amount", "uses": "object:quote.update",
                               "set": {"amount": value}}])


@pytest.mark.parametrize(("doc", "path"), [
    (_manifest(name="Quo\x00tes"), "$.name"),
    (_with_set("a\x00b"), "$.actions[0].set.amount"),
    (_with_set({"k\x00": 1}), "$.actions[0].set.amount"),
    (_with_set({1: "x"}), "$.actions[0].set.amount"),
    (_with_set(1e308), "$.actions[0].set.amount"),
    (_with_set(1e-07), "$.actions[0].set.amount"),
    (_with_set(1e15), "$.actions[0].set.amount"),
    (_with_set(float("nan")), "$.actions[0].set.amount"),
    (_with_set(float("inf")), "$.actions[0].set.amount"),
    (_with_set("lone \ud800 surrogate"), "$"),
])
def test_the_store_refuses_text_that_jsonb_refuses_or_grows(doc: dict[str, Any], path: str) -> None:
    """A2 passes each of these. Postgres refuses the NUL and the surrogate,
    and writes a float in exponent form longer than the store counts it."""
    assert studio_manifest.validate_manifest(doc) == []
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.create_package(db, "ana@x.test", doc))
    assert exc.value.paths == [path]
    assert db.calls == []


@pytest.mark.parametrize("value", [0.5, 1.0, -2.25, 123456789012345.0, 10, -3, True, None])
def test_the_store_takes_a_number_whose_text_does_not_grow(value: Any) -> None:
    doc = _with_set(value)
    assert json.loads(store.check_manifest(doc)) == doc


# ── c. The slug ─────────────────────────────────────────────────────────────


def test_the_slug_is_the_manifest_id_and_8_hex_digits_of_the_new_id() -> None:
    db = _EchoDb()
    pkg = _run(store.create_package(db, " Ana@X.test ", MANIFEST))
    assert len(db.calls) == 1
    _sql, params = db.calls[0]
    new_id = uuid.UUID(params["id"])
    assert params["slug"] == f"quotes-{new_id.hex[:8]}"
    assert re.fullmatch(r"quotes-[0-9a-f]{8}", pkg.slug)
    assert pkg.id == str(new_id)
    assert params["m"] == "ana@x.test"
    assert (params["name"], params["desc"]) == (MANIFEST["name"], MANIFEST["purpose"])
    assert json.loads(params["manifest"]) == MANIFEST
    assert pkg.visibility == "private" and pkg.status == "draft"


def test_a_taken_slug_makes_the_store_try_a_new_id() -> None:
    db = _EchoDb(misses=1)
    pkg = _run(store.create_package(db, "ana@x.test", MANIFEST))
    assert len(db.calls) == 2
    (_, first), (_, second) = db.calls
    assert first["id"] != second["id"] and first["slug"] != second["slug"]
    assert pkg.slug == second["slug"]


def test_when_every_try_is_taken_the_refusal_names_no_slug() -> None:
    db = _EchoDb(misses=store.SLUG_TRIES)
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.create_package(db, "ana@x.test", MANIFEST))
    assert len(db.calls) == store.SLUG_TRIES
    assert exc.value.errors == []
    text = str(exc.value)
    for _, params in db.calls:
        assert params["slug"] not in text
    assert "quotes" not in text


# ── d. The statements ───────────────────────────────────────────────────────


def test_the_insert_writes_a_private_draft_package_and_names_no_org() -> None:
    sql = store._CREATE_SQL
    columns = sql.split("INSERT INTO apps", 1)[1].split(")", 1)[0]
    assert "organization_id" not in columns
    assert "format" in columns and "workspace_path" in columns
    assert "'private', 'draft'" in sql and "'', 'manifest')" in sql
    assert "ON CONFLICT (slug) DO NOTHING" in sql


@pytest.mark.parametrize("name", ["_GET_SQL", "_LIST_SQL", "_UPDATE_SQL"])
def test_each_read_and_update_filters_on_org_format_and_member(name: str) -> None:
    sql = getattr(store, name)
    assert f"organization_id = {store._BOUND_ORG}" in sql
    assert "format = 'manifest'" in sql
    assert "owner_email = :m" in sql
    assert ":org" not in sql


def test_the_update_changes_no_slug_visibility_status_or_format() -> None:
    sets = store._UPDATE_SQL.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    for column in ("slug", "visibility", "status", "format", "owner_email"):
        assert f"{column} =" not in sets, column


def test_the_store_has_no_route_and_no_org_argument() -> None:
    src = inspect.getsource(store)
    assert "APIRouter" not in src and "gateway.routes" not in src
    for fn in (store.create_package, store.get_package, store.list_packages,
               store.update_manifest):
        params = inspect.signature(fn).parameters
        assert list(params)[:2] == ["db", "member"], fn.__name__
        assert not [p for p in params if "org" in p], fn.__name__


@pytest.mark.parametrize("member", ["", "   ", None, 7])
def test_a_bad_member_is_refused_before_the_db(member: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError):
        _run(store.list_packages(db, member))
    assert db.calls == []


@pytest.mark.parametrize("package_id", ["", "not-a-uuid", None, 12])
def test_a_bad_package_id_is_refused_before_the_db(package_id: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError):
        _run(store.get_package(db, "ana@x.test", package_id))
    assert db.calls == []


# ── e. The readers that skip a package ──────────────────────────────────────


def test_each_app_workshop_reader_skips_a_package() -> None:
    """Without the guard, a package shows in /apps, opens on a slug route,
    shows as a pin, or becomes an agent tool. The R8 twin runs each one."""
    from gateway.routes import workspace
    from gateway.routes.apps import _common, lifecycle, pins
    from orchestrator import app_tools

    guard = "format IS DISTINCT FROM 'manifest'"
    for fn in (_common.load_app, lifecycle.list_apps, pins.list_pinned_apps,
               app_tools._granted_live_apps, workspace._app_workspace_for):
        assert guard in inspect.getsource(fn), fn.__qualname__


def test_the_r8_twin_is_named_in_the_ci_skip_guard() -> None:
    """The `--must-run` list of pr-check.yml is kept by hand. Without the
    entry, the R8 twin skips in CI with no database and the job stays green.
    This suite has no database gate, so it always reads the list."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_studio_packages_r8.py" in workflow


def test_an_empty_workspace_path_is_never_the_working_directory() -> None:
    """`Path('')` is `.`, the working directory of the process. A package
    stores `workspace_path = ''`, so the one rule answers None for it."""
    from fastapi import HTTPException
    from gateway.routes.apps import _common

    for value in ("", "   ", "\t", None):
        assert _common.workspace_dir(value) is None, repr(value)
        with pytest.raises(HTTPException) as exc:
            _common.app_workspace(SimpleNamespace(workspace_path=value))
        assert exc.value.status_code == 409
    assert _common.workspace_dir("/srv/apps/quotes") == Path("/srv/apps/quotes")
    assert _common.app_workspace({"workspace_path": "/srv/apps/quotes"}) == Path(
        "/srv/apps/quotes")


class _GraphSession:
    """The sync session of ``acb_graph.tenant_session``: the apps read
    answers *apps*, and each other read answers no row."""

    def __init__(self, apps: list[Any]) -> None:
        self.apps = apps

    def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        rows = self.apps if "FROM apps" in str(stmt) else []
        return SimpleNamespace(fetchall=lambda: list(rows))


def _graph_with(monkeypatch: pytest.MonkeyPatch, apps: list[Any]) -> None:
    import acb_graph

    @contextlib.contextmanager
    def _session(_org: Any) -> Iterator[_GraphSession]:
        yield _GraphSession(apps)

    monkeypatch.setattr(acb_graph, "tenant_session", _session)


def test_app_workspace_for_refuses_an_empty_workspace_path(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An app row with `workspace_path = ''` never binds a session. Without
    the guard, the path is the working directory, which holds every folder
    below it, and the owner may write there."""
    from gateway.routes.workspace import _app_workspace_for

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ben-app").mkdir()
    inside_cwd = (tmp_path / "ben-app" / "index.html").resolve()
    for blank in ("", "   "):
        _graph_with(monkeypatch, [SimpleNamespace(
            id="a1", owner_email="ana@x.test", workspace_path=blank)])
        for write in (False, True):
            assert _app_workspace_for(
                inside_cwd, "ana@x.test", "org-a", write=write) is False, (blank, write)
    # A real folder still binds, so the test is not empty.
    real = tmp_path / "real-app"
    real.mkdir()
    _graph_with(monkeypatch, [SimpleNamespace(
        id="a2", owner_email="ana@x.test", workspace_path=str(real))])
    assert _app_workspace_for((real / "index.html").resolve(), "ana@x.test", "org-a",
                              write=True) is True


def _current_row(manifest: dict[str, Any]) -> tuple[Any, ...]:
    now = datetime.now(UTC)
    return (str(uuid.uuid4()), "quotes-00000000", manifest["name"], manifest["purpose"],
            "ana@x.test", "private", "draft", json.dumps(manifest), None, now, now)


def test_update_manifest_refuses_a_new_manifest_id() -> None:
    """The slug holds the manifest id, so the id never changes. The store
    reads the row with a lock, refuses, and runs no UPDATE."""
    db = _FakeDb(_current_row(MANIFEST))
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.update_manifest(db, "ana@x.test", str(uuid.uuid4()),
                                   _manifest(id="other-id")))
    assert str(exc.value) == store.ID_FIXED
    assert str(exc.value) == "The manifest id cannot change. Create a new package."
    assert exc.value.paths == ["$.id"]
    assert len(db.calls) == 1 and db.calls[0][0].endswith(" FOR UPDATE")
    # The same id updates.
    same = _FakeDb(_current_row(MANIFEST), _current_row(_manifest(name="Price quotes")))
    got = _run(store.update_manifest(same, "ana@x.test", str(uuid.uuid4()),
                                     _manifest(name="Price quotes")))
    assert got is not None and got.name == "Price quotes"
    assert [c[0].split()[0] for c in same.calls] == ["SELECT", "UPDATE"]


_APPS_WRITE = re.compile(r"^\s*(?:INSERT\s+INTO|UPDATE)\s+apps\b", re.IGNORECASE)
_NAMES_PACKAGE = re.compile(r"(?<![A-Za-z0-9_])format(?![A-Za-z0-9_])|'manifest'", re.IGNORECASE)
#: A SET-list fragment that a caller joins into an UPDATE, as `sets` in
#: routes/apps/publish.py: `format = ...` at the start or after a comma.
_SETS_FORMAT = re.compile(r"(?:^|,)\s*format\s*=", re.IGNORECASE)


def _strings(tree: ast.AST) -> Iterator[str]:
    """Each string of a module. An f-string joins its text parts."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            yield "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)


def package_writers(root: Path = _ROOT) -> set[str]:
    """Each module under apps/ and packages/ with a statement that writes a
    package: an INSERT or UPDATE of `apps` that names `format` or
    `'manifest'`, or a SET fragment of `format`."""
    found: set[str] = set()
    for top in ("apps", "packages"):
        for path in (root / top).rglob("*.py"):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for s in _strings(tree):
                if (_APPS_WRITE.match(s) and _NAMES_PACKAGE.search(s)) or _SETS_FORMAT.search(s):
                    found.add(path.relative_to(root).as_posix())
                    break
    return found


def test_only_the_package_store_writes_a_package() -> None:
    """R7 for "only packages_store.py writes a package". The App Workshop
    writers name no `format`, so each of their rows is `code`. The migration
    and the tests are outside apps/ and packages/."""
    assert package_writers() == {"apps/services/gateway/gateway/studio/packages_store.py"}


def _detail_row(**patch: Any) -> SimpleNamespace:
    """An `apps` row with every field that `lifecycle._to_detail` reads."""
    fields: dict[str, Any] = {
        "id": str(uuid.uuid4()), "slug": "old-app", "name": "Old app", "icon": "",
        "description": "", "status": "draft", "visibility": "private",
        "live_version": None, "owner_email": "ana@x.test", "updated_at": None,
        "created_at": None, "is_template": False, "builder_session_id": None,
        "manifest": {"name": "From the row", "entry": "index.html"},
        "workspace_path": "",
    }
    fields.update(patch)
    return SimpleNamespace(**fields)


def test_a_read_of_a_row_with_no_folder_answers_from_the_stored_manifest(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix round 2. The detail of an app and the effective manifest of a run
    are READS. A row with an empty or no `workspace_path` answers from the
    stored manifest, never 409. The working directory holds an `app.json`,
    and no read takes it."""
    from acb_auth import UserContext, UserRole
    from gateway.routes.apps import lifecycle, runtime

    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.json").write_text(
        json.dumps({"name": "From the working directory"}), encoding="utf-8")
    ana = UserContext(email="ana@x.test", role=UserRole.EMPLOYEE)
    stored = {"name": "From the row", "entry": "index.html"}
    for blank in ("", "   ", None):
        row = _detail_row(workspace_path=blank)
        detail = lifecycle._to_detail(row, ana, [])
        assert detail.manifest == stored, blank
        assert detail.workspace_path is None, blank
        assert runtime._effective_manifest(row) == stored, blank
    # A row that has no `workspace_path` attribute at all, as a run's fake row.
    assert runtime._effective_manifest(SimpleNamespace(manifest=stored)) == stored
    # A real folder still reads its own app.json.
    real = tmp_path / "real-app"
    real.mkdir()
    (real / "app.json").write_text(json.dumps({"name": "From the folder"}), encoding="utf-8")
    row = _detail_row(workspace_path=str(real))
    assert lifecycle._to_detail(row, ana, []).manifest == {"name": "From the folder"}
    assert lifecycle._to_detail(row, ana, []).workspace_path == str(real)
    assert runtime._effective_manifest(row) == {"name": "From the folder"}


# ── h. Publish, versions and rollback (A4) ──────────────────────────────────

_CAT_MANIFEST_ACTION = {"id": "make_lead", "uses": "crm.create_lead", "set": {}}


def _pkg_row(manifest: dict[str, Any], live: int | None = None) -> tuple[Any, ...]:
    now = datetime.now(UTC)
    return (str(uuid.uuid4()), "quotes-00000000", manifest["name"], manifest["purpose"],
            "ana@x.test", "private", "draft", json.dumps(manifest), live, now, now)


def _ver_row(manifest: dict[str, Any], n: int = 1) -> tuple[Any, ...]:
    return (str(uuid.uuid4()), n, json.dumps(manifest), "sha", "notes", "ana@x.test",
            datetime.now(UTC), "auto")


def _verbs(db: _FakeDb) -> list[str]:
    return [" ".join(c[0].split()[:3]) for c in db.calls]


def test_publish_writes_the_version_the_pointer_and_the_audit_in_order() -> None:
    doc = _manifest()
    db = _FakeDb(_pkg_row(doc), _ver_row(doc, 1))
    ver = _run(store.publish_version(db, "Ana@X.test", str(uuid.uuid4()), "First.", catalog={}))
    assert ver is not None and ver.version == 1
    assert _verbs(db) == ["SELECT id::text, slug,", "INSERT INTO app_versions",
                          "UPDATE apps SET", "INSERT INTO app_audit"]
    assert db.calls[0][0].rstrip().endswith("FOR UPDATE")
    encoded = store.check_manifest(doc)
    assert db.calls[1][1]["sha"] == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert db.calls[1][1]["manifest"] == encoded
    assert db.calls[1][1]["m"] == "ana@x.test"
    sql = db.calls[1][0]
    assert "bundle_html" in sql and "'', :sha" in sql and "'auto'" in sql
    # The pointer and the status move. The draft stays as it is.
    assert "manifest" not in db.calls[2][0].split("WHERE")[0]
    assert "live_version = :v" in db.calls[2][0] and "status = 'live'" in db.calls[2][0]
    assert json.loads(db.calls[3][1]["detail"])["version"] == 1
    assert db.calls[3][1]["kind"] == "publish"


def test_publish_refuses_an_action_the_catalog_does_not_serve() -> None:
    doc = _manifest(actions=[*MANIFEST["actions"], _CAT_MANIFEST_ACTION])
    assert studio_manifest.validate_manifest(doc) == []
    db = _FakeDb(_pkg_row(doc))
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.publish_version(db, "ana@x.test", str(uuid.uuid4()), "", catalog={}))
    assert any("catalog" in e.message for e in exc.value.errors)
    assert _verbs(db) == ["SELECT id::text, slug,"]
    # With the action in the catalog, the same draft publishes.
    ok = _FakeDb(_pkg_row(doc), _ver_row(doc))
    assert _run(store.publish_version(
        ok, "ana@x.test", str(uuid.uuid4()), "", catalog={"crm.create_lead": "B"})) is not None
    # Class X never publishes.
    nope = _FakeDb(_pkg_row(doc))
    with pytest.raises(store.PackageStoreError):
        _run(store.publish_version(
            nope, "ana@x.test", str(uuid.uuid4()), "", catalog={"crm.create_lead": "X"}))


def test_publish_refuses_a_draft_that_fails_a2_and_writes_nothing() -> None:
    bad = _manifest(views=[{"id": "list", "block": "table", "color": "#ff0000"}])
    db = _FakeDb(_pkg_row(bad))
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.publish_version(db, "ana@x.test", str(uuid.uuid4()), "", catalog={}))
    assert "$.views[0].color" in exc.value.paths
    assert _verbs(db) == ["SELECT id::text, slug,"]


def test_publish_of_a_missing_package_answers_none_and_writes_nothing() -> None:
    db = _FakeDb()
    assert _run(store.publish_version(db, "ana@x.test", str(uuid.uuid4()), "", catalog={})) is None
    assert len(db.calls) == 1


@pytest.mark.parametrize("notes", [None, 5, "a\x00b", "x" * (store.MAX_NOTES_CHARS + 1)])
def test_bad_release_notes_are_refused_before_the_db(notes: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError):
        _run(store.publish_version(db, "ana@x.test", str(uuid.uuid4()), notes, catalog={}))
    assert db.calls == []


def test_rollback_copies_the_old_manifest_into_a_new_version_and_the_draft() -> None:
    old = _manifest(name="Old name")
    cur = _manifest(name="New name")
    db = _FakeDb(_pkg_row(cur, live=2), _ver_row(old, 1), _ver_row(old, 3))
    new = _run(store.rollback(db, "ana@x.test", str(uuid.uuid4()), 1))
    assert new is not None and new.version == 3
    assert _verbs(db) == ["SELECT id::text, slug,", "SELECT v.app_id::text, v.version,",
                          "INSERT INTO app_versions", "UPDATE apps SET",
                          "INSERT INTO app_audit"]
    encoded = store.check_manifest(old)
    assert db.calls[2][1]["manifest"] == encoded
    point = db.calls[3]
    assert point[1]["manifest"] == encoded and point[1]["name"] == "Old name"
    assert "manifest = CAST" in point[0] and "live_version = :v" in point[0]
    audit = json.loads(db.calls[4][1]["detail"])
    assert (audit["to_version"], audit["new_version"], audit["from_version"]) == (1, 3, 2)
    assert audit["draft_overwritten"] is True and db.calls[4][1]["kind"] == "rollback"


def test_rollback_refuses_the_live_version_and_a_missing_one() -> None:
    doc = _manifest()
    live = _FakeDb(_pkg_row(doc, live=2), _ver_row(doc, 2))
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.rollback(live, "ana@x.test", str(uuid.uuid4()), 2))
    assert str(exc.value) == store.ROLLBACK_LIVE
    assert len(live.calls) == 2
    missing = _FakeDb(_pkg_row(doc, live=2))
    assert _run(store.rollback(missing, "ana@x.test", str(uuid.uuid4()), 9)) is None
    assert len(missing.calls) == 2
    none = _FakeDb()
    assert _run(store.rollback(none, "ana@x.test", str(uuid.uuid4()), 1)) is None
    assert len(none.calls) == 1


@pytest.mark.parametrize("version", [0, -1, True, "1", None, 1.5])
def test_rollback_refuses_a_bad_version_number_before_the_db(version: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError):
        _run(store.rollback(db, "ana@x.test", str(uuid.uuid4()), version))
    assert db.calls == []


@pytest.mark.parametrize("name", [
    "_VERSION_LIST_SQL", "_VERSION_GET_SQL", "_PUBLISH_POINT_SQL", "_ROLLBACK_POINT_SQL"])
def test_each_version_statement_filters_on_org_format_and_member(name: str) -> None:
    sql = getattr(store, name)
    assert "current_setting('app.tenant_id', true)" in sql
    assert "format = 'manifest'" in sql and "owner_email = :m" in sql


def test_the_version_insert_and_the_audit_name_no_org_and_publish_locks_the_row() -> None:
    for sql in (store._VERSION_INSERT_SQL, store._AUDIT_SQL):
        assert "organization_id" not in sql
    assert "for_update=True" in inspect.getsource(store.publish_version)
    assert "for_update=True" in inspect.getsource(store.rollback)


def test_a_version_row_is_never_updated_or_deleted_and_no_review_is_proposed() -> None:
    """A4 appends to `app_versions`. The review step of the App Workshop
    (`app.publish_review`) never runs for a package, and `record_app_audit`
    (it opens its own session) is not used. The store imports no route."""
    source = (_ROOT / "apps" / "services" / "gateway" / "gateway" / "studio"
              / "packages_store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    scopes = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, scopes) and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    code = [n for n in ast.walk(tree) if id(n) not in docstrings]
    literals = [n.value for n in code if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    names = {n.id for n in code if isinstance(n, ast.Name)} | {
        n.attr for n in code if isinstance(n, ast.Attribute)}
    joined = "\n".join(literals)
    assert not re.search(r"UPDATE\s+app_versions|DELETE\s+FROM\s+app_versions", joined, re.I)
    assert "publish_review" not in joined and "propose" not in names
    assert "record_app_audit" not in names
    modules = {n.module or "" for n in code if isinstance(n, ast.ImportFrom)}
    assert not any(m.startswith("gateway.routes") for m in modules)


def test_the_a4_r8_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_studio_packages_versions_r8.py" in workflow


def test_publish_and_rollback_run_check_manifest_on_the_stored_manifest(
        monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    real = store.check_manifest

    def spy(doc: object) -> str:
        seen.append(str(doc.get("name") if isinstance(doc, dict) else doc))
        return real(doc)

    monkeypatch.setattr(store, "check_manifest", spy)
    doc = _manifest(name="Stored")
    _run(store.publish_version(
        _FakeDb(_pkg_row(doc), _ver_row(doc)), "ana@x.test", str(uuid.uuid4()), "", catalog={}))
    assert seen == ["Stored"]
    old = _manifest(name="Older")
    _run(store.rollback(
        _FakeDb(_pkg_row(doc, live=2), _ver_row(old, 1), _ver_row(old, 3)),
        "ana@x.test", str(uuid.uuid4()), 1))
    assert seen == ["Stored", "Older"]
