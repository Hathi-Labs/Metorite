"""WS-PKG PKG-5b part 2 — ``GET /shell/manifest`` on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §5.2 and the PKG-5b row of §10.
Code: ``gateway/package_host/resolve.py`` and
``gateway/routes/shell/manifest.py``. The hermetic twin is
``tests/unit/test_package_host_resolve.py``.

The real handler runs on a phase-4 promoted scratch database, as the app
role, through ``gateway.db.tenant_session``, so FORCE RLS binds each
statement. The installs go through ``store.install``, the one writer. Org A
has five members and two groups: Ana and Ben are in ``ops``, Cat is in
``eng``, and Dan is in both. Eve is in no group. The ``app_user`` row of Dan
has a mixed-case address. Org B has one Studio package.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_host_resolve_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-5b part 2).**

1. A private install shows to its owner only.
2. A grantee by the share rule sees a private install. A consent row, a
   share that another member made, and an ``agent:`` subject share nothing.
3. A group install shows to the members of the group only.
4. An org install shows to each member of the org. A paused or archived
   install, an archived app, and an expired install show nothing. A draft
   shows to its owner only, also when the owner shared the app.
5. A claim whose feature the viewer lacks never shows.
6. One app at two scopes shows the widest.
7. One key of two apps: the wider scope wins, then the older install.
8. Org B sees nothing of org A, and org A nothing of org B.
9. ``needs_setup`` follows the needs, and a malformed need fails closed.
10. The version is stable, a match is a 304, and only the viewer's own
    entries move it.
11. A duplicate claim still raises (``test_package_host_store_r8.py``).

**R7: red first, then mutate.** Each mutation ran on 2026-11-04.

* M1. ``resolve.INSTALLS_SQL``: drop ``AND lower(g.subject) = :viewer``.
  ``test_r8_a_grantee_by_the_share_rule_sees_a_private_install`` goes red
  (Cat sees a share to Ben).
* M2. ``resolve.INSTALLS_SQL``: drop the draft rule
  (``version IS NOT NULL OR scope_subject = :viewer``).
  ``test_r8_an_org_install_shows_to_all_and_a_dead_one_to_none`` goes red.
* M3. ``resolve.INSTALLS_SQL``: drop ``package_install.state = 'active'``.
  The same test goes red, and so does
  ``test_r8_the_version_is_stable_and_follows_the_viewer``.
* M4. ``resolve.resolve_for``: pass ``groups=[]``.
  ``test_r8_a_group_install_shows_to_its_members_only``, the widest test and
  the winner test go red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.app_manifests.shell_manifest import load_contributes
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.package_host import store
from gateway.package_host.store import Contribution
from gateway.routes.shell import manifest
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
ANA = f"ana.{TAG}@pkg5r.test"
BEN = f"ben.{TAG}@pkg5r.test"
CAT = f"cat.{TAG}@pkg5r.test"
DAN = f"Dan.{TAG}@pkg5r.test"
EVE = f"eve.{TAG}@pkg5r.test"
ZED = f"zed.{TAG}@pkg5r.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_SLUG_LIKE = f"pkg5r-{TAG}-%"
_EMAIL_LIKE = f"%.{TAG}@pkg5r.test"
_GROUP_LIKE = f"%{TAG}"
_A_APPS = ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8")
_ALL_A = (ANA, BEN, CAT, DAN, EVE)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _mid(name: str) -> str:
    """The manifest id of the app *name*."""
    return f"r-{name}-{TAG}"


def _k(name: str) -> str:
    """A claim key that no system package holds."""
    return f"{name}-{TAG}"


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A: eight Studio packages, each with version 1, five members and
    two groups. Org B: one Studio package and one member. The flag is open
    for every org. Teardown deletes the apps, and the installs, the claims,
    the versions and the grants go by cascade."""
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    p = promoted
    apps: dict[str, str] = {}

    def app(c: Any, name: str, org: str) -> None:
        man = json.dumps({"studio": 1, "id": _mid(name), "name": "P", "purpose": "T"})
        apps[name] = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
            "format, status, manifest) VALUES (:s, 'pkg5r', :o, '', :org, 'manifest', "
            "'live', CAST(:m AS jsonb)) RETURNING id"),
            {"s": f"pkg5r-{TAG}-{name}", "o": ANA, "org": org, "m": man}).scalar_one())
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
        for email, org in ((ANA, p.org_a), (BEN, p.org_a), (CAT, p.org_a), (DAN, p.org_a),
                           (EVE, p.org_a), (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        groups: dict[str, str] = {}
        for group, members in ((OPS, (ANA, BEN, DAN)), (ENG, (CAT, DAN))):
            groups[group] = str(c.execute(text(
                "INSERT INTO org_group (organization_id, slug, display_name) "
                "VALUES (:org, :s, :s) RETURNING id"),
                {"org": p.org_a, "s": group.removeprefix("group:")}).scalar_one())
            for email in members:
                c.execute(text(
                    "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                    "VALUES (:g, :u, :org)"),
                    {"g": groups[group], "u": users[email], "org": p.org_a})
    yield SimpleNamespace(p=p, apps=apps, users=users, groups=groups)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :s"), {"s": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


# ── Helpers ────────────────────────────────────────────────────────────────


def _org(world: Any, tag: str) -> str:
    return world.p.org_a if tag == "a" else world.p.org_b


def _install(world: Any, app: str, scope: str, claims: list[Contribution], *,
             org: str = "a", version: int | None = 1, by: str = ANA,
             ends_at: datetime | None = None) -> str:
    """One install through the store, as the app role. Answers its id."""
    if scope == "org":
        kind, installer = "org", by
    elif scope.startswith("group:"):
        kind, installer = "group", by
    else:
        kind, installer = "private", scope

    async def go() -> str:
        async with (tenant_engine_scope(_dsn(world.p.app_url)),
                    tenant_session(_org(world, org)) as db):
            got = await store.install(
                db, app_id=world.apps[app], version=version, scope_subject=scope,
                scope_kind=kind, installed_by=installer, ends_at=ends_at,
                contributions=claims)
            return got.id

    return asyncio.run(go())


def _claim(key: str, point: str = "jobs", position: int = 0, **payload: Any) -> Contribution:
    return Contribution(point=point, key=key, payload=payload, position=position)


def _admin(world: Any, sql: str, params: dict[str, Any] | None = None) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), params or {})


def _grant(world: Any, app: str, subject: str, by: str) -> None:
    _admin(world, "INSERT INTO app_grants (app_id, subject, granted_by, organization_id) "
                  "VALUES (CAST(:a AS uuid), :s, :by, CAST(:o AS uuid))",
           {"a": world.apps[app], "s": subject, "by": by, "o": world.p.org_a})


def _view(world: Any, who: str, *, org: str = "a", features: tuple[str, ...] = ("feature:*",),
          etag: str | None = None) -> Any:
    """Call the real handler as *who*, with the org bound. Answers the
    status, the body and the headers."""
    oid = _org(world, org)
    user = UserContext(email=who, role=UserRole.EMPLOYEE, access=build_access(list(features)),
                       organization_id=oid)
    headers = [(b"if-none-match", etag.encode())] if etag else []
    req = Request({"type": "http", "method": "GET", "path": "/shell/manifest",
                   "headers": headers})

    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            token = bind_tenant(oid)
            try:
                return await manifest.shell_manifest(req, user)
            finally:
                release_tenant(token)

    resp = asyncio.run(go())
    body = json.loads(resp.body) if resp.status_code == 200 else None
    return SimpleNamespace(status=resp.status_code, body=body, headers=resp.headers)


def _mine(body: dict[str, Any], point: str = "jobs") -> dict[str, dict[str, Any]]:
    """The entries of installs at *point*, by key."""
    return {e["key"]: e for e in body["contributions"].get(point, [])
            if e["install_id"] is not None}


def _sees(world: Any, who: str, key: str, **kw: Any) -> bool:
    return key in _mine(_view(world, who, **kw).body)


# ── 1. Private ─────────────────────────────────────────────────────────────


def test_r8_a_private_install_shows_to_its_owner_only(world) -> None:
    iid = _install(world, "a1", ANA, [_claim(_k("k1"), label="Mine")])
    entry = _mine(_view(world, ANA).body)[_k("k1")]
    assert entry == {"key": _k("k1"), "source": "private", "install_id": iid,
                     "needs_setup": False, "label": "Mine"}
    assert [w for w in _ALL_A if _sees(world, w, _k("k1"))] == [ANA]


# ── 2. The share rule ──────────────────────────────────────────────────────


def test_r8_a_grantee_by_the_share_rule_sees_a_private_install(world) -> None:
    _install(world, "a1", ANA, [_claim(_k("k2"))])
    _grant(world, "a1", BEN, ANA.upper())       # a share by the owner
    _grant(world, "a1", DAN, ANA)               # mixed case in the grant
    _grant(world, "a1", CAT, CAT)               # a consent row
    _grant(world, "a1", EVE, BEN)               # a share by another member
    _grant(world, "a1", "agent:helper", ANA)    # an agent
    assert [w for w in _ALL_A if _sees(world, w, _k("k2"))] == [ANA, BEN, DAN]
    assert not _sees(world, "agent:helper", _k("k2"))


# ── 3. Group ───────────────────────────────────────────────────────────────


def test_r8_a_group_install_shows_to_its_members_only(world) -> None:
    _install(world, "a1", OPS, [_claim(_k("k3"))])
    assert [w for w in _ALL_A if _sees(world, w, _k("k3"))] == [ANA, BEN, DAN]
    assert _mine(_view(world, BEN).body)[_k("k3")]["source"] == "group"


# ── 4. Org, and the installs that show nothing ─────────────────────────────


def test_r8_an_org_install_shows_to_all_and_a_dead_one_to_none(world) -> None:
    now = datetime.now(UTC)
    ids = {
        "live": _install(world, "a1", "org", [_claim(_k("live"))]),
        "paused": _install(world, "a2", "org", [_claim(_k("paused"))]),
        "archived": _install(world, "a3", "org", [_claim(_k("archived"))]),
        "app_gone": _install(world, "a4", "org", [_claim(_k("app_gone"))]),
        "expired": _install(world, "a5", "org", [_claim(_k("expired"))],
                            ends_at=now + timedelta(days=1)),
        "later": _install(world, "a6", "org", [_claim(_k("later"))],
                          ends_at=now + timedelta(days=1)),
        "draft": _install(world, "a7", ANA, [_claim(_k("draft"))], version=None),
    }
    _admin(world, "UPDATE package_install SET state = 'paused' WHERE id = CAST(:i AS uuid)",
           {"i": ids["paused"]})
    _admin(world, "UPDATE package_install SET state = 'archived', archived_at = now() "
                  "WHERE id = CAST(:i AS uuid)", {"i": ids["archived"]})
    _admin(world, "UPDATE apps SET status = 'archived' WHERE id = CAST(:a AS uuid)",
           {"a": world.apps["a4"]})
    _admin(world, "UPDATE package_install SET ends_at = now() - interval '1 minute' "
                  "WHERE id = CAST(:i AS uuid)", {"i": ids["expired"]})
    _grant(world, "a7", BEN, ANA)
    for who in _ALL_A:
        keys = set(_mine(_view(world, who).body))
        want = {_k("live"), _k("later")} | ({_k("draft")} if who == ANA else set())
        assert keys == want, who


# ── 5. The feature ─────────────────────────────────────────────────────────


def test_r8_a_claim_whose_feature_the_viewer_lacks_never_shows(world) -> None:
    _install(world, "a1", "org", [_claim(_k("free")), _claim(_k("crm"), feature="crm"),
                                  _claim(_k("hr"), feature="hr")])
    assert set(_mine(_view(world, BEN, features=("feature:crm",)).body)) == {
        _k("free"), _k("crm")}
    assert set(_mine(_view(world, BEN, features=("feature:projects",)).body)) == {_k("free")}


# ── 6. One app at two scopes ───────────────────────────────────────────────


def test_r8_one_app_at_two_scopes_shows_the_widest(world) -> None:
    private = _install(world, "a1", ANA, [_claim(_k("q"))])
    org = _install(world, "a1", "org", [_claim(_k("q"))])
    group = _install(world, "a2", OPS, [_claim(_k("r"))])
    _install(world, "a2", BEN, [_claim(_k("r"))])
    ana = _mine(_view(world, ANA).body)
    assert (ana[_k("q")]["install_id"], ana[_k("q")]["source"]) == (org, "org")
    assert private != org
    ben = _mine(_view(world, BEN).body)
    assert (ben[_k("r")]["install_id"], ben[_k("r")]["source"]) == (group, "group")
    keys = [e["key"] for e in _view(world, BEN).body["contributions"]["jobs"]]
    assert len(keys) == len(set(keys))


# ── 7. One key of two apps ─────────────────────────────────────────────────


def test_r8_one_key_of_two_apps_has_one_winner(world) -> None:
    # Two groups never conflict at install. Dan is in both: the older wins.
    older = _install(world, "a1", OPS, [_claim(_k("tie"))])
    _install(world, "a2", ENG, [_claim(_k("tie"))])
    assert _mine(_view(world, DAN).body)[_k("tie")]["install_id"] == older
    # A shared private install and the viewer's own: the older wins.
    shared = _install(world, "a3", ANA, [_claim(_k("s"))])
    _grant(world, "a3", BEN, ANA)
    _install(world, "a4", BEN, [_claim(_k("s"))])
    assert _mine(_view(world, BEN).body)[_k("s")]["install_id"] == shared
    # A later group join: the group wins over the older private claim.
    _install(world, "a5", EVE, [_claim(_k("j"))])
    group = _install(world, "a6", ENG, [_claim(_k("j"))])
    _admin(world, "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                  "VALUES (:g, :u, :org)",
           {"g": world.groups[ENG], "u": world.users[EVE], "org": world.p.org_a})
    assert _mine(_view(world, EVE).body)[_k("j")]["install_id"] == group


# ── 8. Two orgs ────────────────────────────────────────────────────────────


def test_r8_an_org_sees_nothing_of_another_org(world) -> None:
    _install(world, "a1", "org", [_claim(_k("in-a"))])
    _install(world, "b1", "org", [_claim(_k("in-b"))], org="b", by=ZED)
    assert set(_mine(_view(world, ANA).body)) == {_k("in-a")}
    assert set(_mine(_view(world, ZED, org="b").body)) == {_k("in-b")}
    # An address of org A, with org B bound, still reads org B only.
    assert set(_mine(_view(world, ANA, org="b").body)) == {_k("in-b")}


# ── 9. Needs ───────────────────────────────────────────────────────────────


def _needs(world: Any, app: str, needs: Any) -> None:
    man = json.dumps({"studio": 1, "id": _mid(app), "name": "P", "purpose": "T",
                      "needs": needs})
    _admin(world, "UPDATE app_versions SET manifest = CAST(:m AS jsonb) "
                  "WHERE app_id = CAST(:a AS uuid)", {"m": man, "a": world.apps[app]})
    _admin(world, "UPDATE apps SET manifest = CAST(:m AS jsonb) WHERE id = CAST(:a AS uuid)",
           {"m": man, "a": world.apps[app]})


def test_r8_needs_setup_follows_the_needs_and_fails_closed(world) -> None:
    system = sorted({c.package for v in load_contributes().values() for c in v})[0]
    cases = {
        "a1": ([{"feature": "crm"}], False, True),
        "a2": ([{"package": system}], False, False),
        "a3": ([{"package": _mid("a8")}], False, False),
        "a4": ([{"package": _mid("a7")}], True, True),
        "a5": ([{"connection": "zoho"}], True, True),
        "a6": ({"feature": "crm"}, True, True),
    }
    for app, (needs, _, _) in cases.items():
        _needs(world, app, needs)
        _install(world, app, "org", [_claim(_k(app))])
    _install(world, "a8", "org", [])
    _install(world, "a7", EVE, [])        # visible to Eve only
    with_crm = _mine(_view(world, BEN, features=("feature:crm",)).body)
    without = _mine(_view(world, BEN, features=("feature:projects",)).body)
    for app, (_, setup_crm, setup_none) in cases.items():
        assert with_crm[_k(app)]["needs_setup"] is setup_crm, app
        assert without[_k(app)]["needs_setup"] is setup_none, app
    # A draft reads the needs of apps.manifest.
    _needs(world, "a7", [{"feature": "crm"}, "bad"])
    _install(world, "a7", ANA, [_claim(_k("draft"))], version=None)
    assert _mine(_view(world, ANA).body)[_k("draft")]["needs_setup"] is True


# ── 9b. The host of a sub-app (PKG-15a) ────────────────────────────────────


def _extends(world: Any, app: str, host: str) -> None:
    man = json.dumps({"studio": 1, "id": _mid(app), "name": "P", "purpose": "T",
                      "extends": host})
    _admin(world, "UPDATE app_versions SET manifest = CAST(:m AS jsonb) "
                  "WHERE app_id = CAST(:a AS uuid)", {"m": man, "a": world.apps[app]})
    _admin(world, "UPDATE apps SET manifest = CAST(:m AS jsonb) WHERE id = CAST(:a AS uuid)",
           {"m": man, "a": world.apps[app]})


def test_r8_a_host_that_only_another_org_holds_is_missing(world) -> None:
    """The host is an install of org A. The sub-app is an install of org B.
    Org B never sees the host, so the sub-app needs setup."""
    _extends(world, "b1", _mid("a8"))
    _install(world, "a8", "org", [])
    _install(world, "b1", "org", [_claim(_k("sub-b"))], org="b")
    entry = _mine(_view(world, ZED, org="b").body)[_k("sub-b")]
    assert entry["needs_setup"] is True
    assert entry["extends"] == _mid("a8")


def test_r8_a_host_in_the_own_org_is_met_until_it_is_paused(world) -> None:
    _extends(world, "a1", _mid("a8"))
    host = _install(world, "a8", "org", [])
    _install(world, "a1", "org", [_claim(_k("sub-a"))])
    assert _mine(_view(world, BEN).body)[_k("sub-a")]["needs_setup"] is False
    assert _mine(_view(world, BEN).body)[_k("sub-a")]["extends"] == _mid("a8")
    _admin(world, "UPDATE package_install SET state = 'paused' WHERE id = CAST(:i AS uuid)",
           {"i": host})
    assert _mine(_view(world, BEN).body)[_k("sub-a")]["needs_setup"] is True
    _admin(world, "UPDATE package_install SET state = 'archived', archived_at = now() "
                  "WHERE id = CAST(:i AS uuid)", {"i": host})
    assert _mine(_view(world, BEN).body)[_k("sub-a")]["needs_setup"] is True


def test_r8_a_host_that_is_a_system_package_is_met(world) -> None:
    system = sorted({c.package for v in load_contributes().values() for c in v})[0]
    _extends(world, "a2", system)
    _install(world, "a2", "org", [_claim(_k("sub-sys"))])
    assert _mine(_view(world, BEN).body)[_k("sub-sys")]["needs_setup"] is False


# ── 10. The version ────────────────────────────────────────────────────────


def test_r8_the_version_is_stable_and_follows_the_viewer(world) -> None:
    first = _view(world, BEN)
    v = first.body["version"]
    assert first.headers["etag"] == f'"{v}"'
    assert _view(world, BEN).body["version"] == v
    not_modified = _view(world, BEN, etag=f'"{v}"')
    assert (not_modified.status, not_modified.body) == (304, None)
    # A private install of Ana moves Ana's version, never Ben's.
    ana_before = _view(world, ANA).body["version"]
    _install(world, "a1", ANA, [_claim(_k("v1"))])
    assert _view(world, BEN).body["version"] == v
    assert _view(world, ANA).body["version"] != ana_before
    # An org install moves Ben's version, and a pause moves it back.
    iid = _install(world, "a2", "org", [_claim(_k("v2"))])
    moved = _view(world, BEN, etag=f'"{v}"')
    assert moved.status == 200 and moved.body["version"] != v
    _admin(world, "UPDATE package_install SET state = 'paused' WHERE id = CAST(:i AS uuid)",
           {"i": iid})
    assert _view(world, BEN).body["version"] == v


# ── 11. The store still refuses a duplicate claim ──────────────────────────


def test_r8_a_duplicate_claim_still_raises(world) -> None:
    _install(world, "a1", "org", [_claim(_k("dup"))])
    with pytest.raises(store.ClaimTaken):
        _install(world, "a2", OPS, [_claim(_k("dup"))])


# ── The switch on a real database ──────────────────────────────────────────


def test_r8_an_org_off_the_list_gets_404(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", world.p.org_b)
    with pytest.raises(HTTPException) as exc:
        _view(world, ANA)
    assert exc.value.status_code == 404
    assert _view(world, ZED, org="b").status == 200
