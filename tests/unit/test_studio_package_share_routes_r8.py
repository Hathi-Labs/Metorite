"""WS-STU J2b-2 — the share of a Studio app with named people, on a real
database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 J2b-2 and §9 item 3, and
``package_host.md`` §6.2. Code: ``POST /studio/packages/{id}/share``
(``routes/studio/packages.py`` ``share_package``),
``package_host/promotion.share_with_people``, and the read of a viewer of
J2a (``GET /studio/packages/{id}`` and the door). The hermetic twin is
``tests/unit/test_studio_package_share_routes.py``.

The real create, publish, share, package and door handlers run on a phase-4
promoted scratch database, as the non-privileged app role, so FORCE RLS binds
each statement. Each call binds its org as the auth layer does. Org A has
Owen (the owner, a holder of ``studio:publish``), Pam (a second holder), Ana,
Ben and Cat. Org B has Zed. Ana's address is in mixed case.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_package_share_routes_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J2b-2).**

a. Owen shares his app with Ana and Ben: 200, and one ``app_grants`` row for
   each, with ``granted_by`` Owen, in org A. A repeat is idempotent.
b. Ana then reads the installed app through the viewer route (200,
   ``access: viewer``), and her write at the door is 403 ``read_only``.
   Cat (not shared) and Zed (org B) get 404.
c. A person whom Pam already shares the app with refuses the whole call
   with 409 ``shared_by_another_owner``. No row is written, and Pam's row
   stays.
d. A member who does not own the package gets 404, and a member without
   ``studio:publish`` gets 403. Neither writes a row.
e. A person outside the org is 422 ``not_a_member``. Org B reaches nothing.
f. An owner with no private install is 409 ``private_only``. A paused
   install is 409 ``not_active``, and an install with an end date is 409
   ``not_kept``.
g. Each share writes one ``app_audit`` row (op ``package.share``) in org A,
   with the addresses and their count and no value of the app. A refusal
   writes no row.
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
OWEN = f"owen.{TAG}@j2b2.test"
PAM = f"pam.{TAG}@j2b2.test"
#: Mixed case in ``app_user`` and in the body, so each read folds it.
ANA = f"Ana.{TAG}@j2b2.test"
ANA_LOW = ANA.lower()
BEN = f"ben.{TAG}@j2b2.test"
CAT = f"cat.{TAG}@j2b2.test"
ZED = f"zed.{TAG}@j2b2.test"
_EMAIL_LIKE = f"%.{TAG}@j2b2.test"
_SLUG_LIKE = f"j2b2{TAG}-%"
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
    return f"j2b2{TAG}-app{next(_COUNTER)}"


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The parts of a request that the handlers read."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/y")
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield self._raw


_BODY_ROUTES = ("create_package", "publish", "share_package")


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
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


async def _door(org: str, email: str, pid: str, action: str, payload: Any) -> tuple[int, Any]:
    token = bind_tenant(org)
    try:
        got = await invoke.invoke_action(pid, action, request=_Request(payload),
                                         user=_user(org, email))
        return 200, got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


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


def _grants(world: Any, pid: str) -> list[tuple[str, str, str, str]]:
    """The share rows of *pid*, read as the superuser, which RLS does not bind."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, subject, role, granted_by FROM app_grants "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY subject"), {"p": pid}).all()
    return [tuple(str(v) for v in r) for r in rows]  # type: ignore[misc]


def _share_audits(world: Any, pid: str) -> list[tuple[str, str, dict[str, Any]]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, kind, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) AND detail->>'op' = 'package.share' "
            "ORDER BY id"), {"p": pid}).all()
    return [(r[0], r[1], r[2] if isinstance(r[2], dict) else json.loads(r[2])) for r in rows]


# ── a. A share writes one row for each person ──────────────────────────────


def test_r8_the_owner_shares_with_two_people_and_a_repeat_is_idempotent(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _share(world.a, OWEN, world.pid, [ANA, BEN]),
                await _share(world.a, OWEN, world.pid, [BEN, ANA])]

    (s1, got1), (s2, got2) = _run(world, go)
    assert (s1, got1) == (200, {"people": [ANA_LOW, BEN]})
    assert (s2, got2) == (200, {"people": [BEN, ANA_LOW]})
    assert _grants(world, world.pid) == [(world.a, ANA_LOW, "use", OWEN),
                                         (world.a, BEN, "use", OWEN)]


# ── b. A person reads the app as a viewer, and writes nothing ──────────────


def test_r8_a_shared_person_reads_the_app_and_cannot_write(world) -> None:
    async def go() -> dict[str, tuple[int, Any]]:
        out = {"before": await _call("get_package", world.a, ANA, world.pid)}
        assert (await _share(world.a, OWEN, world.pid, [ANA]))[0] == 200
        out["ana"] = await _call("get_package", world.a, ANA, world.pid)
        out["ana_find"] = await _door(world.a, ANA, world.pid, "find", {})
        out["ana_add"] = await _door(world.a, ANA, world.pid, "add", {"data": {"site": "x"}})
        out["cat"] = await _call("get_package", world.a, CAT, world.pid)
        out["zed"] = await _call("get_package", world.b, ZED, world.pid)
        return out

    got = _run(world, go)
    assert got["before"] == (404, NOT_FOUND)
    status, body = got["ana"]
    assert status == 200, body
    assert (body["package"]["access"], body["package"]["version"]) == ("viewer", 1)
    assert OWEN not in json.dumps(body, default=str)
    assert got["ana_find"][0] == 200, got["ana_find"]
    status, refused = got["ana_add"]
    assert status == 403 and refused["code"] == "read_only", refused
    assert got["cat"] == (404, NOT_FOUND)
    assert got["zed"] == (404, NOT_FOUND)


# ── c. One sharer for each person ──────────────────────────────────────────


def test_r8_a_person_of_another_sharer_refuses_the_whole_call(world) -> None:
    """Pam holds a private install of the same app and shares it with Ana
    first, through the store. Owen's share with Ben and Ana is then 409, and
    Ben gets no row."""
    async def go() -> tuple[int, Any]:
        async with tenant_session(world.a) as db:
            made = await store.install(db, app_id=world.pid, version=1, scope_subject=PAM,
                                       scope_kind="private", installed_by=PAM, ends_at=None,
                                       contributions=[])
        async with tenant_session(world.a) as db:
            await promotion.share_with_people(db, install_id=made.id, emails=[ANA],
                                              sharer=PAM)
        return await _share(world.a, OWEN, world.pid, [BEN, ANA])

    status, got = _run(world, go)
    assert status == 409 and got["code"] == "shared_by_another_owner", got
    assert ANA not in got["message"] and ANA_LOW not in got["message"]
    assert _grants(world, world.pid) == [(world.a, ANA_LOW, "use", PAM)]
    assert _share_audits(world, world.pid) == []


# ── d. Who may share ───────────────────────────────────────────────────────


def test_r8_a_member_who_is_not_the_owner_gets_404(world) -> None:
    status, got = _run(world, lambda: _share(world.a, PAM, world.pid, [ANA]))
    assert (status, got) == (404, NOT_FOUND)
    assert _grants(world, world.pid) == [] and _share_audits(world, world.pid) == []


def test_r8_an_owner_without_the_share_verb_gets_403(world) -> None:
    status, got = _run(world, lambda: _share(world.a, OWEN, world.pid, [ANA], grants=STUDIO))
    assert status == 403 and got["code"] == "forbidden", got
    assert _grants(world, world.pid) == [] and _share_audits(world, world.pid) == []


# ── e. The org is the bound session ────────────────────────────────────────


def test_r8_a_person_outside_the_org_is_refused(world) -> None:
    status, got = _run(world, lambda: _share(world.a, OWEN, world.pid, [ANA, ZED]))
    assert status == 422 and got["code"] == "not_a_member", got
    assert _grants(world, world.pid) == [] and _share_audits(world, world.pid) == []


def test_r8_org_b_reaches_nothing(world) -> None:
    status, got = _run(world, lambda: _share(world.b, ZED, world.pid, [ANA]))
    assert (status, got) == (404, NOT_FOUND)
    assert _grants(world, world.pid) == [] and _share_audits(world, world.pid) == []


# ── f. The state of the install ────────────────────────────────────────────


def test_r8_an_owner_with_no_private_install_gets_409(world, monkeypatch) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        monkeypatch.setattr(get_settings(), "package_host_orgs", "")
        pid = await _live(world.a)  # no install: the switch was off at the publish
        monkeypatch.setattr(get_settings(), "package_host_orgs", f"{world.a},{world.b}")
        return pid, await _share(world.a, OWEN, pid, [ANA])

    pid, (status, got) = _run(world, go)
    assert status == 409 and got["code"] == "private_only", got
    assert _grants(world, pid) == [] and _share_audits(world, pid) == []


@pytest.mark.parametrize(("change", "code"), [
    ("state = 'paused'", "not_active"),
    ("ends_at = now() + interval '3 days'", "not_kept"),
])
def test_r8_an_install_that_is_not_active_and_kept_gets_409(world, change: str,
                                                             code: str) -> None:
    _admin(world, f"UPDATE package_install SET {change} WHERE id = CAST(:i AS uuid)",
           i=world.install)
    status, got = _run(world, lambda: _share(world.a, OWEN, world.pid, [ANA]))
    assert status == 409 and got["code"] == code, got
    assert _grants(world, world.pid) == [] and _share_audits(world, world.pid) == []


# ── g. The audit row ───────────────────────────────────────────────────────


def test_r8_each_share_writes_one_audit_row_with_no_value(world) -> None:
    async def go() -> None:
        assert (await _share(world.a, OWEN, world.pid, [ANA, BEN]))[0] == 200
        assert (await _share(world.a, OWEN, world.pid, [ZED]))[0] == 422

    _run(world, go)
    [(org, kind, detail)] = _share_audits(world, world.pid)
    assert (org, kind) == (world.a, "action")
    assert detail == {"op": "package.share", "install_id": world.install, "count": 2,
                      "people": [ANA_LOW, BEN]}
    assert "Site visits" not in json.dumps(detail)
