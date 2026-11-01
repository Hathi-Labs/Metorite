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
e. Each of the four App Workshop readers skips a package.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
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
    from gateway.routes.apps import _common, lifecycle, pins
    from orchestrator import app_tools

    guard = "format IS DISTINCT FROM 'manifest'"
    for fn in (_common.load_app, lifecycle.list_apps, pins.list_pinned_apps,
               app_tools._granted_live_apps):
        assert guard in inspect.getsource(fn), fn.__qualname__


def test_the_r8_twin_is_named_in_the_ci_skip_guard() -> None:
    """The `--must-run` list of pr-check.yml is kept by hand. Without the
    entry, the R8 twin skips in CI with no database and the job stays green.
    This suite has no database gate, so it always reads the list."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_studio_packages_r8.py" in workflow
