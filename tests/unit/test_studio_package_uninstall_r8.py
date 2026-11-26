"""WS-PKG PKG-8e — the org and group uninstall of a Studio app, on a real
database (R8).

Spec: ``project-docs/specs/package_host.md`` §4 (PKG-Q6, the org wins),
§5.3, §5.5, §6.1 and the PKG-8e row of §10. Decision: D180.3
(``studio:publish`` gates the share to others). Code: ``uninstall_package``
of ``gateway/routes/studio/packages.py``,
``package_host/studio_app.remove_scope_install`` and ``package_host/store.py``
(``install_at_scope`` and ``remove_install``). The hermetic twin is
``tests/unit/test_studio_package_uninstall.py``.

The real install, uninstall, publish and ``GET /shell/manifest`` handlers
run on a phase-4 promoted scratch database, as the non-privileged app role,
so FORCE RLS binds each statement. Each call binds its org as the auth layer
does. Org A has Owen, Ana and Ben in ``ops`` and Cat in ``eng``. Org B has
Zed.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_package_uninstall_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-8e).**

a. Owen holds ``studio:publish`` and removes the org install of his app. The
   nav entry leaves each member of org A at the next read. The install row
   and each claim are gone, and Owen's private install stays.
b. The same for a group install: the members of the group no longer see it.
c. PKG-Q6 (a): Ana's private install of app X holds the key of app Y. While
   the org install of Y holds that key, Ana sees Y. After the uninstall, Ana
   sees her own entry of X at that key again.
d. The removal deletes the extension values of the install in the same
   transaction, by the cascade (PKG-13).
e. A refusal writes nothing: no verb (403), not the owner (404), a bad
   scope (422), no install at the scope (404), and org B (404). The install
   and its claims stay, and no audit row of the uninstall goes in. A
   failure after the DELETE (the audit write) keeps the install and its
   claims, because the removal and the audit row are one transaction.
f. Org B cannot remove an install of org A, and org A cannot remove an
   install of org B.
g. A removal writes one ``app_audit`` row: who, the scope and the version.
h. Two removals at once give one 200 and one 404.

**R7: red first, then mutate.** M1. ``uninstall_package``: drop the
``studio:publish`` check. Case e goes red. M2. ``remove_scope_install``:
skip ``store.remove_install``. Cases a, b, c and d go red. M3. Move the
audit call above the removal and swallow its refusal. Case e goes red with
an audit row.
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
from fastapi import HTTPException, Response
from gateway.package_host import store
from gateway.routes.shell import manifest as shell_manifest
from gateway.routes.studio import packages
from sqlalchemy import text
from starlette.requests import Request

from tests.unit._studio_approval import approve_live
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@pkg8e.test"
ANA = f"ana.{TAG}@pkg8e.test"
BEN = f"ben.{TAG}@pkg8e.test"
CAT = f"cat.{TAG}@pkg8e.test"
ZED = f"zed.{TAG}@pkg8e.test"
#: The approver of an org install (WS-STU J2b-1, path A): not the owner.
APPROVER = f"approver.{TAG}@pkg8e.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_EMAIL_LIKE = f"%.{TAG}@pkg8e.test"
_GROUP_LIKE = f"%{TAG}"
_SLUG_LIKE = f"p8e{TAG}-%"
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
    return f"p8e{TAG}-app{next(_COUNTER)}"


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
    if route in ("create_package", "publish", "install_package", "uninstall_package"):
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


async def _install(org: str, email: str, pid: str, scope: Any) -> tuple[int, Any]:
    if scope == "org":
        # WS-STU J2b-1 (path A): an org install needs the live version approved.
        await approve_live(org, email, pid, APPROVER)
    return await _call("install_package", org, email, pid, payload={"scope": scope},
                       grants=PUBLISHER)


async def _uninstall(org: str, email: str, pid: str, scope: Any,
                     grants: tuple[str, ...] = PUBLISHER) -> tuple[int, Any]:
    return await _call("uninstall_package", org, email, pid, payload={"scope": scope},
                       grants=grants)


async def _seed(org: str, *, app: str, scope: str, member: str, key: str,
                version: int | None = 1) -> store.Install:
    """One install of *app* at *scope* that claims the nav key *key*, as the
    app role."""
    kind = "org" if scope == "org" else "group" if scope.startswith("group:") else "private"
    async with tenant_session(org) as db:
        return await store.install(
            db, app_id=app, version=version, scope_subject=scope, scope_kind=kind,
            installed_by=member, ends_at=None, contributions=[store.Contribution(
                point="nav", key=key, payload={"href": key, "label": "Squat",
                                               "feature": "studio"})])


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio on for both and the package host on for
    org A only. The members and the groups are seeded as the superuser.
    Every package that a test makes has a slug that starts with
    ``p8e<tag>``, and the teardown removes it. The installs, the claims and
    the values go by cascade."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", p.org_a)
    with p.admin_engine.begin() as c:
        users: dict[str, str] = {}
        for email, org in ((OWEN, p.org_a), (ANA, p.org_a), (BEN, p.org_a), (CAT, p.org_a),
                           (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        for group, members in ((OPS, (OWEN, ANA, BEN)), (ENG, (CAT,))):
            gid = str(c.execute(text(
                "INSERT INTO org_group (organization_id, slug, display_name) "
                "VALUES (:org, :s, :s) RETURNING id"),
                {"org": p.org_a, "s": group.removeprefix("group:")}).scalar_one())
            for email in members:
                c.execute(text(
                    "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                    "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": p.org_a})
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
            "SELECT id::text, organization_id::text, version, scope_kind, scope_subject "
            "FROM package_install WHERE app_id = CAST(:p AS uuid) ORDER BY created_at"),
            {"p": pid}).all()
    keys = ("id", "org", "version", "kind", "subject")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def _shared(world: Any, pid: str) -> list[dict[str, Any]]:
    """The org and group installs of *pid*."""
    return [i for i in _installs(world, pid) if i["kind"] != "private"]


def _claims(world: Any, install_id: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM package_contribution WHERE install_id = CAST(:i AS uuid)"),
            {"i": install_id}).scalar_one())


def _values(world: Any, install_id: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM package_field_value WHERE install_id = CAST(:i AS uuid)"),
            {"i": install_id}).scalar_one())


def _audits(world: Any, pid: str) -> list[dict[str, Any]]:
    """The audit rows of the uninstall route for *pid*, oldest first."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT user_email, kind, app_version, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) AND detail->>'op' = 'package.uninstall' "
            "ORDER BY id"), {"p": pid}).all()
    return [{"by": r[0], "kind": r[1], "app_version": r[2],
             "detail": r[3] if not isinstance(r[3], str) else json.loads(r[3])} for r in rows]


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


# ── a, g. The org uninstall ────────────────────────────────────────────────


def test_r8_an_org_uninstall_takes_the_entry_from_each_member(world) -> None:
    y = _run(world, lambda: _live(world.a, OWEN))
    assert _run(world, lambda: _install(world.a, OWEN, y, "org"))[0] == 201
    [org_install] = _shared(world, y)
    href = f"/apps/{y}"
    for who in (ANA, BEN, CAT):
        assert _view(world, who, world.a).nav[href]["source"] == "org", who

    status, got = _run(world, lambda: _uninstall(world.a, OWEN, y, "org"))
    assert status == 200, got
    assert got == {"removed": True, "install": {
        "id": org_install["id"], "app_id": y, "version": 1, "scope_kind": "org",
        "scope_subject": "org"}}
    assert _shared(world, y) == []
    assert _claims(world, org_install["id"]) == 0
    for who in (ANA, BEN, CAT):
        assert href not in _view(world, who, world.a).nav, who
    # Owen's private install of the publish stays, and he sees it.
    assert [i["kind"] for i in _installs(world, y)] == ["private"]
    assert _view(world, OWEN, world.a).nav[href]["source"] == "private"
    # g. One audit row.
    assert _audits(world, y) == [{"by": OWEN, "kind": "action", "app_version": 1, "detail": {
        "op": "package.uninstall", "scope_kind": "org", "scope": "org", "version": 1}}]


# ── b. The group uninstall ─────────────────────────────────────────────────


def test_r8_a_group_uninstall_takes_the_entry_from_the_members_of_the_group(world) -> None:
    y = _run(world, lambda: _live(world.a, CAT))
    assert _run(world, lambda: _install(world.a, CAT, y, OPS))[0] == 201
    href = f"/apps/{y}"
    for who in (ANA, BEN, OWEN):
        assert _view(world, who, world.a).nav[href]["source"] == "group", who
    # The case of the scope folds, as at install.
    status, got = _run(world, lambda: _uninstall(world.a, CAT, y, OPS.upper()))
    assert status == 200, got
    assert got["install"]["scope_subject"] == OPS
    assert _shared(world, y) == []
    for who in (ANA, BEN, OWEN):
        assert href not in _view(world, who, world.a).nav, who


# ── c. PKG-Q6 (a): the private claim shows again ───────────────────────────


@pytest.mark.parametrize("wider", ["org", "ops"])
def test_r8_a_private_claim_that_the_wider_claim_hid_shows_again(world, wider: str) -> None:
    scope = "org" if wider == "org" else OPS

    async def go() -> tuple[str, store.Install]:
        y = await _live(world.a, OWEN)
        x = await _new(world.a, ANA)
        held = await _seed(world.a, app=x, scope=ANA, member=ANA, key=f"/apps/{y}",
                           version=None)
        assert (await _install(world.a, OWEN, y, scope))[0] == 201
        return y, held

    y, held = _run(world, go)
    href = f"/apps/{y}"
    assert _view(world, ANA, world.a).nav[href]["source"] in ("org", "group")
    status, got = _run(world, lambda: _uninstall(world.a, OWEN, y, scope))
    assert status == 200, got
    entry = _view(world, ANA, world.a).nav[href]
    assert (entry["source"], entry["install_id"], entry["label"]) == (
        "private", held.id, "Squat")


# ── d. The extension values go by the cascade ──────────────────────────────


def test_r8_the_extension_values_of_the_install_go_in_the_same_transaction(world) -> None:
    y = _run(world, lambda: _live(world.a, OWEN))
    assert _run(world, lambda: _install(world.a, OWEN, y, "org"))[0] == 201
    [org_install] = _shared(world, y)
    [private] = [i for i in _installs(world, y) if i["kind"] == "private"]
    with world.p.admin_engine.begin() as c:
        for install in (org_install["id"], private["id"]):
            c.execute(text(
                "INSERT INTO package_field_value (organization_id, install_id, record_kind, "
                "record_id, field_key, field_type, value) VALUES (CAST(:org AS uuid), "
                "CAST(:i AS uuid), 'task', gen_random_uuid(), 'site', 'text', '\"x\"')"),
                {"org": world.a, "i": install})
    assert _values(world, org_install["id"]) == 1
    assert _run(world, lambda: _uninstall(world.a, OWEN, y, "org"))[0] == 200
    assert _values(world, org_install["id"]) == 0
    # The value of the private install stays.
    assert _values(world, private["id"]) == 1


# ── e. A refusal writes nothing ────────────────────────────────────────────


def test_r8_each_refusal_writes_nothing(world, monkeypatch) -> None:
    async def go() -> dict[str, Any]:
        y = await _live(world.a, OWEN)
        assert (await _install(world.a, OWEN, y, "org"))[0] == 201
        return {
            "pid": y,
            "no verb": await _uninstall(world.a, OWEN, y, "org", STUDIO),
            "not the owner": await _uninstall(world.a, BEN, y, "org"),
            "an address": await _uninstall(world.a, OWEN, y, OWEN),
            "no scope": await _call("uninstall_package", world.a, OWEN, y, {}, PUBLISHER),
            "no install at the scope": await _uninstall(world.a, OWEN, y, ENG),
            "a group of no install": await _uninstall(world.a, OWEN, y, f"group:none{TAG}"),
        }

    got = _run(world, go)
    y = got.pop("pid")
    want = {"no verb": 403, "not the owner": 404, "an address": 422, "no scope": 422,
            "no install at the scope": 404, "a group of no install": 404}
    assert {k: s for k, (s, _) in got.items()} == want, got
    assert got["no verb"][1]["code"] == "forbidden"
    assert got["not the owner"][1] == "Not found"
    for case in ("no install at the scope", "a group of no install"):
        assert got[case][1]["code"] == "install_not_found", case
    [org_install] = _shared(world, y)
    assert _claims(world, org_install["id"]) == 1
    assert _audits(world, y) == []
    for who in (ANA, BEN):
        assert f"/apps/{y}" in _view(world, who, world.a).nav, who


def test_r8_a_failure_after_the_delete_keeps_the_install_and_its_claims(
    world, monkeypatch,
) -> None:
    """One transaction: the audit INSERT fails after the DELETE ran, so the
    install row and each claim stay, and no audit row goes in."""
    y = _run(world, lambda: _live(world.a, OWEN))
    assert _run(world, lambda: _install(world.a, OWEN, y, "org"))[0] == 201
    [org_install] = _shared(world, y)

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("the audit write failed")

    monkeypatch.setattr(packages, "record_app_audit_in", broken)
    with pytest.raises(RuntimeError, match="audit write failed"):
        _run(world, lambda: _uninstall(world.a, OWEN, y, "org"))
    assert _shared(world, y) == [org_install]
    assert _claims(world, org_install["id"]) == 1
    assert _audits(world, y) == []
    assert f"/apps/{y}" in _view(world, ANA, world.a).nav


# ── f. Two orgs ────────────────────────────────────────────────────────────


def test_r8_org_b_cannot_remove_an_install_of_org_a(world, monkeypatch) -> None:
    y = _run(world, lambda: _live(world.a, OWEN))
    assert _run(world, lambda: _install(world.a, OWEN, y, "org"))[0] == 201
    # With the host off for org B, the route is 404 before any session.
    assert _run(world, lambda: _uninstall(world.b, ZED, y, "org")) == (404, "Not found")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{world.a},{world.b}")
    for who in (ZED, OWEN):
        status, got = _run(world, lambda who=who: _uninstall(world.b, who, y, "org"))
        assert (status, got) == (404, "Not found"), who
    [org_install] = _shared(world, y)
    assert org_install["org"] == world.a and _claims(world, org_install["id"]) == 1
    assert _audits(world, y) == []


def test_r8_org_a_cannot_remove_an_install_of_org_b(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{world.a},{world.b}")
    z = _run(world, lambda: _live(world.b, ZED))
    assert _run(world, lambda: _install(world.b, ZED, z, "org"))[0] == 201
    for who in (OWEN, ZED):
        status, got = _run(world, lambda who=who: _uninstall(world.a, who, z, "org"))
        assert (status, got) == (404, "Not found"), who
    [org_install] = _shared(world, z)
    assert org_install["org"] == world.b and _claims(world, org_install["id"]) == 1
    assert _audits(world, z) == []
    other = _view(world, ZED, world.b)
    assert other.status == 200 and f"/apps/{z}" in other.nav


# ── h. Two removals at once ────────────────────────────────────────────────


def test_r8_two_removals_at_once_give_one_200_and_one_404(world) -> None:
    y = _run(world, lambda: _live(world.a, OWEN))
    assert _run(world, lambda: _install(world.a, OWEN, y, "org"))[0] == 201

    async def go() -> list[tuple[int, Any]]:
        return list(await asyncio.gather(
            _uninstall(world.a, OWEN, y, "org"), _uninstall(world.a, OWEN, y, "org")))

    got = sorted(s for s, _ in _run(world, go))
    assert got == [200, 404], got
    assert _shared(world, y) == []
    assert len(_audits(world, y)) == 1
