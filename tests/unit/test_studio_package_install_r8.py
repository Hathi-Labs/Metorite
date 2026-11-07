"""WS-PKG PKG-8b — the org and group install of a Studio app, on a real
database (R8).

Spec: ``project-docs/specs/package_host.md`` §4 (PKG-Q6, the org wins),
§5.2, §5.5, §6.1 and the PKG-8b row of §10. Code: ``install_package`` of
``gateway/routes/studio/packages.py``, ``package_host/studio_app.py``,
``package_host/store.py`` (``OVERLAP`` and ``in_group``) and
``studio/packages_store.lock_for_install``. The hermetic twin is
``tests/unit/test_studio_package_install.py``.

The real install, publish and ``GET /shell/manifest`` handlers run on a
phase-4 promoted scratch database, as the non-privileged app role, so FORCE
RLS binds each statement. Each call binds its org as the auth layer does.
Org A has five members: Owen, Ana and Ben are in ``ops``, Cat is in ``eng``,
and Dan was in ``ops`` until an admin removed him. Org B has Zed.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_package_install_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-8b).**

a. Ana holds a private nav claim of app X on the key of app Y. Owen's org
   install of Y goes in. Ana and each other member of org A see Y at that
   key, with the source ``org``. Ana's install of X stays.
b. The same for a group install: a member of the group sees Y, and a member
   outside it sees nothing of Y.
c. Ana's private install of app Z on a key that an org or a group install
   holds is refused (409).
d. Org B sees nothing of org A, and its member cannot install an app of org
   A.
e. An org claim still meets another org claim and a group claim, and a group
   claim still meets an org claim (409).
f. No answer of the route names Ana or her app.
g. The route refuses a scope that §6.1 does not give the member (403 or
   422), and the refusal writes nothing. A draft is 409 ``not_published``.
h. A second call moves the install to the live version (200). A publish
   alone does not move it.
i. The install locks the ``apps`` row before the org lock of the install
   store, so it never holds that lock while it waits for a publish.

**R7: red first, then mutate.** Each mutation ran on 2026-11-07, and the
build report of task 1963 holds the counts.

* M1. ``install_package``: drop the ``studio:publish`` check. The 403 case
  of an org install without the verb goes red.
* M2. ``install_package``: drop the ``in_group`` check. The 403 cases of a
  member outside the group, a removed member and a group that does not
  exist go red.
* M3. ``packages_store._INSTALL_LOCK_SQL``: drop ``FOR KEY SHARE``. The lock
  order test goes red: the install waits on its INSERT, and it holds the org
  lock.
* M4. ``store.OVERLAP["org"]``: set it back to ``TRUE``. The org install of
  case a goes red with 409 ``claim_taken``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException, Response
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
OWEN = f"owen.{TAG}@pkg8b.test"
#: Mixed case in ``app_user``, so the group read folds it.
ANA = f"Ana.{TAG}@pkg8b.test"
ANA_LOW = ANA.lower()
BEN = f"ben.{TAG}@pkg8b.test"
CAT = f"cat.{TAG}@pkg8b.test"
DAN = f"dan.{TAG}@pkg8b.test"
ZED = f"zed.{TAG}@pkg8b.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_EMAIL_LIKE = f"%.{TAG}@pkg8b.test"
_GROUP_LIKE = f"%{TAG}"
_SLUG_LIKE = f"p8b{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")


def _manifest(mid: str, name: str = "Site visits") -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": name, "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


def _mid() -> str:
    return f"p8b{TAG}-app{next(_COUNTER)}"


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


async def _new(org: str, email: str, name: str = "Site visits") -> str:
    status, got = await _call("create_package", org, email, payload=_manifest(_mid(), name))
    assert status == 200, got
    return str(got["package"]["id"])


async def _live(org: str, email: str, name: str = "Site visits") -> str:
    """A new package of *email*, published once."""
    pid = await _new(org, email, name)
    status, got = await _call("publish", org, email, pid, payload={})
    assert status == 200, got
    return pid


async def _install(org: str, email: str, pid: str, scope: Any,
                   grants: tuple[str, ...] = PUBLISHER) -> tuple[int, Any]:
    return await _call("install_package", org, email, pid, payload={"scope": scope},
                       grants=grants)


async def _seed(org: str, *, app: str, scope: str, member: str, key: str,
                label: str = "Squat", version: int | None = 1) -> store.Install:
    """One install of *app* at *scope* that claims the nav key *key*, as the
    app role. A hand-made claim, because the claim of a publish is the href
    of its own app."""
    kind = "org" if scope == "org" else "group" if scope.startswith("group:") else "private"
    async with tenant_session(org) as db:
        return await store.install(
            db, app_id=app, version=version, scope_subject=scope, scope_kind=kind,
            installed_by=member, ends_at=None, contributions=[store.Contribution(
                point="nav", key=key, payload={"href": key, "label": label,
                                               "feature": "studio"})])


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the package host on for org A only.
    The members and the groups are seeded as the superuser. Every package
    that a test makes has a slug that starts with ``p8b<tag>``, and the
    teardown removes it. The installs and the claims go by cascade."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", p.org_a)
    with p.admin_engine.begin() as c:
        users: dict[str, str] = {}
        for email, org, status in ((OWEN, p.org_a, "active"), (ANA, p.org_a, "active"),
                                   (BEN, p.org_a, "active"), (CAT, p.org_a, "active"),
                                   (DAN, p.org_a, "removed"), (ZED, p.org_b, "active")):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', :s, :org) RETURNING id"),
                {"e": email, "s": status, "org": org}).scalar_one())
        for group, org, members in ((OPS, p.org_a, (OWEN, ANA, BEN, DAN)),
                                    (ENG, p.org_a, (CAT,))):
            gid = str(c.execute(text(
                "INSERT INTO org_group (organization_id, slug, display_name) "
                "VALUES (:org, :s, :s) RETURNING id"),
                {"org": org, "s": group.removeprefix("group:")}).scalar_one())
            for email in members:
                c.execute(text(
                    "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                    "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": org})
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


# ── Readers (as the superuser, so RLS hides nothing) ───────────────────────


def _installs(world: Any, pid: str) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text, organization_id::text, version, scope_kind, scope_subject, "
            "installed_by, state FROM package_install "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY created_at"), {"p": pid}).all()
    keys = ("id", "org", "version", "kind", "subject", "by", "state")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def _shared(world: Any, pid: str) -> list[dict[str, Any]]:
    """The org and group installs of *pid*."""
    return [i for i in _installs(world, pid) if i["kind"] != "private"]


def _label(world: Any, install_id: str) -> str:
    with world.p.admin_engine.connect() as c:
        return str(c.execute(text(
            "SELECT payload->>'label' FROM package_contribution "
            "WHERE install_id = CAST(:i AS uuid)"), {"i": install_id}).scalar_one())


def _view(world: Any, who: str, org: str) -> Any:
    """Call the real ``GET /shell/manifest`` as *who* in *org*. Answers the
    status and the nav entries of installs, by key."""
    user = _user(org, who)
    req = Request({"type": "http", "method": "GET", "path": "/shell/manifest", "headers": []})

    async def go() -> Any:
        token = bind_tenant(org)
        try:
            return await shell_manifest.shell_manifest(req, user)
        except HTTPException as exc:
            return SimpleNamespace(status_code=exc.status_code, body=b"")
        finally:
            release_tenant(token)

    resp = _run(world, go)
    if resp.status_code != 200:
        return SimpleNamespace(status=resp.status_code, nav={})
    body = json.loads(resp.body)
    nav = {e["key"]: e for e in body["contributions"].get("nav", [])
           if e["install_id"] is not None}
    return SimpleNamespace(status=200, nav=nav)


# ── a, f. The org wins over a private claim ────────────────────────────────


def test_r8_an_org_install_goes_in_over_a_private_claim_and_shows_to_each_member(world) -> None:
    async def go() -> tuple[str, str, store.Install, tuple[int, Any]]:
        y = await _live(world.a, OWEN)
        x = await _new(world.a, ANA)
        held = await _seed(world.a, app=x, scope=ANA, member=ANA, key=f"/apps/{y}",
                           version=None)
        return y, x, held, await _install(world.a, OWEN, y, "org")

    y, x, held, (status, got) = _run(world, go)
    assert status == 201, got
    assert got["created"] is True
    assert got["install"]["scope_kind"] == "org" and got["install"]["version"] == 1
    # f. The answer names neither Ana nor her app.
    seen = json.dumps(got).lower()
    for secret in (ANA_LOW, x, held.id):
        assert secret not in seen, secret
    [org_install] = _shared(world, y)
    assert (org_install["kind"], org_install["subject"], org_install["by"]) == (
        "org", "org", OWEN)
    href = f"/apps/{y}"
    for who in (ANA, BEN, CAT, OWEN):
        entry = _view(world, who, world.a).nav[href]
        assert (entry["source"], entry["install_id"], entry["label"]) == (
            "org", org_install["id"], "Site visits"), who
    # Ana's install of X stays, and its claim still holds the key.
    [kept] = _installs(world, x)
    assert kept["id"] == held.id and _label(world, held.id) == "Squat"


# ── b. A group install, for the members of the group only ──────────────────


def test_r8_a_group_install_shows_to_the_members_of_the_group_only(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        y = await _live(world.a, OWEN)
        x = await _new(world.a, ANA)
        await _seed(world.a, app=x, scope=ANA, member=ANA, key=f"/apps/{y}", version=None)
        # Owen holds feature:studio only: a group install needs no share verb.
        return y, await _install(world.a, OWEN, y, OPS.upper().replace("GROUP:", "group:"),
                                 grants=STUDIO)

    y, (status, got) = _run(world, go)
    assert status == 201, got
    assert (got["install"]["scope_kind"], got["install"]["scope_subject"]) == ("group", OPS)
    [group_install] = _shared(world, y)
    href = f"/apps/{y}"
    for who in (ANA, BEN):
        entry = _view(world, who, world.a).nav[href]
        assert (entry["source"], entry["install_id"]) == ("group", group_install["id"]), who
    # Cat is in eng, not in ops. Owen's private install shows to Owen only.
    assert href not in _view(world, CAT, world.a).nav
    assert ANA_LOW not in json.dumps(got).lower()


# ── c. A private claim cannot take a key that an org or a group holds ──────


def test_r8_a_private_install_on_a_held_key_is_refused(world) -> None:
    async def go() -> list[Any]:
        y_org = await _live(world.a, OWEN)
        y_ops = await _live(world.a, OWEN)
        assert (await _install(world.a, OWEN, y_org, "org"))[0] == 201
        assert (await _install(world.a, OWEN, y_ops, OPS))[0] == 201
        out: list[Any] = []
        for who, key in ((ANA, y_org), (ANA, y_ops), (CAT, y_ops)):
            z = await _new(world.a, who)
            try:
                out.append(await _seed(world.a, app=z, scope=who, member=who,
                                       key=f"/apps/{key}", version=None))
            except store.InstallError as exc:
                out.append(exc)
        return out

    org_held, ops_held, cat = _run(world, go)
    for refused, source in ((org_held, "org"), (ops_held, "group")):
        assert isinstance(refused, store.ClaimTaken), refused
        assert (refused.status, refused.holder.source) == (409, source)
    # Cat is not in ops, so the ops claim does not meet hers.
    assert isinstance(cat, store.Install), cat


# ── d. Org B ───────────────────────────────────────────────────────────────


def test_r8_org_b_sees_nothing_of_org_a_and_cannot_install_its_app(world, monkeypatch) -> None:
    y = _run(world, lambda: _live(world.a, OWEN))
    assert _run(world, lambda: _install(world.a, OWEN, y, "org"))[0] == 201
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{world.a},{world.b}")
    other = _view(world, ZED, world.b)
    assert other.status == 200 and f"/apps/{y}" not in other.nav
    for who in (ZED, OWEN):
        status, got = _run(world, lambda who=who: _install(world.b, who, y, "org"))
        assert (status, got) == (404, "Not found"), who
    assert len(_shared(world, y)) == 1


def test_r8_an_org_outside_the_package_host_gets_404_and_no_row(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        y = await _live(world.b, ZED)
        return y, await _install(world.b, ZED, y, "org")

    y, (status, got) = _run(world, go)
    assert (status, got) == (404, "Not found")
    assert _installs(world, y) == []


# ── e. An org claim still meets an org and a group claim ───────────────────


def test_r8_an_org_or_group_claim_still_blocks_an_org_or_group_install(world) -> None:
    async def go() -> list[tuple[str, str, store.Install, tuple[int, Any]]]:
        out = []
        for held_scope, new_scope in (("org", "org"), (OPS, "org"), ("org", OPS)):
            y = await _live(world.a, OWEN)
            w = await _live(world.a, OWEN, "Other app")
            held = await _seed(world.a, app=w, scope=held_scope, member=OWEN,
                               key=f"/apps/{y}")
            out.append((y, w, held, await _install(world.a, OWEN, y, new_scope)))
        return out

    for y, w, held, (status, got) in _run(world, go):
        assert status == 409, got
        assert got["code"] == "claim_taken" and got["key"] == f"/apps/{y}"
        assert got["holder"]["package"] == w and got["holder"]["install_id"] == held.id
        assert _shared(world, y) == []


# ── g. The refusals of the route write nothing ─────────────────────────────


def test_r8_the_route_refuses_a_scope_that_the_member_lacks(world) -> None:
    async def go() -> dict[str, Any]:
        mine = await _live(world.a, OWEN)
        cats = await _live(world.a, CAT)
        dans = await _live(world.a, DAN)
        return {
            "pids": (mine, cats, dans),
            "org without the verb": await _install(world.a, OWEN, mine, "org", STUDIO),
            "outside the group": await _install(world.a, CAT, cats, OPS),
            "removed member": await _install(world.a, DAN, dans, OPS),
            "no such group": await _install(world.a, OWEN, mine, f"group:none{TAG}"),
            "an address": await _install(world.a, OWEN, mine, OWEN),
            "no scope": await _call("install_package", world.a, OWEN, mine, {}, PUBLISHER),
            "not the owner": await _install(world.a, BEN, mine, "org"),
        }

    got = _run(world, go)
    pids = got.pop("pids")
    want = {"org without the verb": 403, "outside the group": 403, "removed member": 403,
            "no such group": 403, "an address": 422, "no scope": 422, "not the owner": 404}
    assert {k: s for k, (s, _) in got.items()} == want, got
    for case in ("org without the verb", "outside the group", "removed member",
                 "no such group"):
        assert got[case][1]["code"] == "forbidden", case
    assert got["an address"][1]["code"] == "bad_scope"
    for pid in pids:
        assert _shared(world, pid) == [], pid


def test_r8_a_draft_is_409_not_published_and_writes_nothing(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, OWEN)
        return pid, await _install(world.a, OWEN, pid, "org")

    pid, (status, got) = _run(world, go)
    assert status == 409 and got["code"] == "not_published", got
    assert _installs(world, pid) == []


# ── h. A second call moves the install ─────────────────────────────────────


def test_r8_a_second_call_moves_the_install_to_the_live_version(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, list[tuple[int, Any]]]:
        status, made = await _call("create_package", world.a, OWEN, payload=_manifest(mid))
        assert status == 200, made
        pid = str(made["package"]["id"])
        assert (await _call("publish", world.a, OWEN, pid, payload={}))[0] == 200
        first = await _install(world.a, OWEN, pid, "org")
        saved = await _call("save_draft", world.a, OWEN, pid, _manifest(mid, "Field visits"))
        assert saved[0] == 200, saved
        assert (await _call("publish", world.a, OWEN, pid, payload={}))[0] == 200
        return pid, [first, await _install(world.a, OWEN, pid, "org")]

    pid, (first, second) = _run(world, go)
    assert first[0] == 201 and first[1]["install"]["version"] == 1, first
    assert second[0] == 200 and second[1]["created"] is False, second
    assert second[1]["install"]["version"] == 2
    [org_install] = _shared(world, pid)
    assert org_install["version"] == 2 and org_install["id"] == second[1]["install"]["id"]
    assert _label(world, org_install["id"]) == "Field visits"


def test_r8_a_publish_alone_leaves_the_org_install_at_its_version(world) -> None:
    mid = _mid()

    async def go() -> str:
        status, made = await _call("create_package", world.a, OWEN, payload=_manifest(mid))
        assert status == 200, made
        pid = str(made["package"]["id"])
        assert (await _call("publish", world.a, OWEN, pid, payload={}))[0] == 200
        assert (await _install(world.a, OWEN, pid, "org"))[0] == 201
        assert (await _call("publish", world.a, OWEN, pid, payload={}))[0] == 200
        return pid

    pid = _run(world, go)
    by_kind = {i["kind"]: i["version"] for i in _installs(world, pid)}
    assert by_kind == {"private": 2, "org": 1}


# ── i. The lock order ──────────────────────────────────────────────────────


def _waiting(world: Any, holder: int) -> list[tuple[int, str]]:
    """The backends of the scratch database, other than *holder*, that wait
    on a lock. Each with the text of its statement."""
    with world.p.admin_engine.connect() as c:
        return [(int(r[0]), str(r[1])) for r in c.execute(text(
            "SELECT pid, query FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock' AND pid <> :h"), {"h": holder}).all()]


def _org_lock_free(world: Any) -> bool:
    """True when no session holds the org lock of the install store."""
    with world.p.admin_engine.begin() as c:
        return bool(c.execute(text(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"package_install:{world.a}"}).scalar_one())


def test_r8_the_install_locks_the_app_row_before_the_org_lock(world) -> None:
    """A publish holds the ``apps`` row ``FOR UPDATE``, then takes the org lock
    of the install store. Here a session holds the row, as a publish does.
    The install must wait for the row BEFORE it takes the org lock. Else it
    holds the org lock and waits for the row in the FK check of its INSERT,
    and the publish then waits for the org lock: a deadlock (M3)."""
    y = _run(world, lambda: _live(world.a, OWEN))

    async def go() -> SimpleNamespace:
        held, release = asyncio.Event(), asyncio.Event()
        holder: dict[str, int] = {}

        async def publish_like() -> None:
            async with tenant_session(world.a) as db:
                holder["pid"] = int((await db.execute(text(
                    "SELECT pg_backend_pid()"))).scalar_one())
                await db.execute(text(
                    "SELECT 1 FROM apps WHERE id = CAST(:p AS uuid) FOR UPDATE"), {"p": y})
                held.set()
                await release.wait()

        one = asyncio.create_task(publish_like())
        await asyncio.wait_for(held.wait(), 30)
        two = asyncio.create_task(_install(world.a, OWEN, y, "org"))
        waits: list[tuple[int, str]] = []
        end = time.monotonic() + 30
        while not waits and time.monotonic() < end and not two.done():
            waits = await asyncio.to_thread(_waiting, world, holder["pid"])
            if not waits:
                await asyncio.sleep(0.02)
        free = await asyncio.to_thread(_org_lock_free, world)
        release.set()
        await one
        return SimpleNamespace(waits=waits, free=free, answer=await two)

    out = _run(world, go)
    assert len(out.waits) == 1, out.waits
    assert "FOR KEY SHARE" in out.waits[0][1], out.waits[0][1]
    assert out.free, "the install held the org lock while it waited for the apps row"
    assert out.answer[0] == 201, out.answer
    assert len(_shared(world, y)) == 1
