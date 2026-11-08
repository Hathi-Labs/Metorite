"""WS-PKG PKG-14a-2 — a Studio package with declared fields makes ``fields``
claims, on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §8.3 and the PKG-14a-2 row of
§10. Code: ``gateway/package_host/studio_app.py`` (``claims_of``),
``store.install`` and ``store.set_version``, and the resolve of §5.2. The
hermetic twin is ``tests/unit/test_studio_field_claims.py``.

The real publish handler, the real install handler and the real
``GET /shell/manifest`` handler run on a phase-4 promoted scratch database,
as the non-privileged app role, so FORCE RLS binds each statement. Each call
binds its org as the auth layer does.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_field_claims_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-14a-2).**

1. A publish of a manifest with ``contributes.fields`` writes one ``fields``
   claim for each field, keyed ``<record>:<key>``, with the declaration as
   the payload. The resolve shows the entries to the owner, and to no other
   member of the org or of another org.
2. An org install shows the entries to each member of the org, and to no
   member of another org.
3. A key conflict refuses the publish or the install with ``ClaimTaken``
   (409), and it writes nothing (PKG-Q29). A key that a system package
   holds stays with it.
4. A manifest with a bad field is refused at the save, and so a publish of
   it never happens.

**R7: red first, then mutate.** M1: add ``fields`` to ``store.MANY_CLAIMS``.
The conflict tests of 3 go red. M2: drop ``_field_claims`` from
``claims_of``. Tests 1, 2 and 3 go red.
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
from acb_common.app_manifests.shell_manifest import SystemContribution
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException, Response
from gateway.package_host import field_values as fv
from gateway.package_host import store
from gateway.routes.shell import manifest as shell_manifest
from gateway.routes.studio import packages
from sqlalchemy import text
from starlette.requests import Request

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@pkg14f.test"
BEN = f"ben.{TAG}@pkg14f.test"
ZED = f"zed.{TAG}@pkg14f.test"
_SLUG = f"p14f{TAG}-"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")

WARRANTY = {"record": "task", "key": "warranty_end", "type": "date", "label": "Warranty end"}
TIER = {"record": "crm.deal", "key": "tier", "type": "select", "options": ["Gold", "Silver"]}
_RESOLVE_KEYS = ("key", "source", "install_id", "needs_setup")


def _manifest(mid: str, fields: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "studio": 1, "id": mid, "name": "Warranty", "purpose": "Track the warranty of a deal.",
        "objects": [{"name": "claim", "fields": [{"key": "note", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:claim.list"}],
    }
    if fields is not None:
        out["contributes"] = {"fields": fields}
    return out


def _mid() -> str:
    return f"{_SLUG}app{next(_COUNTER)}"


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The body stream that a package handler reads."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def stream(self) -> Any:
        yield self._raw


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound. Answers the
    status and the answer, or the status and the detail of a refusal."""
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
        default = 201 if route == "install_package" else 200
        return response.status_code or default, got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _new(org: str, email: str, fields: list[dict[str, Any]] | None) -> str:
    status, got = await _call("create_package", org, email, payload=_manifest(_mid(), fields))
    assert status == 200, got
    return str(got["package"]["id"])


async def _publish(org: str, email: str, pid: str) -> tuple[int, Any]:
    return await _call("publish", org, email, pid, payload={})


async def _install_org(org: str, email: str, pid: str) -> tuple[int, Any]:
    return await _call("install_package", org, email, pid, payload={"scope": "org"},
                       grants=PUBLISHER)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio on for both and the package host on for
    both. Every package that a test makes has a slug that starts with
    ``p14f<tag>``, and the teardown removes it. The installs and the claims
    go by cascade."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": f"{_SLUG}%"}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"{_SLUG}%"})


# ── Readers (as the superuser, so RLS hides nothing) ───────────────────────


def _installs(world: Any, pid: str) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text, organization_id::text, scope_kind, version FROM package_install "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY created_at"), {"p": pid}).all()
    return [dict(zip(("id", "org", "kind", "version"), r, strict=True)) for r in rows]


def _field_claims(world: Any, install_id: str) -> list[tuple[str, Any, int]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT key, payload, position FROM package_contribution "
            "WHERE install_id = CAST(:i AS uuid) AND point = 'fields' ORDER BY position"),
            {"i": install_id}).all()
    return [(r[0], r[1] if not isinstance(r[1], str) else json.loads(r[1]), r[2]) for r in rows]


def _versions(world: Any, pid: str) -> list[int]:
    with world.p.admin_engine.connect() as c:
        return [int(r[0]) for r in c.execute(text(
            "SELECT version FROM app_versions WHERE app_id = CAST(:p AS uuid) ORDER BY version"),
            {"p": pid}).all()]


def _fields(world: Any, who: str, org: str) -> dict[str, dict[str, Any]]:
    """The ``fields`` entries of installs that the real ``GET /shell/manifest``
    answers to *who* in *org*, by key."""
    user = _user(org, who)
    req = Request({"type": "http", "method": "GET", "path": "/shell/manifest", "headers": []})

    async def go() -> Any:
        token = bind_tenant(org)
        try:
            return await shell_manifest.shell_manifest(req, user)
        finally:
            release_tenant(token)

    resp = _run(world, go)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    return {e["key"]: e for e in body["contributions"].get("fields", [])
            if e["install_id"] is not None}


def _declared(entry: dict[str, Any]) -> Any:
    """The field that the door would parse from one resolve entry."""
    kind, _, key = entry["key"].partition(":")
    assert kind in fv.HOST_RECORD_KINDS
    return fv.declared_field(key, {k: v for k, v in entry.items() if k not in _RESOLVE_KEYS})


# ── 1. A publish writes the claims, and the owner sees them ────────────────


def test_r8_a_publish_writes_one_fields_claim_for_each_declared_field(world) -> None:
    async def go() -> str:
        pid = await _new(world.a, ANA, [WARRANTY, TIER])
        status, got = await _publish(world.a, ANA, pid)
        assert status == 200, got
        return pid

    pid = _run(world, go)
    [row] = _installs(world, pid)
    assert row["kind"] == "private" and row["org"] == world.a
    assert _field_claims(world, row["id"]) == [
        ("task:warranty_end", {"type": "date", "many": False, "label": "Warranty end"}, 0),
        ("crm.deal:tier", {"type": "select", "many": False, "options": ["Gold", "Silver"]}, 1)]


def test_r8_the_resolve_shows_the_fields_to_the_owner_only(world) -> None:
    async def go() -> str:
        pid = await _new(world.a, ANA, [WARRANTY, TIER])
        assert (await _publish(world.a, ANA, pid))[0] == 200
        return pid

    pid = _run(world, go)
    [row] = _installs(world, pid)
    mine = _fields(world, ANA, world.a)
    assert {"task:warranty_end", "crm.deal:tier"} <= set(mine)
    entry = mine["task:warranty_end"]
    assert (entry["source"], entry["install_id"], entry["needs_setup"]) == (
        "private", row["id"], False)
    assert _declared(entry).label == "Warranty end"
    assert _declared(mine["crm.deal:tier"]).options == ("Gold", "Silver")
    for who, org in ((BEN, world.a), (ZED, world.b), (ANA, world.b)):
        seen = _fields(world, who, org)
        assert not any(e["install_id"] == row["id"] for e in seen.values()), (who, org)


def test_r8_a_manifest_with_no_fields_writes_no_fields_claim(world) -> None:
    async def go() -> str:
        pid = await _new(world.a, ANA, None)
        assert (await _publish(world.a, ANA, pid))[0] == 200
        return pid

    pid = _run(world, go)
    [row] = _installs(world, pid)
    assert _field_claims(world, row["id"]) == []


# ── 2. An org install shows the fields to the org only ─────────────────────


def test_r8_an_org_install_shows_the_fields_to_each_member_of_the_org_only(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, [WARRANTY])
        assert (await _publish(world.a, ANA, pid))[0] == 200
        return pid, await _install_org(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 201, got
    org_row = next(r for r in _installs(world, pid) if r["kind"] == "org")
    assert [k for k, _, _ in _field_claims(world, org_row["id"])] == ["task:warranty_end"]
    for who in (ANA, BEN):
        entry = _fields(world, who, world.a)["task:warranty_end"]
        assert (entry["source"], entry["install_id"]) == ("org", org_row["id"]), who
        assert _declared(entry).type == "date"
    for who in (ZED, ANA):
        assert "task:warranty_end" not in _fields(world, who, world.b), who


# ── 3. A key conflict refuses, and a system key stays ──────────────────────


def test_r8_a_fields_key_that_an_org_install_holds_refuses_the_publish(world) -> None:
    """App P holds ``task:warranty_end`` at the org. Ben's new app Q claims
    the same key in private, and the org claim overlaps it (§4). So the
    publish of Q is 409, and it writes no version and no install."""
    async def setup() -> str:
        pid = await _new(world.a, ANA, [WARRANTY])
        assert (await _publish(world.a, ANA, pid))[0] == 200
        assert (await _install_org(world.a, ANA, pid))[0] == 201
        return pid

    held_app = _run(world, setup)
    held = next(r for r in _installs(world, held_app) if r["kind"] == "org")

    async def go() -> tuple[str, tuple[int, Any]]:
        qid = await _new(world.a, BEN, [{**WARRANTY, "type": "text"}])
        return qid, await _publish(world.a, BEN, qid)

    qid, (status, got) = _run(world, go)
    assert status == 409, got
    assert (got["code"], got["point"], got["key"]) == ("claim_taken", "fields", "task:warranty_end")
    assert got["holder"] == {"source": "org", "package": held_app, "install_id": held["id"],
                             "scope_subject": "org"}
    assert _versions(world, qid) == [] and _installs(world, qid) == []
    # The same field key on another record kind is free.

    async def other_kind() -> tuple[int, Any]:
        rid = await _new(world.a, BEN, [{**WARRANTY, "record": "crm.deal"}])
        return await _publish(world.a, BEN, rid)

    assert _run(world, other_kind)[0] == 200


def test_r8_a_fields_key_conflict_refuses_an_org_install(world) -> None:
    """Two apps declare ``crm.deal:tier``. The first org install takes the
    key, and the second org install is 409 and writes nothing. In org B the
    key is free, because the claim of org A is not visible there."""
    async def go() -> tuple[str, str, tuple[int, Any]]:
        first = await _new(world.a, ANA, [TIER])
        second = await _new(world.a, BEN, [TIER])
        for pid, who in ((first, ANA), (second, BEN)):
            assert (await _publish(world.a, who, pid))[0] == 200
        assert (await _install_org(world.a, ANA, first))[0] == 201
        return first, second, await _install_org(world.a, BEN, second)

    first, second, (status, got) = _run(world, go)
    assert status == 409, got
    assert (got["code"], got["point"], got["key"]) == ("claim_taken", "fields", "crm.deal:tier")
    assert got["holder"]["source"] == "org" and got["holder"]["package"] == first
    assert [r["kind"] for r in _installs(world, second)] == ["private"]

    async def in_b() -> tuple[int, Any]:
        pid = await _new(world.b, ZED, [TIER])
        assert (await _publish(world.b, ZED, pid))[0] == 200
        return await _install_org(world.b, ZED, pid)

    assert _run(world, in_b)[0] == 201


def test_r8_a_fields_key_that_a_system_package_holds_refuses_the_publish(
        world, monkeypatch) -> None:
    held = SystemContribution(point="fields", key="task:warranty_end", package="zz-system")
    monkeypatch.setattr(store, "_system_keys", lambda: {("fields", held.key): held})

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, [WARRANTY])
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 409, got
    assert (got["point"], got["key"]) == ("fields", "task:warranty_end")
    assert got["holder"]["source"] == "system" and got["holder"]["package"] == "zz-system"
    assert _versions(world, pid) == [] and _installs(world, pid) == []


# ── 4. A bad field never reaches a publish ─────────────────────────────────


@pytest.mark.parametrize("entry", [
    {**WARRANTY, "record": "crm.leads"}, {**WARRANTY, "key": "Bad"},
    {**WARRANTY, "type": "file"}, {**WARRANTY, "hot": True}])
def test_r8_a_bad_field_is_refused_at_the_save(world, entry) -> None:
    async def go() -> tuple[int, Any]:
        return await _call("create_package", world.a, ANA, payload=_manifest(_mid(), [entry]))

    status, got = _run(world, go)
    assert status == 422, got
    with world.p.admin_engine.connect() as c:
        made = c.execute(text("SELECT count(*) FROM apps WHERE slug LIKE :t AND "
                              "organization_id = CAST(:o AS uuid)"),
                         {"t": f"{_SLUG}%", "o": world.a}).scalar_one()
    assert made == 0
