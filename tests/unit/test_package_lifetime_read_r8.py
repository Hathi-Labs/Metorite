"""WS-PKG PKG-10e-1 — the lifetime read of the private install, on a real
database (R8).

Spec: ``project-docs/specs/package_host.md`` §6.2 and the PKG-10e-1 row of
§10. Code: ``package_lifetime`` and ``_owned_private_install`` of
``gateway/routes/studio/packages.py``, and ``read_lifetime`` of
``gateway/package_host/store.py``. The hermetic twin is
``tests/unit/test_package_lifetime_read.py``.

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds each statement. Each call binds
its org as the auth layer does. Org A has Owen, who owns the package, and
Ben. Org B has Zed.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_package_lifetime_read_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-10e-1).**

a. Owen publishes from chat. The read gives state ``active`` and the
   ``ends_at`` of the row, and ``now`` is the server clock.
b. Owen publishes from the IDE. The read gives ``ends_at`` null.
c. After ``store.expire_due``, the read gives state ``archived``, the
   ``archived_at`` of the row and ``restore_until`` = ``archived_at`` plus
   30 days.
d. A paused install gives state ``paused``.
e. Ben, a member of org A who does not own the package, gets 404.
f. Zed of org B gets 404, with the host on for org B too.
g. An org install of the same app is never the answer. An owner with only
   an org install gets 200 ``{"install": null}``.
h. With ``PACKAGE_HOST_ORGS`` off for org A, the read is 404 and runs no
   statement on the install.
i. The read changes no row.
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
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException, Response
from gateway.package_host import store as host_store
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
OWEN = f"owen.{TAG}@pkg10e.test"
BEN = f"ben.{TAG}@pkg10e.test"
ZED = f"zed.{TAG}@pkg10e.test"
_EMAIL_LIKE = f"%.{TAG}@pkg10e.test"
_SLUG_LIKE = f"p10e{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
DAY = timedelta(days=1)
#: The slack between the clock of the host and the clock of the database.
SLACK = timedelta(minutes=2)


def _manifest(mid: str) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": "Site visits", "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


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
    if route in ("create_package", "publish", "install_package"):
        kwargs["request"] = _Request(payload)
    if route == "install_package":
        kwargs["response"] = Response()
    token = bind_tenant(org)
    try:
        return 200, await handler(**kwargs)
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _read(org: str, email: str, pid: str) -> tuple[int, Any]:
    return await _call("package_lifetime", org, email, pid)


async def _new(org: str, email: str) -> str:
    status, got = await _call("create_package", org, email,
                              payload=_manifest(f"p10e{TAG}-app{next(_COUNTER)}"))
    assert status == 200, got
    return str(got["package"]["id"])


async def _app(org: str, email: str, origin: str = "chat") -> str:
    """A new package of *email*, published one time from *origin* (PKG-12)."""
    pid = await _new(org, email)
    status, got = await _call("publish", org, email, pid, payload={"origin": origin})
    assert status == 200, got
    return pid


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio on for both and the package host on for
    org A only. Every package that a test makes has a slug that starts with
    ``p10e<tag>``, and the teardown removes it. The installs and the claims
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


def _rows(world: Any, pid: str) -> list[dict[str, Any]]:
    """Each install of *pid*, whole, with ``xmin`` to show a rewrite."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text, scope_kind, scope_subject, state, ends_at, archived_at, "
            "updated_at, xmin::text FROM package_install "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY scope_kind"), {"p": pid}).all()
    keys = ("id", "kind", "subject", "state", "ends_at", "archived_at", "updated_at", "xmin")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def _row(world: Any, pid: str) -> dict[str, Any]:
    [row] = _rows(world, pid)
    return row


def _tenant(world: Any, org: str, fn: Any, **kw: Any) -> Any:
    """One store call as the app role, bound to *org*."""
    async def go() -> Any:
        async with tenant_session(org) as db:
            return await fn(db, **kw)
    return _run(world, go)


def _expire_at(world: Any, install_id: str, at: datetime) -> None:
    """Archive one install at *at*, through ``expire_due``. The end date
    goes to one day before *at* first, as the superuser."""
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE package_install SET ends_at = :e WHERE id = CAST(:i AS uuid)"),
                  {"e": at - DAY, "i": install_id})
    got = _tenant(world, world.a, host_store.expire_due, now=at)
    assert got["archived"] >= 1, got


def _at(value: str | None) -> datetime | None:
    """The time of an ISO answer. A compare of two times, and not of two
    strings, does not hang on the time zone of the session."""
    return None if value is None else datetime.fromisoformat(value)


# ── a, b. Active ───────────────────────────────────────────────────────────


def test_r8_a_an_owner_with_an_end_date_gets_the_date_and_the_server_clock(world) -> None:
    pid = _run(world, lambda: _app(world.a, OWEN))
    made = _row(world, pid)
    assert (made["kind"], made["subject"], made["state"]) == ("private", OWEN, "active")
    assert made["ends_at"] is not None
    before = datetime.now(UTC)
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    after = datetime.now(UTC)
    assert status == 200, got
    install = got["install"]
    assert install["state"] == "active"
    assert _at(install["ends_at"]) == made["ends_at"]
    assert (install["archived_at"], install["restore_until"]) == (None, None)
    now = datetime.fromisoformat(install["now"])
    assert before - SLACK <= now <= after + SLACK
    # i. The read changes no row.
    assert _row(world, pid) == made


def test_r8_b_an_active_install_with_no_end_date_gives_null(world) -> None:
    pid = _run(world, lambda: _app(world.a, OWEN, origin="ide"))
    assert _row(world, pid)["ends_at"] is None
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    assert status == 200, got
    assert (got["install"]["state"], got["install"]["ends_at"]) == ("active", None)


# ── c. Archived ────────────────────────────────────────────────────────────


def test_r8_c_an_archived_install_gives_the_restore_time(world) -> None:
    pid = _run(world, lambda: _app(world.a, OWEN))
    at = datetime.now(UTC) - 2 * DAY
    _expire_at(world, _row(world, pid)["id"], at)
    archived = _row(world, pid)
    assert (archived["state"], archived["archived_at"]) == ("archived", at)
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    assert status == 200, got
    install = got["install"]
    assert install["state"] == "archived"
    assert _at(install["archived_at"]) == at
    assert _at(install["restore_until"]) == at + timedelta(days=30)
    assert _row(world, pid) == archived


# ── d. Paused ──────────────────────────────────────────────────────────────


def test_r8_d_a_paused_install_gives_state_paused(world) -> None:
    pid = _run(world, lambda: _app(world.a, OWEN))
    made = _row(world, pid)
    assert _tenant(world, world.a, host_store.pause_install, install_id=made["id"]) is True
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    assert status == 200, got
    assert got["install"]["state"] == "paused"
    assert _at(got["install"]["ends_at"]) == made["ends_at"]
    assert got["install"]["restore_until"] is None


# ── e, f. Another member, another org ──────────────────────────────────────


def test_r8_e_a_member_who_does_not_own_the_package_gets_404(world) -> None:
    pid = _run(world, lambda: _app(world.a, OWEN))
    made = _row(world, pid)
    assert _run(world, lambda: _read(world.a, BEN, pid)) == (404, "Not found")
    _expire_at(world, made["id"], datetime.now(UTC) - DAY)
    assert _run(world, lambda: _read(world.a, BEN, pid)) == (404, "Not found")


def test_r8_f_a_member_of_org_b_gets_404(world, monkeypatch) -> None:
    pid = _run(world, lambda: _app(world.a, OWEN))
    made = _row(world, pid)
    # The host is on for org B too, so the 404 is the wall of the org.
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{world.a},{world.b}")
    assert _run(world, lambda: _read(world.b, ZED, pid)) == (404, "Not found")
    assert _row(world, pid) == made


# ── g. An org install is never the answer ──────────────────────────────────


def test_r8_g_an_owner_with_only_an_org_install_gets_200_null(world) -> None:
    pid = _run(world, lambda: _app(world.a, OWEN))
    status, got = _run(world, lambda: _call(
        "install_package", world.a, OWEN, pid, payload={"scope": "org"}, grants=PUBLISHER))
    assert status == 200, got
    assert got["install"]["scope_kind"] == "org"
    kinds = {r["kind"] for r in _rows(world, pid)}
    assert kinds == {"org", "private"}
    # With both, the answer is the private install.
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    assert status == 200 and got["install"]["ends_at"] is not None, got
    # Remove the private install. The org install stays and is not the answer.
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM package_install WHERE app_id = CAST(:p AS uuid) "
                       "AND scope_kind = 'private'"), {"p": pid})
    [org_row] = _rows(world, pid)
    assert org_row["kind"] == "org"
    assert _run(world, lambda: _read(world.a, OWEN, pid)) == (200, {"install": None})


def test_r8_g_a_draft_with_no_install_gets_200_null(world) -> None:
    pid = _run(world, lambda: _new(world.a, OWEN))
    assert _rows(world, pid) == []
    assert _run(world, lambda: _read(world.a, OWEN, pid)) == (200, {"install": None})


# ── h. The switch ──────────────────────────────────────────────────────────


def test_r8_h_with_the_host_off_the_read_is_404_and_reads_nothing(world, monkeypatch) -> None:
    pid = _run(world, lambda: _app(world.a, OWEN))
    made = _row(world, pid)
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    seen: list[str] = []

    async def spy(*_a: Any, **_k: Any) -> Any:
        seen.append("read")
        raise AssertionError("the read ran with the host off")

    monkeypatch.setattr(host_store, "private_install_id", spy)
    monkeypatch.setattr(host_store, "read_lifetime", spy)
    assert _run(world, lambda: _read(world.a, OWEN, pid)) == (404, "Not found")
    assert seen == []
    assert _row(world, pid) == made
