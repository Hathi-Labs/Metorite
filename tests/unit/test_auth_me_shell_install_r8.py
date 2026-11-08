"""WS-PKG PKG-6c-1 — a star for an install pane, on a real database (R8).

Spec: ``project-docs/specs/package_host.md``, the PKG-6c-1 row of §10.
Code: ``put_my_shell`` and ``stored_shell`` of ``routes/admin/me.py``, and
``gateway/package_host/shell_pins.py``. The hermetic half is
``tests/unit/test_auth_me_shell.py::TestAnInstallPane``.

Each save runs as the app role (no superuser, no BYPASSRLS), through the
tenant session, so FORCE RLS binds each statement. Each check of rows reads
as the superuser. Org A has three members, one group and five Studio
packages with version 1. Org B has one member and one Studio package.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_auth_me_shell_install_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-6c-1).**

a. A member of org A stores the href of an org install of A, and reads it
   back. A member of org B who sends the same href has it dropped.
b. Member Y of org A cannot store the href of the private install of
   member X. X can.
c. A group install is stored only by a member of the group.
d. After a pause, a removal or an archive of the app, the next save drops
   the href.
e. With the flag off, an install href answers 422 and the row stays.
f. A fault in the resolve answers 503 and the row stays.
g. A claim with a feature that the member lacks does not count.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.package_host import resolve, store
from gateway.package_host.store import Contribution, InstallError
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@pkg6c.test"
BEN = f"ben.{TAG}@pkg6c.test"
CAT = f"cat.{TAG}@pkg6c.test"
ZED = f"zed.{TAG}@pkg6c.test"
OPS = f"group:ops{TAG}"
_SLUG_LIKE = f"pkg6c-{TAG}-%"
_EMAIL_LIKE = f"%.{TAG}@pkg6c.test"
_GROUP_LIKE = f"%{TAG}"
_A_APPS = ("a1", "a2", "a3", "a4", "a5")
_PEOPLE = (ANA, BEN, CAT, ZED)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _href(name: str) -> str:
    """An href that no system pane owns."""
    return f"/apps/{name}-{TAG}"


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """The rows of both orgs, and ``PACKAGE_HOST_ORGS`` naming both. The
    teardown deletes the apps (the installs and the claims go by cascade),
    the group, the members and their layouts."""
    p = promoted
    apps: dict[str, str] = {}

    def app(c: Any, name: str, org: str) -> None:
        man = json.dumps({"studio": 1, "id": f"r-{name}-{TAG}", "name": "P", "purpose": "T"})
        apps[name] = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
            "format, status, manifest) VALUES (:s, 'pkg6c', :o, '', :org, 'manifest', "
            "'live', CAST(:m AS jsonb)) RETURNING id"),
            {"s": f"pkg6c-{TAG}-{name}", "o": ANA, "org": org, "m": man}).scalar_one())
        c.execute(text(
            "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
            "bundle_sha256, published_by, organization_id) VALUES "
            "(CAST(:i AS uuid), 1, CAST(:m AS jsonb), '', 'sha', :by, CAST(:o AS uuid))"),
            {"i": apps[name], "m": man, "by": ANA, "o": org})

    with p.admin_engine.begin() as c:
        for name in _A_APPS:
            app(c, name, p.org_a)
        app(c, "b1", p.org_b)
        users: dict[str, str] = {}
        for email, org in ((ANA, p.org_a), (BEN, p.org_a), (CAT, p.org_a), (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name) "
            "VALUES (:org, :s, :s) RETURNING id"),
            {"org": p.org_a, "s": OPS.removeprefix("group:")}).scalar_one())
        for email in (ANA, BEN):
            c.execute(text(
                "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": p.org_a})
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    yield SimpleNamespace(p=p, apps=apps)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :s"), {"s": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text("DELETE FROM user_settings WHERE user_id LIKE :e"), {"e": _EMAIL_LIKE})
        c.execute(text("DELETE FROM people WHERE email LIKE :e"), {"e": _EMAIL_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


# ── Helpers ────────────────────────────────────────────────────────────────


def _org(world: Any, tag: str) -> str:
    return world.p.org_a if tag == "a" else world.p.org_b


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the engine of the app role."""

    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()

    return asyncio.run(go())


def _user(world: Any, email: str, tag: str, perms: tuple[str, ...] = ()) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=_org(world, tag),
                       access=build_access(["feature:tasks", *perms], roles=["member"]))


def _nav(href: str, feature: str | None = None) -> Contribution:
    payload: dict[str, Any] = {"href": href, "label": "Field visits"}
    if feature is not None:
        payload["feature"] = feature
    return Contribution(point="nav", key=href, payload=payload)


def _install(world: Any, app: str, scope: str, claims: list[Contribution], *,
             org: str = "a", by: str = ANA) -> Any:
    """One install through the store, as the app role."""
    kind = "org" if scope == "org" else "group" if scope.startswith("group:") else "private"
    installer = scope if kind == "private" else by

    async def go() -> Any:
        async with tenant_session(_org(world, org)) as db:
            return await store.install(
                db, app_id=world.apps[app], version=1, scope_subject=scope,
                scope_kind=kind, installed_by=installer, ends_at=None,
                contributions=claims)

    got = _go(world, go)
    assert isinstance(got, store.Install), got
    return got


def _dispose(world: Any, fn: Any, install_id: str) -> Any:
    async def go() -> Any:
        try:
            async with tenant_session(world.p.org_a) as db:
                return await fn(db, install_id=install_id)
        except InstallError as exc:
            return exc

    return _go(world, go)


def _put(world: Any, user: UserContext, body: dict[str, Any]) -> Any:
    """``PUT /auth/me/shell`` as the member, in the member's org. Answers the
    body, or the ``HTTPException``."""
    from gateway.routes.admin.me import put_my_shell

    async def go() -> Any:
        token = bind_tenant(user.organization_id)
        try:
            return await put_my_shell(body=body, user=user)
        except HTTPException as exc:
            return exc
        finally:
            release_tenant(token)

    return _go(world, go)


def _get(world: Any, user: UserContext) -> dict[str, Any]:
    from gateway.routes.admin.me import get_my_shell

    async def go() -> dict[str, Any]:
        token = bind_tenant(user.organization_id)
        try:
            return await get_my_shell(user=user)
        finally:
            release_tenant(token)

    return _go(world, go)


def _stored(world: Any, email: str, tag: str = "a") -> Any:
    """The stored ``shell_prefs`` of *email* in the org, as the superuser."""
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT shell_prefs FROM user_settings "
            "WHERE user_id = :u AND organization_id = CAST(:o AS uuid)"),
            {"u": email, "o": _org(world, tag)}).first()
    return None if row is None else row[0]


def _layout(pins: list[str], known: list[str] | None = None) -> dict[str, Any]:
    return {"preset": "engineer", "answered": "answered", "pins": pins,
            "known": known if known is not None else pins}


# ── a. An org install, and the other org ───────────────────────────────────


def test_r8_an_org_install_is_stored_in_its_org_and_dropped_in_the_other(world) -> None:
    h = _href("org")
    _install(world, "a1", "org", [_nav(h)])
    ana = _user(world, ANA, "a")
    got = _put(world, ana, _layout(["/tasks", h]))
    assert isinstance(got, dict), got
    assert got["pins"] == ["/tasks", h] and got["known"] == ["/tasks", h]
    assert _stored(world, ANA)["pins"] == ["/tasks", h]
    again = _get(world, ana)
    assert again["pins"] == ["/tasks", h] and again["known"] == ["/tasks", h]

    zed = _user(world, ZED, "b")
    got = _put(world, zed, _layout(["/tasks", h]))
    assert isinstance(got, dict), got
    assert got["pins"] == ["/tasks"] and got["known"] == ["/tasks"]
    assert _stored(world, ZED, "b")["pins"] == ["/tasks"]
    # The same address in org B has no install of org A either.
    got = _put(world, _user(world, ANA, "b"), _layout([h]))
    assert isinstance(got, dict) and got["pins"] == [], got


# ── b. A private install ───────────────────────────────────────────────────


def test_r8_a_member_cannot_store_the_private_install_of_another(world) -> None:
    h = _href("mine")
    _install(world, "a2", ANA, [_nav(h)])
    got = _put(world, _user(world, BEN, "a"), _layout(["/tasks", h]))
    assert isinstance(got, dict), got
    assert got["pins"] == ["/tasks"]
    assert _stored(world, BEN)["pins"] == ["/tasks"]
    assert _stored(world, BEN)["known"] == ["/tasks"]
    got = _put(world, _user(world, ANA, "a"), _layout([h]))
    assert isinstance(got, dict) and got["pins"] == [h], got


# ── c. A group install ─────────────────────────────────────────────────────


def test_r8_a_group_install_is_stored_by_a_group_member_only(world) -> None:
    h = _href("ops")
    _install(world, "a3", OPS, [_nav(h)])
    got = _put(world, _user(world, BEN, "a"), _layout([h]))
    assert isinstance(got, dict) and got["pins"] == [h], got
    got = _put(world, _user(world, CAT, "a"), _layout([h, "/projects"]))
    assert isinstance(got, dict) and got["pins"] == ["/projects"], got
    assert _stored(world, CAT)["pins"] == ["/projects"]


# ── d. A dead install ──────────────────────────────────────────────────────


def test_r8_after_a_pause_a_removal_or_an_archive_the_next_save_drops_the_href(world) -> None:
    hp, hr, ha = _href("paused"), _href("removed"), _href("archived")
    paused = _install(world, "a1", "org", [_nav(hp)])
    removed = _install(world, "a2", "org", [_nav(hr)])
    _install(world, "a4", "org", [_nav(ha)])
    ana = _user(world, ANA, "a")
    got = _put(world, ana, _layout([hp, hr, ha]))
    assert isinstance(got, dict) and got["pins"] == [hp, hr, ha], got

    assert _dispose(world, store.pause_install, paused.id) is True
    assert _dispose(world, store.remove_install, removed.id) is True
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE apps SET status = 'archived' WHERE id = CAST(:i AS uuid)"),
                  {"i": world.apps["a4"]})
    got = _put(world, ana, _layout([hp, hr, ha, "/tasks"]))
    assert isinstance(got, dict), got
    assert got["pins"] == ["/tasks"] and got["known"] == ["/tasks"]
    assert _stored(world, ANA)["pins"] == ["/tasks"]

    # A resume brings the install back, and the next save keeps it.
    assert _dispose(world, store.resume_install, paused.id) is True
    got = _put(world, ana, _layout([hp]))
    assert isinstance(got, dict) and got["pins"] == [hp], got


# ── e. The flag off ────────────────────────────────────────────────────────


def test_r8_with_the_flag_off_an_install_href_is_422_and_the_row_stays(world, monkeypatch) -> None:
    h = _href("dark")
    _install(world, "a1", "org", [_nav(h)])
    ana = _user(world, ANA, "a")
    got = _put(world, ana, _layout([h]))
    assert isinstance(got, dict) and got["pins"] == [h], got
    before = _stored(world, ANA)

    monkeypatch.setattr(get_settings(), "package_host_orgs", world.p.org_b)
    got = _put(world, ana, _layout(["/tasks", h]))
    assert isinstance(got, HTTPException) and got.status_code == 422, got
    assert _stored(world, ANA) == before
    # The read drops the stored install href while the flag is off.
    assert _get(world, ana)["pins"] == []
    monkeypatch.setattr(get_settings(), "package_host_orgs", world.p.org_a)
    assert _get(world, ana)["pins"] == [h]


# ── f. A fault in the resolve ──────────────────────────────────────────────


def test_r8_a_fault_in_the_resolve_is_503_and_writes_nothing(world, monkeypatch) -> None:
    h = _href("fault")
    _install(world, "a1", "org", [_nav(h)])
    ana = _user(world, ANA, "a")
    got = _put(world, ana, _layout(["/tasks"]))
    assert isinstance(got, dict), got
    before = _stored(world, ANA)

    async def broken(db: Any, *, viewer: str, can_use: Any) -> dict[str, Any]:
        await db.execute(text("SELECT 1"))
        raise RuntimeError("resolve away")

    monkeypatch.setattr(resolve, "resolve_for", broken)
    got = _put(world, ana, _layout(["/projects", h]))
    assert isinstance(got, HTTPException) and got.status_code == 503, got
    assert _stored(world, ANA) == before


# ── g. A feature the member lacks ──────────────────────────────────────────


def test_r8_a_claim_with_a_feature_the_member_lacks_does_not_count(world) -> None:
    h = _href("gated")
    _install(world, "a5", "org", [_nav(h, feature="studio")])
    got = _put(world, _user(world, BEN, "a"), _layout([h]))
    assert isinstance(got, dict) and got["pins"] == [], got
    got = _put(world, _user(world, BEN, "a", ("feature:studio",)), _layout([h]))
    assert isinstance(got, dict) and got["pins"] == [h], got
