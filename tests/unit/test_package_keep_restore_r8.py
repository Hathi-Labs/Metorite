"""WS-PKG PKG-10d — Keep and Restore of the private install, on a real
database (R8).

Spec: ``project-docs/specs/package_host.md`` §6.2, PKG-Q7 and the PKG-10d
row of §10. Code: ``keep_package``, ``restore_package`` and
``_own_private_install`` of ``gateway/routes/studio/packages.py``, and
``private_install_id``, ``keep_install`` and ``restore_install`` of
``gateway/package_host/store.py``. The hermetic twin is
``tests/unit/test_package_keep_restore.py``.

The real publish, keep and restore handlers run on a phase-4 promoted
scratch database, as the non-privileged app role, so FORCE RLS binds each
statement. Each call binds its org as the auth layer does. Org A has Owen,
who owns the package, and Ben. Org B has Zed.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_package_keep_restore_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-10d).**

a. Owen publishes from chat, so his private install ends 30 days out. A
   keep makes ``ends_at`` NULL. A second keep answers 200 with ``changed``
   False and writes nothing.
b. An archived install restores within 30 days of ``archived_at``. It is
   active again, ``archived_at`` is NULL and ``ends_at`` is about 30 days
   after now. The install id and each claim row stay the same rows.
c. A restore after 30 days is 409 ``restore_closed`` and writes nothing.
d. Ben, a member of org A who does not own the package, gets 404 on each
   route, and nothing changes.
e. Zed of org B gets 404 on each route, with the host on for org B too, and
   nothing changes.
f. With ``PACKAGE_HOST_ORGS`` off for org A, each route is 404 and nothing
   changes.
g. A keep of an archived install and a restore of an active install are
   409 ``state_refused`` and write nothing. An owner with no private install
   gets 409 ``private_only``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.studio import packages
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@pkg10d.test"
BEN = f"ben.{TAG}@pkg10d.test"
ZED = f"zed.{TAG}@pkg10d.test"
_EMAIL_LIKE = f"%.{TAG}@pkg10d.test"
_SLUG_LIKE = f"p10d{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
DAY = timedelta(days=1)
#: The slack between the clock of the host and the clock of the database.
SLACK = timedelta(minutes=2)
ROUTES = ("keep_package", "restore_package")


def _manifest(mid: str) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": "Site visits", "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(STUDIO)), organization_id=org)


class _Request:
    """The body stream that a package handler reads."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def stream(self) -> Any:
        yield self._raw


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound. Answers the
    status and the answer, or the status and the detail of a refusal."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "publish"):
        kwargs["request"] = _Request(payload)
    token = bind_tenant(org)
    try:
        return 200, await handler(**kwargs)
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _new(org: str, email: str) -> str:
    status, got = await _call("create_package", org, email,
                              payload=_manifest(f"p10d{TAG}-app{next(_COUNTER)}"))
    assert status == 200, got
    return str(got["package"]["id"])


async def _chat_app(org: str, email: str) -> str:
    """A new package of *email*, published one time from chat (PKG-12)."""
    pid = await _new(org, email)
    status, got = await _call("publish", org, email, pid, payload={"origin": "chat"})
    assert status == 200, got
    return pid


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


def _both(world: Any, org: str, email: str, pid: str) -> list[tuple[int, Any]]:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(r, org, email, pid) for r in ROUTES]
    return _run(world, go)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio on for both and the package host on for
    org A only. Every package that a test makes has a slug that starts with
    ``p10d<tag>``, and the teardown removes it. The installs and the claims
    go by cascade."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", p.org_a)
    with p.admin_engine.begin() as c:
        for email, org in ((OWEN, p.org_a), (BEN, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"), {"e": email, "org": org})
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


# ── Readers and writers (as the superuser, so RLS hides nothing) ───────────


def _row(world: Any, pid: str) -> dict[str, Any]:
    """The one install of *pid*, whole, with ``xmin`` to show a rewrite."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text, scope_kind, scope_subject, version, state, ends_at, "
            "archived_at, updated_at, xmin::text FROM package_install "
            "WHERE app_id = CAST(:p AS uuid)"), {"p": pid}).all()
    assert len(rows) == 1, rows
    keys = ("id", "kind", "subject", "version", "state", "ends_at", "archived_at",
            "updated_at", "xmin")
    return dict(zip(keys, rows[0], strict=True))


def _claims(world: Any, install_id: str) -> list[tuple[Any, ...]]:
    """The claim rows of one install, with ``xmin``, so a rewrite shows."""
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT point, key, payload::text, position, xmin::text FROM package_contribution "
            "WHERE install_id = CAST(:i AS uuid) ORDER BY point, key"), {"i": install_id}).all()]


def _archive(world: Any, pid: str, days_ago: int) -> None:
    """Archive the install of *pid*, *days_ago* days before the database clock."""
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE package_install SET state = 'archived', "
            "archived_at = now() - make_interval(days => :d), ends_at = now() - "
            "make_interval(days => :d) WHERE app_id = CAST(:p AS uuid)"),
            {"p": pid, "d": days_ago})


# ── a. Keep ────────────────────────────────────────────────────────────────


def test_r8_a_the_owner_keeps_and_a_second_keep_writes_nothing(world) -> None:
    pid = _run(world, lambda: _chat_app(world.a, OWEN))
    made = _row(world, pid)
    assert (made["kind"], made["subject"], made["state"]) == ("private", OWEN, "active")
    assert made["ends_at"] is not None

    status, got = _run(world, lambda: _call("keep_package", world.a, OWEN, pid))
    assert status == 200, got
    assert got == {"install": {"id": made["id"], "app_id": pid, "ends_at": None},
                   "changed": True}
    kept = _row(world, pid)
    assert kept["id"] == made["id"] and kept["ends_at"] is None and kept["state"] == "active"

    status, again = _run(world, lambda: _call("keep_package", world.a, OWEN, pid))
    assert status == 200, again
    assert again["changed"] is False
    assert _row(world, pid) == kept


# ── b. Restore within 30 days ──────────────────────────────────────────────


def test_r8_b_an_archived_install_restores_with_the_same_id_and_claim_rows(world) -> None:
    pid = _run(world, lambda: _chat_app(world.a, OWEN))
    _archive(world, pid, 29)
    archived = _row(world, pid)
    assert archived["state"] == "archived"
    claims = _claims(world, archived["id"])
    assert claims, "a publish writes the nav claim"

    before = datetime.now(UTC)
    status, got = _run(world, lambda: _call("restore_package", world.a, OWEN, pid))
    after = datetime.now(UTC)
    assert status == 200, got
    assert got["install"]["id"] == archived["id"] and got["install"]["state"] == "active"
    restored = _row(world, pid)
    assert restored["id"] == archived["id"]
    assert (restored["state"], restored["archived_at"]) == ("active", None)
    assert (restored["kind"], restored["subject"], restored["version"]) == (
        "private", OWEN, archived["version"])
    # PKG-Q7: 30 days after the restore, by the server clock.
    assert before + 30 * DAY - SLACK <= restored["ends_at"] <= after + 30 * DAY + SLACK
    assert got["install"]["ends_at"] == restored["ends_at"]
    # The same claim rows: the restore rewrote none of them.
    assert _claims(world, restored["id"]) == claims


# ── c. Restore after 30 days ───────────────────────────────────────────────


def test_r8_c_a_restore_after_30_days_is_refused_and_writes_nothing(world) -> None:
    pid = _run(world, lambda: _chat_app(world.a, OWEN))
    _archive(world, pid, 31)
    archived = _row(world, pid)
    status, got = _run(world, lambda: _call("restore_package", world.a, OWEN, pid))
    assert status == 409, got
    assert got["code"] == "restore_closed"
    assert _row(world, pid) == archived


# ── d, e. Another member, another org ──────────────────────────────────────


def test_r8_d_a_member_who_does_not_own_the_package_gets_404(world) -> None:
    pid = _run(world, lambda: _chat_app(world.a, OWEN))
    made = _row(world, pid)
    for status, got in _both(world, world.a, BEN, pid):
        assert (status, got) == (404, "Not found")
    _archive(world, pid, 1)
    archived = _row(world, pid)
    for status, got in _both(world, world.a, BEN, pid):
        assert (status, got) == (404, "Not found")
    assert _row(world, pid) == archived and archived["id"] == made["id"]


def test_r8_e_a_member_of_org_b_gets_404(world, monkeypatch) -> None:
    pid = _run(world, lambda: _chat_app(world.a, OWEN))
    _archive(world, pid, 1)
    archived = _row(world, pid)
    # The host is on for org B too, so the 404 is the wall of the org.
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{world.a},{world.b}")
    for status, got in _both(world, world.b, ZED, pid):
        assert (status, got) == (404, "Not found")
    assert _row(world, pid) == archived


# ── f. The switch ──────────────────────────────────────────────────────────


def test_r8_f_with_the_host_off_each_route_is_404_and_nothing_changes(
    world, monkeypatch,
) -> None:
    pid = _run(world, lambda: _chat_app(world.a, OWEN))
    made = _row(world, pid)
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    for status, got in _both(world, world.a, OWEN, pid):
        assert (status, got) == (404, "Not found")
    assert _row(world, pid) == made
    _archive(world, pid, 1)
    archived = _row(world, pid)
    for status, got in _both(world, world.a, OWEN, pid):
        assert (status, got) == (404, "Not found")
    assert _row(world, pid) == archived


# ── g. The state rules and the private rule ────────────────────────────────


def test_r8_g_a_wrong_state_is_409_and_writes_nothing(world) -> None:
    pid = _run(world, lambda: _chat_app(world.a, OWEN))
    active = _row(world, pid)
    status, got = _run(world, lambda: _call("restore_package", world.a, OWEN, pid))
    assert (status, got["code"]) == (409, "state_refused"), got
    assert _row(world, pid) == active
    _archive(world, pid, 1)
    archived = _row(world, pid)
    status, got = _run(world, lambda: _call("keep_package", world.a, OWEN, pid))
    assert (status, got["code"]) == (409, "state_refused"), got
    assert _row(world, pid) == archived


def test_r8_g_an_owner_with_no_private_install_gets_409_private_only(world) -> None:
    """A draft that was never published has no install."""
    pid = _run(world, lambda: _new(world.a, OWEN))
    for status, got in _both(world, world.a, OWEN, pid):
        assert status == 409, got
        assert got == {"code": "private_only", "message": packages.NO_PRIVATE_INSTALL}
