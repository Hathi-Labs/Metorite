"""WS-STU J3a — the kill switch of a package, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §9 item 7 and §14 J3a, and
``package_host.md`` §5.3 (PKG-9). Code: ``pause_package`` and
``resume_package`` of ``gateway/studio/packages_store.py``, the door
(``routes/studio/invoke.py``) and ``resolve.INSTALLS_SQL``. The hermetic twin
is ``tests/unit/test_studio_package_pause.py``.

The real create, publish, install, package and door handlers run on a phase-4
promoted scratch database, as the non-privileged app role, so FORCE RLS binds
each statement. Org A has Owen (the owner, a holder of ``studio:publish``),
Ada (an admin who pauses), and Ana in ``ops``. Org B has Zed. Owen publishes
app P with one record, so P has his private install, and installs P for
``ops``.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_package_pause_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J3a).**

a. After a pause, each next door call of the owner gets 423
   ``package_paused``, writes one audit row, and changes no record. The
   pause writes one audit row (op ``package.pause``) that names the actor.
b. A pause moves each install of the package to ``paused``, and the next
   ``resolve_for`` of the owner and of a viewer shows no claim of it.
c. A resume brings back the installs, the claims and the door.
d. Org B cannot pause or resume a package of org A, and nothing changes.
e. A publish keeps the pause.
f. A viewer gets 404 at the door of a paused package, also through an
   install that the owner made during the pause.
g. The resend of an approved card gets 423, spends nothing, and the
   request stays approved, so it runs after a resume.
h. A pause during the body read gives 423 at the second check.
i. A second pause and a resume of a running package write no audit row.
j. An archived install stays archived through a pause and a resume.
"""

from __future__ import annotations

import asyncio
import inspect
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
from fastapi.responses import JSONResponse
from gateway import app_requests
from gateway.package_host import resolve
from gateway.routes.studio import invoke, packages
from gateway.studio import objects_store, packages_store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@j3a.test"
#: Mixed case, so the stamp folds it.
ADA = f"Ada.{TAG}@j3a.test"
ANA = f"ana.{TAG}@j3a.test"
ZED = f"zed.{TAG}@j3a.test"
OPS = f"group:ops{TAG}"
_EMAIL_LIKE = f"%.{TAG}@j3a.test"
_GROUP_LIKE = f"%{TAG}"
_SLUG_LIKE = f"j3a{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
NOT_FOUND = "Not found"
PAUSED = {"code": "package_paused", "message": invoke.PAUSED_TEXT}
_DATA = {"site": "Plant 4, Hosur"}


def _manifest(mid: str, name: str = "Site visits") -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": name, "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [
            {"id": "peek", "uses": "object:visit.get"},
            {"id": "find", "uses": "object:visit.list"},
            {"id": "add", "uses": "object:visit.create"},
            {"id": "change", "uses": "object:visit.update"},
            {"id": "remove", "uses": "object:visit.delete"},
        ],
    }


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The parts of a request that the door reads. *on_read* runs at the
    read. *fails* makes the read fail, so a resend that reads a byte errors."""

    def __init__(self, payload: Any = None, on_read: Callable[[], Any] | None = None,
                 fails: bool = False, query: dict[str, str] | None = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/y")
        self.query_params = dict(query or {})
        self._on_read, self._fails = on_read, fails
        self.reads = 0

    async def stream(self) -> Any:
        self.reads += 1
        if self._fails:
            raise OSError("the read of the body failed")
        if self._on_read is not None:
            done = self._on_read()
            if inspect.isawaitable(done):
                await done
        yield self._raw


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "save_draft", "publish", "install_package",
                 "uninstall_package"):
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


async def _door(org: str, email: str, pid: str, action: str, payload: Any = None,
                request: _Request | None = None) -> tuple[int, Any]:
    """Call the real door with *org* bound."""
    token = bind_tenant(org)
    try:
        got = await invoke.invoke_action(pid, action, request=request or _Request(payload),
                                         user=_user(org, email))
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)
    if isinstance(got, JSONResponse):
        return got.status_code, json.loads(got.body)
    return 200, got


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _stop(org: str, verb: str, actor: str, pid: str) -> Any:
    """One pause or resume through the store, in its own transaction."""
    fn = packages_store.pause_package if verb == "pause" else packages_store.resume_package
    async with tenant_session(org) as db:
        return await fn(db, actor, pid)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the package host on for both. Owen
    publishes app P with one record, which keeps his private install, and
    installs P for ``ops``."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        users: dict[str, str] = {}
        for email, org in ((OWEN, p.org_a), (ADA, p.org_a), (ANA, p.org_a), (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name) "
            "VALUES (:org, :s, :s) RETURNING id"),
            {"org": p.org_a, "s": OPS.removeprefix("group:")}).scalar_one())
        for email in (OWEN, ANA):
            c.execute(text(
                "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": p.org_a})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, mid=f"j3a{TAG}-app{next(_COUNTER)}")

    async def setup() -> None:
        status, got = await _call("create_package", w.a, OWEN, payload=_manifest(w.mid))
        assert status == 200, got
        w.pid = str(got["package"]["id"])
        status, got = await _call("publish", w.a, OWEN, w.pid, payload={})
        assert status == 200, got
        async with tenant_session(w.a) as db:
            obj = await objects_store.get_object_by_name(db, "visit", package_id=w.pid)
            assert obj is not None
            rec = await objects_store.create_record(db, OWEN, obj.id, _DATA, package_id=w.pid,
                                                  viewer=Viewer(OWEN))
            assert rec is not None
        w.obj, w.rec = obj.id, rec.id
        status, got = await _call("install_package", w.a, OWEN, w.pid,
                                  payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 201, got
        w.group_install = str(got["install"]["id"])

    _run(w, setup)
    invoke._IN_FLIGHT.clear()
    invoke._READS_IN_FLIGHT.clear()
    yield w
    invoke._IN_FLIGHT.clear()
    invoke._READS_IN_FLIGHT.clear()
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs WHERE app = 'studio' "
                       "AND member_email = :o"), {"o": OWEN})
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


# ── Reads as the superuser, which RLS does not bind ────────────────────────


def _rows(world: Any, sql: str, **params: Any) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).all())


def _admin(world: Any, sql: str, **params: Any) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), params)


def _installs(world: Any) -> dict[str, str]:
    """The state of each install of P, by install id."""
    return {str(r[0]): str(r[1]) for r in _rows(
        world, "SELECT id::text, state FROM package_install WHERE app_id = CAST(:p AS uuid)",
        p=world.pid)}


def _pause_cols(world: Any) -> tuple[Any, Any]:
    [row] = _rows(world, "SELECT paused_at, paused_by FROM apps WHERE id = CAST(:p AS uuid)",
                  p=world.pid)
    return row[0], row[1]


def _detail(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else json.loads(raw)


def _door_rows(world: Any, email: str) -> list[dict[str, Any]]:
    """The door rows of *email* for P, oldest first."""
    return [_detail(r[0]) for r in _rows(
        world, "SELECT detail FROM app_audit WHERE app_id = CAST(:p AS uuid) "
        "AND lower(user_email) = lower(:e) AND detail->>'action' IS NOT NULL ORDER BY id",
        p=world.pid, e=email)]


def _stop_rows(world: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """The pause and resume rows of P, oldest first: org, actor, detail."""
    return [(str(r[0]), str(r[1]), _detail(r[2])) for r in _rows(
        world, "SELECT organization_id::text, user_email, detail FROM app_audit "
        "WHERE app_id = CAST(:p AS uuid) AND kind = 'action' "
        "AND detail->>'op' IN ('package.pause', 'package.resume') ORDER BY id", p=world.pid)]


def _records(world: Any) -> list[tuple[str, Any]]:
    return [(str(r[0]), r[1]) for r in _rows(
        world, "SELECT id::text, data FROM studio_records WHERE object_id = CAST(:o AS uuid) "
        "ORDER BY id", o=world.obj)]


def _seen(world: Any, who: str, org: str | None = None) -> set[str]:
    """The install ids of P in the answer of ``resolve_for`` for *who*."""
    async def go() -> dict[str, Any]:
        async with tenant_session(org or world.a) as db:
            return await resolve.resolve_for(db, viewer=who, can_use=lambda _slug: True)

    body = _run(world, go)
    mine = set(_installs(world))
    return {e["install_id"] for entries in body["contributions"].values() for e in entries
            if e["install_id"] in mine}


# ── a. The owner's door after a pause ──────────────────────────────────────


def test_r8_after_a_pause_each_door_call_gets_423_one_row_and_no_change(world) -> None:
    before = _records(world)
    calls = [("peek", {"id": world.rec}), ("find", {}), ("add", {"data": {"site": "new"}}),
             ("change", {"id": world.rec, "data": {"site": "changed"}}),
             ("remove", {"id": world.rec}), ("not_declared", {})]

    async def go() -> tuple[Any, list[tuple[int, Any]]]:
        stop = await _stop(world.a, "pause", ADA, world.pid)
        return stop, [await _door(world.a, OWEN, world.pid, a, body) for a, body in calls]

    stop, answers = _run(world, go)
    assert stop == packages_store.PackageStop(world.pid, paused=True, changed=True, installs=2)
    for status, got in answers:
        assert (status, got) == (423, PAUSED)
    assert _records(world) == before
    rows = _door_rows(world, OWEN)
    assert [(r["action"], r["outcome"], r["reason"]) for r in rows] == [
        (a, "refused", "package_paused") for a, _ in calls]
    assert _stop_rows(world) == [(world.a, ADA.lower(), {"op": "package.pause", "installs": 2})]
    at, by = _pause_cols(world)
    assert at is not None and by == ADA.lower()


# ── b. Each install pauses, and the resolve shows no claim ─────────────────


def test_r8_each_install_is_paused_and_the_resolve_shows_no_claim(world) -> None:
    installs = _installs(world)
    assert set(installs.values()) == {"active"} and len(installs) == 2
    assert _seen(world, OWEN) and _seen(world, ANA) == {world.group_install}
    _run(world, lambda: _stop(world.a, "pause", ADA, world.pid))
    assert set(_installs(world).values()) == {"paused"}
    assert _seen(world, OWEN) == set() and _seen(world, ANA) == set()


# ── c. A resume restores the installs, the claims and the door ─────────────


def test_r8_a_resume_restores_the_installs_the_claims_and_the_door(world) -> None:
    owner_before, ana_before = _seen(world, OWEN), _seen(world, ANA)

    async def go() -> tuple[Any, list[tuple[int, Any]]]:
        await _stop(world.a, "pause", ADA, world.pid)
        stop = await _stop(world.a, "resume", ADA, world.pid)
        return stop, [await _door(world.a, OWEN, world.pid, "find", {}),
                      await _door(world.a, ANA, world.pid, "find", {})]

    stop, answers = _run(world, go)
    assert stop == packages_store.PackageStop(world.pid, paused=False, changed=True, installs=2)
    assert set(_installs(world).values()) == {"active"}
    assert _seen(world, OWEN) == owner_before and _seen(world, ANA) == ana_before
    for status, got in answers:
        assert status == 200 and [r["id"] for r in got["records"]] == [world.rec], got
    assert _pause_cols(world) == (None, None)
    assert [d["op"] for _, _, d in _stop_rows(world)] == ["package.pause", "package.resume"]


# ── d. Org B cannot stop a package of org A ────────────────────────────────


def test_r8_org_b_cannot_pause_or_resume_a_package_of_org_a(world) -> None:
    installs = _installs(world)
    assert _run(world, lambda: _stop(world.b, "pause", ZED, world.pid)) is None
    assert _pause_cols(world) == (None, None) and _installs(world) == installs
    assert _stop_rows(world) == []
    _run(world, lambda: _stop(world.a, "pause", ADA, world.pid))
    assert _run(world, lambda: _stop(world.b, "resume", ZED, world.pid)) is None
    assert _pause_cols(world)[0] is not None
    assert set(_installs(world).values()) == {"paused"}
    assert len(_stop_rows(world)) == 1


# ── e. A publish keeps the pause ───────────────────────────────────────────


def test_r8_a_publish_keeps_the_pause(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        await _stop(world.a, "pause", ADA, world.pid)
        saved = await _call("save_draft", world.a, OWEN, world.pid,
                            payload=_manifest(world.mid, "Second name"))
        published = await _call("publish", world.a, OWEN, world.pid, payload={})
        return [saved, published, await _door(world.a, OWEN, world.pid, "find", {})]

    saved, published, door = _run(world, go)
    assert saved[0] == 200 and published[0] == 200, (saved, published)
    assert _pause_cols(world)[0] is not None
    assert set(_installs(world).values()) == {"paused"}
    assert door == (423, PAUSED)
    assert _seen(world, OWEN) == set()


# ── f. A viewer gets 404, also through a new install ───────────────────────


def test_r8_a_viewer_gets_404_also_through_an_install_made_during_the_pause(world) -> None:
    """WS-STU J2b-1 (path A): the org install refuses a paused package, even
    with an approval (423). So the install made during the pause is a group
    install. The setup install of ``ops`` goes first, so the new one is new."""
    async def go() -> list[Any]:
        gone = await _call("uninstall_package", world.a, OWEN, world.pid,
                           payload={"scope": OPS}, grants=PUBLISHER)
        assert gone[0] == 200, gone
        async with tenant_session(world.a) as db:
            await packages_store.request_org_approval(db, OWEN, world.pid, 1)
        async with tenant_session(world.a) as db:
            await packages_store.approve_version(db, ADA, world.pid, 1, can_publish=True)
        await _stop(world.a, "pause", ADA, world.pid)
        org = await _call("install_package", world.a, OWEN, world.pid,
                          payload={"scope": "org"}, grants=PUBLISHER)
        assert org == (423, PAUSED), org
        made = await _call("install_package", world.a, OWEN, world.pid,
                           payload={"scope": OPS}, grants=PUBLISHER)
        return [made, await _door(world.a, ANA, world.pid, "find", {}),
                await _door(world.a, ANA, world.pid, "peek", {"id": world.rec}),
                await _call("get_package", world.a, ANA, world.pid)]

    made, *answers = _run(world, go)
    assert made[0] == 201, made
    new = str(made[1]["install"]["id"])
    assert _installs(world)[new] == "active"
    for status, got in answers:
        assert (status, got) == (404, NOT_FOUND)
    assert _seen(world, ANA) == set() and _door_rows(world, ANA) == []
    # A second pause moves the new install too, and its row names one install.
    again = _run(world, lambda: _stop(world.a, "pause", ADA, world.pid))
    assert again == packages_store.PackageStop(world.pid, paused=True, changed=True, installs=1)
    assert set(_installs(world).values()) == {"paused"}


# ── g. The resend of an approved card ──────────────────────────────────────


def test_r8_a_resend_of_an_approved_card_gets_423_and_the_request_stays_approved(
        world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "app_requests_orgs", "*")
    card = {"data": {"site": "Card site"}}

    async def state(rid: str) -> str | None:
        async with tenant_session(world.a) as db:
            found = await app_requests.read_request(db, org=world.a, member=OWEN,
                                                    request_id=rid)
        return None if found is None else str(found["state"])

    async def go() -> dict[str, Any]:
        got: dict[str, Any] = {"asked": await _door(
            world.a, OWEN, world.pid, "add", request=_Request(card, query={"ask": "1"}))}
        rid = got["asked"][1]["request_id"]
        async with tenant_session(world.a) as db:
            await app_requests.decide(db, org=world.a, member=OWEN, request_id=rid,
                                      decision="approve", idempotency_key=uuid.uuid4().hex)
        await _stop(world.a, "pause", ADA, world.pid)
        resend = _Request(card, fails=True, query={"request_id": rid})
        got["paused"] = await _door(world.a, OWEN, world.pid, "add", request=resend)
        got["reads"] = resend.reads
        got["count"] = len(_records(world))
        got["state"] = await state(rid)
        await _stop(world.a, "resume", ADA, world.pid)
        got["ran"] = await _door(world.a, OWEN, world.pid, "add",
                                 request=_Request(card, fails=True, query={"request_id": rid}))
        got["final"] = await state(rid)
        return got

    count = len(_records(world))
    got = _run(world, go)
    assert got["asked"][0] == 202, got["asked"]
    assert got["paused"] == (423, PAUSED)
    assert got["reads"] == 0 and got["count"] == count
    assert got["state"] == "approved"
    assert got["ran"][0] == 200 and got["ran"][1]["record"]["data"] == card["data"], got["ran"]
    assert got["final"] == "done" and len(_records(world)) == count + 1


# ── h. A pause during the body read ────────────────────────────────────────


def test_r8_a_pause_during_the_read_gives_423_at_the_second_check(world) -> None:
    async def pause() -> None:
        await _stop(world.a, "pause", ADA, world.pid)

    request = _Request({}, on_read=pause)
    status, got = _run(world, lambda: _door(world.a, OWEN, world.pid, "find", request=request))
    assert (status, got) == (423, PAUSED)
    assert request.reads == 1
    [row] = _door_rows(world, OWEN)
    assert (row["action"], row["reason"]) == ("find", "package_paused")


# ── i. A call that moves nothing writes no row ─────────────────────────────


def test_r8_a_second_pause_and_a_resume_of_a_running_package_write_nothing(world) -> None:
    assert _run(world, lambda: _stop(world.a, "resume", ADA, world.pid)) == \
        packages_store.PackageStop(world.pid, paused=False, changed=False, installs=0)
    assert _stop_rows(world) == []
    _run(world, lambda: _stop(world.a, "pause", ADA, world.pid))
    first = _pause_cols(world)
    again = _run(world, lambda: _stop(world.a, "pause", OWEN, world.pid))
    assert again == packages_store.PackageStop(world.pid, paused=True, changed=False, installs=0)
    assert _pause_cols(world) == first and len(_stop_rows(world)) == 1


# ── j. An archived install stays archived ──────────────────────────────────


def test_r8_an_archived_install_stays_archived(world) -> None:
    _admin(world, "UPDATE package_install SET state = 'archived', archived_at = now() "
           "WHERE id = CAST(:i AS uuid)", i=world.group_install)
    paused = _run(world, lambda: _stop(world.a, "pause", ADA, world.pid))
    assert paused is not None and paused.installs == 1
    assert _installs(world)[world.group_install] == "archived"
    resumed = _run(world, lambda: _stop(world.a, "resume", ADA, world.pid))
    assert resumed is not None and resumed.installs == 1
    assert _installs(world)[world.group_install] == "archived"


def test_r8_this_suite_is_named_in_the_ci_skip_guard() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for workflow in ("pr-check.yml", "deploy.yml"):
        body = (root / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "tests/unit/test_studio_package_pause_r8.py" in body, workflow
