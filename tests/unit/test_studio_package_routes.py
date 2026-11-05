"""The package routes of AI Studio — the hermetic half (STU-A5).

Spec: ``project-docs/specs/ai_studio.md`` §14 A5. Code:
``gateway/routes/studio/packages.py`` over the package store (A3 and A4) and
the object store (C1 and the sync of A5). The R8 half, on a real Postgres, is
``test_studio_package_routes_r8.py``. It proves the walls of the member and
the org, the rows, and the locks.

Here the handlers run on a fake world. The fake session keeps the packages,
the versions and the objects in memory. It filters each read on the bound org
and the member, and it rolls the world back when a request fails, as
``tenant_session`` does. The store functions that the routes call are
replaced by small fakes, but the checks of the manifest and of the notes are
the real ones (``check_manifest`` and ``_check_publishable``).

This file proves:

- the switch: each of the six routes answers 404 for an org outside
  ``STUDIO_ORGS``, and reads no byte and opens no session;
- the org and the member come from the session, never from the body;
- the order of the checks: the switch, the id, the body cap, the owner, then
  the parse, and the catalog before the store session;
- the answers: 201, 409 ``package_exists`` and ``package_limit``, 413, 422,
  503 ``catalog_unavailable`` and ``store_busy``;
- a publish runs ``sync_objects`` after ``publish_version`` on the manifest
  of the new version, and a refusal of the sync leaves no version row;
- the audit rows of a create and of a sync that changed rows;
- the fences: the switch is the first line of each handler, ``packages.py``
  runs no SQL, a sync follows each publish or rollback, and ``define_object``
  has one caller.

**The mutations (R7).** Each was run on 2026-11-03. The list and the red tests
are in the build report, and the R8 half holds the ones that need a database.

* M1. Drop the switch check from ``list_packages``.
  ``test_a_org_outside_the_switch_is_404_on_each_route`` and
  ``test_each_handler_checks_the_switch_first`` go red.
* M2. Drop the ``sync_objects`` call from ``publish``.
  ``test_a_publish_syncs_the_objects_of_the_new_version`` and
  ``test_each_publish_or_rollback_is_followed_by_a_sync`` go red.
* M3. Call ``sync_objects`` before ``publish_version``.
  ``test_each_publish_or_rollback_is_followed_by_a_sync`` and
  ``test_a_publish_syncs_the_objects_of_the_new_version`` go red.
* M5. Swallow ``ObjectsOutOfSync`` in ``publish``.
  ``test_a_refused_sync_is_409_and_leaves_no_version`` goes red.
* M6. Pass an empty catalog to ``publish_version``.
  ``test_the_catalog_is_passed_to_the_publish`` goes red.
* M7. Use ``record_app_audit`` in place of ``record_app_audit_bound``.
  ``test_the_audit_rows_use_the_bound_writer`` goes red.
* M8. Read the body before the switch in ``create_package``.
  ``test_a_org_outside_the_switch_is_404_on_each_route`` goes red.
* M9. Take the owner from the body.
  ``test_a_body_cannot_name_the_owner_or_the_org`` goes red.
* M11. Drop the ``_BOUND_ORG`` bind from the subquery of ``_LIST_FIELDS_SQL``,
  from ``_LIST_OBJECTS_SQL`` or from ``_SUMMARY_SQL``.
  ``test_each_store_statement_of_a5_binds_the_org_in_each_select`` goes red.
  The R8 half stays green, because RLS hides the missing bind.
"""

from __future__ import annotations

import ast
import copy
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, packages
from gateway.studio import objects_store
from gateway.studio import packages_store as store

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
ANA = "ana@example.test"
BEN = "ben@example.test"
NOT_FOUND = {"detail": "Not found"}
_ROOT = Path(__file__).resolve().parents[2]
_PACKAGES_PY = _ROOT / "apps/services/gateway/gateway/routes/studio/packages.py"


# ── The fake world ─────────────────────────────────────────────────────────


class _DB:
    def __init__(self, world: Any, org: str | None) -> None:
        assert org, "a store session opened with no bound org"
        self.world = world
        self.org = org

    def mine(self, member: str, pid: str) -> dict[str, Any] | None:
        row = self.world.packages.get(pid)
        if row and row["org"] == self.org and row["owner"] == member.strip().lower():
            return row
        return None


def _package(row: dict[str, Any]) -> store.Package:
    return store.Package(
        id=row["id"], slug=row["slug"], name=row["manifest"]["name"],
        description=row["manifest"]["purpose"], owner_email=row["owner"],
        visibility="private", status=row["status"], manifest=copy.deepcopy(row["manifest"]),
        live_version=row["live_version"], created_at=row["created"], updated_at=row["updated"])


def _version(app: str, row: dict[str, Any], *, manifest: bool = True) -> store.PackageVersion:
    return store.PackageVersion(
        package_id=app, version=row["version"],
        manifest=copy.deepcopy(row["manifest"]) if manifest else None,
        bundle_sha256=row["sha"], release_notes=row["notes"], published_by=row["by"],
        published_at=row["at"], review_status="auto")


@pytest.fixture
def world(monkeypatch):  # noqa: C901 - one fake world, many small fakes
    w = SimpleNamespace(
        packages={}, versions=[], objects={}, org=None, log=[], sessions=0, audits=[],
        sync_calls=[], sync_error=None, catalog_fail=False, catalog_ids=["x"],
        publish_catalogs=[], version_reads=[], expected=[],
        clock=datetime(2026, 11, 3, tzinfo=UTC))

    def tick() -> datetime:
        w.clock += timedelta(seconds=1)
        return w.clock

    @asynccontextmanager
    async def _session(*args: Any):
        w.sessions += 1
        w.log.append("store-open")
        snapshot = copy.deepcopy((w.packages, w.versions, w.objects))
        try:
            yield _DB(w, w.org)
        except BaseException:
            w.packages, w.versions, w.objects = snapshot
            raise
        finally:
            w.log.append("store-close")

    async def get_package(db, member, pid, *, for_update=False):
        row = db.mine(member, pid)
        return None if row is None else _package(row)

    async def list_summaries(db, member):
        rows = [r for r in w.packages.values()
                if r["org"] == db.org and r["owner"] == member.strip().lower()]
        return [store.PackageSummary(
            id=r["id"], slug=r["slug"], name=r["manifest"]["name"],
            description=r["manifest"]["purpose"], status=r["status"],
            live_version=r["live_version"], created_at=r["created"], updated_at=r["updated"],
            manifest_id=r["manifest"]["id"]) for r in rows]

    async def create_package(db, member, manifest):
        store.check_manifest(manifest)
        pid = str(uuid.uuid4())
        now = tick()
        w.packages[pid] = {
            "id": pid, "org": db.org, "owner": member.strip().lower(), "status": "draft",
            "slug": f"{manifest['id']}-{pid[:8]}", "manifest": copy.deepcopy(manifest),
            "live_version": None, "created": now, "updated": now}
        return _package(w.packages[pid])

    async def update_manifest(db, member, pid, manifest, *, expected_hash=None):
        row = db.mine(member, pid)
        store.check_manifest(manifest)
        if row is None:
            return None
        store._expect(row["manifest"], expected_hash)  # the real compare of the store
        if row["manifest"]["id"] != manifest["id"]:
            raise store.PackageStoreError(store.ID_FIXED, [
                store.ManifestError(path="$.id", message=store.ID_FIXED)])
        row["manifest"] = copy.deepcopy(manifest)
        row["updated"] = tick()
        return _package(row)

    async def publish_version(db, member, pid, notes, *, catalog, expected_hash=None):
        w.log.append("publish")
        w.publish_catalogs.append(dict(catalog))
        w.expected.append(expected_hash)
        row = db.mine(member, pid)
        if row is None:
            return None
        note = store._notes(notes)
        store._expect(row["manifest"], expected_hash)
        encoded = store._check_publishable(row["manifest"], catalog)
        number = 1 + max((v["version"] for v in w.versions if v["app"] == pid), default=0)
        entry = {"app": pid, "version": number, "manifest": copy.deepcopy(row["manifest"]),
                 "sha": store._sha256(encoded), "notes": note, "by": member, "at": tick()}
        w.versions.append(entry)
        row["status"], row["live_version"] = "live", number
        return _version(pid, entry)

    async def list_versions(db, member, pid, *, limit=100, before=None):
        w.version_reads.append((limit, before))
        if db.mine(member, pid) is None:
            return []
        rows = sorted((v for v in w.versions if v["app"] == pid
                       and (before is None or v["version"] < before)),
                      key=lambda v: -v["version"])
        return [_version(pid, v, manifest=False) for v in rows[:limit]]

    async def sync_objects(db, actor, *, package_id, objects):
        w.log.append("sync")
        w.sync_calls.append({"actor": actor, "package_id": package_id,
                             "objects": copy.deepcopy(objects)})
        if w.sync_error is not None:
            raise w.sync_error
        have = w.objects.setdefault(package_id, {})
        made = tuple(o["name"] for o in objects if o["name"] not in have)
        for o in objects:
            have[o["name"]] = o
        return objects_store.SyncResult(objects_created=made)

    async def list_objects(db, package_id):
        now = w.clock
        return [objects_store.StudioObject(
            id=str(uuid.uuid4()), package_id=package_id, name=o["name"], label=o.get("label"),
            created_by=ANA, created_at=now, updated_at=now,
            fields=tuple(objects_store.parse_field_spec(f) for f in o.get("fields", [])))
            for o in w.objects.get(package_id, {}).values()]

    async def catalog_rows():
        w.log.append("catalog")
        if w.catalog_fail:
            raise RuntimeError("the catalog is down")
        return [{"id": i, "class": "A"} for i in w.catalog_ids]

    async def audit(**kw: Any) -> None:
        w.audits.append(kw)

    monkeypatch.setattr(packages, "_tenant_session", _session)
    monkeypatch.setattr(packages, "record_app_audit_bound", audit)
    monkeypatch.setattr(actions, "catalog_rows", catalog_rows)
    for name, fn in (("get_package", get_package), ("list_summaries", list_summaries),
                     ("create_package", create_package), ("update_manifest", update_manifest),
                     ("publish_version", publish_version), ("list_versions", list_versions)):
        monkeypatch.setattr(store, name, fn)
    monkeypatch.setattr(objects_store, "sync_objects", sync_objects)
    monkeypatch.setattr(objects_store, "list_objects", list_objects)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    return w


def _client(world: Any, org: str | None = ORG, email: str = ANA) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)

    def _user() -> UserContext:
        world.org = org
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           access=build_access(["feature:studio"]), organization_id=org)

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


_FIELDS = [
    {"key": "site", "type": "text"},
    {"key": "status", "type": "select", "options": ["planned", "done"], "hot": True},
]


def _manifest(mid: str = "visits", **extra: Any) -> dict[str, Any]:
    return {"studio": 1, "id": mid, "name": "Visits", "purpose": "Track install visits.",
            "objects": [{"name": "visit", "fields": _FIELDS}],
            "actions": [{"id": "peek", "uses": "object:visit.get"}], **extra}


def _create(c: TestClient, manifest: dict[str, Any] | None = None) -> str:
    r = c.post("/studio/packages", json=manifest or _manifest())
    assert r.status_code == 201, r.text
    return str(r.json()["package"]["id"])


_JSON = {"content-type": "application/json"}
_BAD = b"{not json"


def _six(c: TestClient, pid: str, body: bytes = _BAD) -> list[Any]:
    """One call of each route, with *body* where a route reads one."""
    return [
        c.post("/studio/packages", content=body, headers=_JSON),
        c.get("/studio/packages"),
        c.get(f"/studio/packages/{pid}"),
        c.put(f"/studio/packages/{pid}/draft", content=body, headers=_JSON),
        c.post(f"/studio/packages/{pid}/versions", content=body, headers=_JSON),
        c.get(f"/studio/packages/{pid}/versions"),
    ]


# ── The switch ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "other-org-id", f"{OTHER}"])
def test_a_org_outside_the_switch_is_404_on_each_route(world, monkeypatch, value):
    pid = _create(_client(world))
    monkeypatch.setattr(get_settings(), "studio_orgs", value)
    opened = world.sessions
    answers = _six(_client(world), pid)
    assert [r.status_code for r in answers] == [404] * 6
    assert all(r.json() == NOT_FOUND for r in answers)
    # A body that is not JSON would be 422 if a route read it first.
    assert world.sessions == opened
    assert world.audits == world.audits[:1]


def test_a_listed_org_and_star_are_served(world, monkeypatch):
    for value in (ORG, f"x, {ORG.upper()} ,y", "*"):
        monkeypatch.setattr(get_settings(), "studio_orgs", value)
        assert _client(world).get("/studio/packages").status_code == 200


def test_an_empty_switch_is_the_default_off(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    assert _client(world).post("/studio/packages", json=_manifest()).status_code == 404
    assert world.packages == {}


# ── Create ─────────────────────────────────────────────────────────────────


def test_a_create_makes_a_private_draft_of_the_caller(world):
    r = _client(world).post("/studio/packages", json=_manifest())
    assert r.status_code == 201, r.text
    got = r.json()["package"]
    assert (got["name"], got["status"], got["visibility"], got["live_version"]) == (
        "Visits", "draft", "private", None)
    assert got["manifest"] == _manifest()
    row = world.packages[got["id"]]
    assert (row["org"], row["owner"]) == (ORG, ANA)


def test_a_body_cannot_name_the_owner_or_the_org(world):
    c = _client(world, email=BEN)
    # A manifest has no such key, so A2 refuses it, and nothing is written.
    r = c.post("/studio/packages", json=_manifest(owner_email=ANA, organization_id=OTHER))
    assert r.status_code == 422
    assert world.packages == {}
    pid = _create(c)
    r = c.post(f"/studio/packages/{pid}/versions",
               json={"notes": "n", "owner_email": ANA, "organization_id": OTHER})
    assert r.status_code == 201, r.text
    assert world.versions[0]["by"] == BEN
    assert world.packages[pid]["owner"] == BEN and world.packages[pid]["org"] == ORG
    assert world.sync_calls[0]["actor"] == BEN


def test_a_create_audits_the_op_a_hash_and_a_size_and_no_manifest(world):
    pid = _create(_client(world))
    assert len(world.audits) == 1
    audit = world.audits[0]
    assert (audit["app_id"], audit["user_email"], audit["kind"]) == (pid, ANA, "action")
    detail = audit["detail"]
    assert detail["op"] == "package.create"
    assert re.fullmatch(r"[0-9a-f]{64}", detail["sha256"]) and detail["bytes"] > 100
    assert "Track install visits" not in str(detail)


def test_the_same_manifest_id_twice_is_409_with_the_first_id(world):
    c = _client(world)
    first = _create(c)
    r = c.post("/studio/packages", json=_manifest())
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "package_exists"
    assert r.json()["detail"]["package_id"] == first
    assert len(world.packages) == 1


def test_another_member_can_use_the_same_manifest_id(world):
    _create(_client(world))
    assert _client(world, email=BEN).post("/studio/packages", json=_manifest()).status_code == 201
    assert _client(world, org=OTHER).post("/studio/packages", json=_manifest()).status_code == 201


def test_a_member_holds_at_most_100_packages(world):
    c = _client(world)
    for i in range(packages.MAX_PACKAGES_PER_MEMBER):
        _create(c, _manifest(f"app-{i}"))
    r = c.post("/studio/packages", json=_manifest("one-more"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "package_limit"
    assert len(world.packages) == 100
    # Another member is not counted.
    assert _client(world, email=BEN).post("/studio/packages", json=_manifest()).status_code == 201


def test_a_manifest_that_a2_refuses_is_422_with_paths(world):
    bad = _manifest()
    bad["objects"][0]["fields"] = [{"key": "Bad Key", "type": "nope"}]
    r = _client(world).post("/studio/packages", json=bad)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["message"] and detail["errors"]
    assert all({"path", "message"} == set(e) for e in detail["errors"])
    assert world.packages == {}
    assert world.audits == []


def test_the_errors_of_a_422_are_at_most_25(world):
    bad = _manifest()
    bad["objects"] = [{"name": f"o{i}", "fields": [{"key": "Bad", "type": "nope"}]}
                      for i in range(40)]
    r = _client(world).post("/studio/packages", json=bad)
    assert r.status_code == 422
    assert 0 < len(r.json()["detail"]["errors"]) <= packages.MAX_PROBLEMS


@pytest.mark.parametrize("body", [b"", b"{not json", b"[]", b"null", b'"text"', b"12"])
def test_a_body_that_is_not_a_json_object_is_422(world, body):
    r = _client(world).post("/studio/packages", content=body, headers=_JSON)
    assert r.status_code == 422, r.text
    assert world.packages == {}


def test_a_body_past_the_cap_is_413_and_opens_no_session(world):
    r = _client(world).post("/studio/packages", content=b"x" * (packages.MAX_BODY_BYTES + 1),
                            headers=_JSON)
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "body_too_large"
    assert world.sessions == 0


def test_a_body_at_the_cap_is_read(world):
    r = _client(world).post("/studio/packages", content=b" " * packages.MAX_BODY_BYTES,
                            headers=_JSON)
    assert r.status_code == 422  # read in full, and it is not a JSON object


@pytest.mark.parametrize("depth", [1500, 150_000])
def test_a_body_nested_too_deep_is_422_never_500(world, depth):
    body = ('{"studio": 1, "id": "deep", "name": "D", "purpose": "p", "objects": '
            + "[" * depth + "]" * depth + "}")
    r = _client(world).post("/studio/packages", content=body, headers=_JSON)
    assert r.status_code == 422, r.text[:200]
    assert world.packages == {}


# ── List and get ───────────────────────────────────────────────────────────


def test_the_list_holds_the_caller_only_and_no_manifest(world):
    a1, a2 = _create(_client(world), _manifest("one")), _create(_client(world), _manifest("two"))
    _create(_client(world, email=BEN), _manifest("bens"))
    _create(_client(world, org=OTHER), _manifest("theirs"))
    got = _client(world).get("/studio/packages").json()["packages"]
    assert sorted(p["id"] for p in got) == sorted([a1, a2])
    assert all("manifest" not in p for p in got)
    assert {p["manifest_id"] for p in got} == {"one", "two"}


def test_a_get_returns_the_manifest_and_the_object_rows(world):
    c = _client(world)
    pid = _create(c)
    c.post(f"/studio/packages/{pid}/versions", json={})
    got = c.get(f"/studio/packages/{pid}").json()
    assert got["package"]["manifest"] == _manifest()
    assert [o["name"] for o in got["objects"]] == ["visit"]
    assert [f["key"] for f in got["objects"][0]["fields"]] == ["site", "status"]
    assert got["objects"][0]["rules"] == []


@pytest.mark.parametrize("org,email", [(ORG, BEN), (OTHER, ANA)])
def test_a_package_of_another_member_or_org_is_404_on_each_route(world, org, email):
    theirs = _create(_client(world, org=org, email=email))
    before = copy.deepcopy(world.packages[theirs])
    c = _client(world)
    # A body that is not JSON: a 422 here would tell a stranger that the package exists.
    answers = _six(c, theirs)[2:]
    assert [r.status_code for r in answers] == [404] * 4
    assert all(r.json() == NOT_FOUND for r in answers)
    assert c.get("/studio/packages").json()["packages"] == []
    assert world.packages[theirs] == before
    assert world.versions == [] and world.sync_calls == []


@pytest.mark.parametrize("bad", ["not-a-uuid", "123", "00000000-0000-0000-0000-00000000000", "%20"])
def test_a_bad_id_is_404_not_422(world, bad):
    c = _client(world)
    assert c.get(f"/studio/packages/{bad}").status_code == 404
    assert c.get(f"/studio/packages/{bad}/versions").status_code == 404
    assert c.put(f"/studio/packages/{bad}/draft", json=_manifest()).status_code == 404
    assert c.post(f"/studio/packages/{bad}/versions", json={}).status_code == 404


def test_an_answer_names_no_owner(world):
    c = _client(world)
    pid = _create(c)
    c.post(f"/studio/packages/{pid}/versions", json={})
    for r in (c.get("/studio/packages"), c.get(f"/studio/packages/{pid}"),
              c.get(f"/studio/packages/{pid}/versions")):
        assert "owner_email" not in r.text and "organization_id" not in r.text


# ── The draft ──────────────────────────────────────────────────────────────


def test_a_draft_save_replaces_the_manifest_and_writes_no_object(world):
    c = _client(world)
    pid = _create(c)
    changed = _manifest(actions=[{"id": "peek", "uses": "object:visit.get"},
                                 {"id": "find", "uses": "object:visit.list"}])
    r = c.put(f"/studio/packages/{pid}/draft", json=changed)
    assert r.status_code == 200, r.text
    assert world.packages[pid]["manifest"] == changed
    assert world.sync_calls == [] and world.objects == {} and world.versions == []
    # A draft save writes no audit row.
    assert len(world.audits) == 1


def test_a_draft_with_a_new_manifest_id_is_422(world):
    c = _client(world)
    pid = _create(c)
    r = c.put(f"/studio/packages/{pid}/draft", json=_manifest("renamed"))
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["path"] == "$.id"
    assert world.packages[pid]["manifest"]["id"] == "visits"


def test_a_bad_draft_keeps_the_old_one(world):
    c = _client(world)
    pid = _create(c)
    bad = _manifest()
    bad["objects"][0]["fields"] = [{"key": "x", "type": "nope"}]
    assert c.put(f"/studio/packages/{pid}/draft", json=bad).status_code == 422
    assert world.packages[pid]["manifest"] == _manifest()


def test_a_non_owner_is_404_before_the_body_is_parsed(world):
    pid = _create(_client(world))
    stranger = _client(world, email=BEN)
    # An invalid body would be 422 if the parse came before the owner read.
    r = stranger.put(f"/studio/packages/{pid}/draft", content=_BAD, headers=_JSON)
    assert r.status_code == 404
    assert _client(world).put(f"/studio/packages/{pid}/draft", content=_BAD,
                              headers=_JSON).status_code == 422


def _body_route(c: TestClient, route: str, pid: str, body: bytes) -> Any:
    if route == "draft":
        return c.put(f"/studio/packages/{pid}/draft", content=body, headers=_JSON)
    return c.post(f"/studio/packages/{pid}/versions", content=body, headers=_JSON)


@pytest.mark.parametrize("route", ["draft", "versions"])
@pytest.mark.parametrize("email", [ANA, BEN])
def test_a_body_past_the_cap_on_a_package_is_413_and_opens_no_session(world, route, email):
    """The cap comes before the owner read, so a big body costs no read of the
    database. A 413 says nothing about the package, so a stranger gets it too."""
    pid = _create(_client(world))
    opened = world.sessions
    r = _body_route(_client(world, email=email), route, pid,
                    b"x" * (packages.MAX_BODY_BYTES + 1))
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "body_too_large"
    assert world.sessions == opened
    assert world.versions == [] and world.sync_calls == []


# ── Publish ────────────────────────────────────────────────────────────────


def test_a_publish_syncs_the_objects_of_the_new_version(world):
    c = _client(world)
    pid = _create(c)
    # A later draft edit must not reach the sync of the published version.
    r = c.post(f"/studio/packages/{pid}/versions", json={"notes": "first"})
    assert r.status_code == 201, r.text
    assert world.log.index("publish") < world.log.index("sync")
    call = world.sync_calls[0]
    assert call["package_id"] == pid
    assert call["objects"] == _manifest()["objects"]
    got = r.json()
    assert got["version"]["version"] == 1 and got["version"]["notes"] == "first"
    assert got["sync"]["objects_created"] == ["visit"]
    assert [o["name"] for o in got["objects"]] == ["visit"]
    assert world.packages[pid]["status"] == "live"


def test_a_publish_runs_the_publish_and_the_sync_in_one_session(world):
    c = _client(world)
    pid = _create(c)
    world.log.clear()
    assert c.post(f"/studio/packages/{pid}/versions", json={}).status_code == 201
    # owner read, catalog read, then ONE store session with both calls in it.
    assert world.log == ["store-open", "store-close", "catalog",
                         "store-open", "publish", "sync", "store-close"]


def test_the_catalog_is_passed_to_the_publish(world):
    world.catalog_ids = ["knowledge.list_pages", "knowledge.propose"]
    c = _client(world)
    pid = _create(c)
    assert c.post(f"/studio/packages/{pid}/versions", json={}).status_code == 201
    assert world.publish_catalogs == [{"knowledge.list_pages": "A", "knowledge.propose": "A"}]


def test_two_publishes_give_n_and_n_plus_1(world):
    c = _client(world)
    pid = _create(c)
    a = c.post(f"/studio/packages/{pid}/versions", json={}).json()
    b = c.post(f"/studio/packages/{pid}/versions", json={}).json()
    assert (a["version"]["version"], b["version"]["version"]) == (1, 2)
    assert b["sync"]["objects_created"] == []


def test_a_refused_sync_is_409_and_leaves_no_version(world):
    c = _client(world)
    pid = _create(c)
    world.sync_error = objects_store.ObjectsOutOfSync([
        objects_store.SyncProblem("field_removed", "visit", field="site", message="Gone."),
        objects_store.SyncProblem("rules_refused", "visit", rule="cap", message="No.")])
    r = c.post(f"/studio/packages/{pid}/versions", json={})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "objects_out_of_sync"
    assert [(p["kind"], p["object"], p["field"], p["rule"]) for p in detail["problems"]] == [
        ("field_removed", "visit", "site", None), ("rules_refused", "visit", None, "cap")]
    assert world.versions == []
    assert (world.packages[pid]["status"], world.packages[pid]["live_version"]) == ("draft", None)
    assert len(world.audits) == 1  # the create only


def test_the_problems_of_a_409_are_at_most_25(world):
    c = _client(world)
    pid = _create(c)
    world.sync_error = objects_store.ObjectsOutOfSync([
        objects_store.SyncProblem("field_removed", "visit", field=f"f{i}", message="x")
        for i in range(60)])
    r = c.post(f"/studio/packages/{pid}/versions", json={})
    assert len(r.json()["detail"]["problems"]) == packages.MAX_PROBLEMS


def test_a_busy_org_lock_is_503_with_retry_after_and_no_version(world):
    c = _client(world)
    pid = _create(c)
    world.sync_error = objects_store.StoreBusy("This app is busy. Try again in a moment.")
    r = c.post(f"/studio/packages/{pid}/versions", json={})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "store_busy"
    assert r.headers["retry-after"] == "2"
    assert world.versions == []


def test_a_catalog_that_fails_is_503_and_nothing_is_written(world):
    c = _client(world)
    pid = _create(c)
    world.catalog_fail = True
    world.log.clear()
    r = c.post(f"/studio/packages/{pid}/versions", json={})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "catalog_unavailable"
    assert "publish" not in world.log and world.versions == [] and world.sync_calls == []


def test_a_manifest_that_a2_refuses_with_the_catalog_is_422_and_no_version(world):
    c = _client(world)
    manifest = _manifest(actions=[{"id": "flow", "uses": "knowledge.nope"}])
    pid = _create(c, manifest)
    r = c.post(f"/studio/packages/{pid}/versions", json={})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["errors"]
    assert world.versions == [] and world.sync_calls == []


@pytest.mark.parametrize("notes", [5, None, ["a"], {"a": 1}])
def test_notes_that_are_not_text_are_422(world, notes):
    c = _client(world)
    pid = _create(c)
    r = c.post(f"/studio/packages/{pid}/versions", json={"notes": notes})
    assert r.status_code == 422
    assert world.versions == []


def test_notes_past_2000_characters_are_422(world):
    c = _client(world)
    pid = _create(c)
    assert c.post(f"/studio/packages/{pid}/versions",
                  json={"notes": "n" * 2001}).status_code == 422
    assert c.post(f"/studio/packages/{pid}/versions",
                  json={"notes": "n" * 2000}).status_code == 201


def test_a_non_owner_publish_is_404_and_reads_no_catalog(world):
    pid = _create(_client(world))
    world.log.clear()
    r = _client(world, email=BEN).post(f"/studio/packages/{pid}/versions", json={})
    assert r.status_code == 404
    assert "catalog" not in world.log and "publish" not in world.log
    assert world.versions == [] and world.sync_calls == []


def test_an_empty_body_publishes_with_empty_notes(world):
    c = _client(world)
    pid = _create(c)
    r = c.post(f"/studio/packages/{pid}/versions")
    assert r.status_code == 201
    assert r.json()["version"]["notes"] == ""


def test_a_sync_that_changed_rows_writes_one_audit_row(world):
    c = _client(world)
    pid = _create(c)
    world.audits.clear()
    c.post(f"/studio/packages/{pid}/versions", json={})
    assert len(world.audits) == 1
    audit = world.audits[0]
    assert (audit["app_id"], audit["user_email"], audit["kind"], audit["app_version"]) == (
        pid, ANA, "action", 1)
    assert audit["detail"]["op"] == "package.objects_sync"
    assert audit["detail"]["objects"] == ["visit"]
    # A publish with no change writes no sync row.
    world.audits.clear()
    c.post(f"/studio/packages/{pid}/versions", json={})
    assert world.audits == []


def test_a_404_and_a_refusal_write_no_audit_row(world):
    c = _client(world)
    pid = _create(c)
    world.audits.clear()
    _client(world, email=BEN).post(f"/studio/packages/{pid}/versions", json={})
    world.sync_error = objects_store.ObjectsOutOfSync([])
    c.post(f"/studio/packages/{pid}/versions", json={})
    assert world.audits == []


def test_the_audit_rows_use_the_bound_writer():
    names = {n.id for n in ast.walk(_tree()) if isinstance(n, ast.Name)}
    names |= {a.name for n in ast.walk(_tree()) if isinstance(n, ast.ImportFrom)
              for a in n.names}
    assert "record_app_audit_bound" in names
    assert "record_app_audit" not in names


# ── The versions ───────────────────────────────────────────────────────────


def test_the_versions_list_newest_first_with_no_manifest(world):
    c = _client(world)
    pid = _create(c)
    assert c.get(f"/studio/packages/{pid}/versions").json() == {
        "versions": [], "next_before": None}
    for n in ("a", "b"):
        c.post(f"/studio/packages/{pid}/versions", json={"notes": n})
    got = c.get(f"/studio/packages/{pid}/versions").json()["versions"]
    assert [v["version"] for v in got] == [2, 1]
    assert [v["notes"] for v in got] == ["b", "a"]
    assert all("manifest" not in v for v in got)


def _many_versions(world: Any, pid: str, count: int) -> None:
    for n in range(1, count + 1):
        world.versions.append({"app": pid, "version": n, "manifest": _manifest(), "sha": "0" * 64,
                               "notes": "", "by": ANA, "at": world.clock})


def test_the_versions_list_holds_at_most_100_and_a_cursor_reads_the_rest(world):
    c = _client(world)
    pid = _create(c)
    _many_versions(world, pid, 105)
    first = c.get(f"/studio/packages/{pid}/versions").json()
    assert [v["version"] for v in first["versions"]] == list(range(105, 5, -1))
    assert first["next_before"] == 6
    rest = c.get(f"/studio/packages/{pid}/versions", params={"before": 6}).json()
    assert [v["version"] for v in rest["versions"]] == [5, 4, 3, 2, 1]
    assert rest["next_before"] is None
    assert world.version_reads == [(packages.VERSIONS_PAGE, None), (packages.VERSIONS_PAGE, 6)]
    assert packages.VERSIONS_PAGE == 100


def test_a_full_page_that_ends_at_version_1_has_no_next(world):
    c = _client(world)
    pid = _create(c)
    _many_versions(world, pid, packages.VERSIONS_PAGE)
    got = c.get(f"/studio/packages/{pid}/versions").json()
    assert len(got["versions"]) == packages.VERSIONS_PAGE and got["next_before"] is None


@pytest.mark.parametrize("bad", ["", "0", "-1", "x", "1.5", "²", str(2**31), "9" * 40])
def test_a_bad_cursor_is_422_and_reads_nothing(world, bad):
    c = _client(world)
    pid = _create(c)
    opened = world.sessions
    r = c.get(f"/studio/packages/{pid}/versions", params={"before": bad})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["errors"][0]["path"] == "before"
    assert world.sessions == opened


def test_a_cursor_does_not_open_the_versions_of_a_stranger(world, monkeypatch):
    pid = _create(_client(world))
    _many_versions(world, pid, 3)
    for c in (_client(world, email=BEN), _client(world, org=OTHER)):
        r = c.get(f"/studio/packages/{pid}/versions", params={"before": 3})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    r = _client(world).get(f"/studio/packages/{pid}/versions", params={"before": "x"})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)


# ── The fences (R7) ────────────────────────────────────────────────────────


def _tree() -> ast.Module:
    return ast.parse(_PACKAGES_PY.read_text(encoding="utf-8"))


def _handlers(tree: ast.Module) -> list[ast.AsyncFunctionDef]:
    out = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
                for d in node.decorator_list):
            out.append(node)
    return out


def test_there_are_six_handlers():
    names = sorted(h.name for h in _handlers(_tree()))
    assert names == ["create_package", "get_package", "list_packages", "list_versions",
                     "publish", "save_draft"]


def test_each_handler_checks_the_switch_first():
    """The first statement of a handler, after its docstring, is the switch
    check that raises 404. No body read, no id parse and no session comes first."""
    for handler in _handlers(_tree()):
        body = list(handler.body)
        if (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        first = body[0]
        assert isinstance(first, ast.If), handler.name
        test = ast.unparse(first.test)
        assert test == "not flags.studio_allowed(user.organization_id)", handler.name
        assert isinstance(first.body[0], ast.Raise), handler.name
        assert "404" in ast.unparse(first.body[0]), handler.name


def test_packages_module_runs_no_sql():
    """The route module holds no SQL: no import of the driver layer, no
    ``execute`` call and no statement text. The stores own all of it."""
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            assert not any(n.startswith("sqlalchemy") or n in ("text", "psycopg", "asyncpg")
                           for n in names), ast.unparse(node)
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("execute", "executemany", "scalars"), ast.unparse(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not re.search(
                r"\b(SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b",
                node.value, re.S | re.I), node.value[:60]


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            called = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if called == name:
                out.append(sub)
    return out


def test_each_publish_or_rollback_is_followed_by_a_sync():
    """A call of ``publish_version`` or ``rollback`` changes the live version.
    The rows must follow it, in the same function and so the same session,
    and the sync must come AFTER it (the one lock order)."""
    tree = _tree()
    seen = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for name in ("publish_version", "rollback"):
            for call in _calls(fn, name):
                seen += 1
                syncs = _calls(fn, "sync_objects")
                assert syncs, f"{fn.name}: {name} with no sync_objects"
                assert any((s.lineno, s.col_offset) > (call.lineno, call.col_offset)
                           for s in syncs), f"{fn.name}: sync_objects before {name}"
    assert seen >= 1


def test_define_object_has_one_caller():
    """``define_object`` takes the org lock, then a KEY SHARE on the ``apps``
    row. A publish takes them the other way round, so a caller outside the
    sync can deadlock with a publish. The one caller is ``_sync_new_objects``,
    and only ``_apply_sync`` calls that, and only ``sync_objects`` calls
    ``_apply_sync``."""
    found: list[tuple[str, str]] = []
    callers: dict[str, set[str]] = {"_sync_new_objects": set(), "_apply_sync": set()}
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            parts = path.relative_to(_ROOT).parts
            if "tests" in parts or "node_modules" in parts or ".venv" in parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "define_object" not in text and "_sync_new_objects" not in text:
                continue
            tree = ast.parse(text)
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
                    continue
                for call in _calls(fn, "define_object"):
                    if call.lineno >= fn.lineno:
                        found.append((path.name, fn.name))
                for name in callers:
                    if _calls(fn, name):
                        callers[name].add(fn.name)
    assert sorted(set(found)) == [("objects_store.py", "_sync_new_objects")], found
    assert callers == {"_sync_new_objects": {"_apply_sync"}, "_apply_sync": {"sync_objects"}}


# The store statements of A5. RLS is the backstop, and it hides a missing org
# filter from each test on a real database, so a source fence holds it.
_A5_SQL = [(objects_store, "_LIST_OBJECTS_SQL"), (objects_store, "_LIST_FIELDS_SQL"),
           (store, "_SUMMARY_SQL"), (store, "_VERSION_LIST_SQL")]


def test_the_versions_statement_reads_one_page_newest_first():
    sql = store._VERSION_LIST_SQL
    assert sql.endswith("ORDER BY v.version DESC LIMIT :limit"), sql
    assert "v.version < CAST(:before AS bigint)" in sql
    assert store.MAX_VERSIONS_PAGE == 100


@pytest.mark.parametrize("module,name", _A5_SQL, ids=[n for _, n in _A5_SQL])
def test_each_store_statement_of_a5_binds_the_org_in_each_select(module, name):
    """Each SELECT of the statement, and each subquery, filters on the bound
    org. No statement names an org as a value."""
    sql = getattr(module, name)
    bound = re.escape(f"organization_id = {module._BOUND_ORG}")
    binds = re.findall(r"(?<![\w])(?:\w+\.)?" + bound, sql)
    selects = re.findall(r"\bSELECT\b", sql)
    assert selects and len(binds) >= len(selects), (name, sql)
    assert ":org" not in sql, name


def test_each_hot_cap_refusal_is_one_sentence_with_a_next_step():
    """A sync problem is one sentence, and a cap refusal tells the member what
    to do: turn a hot field cold first."""
    hot = [{"key": f"h{i}", "type": "text", "hot": True}
           for i in range(objects_store.MAX_HOT_PER_OBJECT + 1)]
    refusals = []
    for call in (lambda: objects_store._check_hot_room(objects_store.MAX_HOT_PER_OBJECT, 0),
                 lambda: objects_store._check_hot_room(0, objects_store.MAX_HOT_PER_ORG),
                 lambda: objects_store._check_fields(hot)):
        with pytest.raises(objects_store.BudgetExceeded) as exc:
            call()
        refusals.append(str(exc.value))
    for message in refusals:
        assert message.endswith(", so turn a hot field cold first."), message
        assert message.count(". ") == 0 and message.count(".") == 1, message


def test_sync_objects_has_one_route_caller():
    tree = _tree()
    assert len(_calls(tree, "sync_objects")) == 1
    assert not _calls(tree, "define_object") and not _calls(tree, "set_constraints")
    assert not _calls(tree, "add_field") and not _calls(tree, "set_field_hot")


def test_the_r8_suite_is_named_in_the_ci_skip_guard():
    """A fence that never runs is no fence. The ``--must-run`` list names the
    pytest ARGUMENT, so a mention in a comment does not count."""
    workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
    assert block, "the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_routes_r8\.py(?: \\)?$",
                     block.group(1), re.M)


# ── The expected hash of a draft (G1c review round 1, P1) ─────────────────


def _h(manifest: dict[str, Any]) -> str:
    from acb_common.studio_manifest import manifest_hash

    return manifest_hash(manifest)


def _two_fields() -> dict[str, Any]:
    return _manifest(objects=[{"name": "visit", "fields": [*_FIELDS, {"key": "n", "type": "text"}]}])


def test_a_publish_passes_its_based_on_to_the_store(world):
    """Mutation: drop ``expected_hash`` from the call of ``publish_version`` -> red."""
    c = _client(world)
    pid = _create(c)
    r = c.post(f"/studio/packages/{pid}/versions", json={"based_on": _h(_manifest()).upper()})
    assert r.status_code == 201, r.text
    assert world.expected == [_h(_manifest())]
    assert c.post(f"/studio/packages/{pid}/versions", json={}).status_code == 201
    assert world.expected[-1] is None


def test_a_publish_with_a_stale_based_on_is_409_draft_changed_and_writes_nothing(world):
    c = _client(world)
    pid = _create(c)
    assert c.put(f"/studio/packages/{pid}/draft", json=_two_fields()).status_code == 200
    r = c.post(f"/studio/packages/{pid}/versions", json={"based_on": _h(_manifest())})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": packages.DRAFT_CHANGED,
                                  "message": store.DRAFT_CHANGED_TEXT}
    assert world.versions == [] and world.sync_calls == [] and world.audits[1:] == []


def test_a_draft_save_with_a_stale_based_on_is_409_and_keeps_the_draft(world):
    """Mutation: drop ``expected_hash`` from the call of ``update_manifest`` -> red."""
    c = _client(world)
    pid = _create(c)
    first = _h(_manifest())
    assert c.put(f"/studio/packages/{pid}/draft?based_on={first}",
                 json=_two_fields()).status_code == 200
    r = c.put(f"/studio/packages/{pid}/draft?based_on={first}", json=_manifest())
    assert r.status_code == 409 and r.json()["detail"]["code"] == packages.DRAFT_CHANGED, r.text
    assert world.packages[pid]["manifest"] == _two_fields()


@pytest.mark.parametrize("bad", ["", "abc", "g" * 64, "a" * 65])
def test_a_based_on_that_is_not_a_hash_is_422_on_both_routes(world, bad):
    c = _client(world)
    pid = _create(c)
    put = c.put(f"/studio/packages/{pid}/draft", params={"based_on": bad}, json=_two_fields())
    pub = c.post(f"/studio/packages/{pid}/versions", json={"based_on": bad})
    assert put.status_code == 422 and pub.status_code == 422, (put.text, pub.text)
    # The draft save takes it as a query parameter, and the publish in its body.
    assert put.json()["detail"]["errors"] == [{"path": "?based_on", "message": packages.BAD_HASH}]
    assert pub.json()["detail"]["errors"] == [{"path": "$.based_on", "message": packages.BAD_HASH}]
    assert world.versions == [] and world.packages[pid]["manifest"] == _manifest()


def test_the_store_compares_the_hash_under_its_row_lock() -> None:
    """The compare comes after the locked read in both writers, so no write
    lands between the compare and the write."""
    import inspect

    for fn in (store.publish_version, store.update_manifest):
        src = inspect.getsource(fn)
        assert src.index("for_update=True") < src.index("_expect(current.manifest, expected_hash)")
