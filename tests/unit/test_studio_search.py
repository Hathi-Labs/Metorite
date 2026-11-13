"""WS-STU C5a: company search over Studio records, the hermetic half.

Spec: ``project-docs/specs/ai_studio.md`` §14 C5a. The real database half is
``test_studio_search_r8.py``. Here the store, the session and the package reads
are fakes that record each call.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from acb_common.studio_access import Viewer
from acb_common.studio_fields import parse_field_spec
from acb_common.studio_query import QueryError
from gateway.retrieval import sources
from gateway.studio import objects_store, packages_store, search

ORG = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001"
NOW = datetime(2026, 11, 13, tzinfo=UTC)


def member(features: tuple[str, ...] = ("studio",), org: str | None = ORG) -> UserContext:
    return UserContext(
        email="Ana@Customer.Example", role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(f"feature:{f}" for f in features)),
        organization_id=org)


def field(key: str, type_: str = "text") -> Any:
    return parse_field_spec({"key": key, "type": type_})


def package(pid: str, search_objects: list[str], paused: bool = False) -> Any:
    return SimpleNamespace(
        id=pid, name=f"App {pid}", paused_at=NOW if paused else None,
        manifest={"shell": {"search": search_objects}})


def record(rid: str, data: dict[str, Any]) -> Any:
    return objects_store.Record(id=rid, object_id="o", data=data, created_by="w@x.test",
                                updated_by="w@x.test", created_at=NOW, updated_at=NOW)


class World:
    """The fakes. ``rows`` maps (package id, object, field key) to records."""

    def __init__(self) -> None:
        self.packages: list[Any] = []
        self.rows: dict[tuple[str, str, str], list[Any]] = {}
        self.fields: dict[str, list[Any]] = {"note": [field("title"), field("body")]}
        self.queries: list[dict[str, Any]] = []
        self.objects_read: list[str] = []
        self.fail: dict[str, BaseException] = {}
        self.refuse: set[str] = set()

    async def list_packages(self, db: Any, who: str) -> list[Any]:
        return self.packages

    async def get_object_by_name(self, db: Any, name: str, *, package_id: str) -> Any:
        self.objects_read.append(name)
        return objects_store.StudioObject(
            id=f"obj-{name}", package_id=package_id, name=name, label=None, created_by="o@x.test",
            created_at=NOW, updated_at=NOW, fields=tuple(self.fields.get(name, ())))

    async def query_records(self, db: Any, *, package_id: str, object_id: str,
                            viewer: Any, filter: Any = None, limit: Any = None,
                            budget_s: Any = None, **_: Any) -> Any:
        self.queries.append({"package": package_id, "object": object_id, "viewer": viewer,
                             "filter": filter, "limit": limit, "budget_s": budget_s})
        if package_id in self.fail:
            raise self.fail[package_id]
        [cond] = filter
        if cond["field"] in self.refuse:
            raise QueryError("query_field_unknown", "no")
        rows = self.rows.get((package_id, object_id.removeprefix("obj-"), cond["field"]), [])
        return SimpleNamespace(records=tuple(rows[: int(limit)]))


@pytest.fixture()
def world(monkeypatch) -> World:
    w = World()

    @contextlib.asynccontextmanager
    async def session():
        yield SimpleNamespace(begin_nested=lambda: _Nested())

    monkeypatch.setattr(search, "_tenant_session", session)
    monkeypatch.setattr(packages_store, "list_packages", w.list_packages)
    monkeypatch.setattr(objects_store, "get_object_by_name", w.get_object_by_name)
    monkeypatch.setattr(objects_store, "query_records", w.query_records)
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_orgs", ORG, raising=False)
    monkeypatch.setattr(settings, "studio_search_orgs", ORG, raising=False)
    monkeypatch.setattr(settings, "package_host_orgs", "", raising=False)
    return w


class _Nested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


def find(user: UserContext | None = None, q: str = "acme", k: int = 10) -> list[sources.Hit]:
    return asyncio.run(sources.REGISTRY["studio"].find(user or member(), q, k))


def test_the_source_is_last_in_the_registry_with_its_feature() -> None:
    assert list(sources.REGISTRY)[-1] == "studio"
    source = sources.REGISTRY["studio"]
    assert (source.key, source.feature, source.label) == ("studio", "studio", "Studio apps")
    assert source.gate is not None and source.meaning is False


def test_the_flag_off_reads_nothing(world, monkeypatch) -> None:
    world.packages = [package("p1", ["note"])]
    world.rows[("p1", "note", "title")] = [record("r1", {"title": "Acme"})]
    for value in ("", "other-org"):
        monkeypatch.setattr(get_settings(), "studio_search_orgs", value)
        assert find() == []
        assert world.queries == [] and world.objects_read == []


def test_each_gate_closes_the_source(world, monkeypatch) -> None:
    world.packages = [package("p1", ["note"])]
    world.rows[("p1", "note", "title")] = [record("r1", {"title": "Acme"})]
    assert len(find()) == 1
    assert find(member(("projects",))) == []  # no feature:studio
    assert find(member(org=None)) == []
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    assert find() == []
    assert len(world.queries) == 2  # the one open call asked the two text fields


def test_only_the_objects_of_shell_search_are_read(world) -> None:
    world.packages = [package("p1", ["note"]), package("p2", [])]
    world.fields["draft"] = [field("title")]
    world.rows[("p1", "draft", "title")] = [record("x", {"title": "acme"})]
    assert find() == []
    assert set(world.objects_read) == {"note"}
    assert {q["package"] for q in world.queries} == {"p1"}


def test_a_paused_package_is_skipped(world) -> None:
    world.packages = [package("p1", ["note"], paused=True)]
    assert find() == [] and world.queries == []


def test_the_query_always_passes_the_session_viewer_and_contains(world) -> None:
    world.packages = [package("p1", ["note"])]
    find()
    assert world.queries
    for q in world.queries:
        assert q["viewer"] == Viewer("ana@customer.example")
        assert q["filter"][0]["op"] == "contains" and q["filter"][0]["value"] == "acme"


def test_the_title_and_snippet_come_from_the_text_fields_only(world) -> None:
    world.fields["note"] = [field("title"), field("body"), field("amount", "number"),
                            field("secret_note", "checkbox")]
    world.packages = [package("p1", ["note"])]
    world.rows[("p1", "note", "title")] = [record("r1", {
        "title": "Acme deal", "body": "line one\nline two", "amount": 99, "secret_note": True})]
    [hit] = find()
    assert hit.title == "Acme deal" and hit.snippet == "line one line two"
    assert "99" not in repr(hit)
    assert hit.id == "p1/note/r1" and hit.href == "/apps/p1" and hit.hint == "App p1 · note"


def test_a_field_the_store_refuses_is_skipped(world) -> None:
    world.packages = [package("p1", ["note"])]
    world.refuse = {"title"}
    world.rows[("p1", "note", "body")] = [record("r1", {"body": "Acme"})]
    assert [h.id for h in find()] == ["p1/note/r1"]


def test_caps_for_each_package_and_in_all(world) -> None:
    world.packages = [package(f"p{i}", ["note"]) for i in range(4)]
    for i in range(4):
        world.rows[(f"p{i}", "note", "title")] = [
            record(f"r{i}{j}", {"title": f"Acme {j}"}) for j in range(9)]
    hits = find(k=100)
    assert len(hits) == 4 * search.PER_PACKAGE <= search.MAX_TOTAL
    assert all(q["limit"] <= search.PER_PACKAGE for q in world.queries)
    assert len(find(k=7)) == 7


@pytest.mark.parametrize("error", [QueryError("query_timeout", "slow"), RuntimeError("db down")])
def test_a_failed_or_slow_package_is_skipped_and_the_next_is_read(world, error) -> None:
    world.packages = [package(f"p{i}", ["note"]) for i in (1, 2, 3)]
    for i in (1, 3):
        world.rows[(f"p{i}", "note", "title")] = [record(f"r{i}", {"title": "Acme"})]
    world.fail = {"p2": error}
    assert [h.id for h in find()] == ["p1/note/r1", "p3/note/r3"]


def test_a_package_that_raises_outside_a_statement_never_fails_the_search(
        world, monkeypatch) -> None:
    """The package-level guard: the next package is read (continue, not break)."""
    world.packages = [package(f"p{i}", ["note"]) for i in (1, 2, 3)]
    for i in (1, 3):
        world.rows[(f"p{i}", "note", "title")] = [record(f"r{i}", {"title": "Acme"})]
    real = search._read_package

    async def boom(db: Any, pkg: Any, *a: Any) -> Any:
        if pkg.id == "p2":
            raise RuntimeError("bad manifest")
        return await real(db, pkg, *a)

    monkeypatch.setattr(search, "_read_package", boom)
    assert [h.id for h in find()] == ["p1/note/r1", "p3/note/r3"]


def test_each_statement_gets_the_time_left_as_its_deadline(world) -> None:
    world.packages = [package("p1", ["note"])]
    find()
    assert world.queries
    for q in world.queries:
        assert 0 < q["budget_s"] <= search.BUDGET_S


def test_an_spent_clock_reads_no_statement(world, monkeypatch) -> None:
    monkeypatch.setattr(search, "BUDGET_S", 0.0)
    world.packages = [package("p1", ["note"])]
    assert find() == [] and world.queries == []


def test_the_search_calls_no_wait_for() -> None:
    """A statement is never cancelled from outside: the store holds the deadline."""
    import inspect

    assert "wait_for" not in inspect.getsource(search)
    assert "budget_s" in inspect.signature(objects_store.query_records).parameters


def test_the_flag_layers_each_close_search_records_itself(world, monkeypatch) -> None:
    """Fences ``search_records`` on its own, not through the source gate."""
    world.packages = [package("p1", ["note"])]
    world.rows[("p1", "note", "title")] = [record("r1", {"title": "Acme"})]
    assert len(asyncio.run(search.search_records(member(), "acme", 5))) == 1
    for key in ("studio_search_orgs", "studio_orgs"):
        monkeypatch.setattr(get_settings(), key, "")
        n = len(world.queries)
        assert asyncio.run(search.search_records(member(), "acme", 5)) == []
        assert len(world.queries) == n
        monkeypatch.setattr(get_settings(), key, ORG)


def _install(app_id: str, manifest: dict[str, Any]) -> Any:
    from gateway.package_host import resolve

    return resolve.InstallRow(id=f"i-{app_id}", app_id=app_id, scope_kind="org",
                              scope_subject="", created_at=NOW, manifest=manifest, version=1)


def _installed_world(world: World, monkeypatch, rows: list[Any]) -> None:
    from gateway.package_host import resolve

    async def visible(db: Any, *, viewer: str) -> list[Any]:
        return rows

    monkeypatch.setattr(resolve, "visible_installs", visible)
    monkeypatch.setattr(get_settings(), "package_host_orgs", ORG, raising=False)


def _manifest(actions: list[str], fields: list[str] | None = None) -> dict[str, Any]:
    return {"name": "Notes app", "shell": {"search": ["note"]},
            "objects": [{"name": "note", "fields": [
                {"key": k, "type": "text"} for k in (fields or ["title", "body"])]}],
            "actions": [{"id": f"a{i}", "uses": u} for i, u in enumerate(actions)]}


@pytest.mark.parametrize("actions,found", [
    ([], False), (["object:note.create"], False), (["object:other.list"], False),
    (["object:note.list"], True), (["object:note.get"], True)])
def test_a_viewer_searches_an_object_only_with_a_declared_read_action(
        world, monkeypatch, actions, found) -> None:
    _installed_world(world, monkeypatch, [_install("app1", _manifest(actions))])
    world.rows[("app1", "note", "title")] = [record("r1", {"title": "Acme"})]
    assert (len(find()) == 1) is found
    assert bool(world.queries) is found


def test_a_viewer_snippet_holds_installed_fields_only(world, monkeypatch) -> None:
    """The project() layer. The store row holds a field of a newer version."""
    _installed_world(world, monkeypatch,
                     [_install("app1", _manifest(["object:note.list"], ["title", "body"]))])
    world.fields["note"] = [field("title"), field("body"), field("memo")]
    world.rows[("app1", "note", "title")] = [record("r1", {
        "title": "Acme", "body": "shown", "memo": "SECRET"})]
    [hit] = find()
    assert hit.snippet == "shown" and "SECRET" not in repr(hit)
    assert {c["filter"][0]["field"] for c in world.queries} == {"title", "body"}


def test_owned_and_installed_packages_share_the_cap(world, monkeypatch) -> None:
    world.packages = [package(f"p{i}", ["note"]) for i in range(12)]
    rows = [_install(f"app{i}", _manifest(["object:note.list"])) for i in range(2)]
    _installed_world(world, monkeypatch, rows)
    for pid in [f"p{i}" for i in range(12)] + ["app0", "app1"]:
        world.rows[(pid, "note", "title")] = [record(f"r-{pid}", {"title": "Acme"})]
    ids = {h.id.split("/")[0] for h in find(k=100)}
    assert {"app0", "app1"} <= ids
    assert len(ids) == search.MAX_PACKAGES


def test_the_clock_starts_before_the_packages_load(world, monkeypatch) -> None:
    clock = iter([0.0, 0.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(search, "time", SimpleNamespace(monotonic=lambda: next(clock, 99.0)))
    world.packages = [package("p1", ["note"])]
    world.rows[("p1", "note", "title")] = [record("r1", {"title": "Acme"})]
    assert find() == [] and world.queries == []


def test_an_empty_word_reads_nothing(world) -> None:
    world.packages = [package("p1", ["note"])]
    assert find(q="   ") == [] and world.queries == []
