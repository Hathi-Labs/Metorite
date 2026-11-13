"""WS-STU J2b-5 — the per-person unshare of a Studio app, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 J2b-5. Code:
``DELETE /studio/packages/{id}/share/{email}`` (``routes/studio/packages.py``
``unshare_package``), ``package_host/promotion.unshare_person``, the share
of J2b-2 and the read of a viewer of J2a (``GET /studio/packages/{id}``).
The hermetic twin is ``tests/unit/test_studio_package_unshare_routes.py``.

The real create, publish, share, unshare and package handlers run on a
phase-4 promoted scratch database, as the non-privileged app role, so FORCE
RLS binds each statement. Each call binds its org as the auth layer does.
Org A has Owen (the owner, a holder of ``studio:publish``), Pam (a second
holder), Ana, Ben and Cat. Org B has Zed. Ana's address is in mixed case.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_package_unshare_routes_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J2b-5).**

a. Owen shares with Ana and Ben, then takes the share back from Ana: 200
   ``removed: true``. Ana's viewer read is 404 again, and Ben still reads.
   A repeat is 200 ``removed: false``.
b. A consent row of Cat (``granted_by`` Cat) stays, and the answer is
   ``removed: false``.
c. A share row that Pam made for Ana stays when Owen takes his share back.
d. A member who does not own the package gets 404, and an owner without
   ``studio:publish`` gets 403. Neither removes a row or writes an audit row.
e. Org B gets 404 and removes nothing.
f. A paused install, and an install with an end date, still unshare.
g. Each unshare writes one ``app_audit`` row (op ``package.unshare``) in org
   A, with only the op, the install, the person and ``removed``.
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
from fastapi import HTTPException
from gateway.package_host import promotion, store
from gateway.routes.studio import invoke, packages
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@j2b5.test"
PAM = f"pam.{TAG}@j2b5.test"
#: Mixed case in ``app_user`` and in the path, so each read folds it.
ANA = f"Ana.{TAG}@j2b5.test"
ANA_LOW = ANA.lower()
BEN = f"ben.{TAG}@j2b5.test"
CAT = f"cat.{TAG}@j2b5.test"
ZED = f"zed.{TAG}@j2b5.test"
_EMAIL_LIKE = f"%.{TAG}@j2b5.test"
_SLUG_LIKE = f"j2b5{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
NOT_FOUND = "Not found"


def _manifest(mid: str) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": "Site visits", "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [
            {"id": "find", "uses": "object:visit.list"},
            {"id": "add", "uses": "object:visit.create"},
        ],
    }


def _mid() -> str:
    return f"j2b5{TAG}-app{next(_COUNTER)}"


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The parts of a request that the handlers read."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/packages/x")
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield self._raw


_BODY_ROUTES = ("create_package", "publish", "share_package")


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO,
                extra: dict[str, Any] | None = None) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants), **(extra or {})}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in _BODY_ROUTES:
        kwargs["request"] = _Request(payload)
    token = bind_tenant(org)
    try:
        return 200, await handler(**kwargs)
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _share(org: str, email: str, pid: str, people: Any,
                 grants: tuple[str, ...] = PUBLISHER) -> tuple[int, Any]:
    return await _call("share_package", org, email, pid, {"people": people}, grants)


async def _unshare(org: str, email: str, pid: str, person: str,
                   grants: tuple[str, ...] = PUBLISHER) -> tuple[int, Any]:
    return await _call("unshare_package", org, email, pid, grants=grants,
                       extra={"email": person})


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _live(org: str) -> str:
    """A new package of Owen, published once from the IDE: a kept private install."""
    status, got = await _call("create_package", org, OWEN, payload=_manifest(_mid()))
    assert status == 200, got
    pid = str(got["package"]["id"])
    status, got = await _call("publish", org, OWEN, pid, payload={})
    assert status == 200, got
    return pid


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the package host on for both. Owen
    publishes one app, so he holds a kept private install of it."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        for email, org in ((OWEN, p.org_a), (PAM, p.org_a), (ANA, p.org_a), (BEN, p.org_a),
                           (CAT, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"), {"e": email, "org": org})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b)

    async def setup() -> None:
        w.pid = await _live(w.a)
        async with tenant_session(w.a) as db:
            w.install = await store.private_install_id(db, app_id=w.pid, member=OWEN)
        assert w.install is not None
        status, got = await _share(w.a, OWEN, w.pid, [ANA, BEN])
        assert status == 200, got

    _run(w, setup)
    yield w
    invoke._READS_IN_FLIGHT.clear()
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_grants", "app_id"), ("package_install", "app_id"),
                           ("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def _admin(world: Any, sql: str, **params: Any) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), params)


def _grants(world: Any, pid: str) -> list[tuple[str, str, str]]:
    """The grant rows of *pid*, read as the superuser, which RLS does not bind."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, subject, granted_by FROM app_grants "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY subject"), {"p": pid}).all()
    return [tuple(str(v) for v in r) for r in rows]  # type: ignore[misc]


def _unshare_audits(world: Any, pid: str) -> list[tuple[str, str, dict[str, Any]]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, kind, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) AND detail->>'op' = 'package.unshare' "
            "ORDER BY id"), {"p": pid}).all()
    return [(r[0], r[1], r[2] if isinstance(r[2], dict) else json.loads(r[2])) for r in rows]


def _both(world: Any) -> list[tuple[str, str, str]]:
    return [(world.a, ANA_LOW, OWEN), (world.a, BEN, OWEN)]


# ── a. Owen takes the share back from Ana, and Ben keeps it ────────────────


def test_r8_the_owner_unshares_one_person_and_the_other_keeps_the_app(world) -> None:
    async def go() -> dict[str, tuple[int, Any]]:
        out = {"ana_before": await _call("get_package", world.a, ANA, world.pid)}
        out["unshare"] = await _unshare(world.a, OWEN, world.pid, ANA)
        out["ana"] = await _call("get_package", world.a, ANA, world.pid)
        out["ben"] = await _call("get_package", world.a, BEN, world.pid)
        out["again"] = await _unshare(world.a, OWEN, world.pid, ANA_LOW)
        return out

    got = _run(world, go)
    assert got["ana_before"][0] == 200, got["ana_before"]
    assert got["unshare"] == (200, {"removed": True})
    assert got["ana"] == (404, NOT_FOUND)
    status, body = got["ben"]
    assert status == 200 and body["package"]["access"] == "viewer", body
    assert got["again"] == (200, {"removed": False})
    assert _grants(world, world.pid) == [(world.a, BEN, OWEN)]


# ── b. A consent row stays ─────────────────────────────────────────────────


def test_r8_a_consent_row_stays(world) -> None:
    _admin(world, "INSERT INTO app_grants (app_id, subject, granted_by, organization_id) "
                  "VALUES (CAST(:a AS uuid), :s, :s, CAST(:o AS uuid))",
           a=world.pid, s=CAT, o=world.a)
    got = _run(world, lambda: _unshare(world.a, OWEN, world.pid, CAT))
    assert got == (200, {"removed": False})
    assert (world.a, CAT, CAT) in _grants(world, world.pid)


# ── c. A row of another sharer stays ───────────────────────────────────────


def test_r8_a_share_of_another_owner_stays(world) -> None:
    """Pam holds a private install of the same app. Owen takes his share of
    Ana back, then Pam shares with Ana. Owen's second unshare of Ana finds
    only Pam's row, and leaves it."""
    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        first = await _unshare(world.a, OWEN, world.pid, ANA)
        async with tenant_session(world.a) as db:
            made = await store.install(db, app_id=world.pid, version=1, scope_subject=PAM,
                                       scope_kind="private", installed_by=PAM, ends_at=None,
                                       contributions=[])
        async with tenant_session(world.a) as db:
            await promotion.share_with_people(db, install_id=made.id, emails=[ANA],
                                              sharer=PAM)
        return first, await _unshare(world.a, OWEN, world.pid, ANA)

    first, second = _run(world, go)
    assert first == (200, {"removed": True})
    assert second == (200, {"removed": False})
    assert _grants(world, world.pid) == [(world.a, ANA_LOW, PAM), (world.a, BEN, OWEN)]


# ── d. Who may unshare ─────────────────────────────────────────────────────


def test_r8_a_member_who_is_not_the_owner_gets_404(world) -> None:
    status, got = _run(world, lambda: _unshare(world.a, PAM, world.pid, ANA))
    assert (status, got) == (404, NOT_FOUND)
    assert _grants(world, world.pid) == _both(world)
    assert _unshare_audits(world, world.pid) == []


def test_r8_an_owner_without_the_share_verb_gets_403(world) -> None:
    status, got = _run(world, lambda: _unshare(world.a, OWEN, world.pid, ANA, grants=STUDIO))
    assert status == 403 and got["code"] == "forbidden", got
    assert _grants(world, world.pid) == _both(world)
    assert _unshare_audits(world, world.pid) == []


# ── e. Org B reaches nothing ───────────────────────────────────────────────


def test_r8_org_b_gets_404_and_removes_nothing(world) -> None:
    status, got = _run(world, lambda: _unshare(world.b, ZED, world.pid, ANA))
    assert (status, got) == (404, NOT_FOUND)
    assert _grants(world, world.pid) == _both(world)
    assert _unshare_audits(world, world.pid) == []


# ── f. A pause never blocks the unshare ────────────────────────────────────


@pytest.mark.parametrize("change", ["state = 'paused'",
                                    "ends_at = now() + interval '3 days'"])
def test_r8_an_install_that_is_paused_or_ends_still_unshares(world, change: str) -> None:
    _admin(world, f"UPDATE package_install SET {change} WHERE id = CAST(:i AS uuid)",
           i=world.install)
    got = _run(world, lambda: _unshare(world.a, OWEN, world.pid, ANA))
    assert got == (200, {"removed": True})
    assert _grants(world, world.pid) == [(world.a, BEN, OWEN)]


# ── g. The audit row ───────────────────────────────────────────────────────


def test_r8_each_unshare_writes_one_audit_row_with_no_value(world) -> None:
    async def go() -> None:
        assert (await _unshare(world.a, OWEN, world.pid, ANA))[0] == 200
        assert (await _unshare(world.a, OWEN, world.pid, ANA))[0] == 200
        assert (await _unshare(world.a, OWEN, world.pid, "group:ops"))[0] == 422

    _run(world, go)
    rows = _unshare_audits(world, world.pid)
    assert [(org, kind) for org, kind, _ in rows] == [(world.a, "action")] * 2
    assert [d for _, _, d in rows] == [
        {"op": "package.unshare", "install_id": world.install, "removed": True,
         "person": ANA_LOW},
        {"op": "package.unshare", "install_id": world.install, "removed": False,
         "person": ANA_LOW},
    ]
