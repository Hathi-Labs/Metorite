"""WS-PKG PKG-7 — ``/auth/me`` reads the slugs of the installs, on a real
database (R8).

Spec: ``project-docs/specs/package_host.md`` §10, the PKG-7 row. Code:
``gateway/routes/admin/me.py`` (``_install_slugs`` and
``resolve_features``) and ``gateway/package_host/resolve.py``
(``install_feature_slugs``). The hermetic twin is
``tests/unit/test_auth_me_features.py``.

The real handler ``get_me`` runs on a phase-4 promoted scratch database, as
the app role, through ``gateway.db.tenant_session``, so FORCE RLS binds each
statement. The installs go through ``store.install``, the one writer. The
``world`` fixture and the ``_install`` helper copy the shape of
``tests/unit/test_package_host_resolve_r8.py``. Org A has two members, Ana and
Ben. Org B has one member, Zed. Each org has one Studio package.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_auth_me_install_features_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-7).**

a. Flag on: an org install whose claim names ``feature = "fieldvisits"``
   puts it in ``features`` for a ``*`` holder and for a ``feature:*``
   holder.
b. It is in ``features_denied`` for a guest.
c. A private install of Ben adds nothing to the lists of Ana.
d. Org B never sees the slug of org A.
e. Flag off: the body equals the body without the install.
f. A bad slug is in no list.

**R7: red first, then mutate.** The mutation ran on 2026-11-06.

* M1. ``me._install_slugs``: drop the check of ``package_host_allowed``.
  ``test_r8_flag_off_the_body_is_the_body_without_the_install`` goes red, and
  so does ``test_pkg7_flag_off_never_calls_the_resolve`` of the hermetic twin.
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
from gateway.package_host import store
from gateway.package_host.store import Contribution
from gateway.routes.admin import me
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@pkg7r.test"
BEN = f"ben.{TAG}@pkg7r.test"
ZED = f"zed.{TAG}@pkg7r.test"
_SLUG_LIKE = f"pkg7r-{TAG}-%"
_EMAIL_LIKE = f"%.{TAG}@pkg7r.test"

#: The slug of the acceptance. ``feature_catalog`` and ``FEATURES`` do not
#: hold it, so only the install can put it in a list.
SLUG = "fieldvisits"
STAR = ("*",)
ALL_FEATURES = ("feature:*",)
GUEST = ("feature:chat",)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _mid(name: str) -> str:
    return f"r7-{name}-{TAG}"


def _k(name: str) -> str:
    """A claim key that no system package holds."""
    return f"{name}-{TAG}"


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A: two Studio packages with version 1, and Ana and Ben. Org B:
    one Studio package, and Zed. The flag is open for every org. Teardown
    deletes the apps, and the installs, the claims and the versions go by
    cascade."""
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    p = promoted
    apps: dict[str, str] = {}

    def app(c: Any, name: str, org: str) -> None:
        man = json.dumps({"studio": 1, "id": _mid(name), "name": "P", "purpose": "T"})
        apps[name] = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
            "format, status, manifest) VALUES (:s, 'pkg7r', :o, '', :org, 'manifest', "
            "'live', CAST(:m AS jsonb)) RETURNING id"),
            {"s": f"pkg7r-{TAG}-{name}", "o": ANA, "org": org, "m": man}).scalar_one())
        c.execute(text(
            "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
            "bundle_sha256, published_by, organization_id) VALUES "
            "(CAST(:i AS uuid), 1, CAST(:m AS jsonb), '', 'sha', :by, CAST(:o AS uuid))"),
            {"i": apps[name], "m": man, "by": ANA, "o": org})

    with p.admin_engine.begin() as c:
        app(c, "a1", p.org_a)
        app(c, "a2", p.org_a)
        app(c, "b1", p.org_b)
        for email, org in ((ANA, p.org_a), (BEN, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"),
                {"e": email, "org": org})
    yield SimpleNamespace(p=p, apps=apps)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :s"), {"s": _SLUG_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


# ── Helpers ────────────────────────────────────────────────────────────────


def _org(world: Any, tag: str) -> str:
    return world.p.org_a if tag == "a" else world.p.org_b


def _install(world: Any, app: str, scope: str, claims: list[Contribution], *,
             org: str = "a", by: str = ANA) -> str:
    """One install through the store, as the app role. Answers its id."""
    kind, installer = ("org", by) if scope == "org" else ("private", scope)

    async def go() -> str:
        async with (tenant_engine_scope(_dsn(world.p.app_url)),
                    tenant_session(_org(world, org)) as db):
            got = await store.install(
                db, app_id=world.apps[app], version=1, scope_subject=scope,
                scope_kind=kind, installed_by=installer, ends_at=None,
                contributions=claims)
            return got.id

    return asyncio.run(go())


def _claim(key: str, **payload: Any) -> Contribution:
    return Contribution(point="jobs", key=key, payload=payload, position=0)


def _me(world: Any, who: str, *, org: str = "a",
        features: tuple[str, ...] = STAR) -> dict[str, Any]:
    """Call the real ``get_me`` as *who*, with the org bound."""
    oid = _org(world, org)
    user = UserContext(email=who, role=UserRole.EMPLOYEE,
                       access=build_access(list(features)), organization_id=oid)

    async def go() -> dict[str, Any]:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            token = bind_tenant(oid)
            try:
                return await me.get_me(user=user)
            finally:
                release_tenant(token)

    return asyncio.run(go())


def _allowed(body: dict[str, Any]) -> list[str]:
    return body["features"]


def _denied(body: dict[str, Any]) -> list[str]:
    return [d["slug"] for d in body["features_denied"]]


def _anywhere(body: dict[str, Any], slug: str) -> bool:
    return slug in _allowed(body) or slug in _denied(body)


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", value)


# ── a. A `*` holder and a `feature:*` holder ───────────────────────────────


def test_r8_an_org_install_slug_reaches_a_star_and_a_feature_star_holder(world) -> None:
    before = _me(world, ANA)
    assert before["organization"]["id"] == world.p.org_a
    assert not _anywhere(before, SLUG)
    _install(world, "a1", "org", [_claim(_k("fv"), label="Field visits", feature=SLUG)])
    for who, features in ((ANA, STAR), (BEN, ALL_FEATURES)):
        body = _me(world, who, features=features)
        assert SLUG in _allowed(body), who
        assert SLUG not in _denied(body), who
        assert _allowed(body).count(SLUG) == 1


# ── b. A guest ─────────────────────────────────────────────────────────────


def test_r8_an_org_install_slug_is_denied_to_a_guest(world) -> None:
    _install(world, "a1", "org", [_claim(_k("fv"), feature=SLUG)])
    body = _me(world, BEN, features=GUEST)
    assert SLUG not in _allowed(body)
    assert {"slug": SLUG, "permission": f"feature:{SLUG}"} in body["features_denied"]


# ── c. A private install of another member ─────────────────────────────────


def test_r8_a_private_install_of_ben_adds_nothing_for_ana(world) -> None:
    mine = f"benonly{TAG}"
    before = _me(world, ANA)
    _install(world, "a1", BEN, [_claim(_k("ben"), feature=mine)], by=BEN)
    assert mine in _allowed(_me(world, BEN, features=ALL_FEATURES))
    after = _me(world, ANA)
    assert not _anywhere(after, mine)
    assert after["features"] == before["features"]
    assert after["features_denied"] == before["features_denied"]


# ── d. Two orgs ────────────────────────────────────────────────────────────


def test_r8_org_b_never_sees_the_slug_of_org_a(world) -> None:
    _install(world, "a1", "org", [_claim(_k("fv"), feature=SLUG)])
    theirs = f"orgbonly{TAG}"
    _install(world, "b1", "org", [_claim(_k("b"), feature=theirs)], org="b", by=ZED)
    zed = _me(world, ZED, org="b")
    assert zed["organization"]["id"] == world.p.org_b
    assert not _anywhere(zed, SLUG)
    assert theirs in _allowed(zed)
    assert not _anywhere(_me(world, ANA), theirs)


# ── e. The flag off ────────────────────────────────────────────────────────


def test_r8_flag_off_the_body_is_the_body_without_the_install(world, monkeypatch) -> None:
    _flag(monkeypatch, "")
    without = json.dumps(_me(world, ANA), sort_keys=True)
    _flag(monkeypatch, "*")
    assert json.dumps(_me(world, ANA), sort_keys=True) == without
    _install(world, "a1", "org", [_claim(_k("fv"), feature=SLUG)])
    assert SLUG in _allowed(_me(world, ANA))
    _flag(monkeypatch, "")
    assert json.dumps(_me(world, ANA), sort_keys=True) == without
    # The other org's id in the list keeps org A out too.
    _flag(monkeypatch, world.p.org_b)
    assert json.dumps(_me(world, ANA), sort_keys=True) == without


# ── f. A bad slug ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["Field Visits", "a:b", "*", "-x", "UPPER", "a/b"])
def test_r8_a_bad_slug_is_in_no_list(world, bad) -> None:
    _install(world, "a1", "org", [
        _claim(_k("bad"), feature=bad),
        _claim(_k("good"), feature=SLUG),
    ])
    body = _me(world, ANA)
    assert SLUG in _allowed(body)
    assert not _anywhere(body, bad)
    assert not _anywhere(body, bad.strip().lower())


def test_r8_a_slug_that_is_not_text_is_in_no_list(world) -> None:
    _install(world, "a2", "org", [
        _claim(_k("num"), feature=7),
        _claim(_k("list"), feature=["x"]),
        _claim(_k("good"), feature=SLUG),
    ])
    body = _me(world, ANA)
    assert SLUG in _allowed(body)
    assert "7" not in _allowed(body) + _denied(body)
    assert "x" not in _allowed(body) + _denied(body)
