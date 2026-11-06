"""WS-PKG PKG-8a — a publish keeps the owner's private install, on a real
database (R8).

Spec: ``project-docs/specs/package_host.md``, the PKG-8a row of §10. Code:
``gateway/routes/studio/packages.py`` (``publish`` and ``_keep_install``),
``gateway/package_host/studio_app.py`` and ``store.set_version``. The
hermetic twin is ``tests/unit/test_studio_publish_install.py``.

The real publish handler and the real ``GET /shell/manifest`` handler run on
a phase-4 promoted scratch database, as the non-privileged app role, so FORCE
RLS binds each statement. Each call binds its org as the auth layer does.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_publish_install_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-8a).**

1. With ``PACKAGE_HOST_ORGS`` on for the org, a publish writes one private
   install for the owner at the published version, with no end date, and one
   ``nav`` claim keyed by its href. With the switch off, it writes no row,
   and the answer has the same keys.
2. A second publish moves that install to the new version and rewrites its
   claim. It writes no second row and is not 409. Two publishes at once
   leave one install at the newest version. A paused install stays paused.
3. A refused install rolls the publish back. A 409 or a 422 of the store is
   its ``detail_for`` of the member.
4. ``GET /shell/manifest`` shows the entry to the owner, and to no other
   member of the org or of another org.

**R7: red first, then mutate.** Each mutation ran on 2026-11-07, and the
build report holds the counts.

* M1. ``packages._keep_install``: return at once. 7 of the 8 tests go red.
  Only the test of the switch off stays green.
* M2. ``studio_app.keep_private_install``: skip ``set_version`` and always
  call ``install``. 3 tests go red with 409 ``already_installed``: the
  second publish, two publishes at once, and the paused install.
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
from gateway.package_host import store, studio_app
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
ANA = f"Ana.{TAG}@pkg8a.test"
ANA_LOW = ANA.lower()
BEN = f"ben.{TAG}@pkg8a.test"
ZED = f"zed.{TAG}@pkg8a.test"
_COUNTER = iter(range(10_000))
_ANSWER_KEYS = {"version", "objects", "sync"}


def _manifest(mid: str, name: str = "Field visits") -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": name, "purpose": "Track install visits.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


def _mid() -> str:
    return f"p8{TAG}-app{next(_COUNTER)}"


def _user(org: str, email: str, features: tuple[str, ...] = ("feature:studio",)) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(features)), organization_id=org)


class _Request:
    """The body stream that a package handler reads."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def stream(self) -> Any:
        yield self._raw


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound. Answers (200,
    answer) or the status and the detail of a refusal."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "save_draft", "publish"):
        kwargs["request"] = _Request(payload)
    token = bind_tenant(org)
    try:
        return 200, await handler(**kwargs)
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _new(org: str, email: str, manifest: dict[str, Any]) -> str:
    status, got = await _call("create_package", org, email, payload=manifest)
    assert status == 200, got
    return str(got["package"]["id"])


async def _publish(org: str, email: str, pid: str) -> tuple[int, Any]:
    return await _call("publish", org, email, pid, payload={})


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
    ``p8<tag>``, and the teardown removes it. The installs and the claims go
    by cascade."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", p.org_a)
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": f"p8{TAG}-%"}).all()]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM app_versions WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"p8{TAG}-%"})


# ── Readers (as the superuser, so RLS hides nothing) ───────────────────────


def _installs(world: Any, pid: str) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text, organization_id::text, version, scope_kind, scope_subject, "
            "installed_by, ends_at, state FROM package_install "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY created_at"), {"p": pid}).all()
    keys = ("id", "org", "version", "kind", "subject", "by", "ends_at", "state")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def _claims(world: Any, install_id: str) -> list[tuple[str, str, Any, int]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT point, key, payload, position FROM package_contribution "
            "WHERE install_id = CAST(:i AS uuid) ORDER BY point, key"), {"i": install_id}).all()
    return [(r[0], r[1], r[2] if not isinstance(r[2], str) else json.loads(r[2]), r[3])
            for r in rows]


def _versions(world: Any, pid: str) -> list[int]:
    with world.p.admin_engine.connect() as c:
        return [int(r[0]) for r in c.execute(text(
            "SELECT version FROM app_versions WHERE app_id = CAST(:p AS uuid) ORDER BY version"),
            {"p": pid}).all()]


def _app(world: Any, pid: str) -> tuple[Any, ...]:
    with world.p.admin_engine.connect() as c:
        return tuple(c.execute(text(
            "SELECT status, live_version FROM apps WHERE id = CAST(:p AS uuid)"),
            {"p": pid}).one())


def _view(world: Any, who: str, org: str,
          features: tuple[str, ...] = ("feature:studio",)) -> Any:
    """Call the real ``GET /shell/manifest`` as *who* in *org*. Answers the
    status and the nav entries of installs, by key."""
    user = _user(org, who, features)
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


def _expected_payload(pid: str, label: str = "Field visits") -> dict[str, Any]:
    href = f"/apps/{pid}"
    return {"href": href, "label": label, "icon": "Boxes", "feature": "studio"}


# ── 1. One private install, and nothing with the switch off ───────────────


def test_r8_with_the_switch_off_a_publish_writes_no_install(world, monkeypatch) -> None:
    for value in ("", world.b):
        monkeypatch.setattr(get_settings(), "package_host_orgs", value)

        async def go() -> tuple[str, tuple[int, Any]]:
            pid = await _new(world.a, ANA, _manifest(_mid()))
            return pid, await _publish(world.a, ANA, pid)

        pid, (status, got) = _run(world, go)
        assert status == 200, got
        assert set(got) == _ANSWER_KEYS
        assert _versions(world, pid) == [1]
        assert _installs(world, pid) == [], value


def test_r8_a_publish_writes_one_private_install_and_one_nav_claim(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert set(got) == _ANSWER_KEYS          # the answer is the answer of the switch off
    [row] = _installs(world, pid)
    assert {k: row[k] for k in ("org", "version", "kind", "subject", "by", "ends_at",
                                "state")} == {
        "org": world.a, "version": 1, "kind": "private", "subject": ANA_LOW, "by": ANA_LOW,
        "ends_at": None, "state": "active"}
    assert _claims(world, row["id"]) == [("nav", f"/apps/{pid}", _expected_payload(pid), 0)]


# ── 2. A later publish moves the same install ──────────────────────────────


def test_r8_a_second_publish_moves_the_install_and_rewrites_its_claim(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _new(world.a, ANA, _manifest(mid))
        first = await _publish(world.a, ANA, pid)
        saved = await _call("save_draft", world.a, ANA, pid, payload=_manifest(mid, "Site visits"))
        assert saved[0] == 200, saved
        return pid, [first, await _publish(world.a, ANA, pid)]

    pid, got = _run(world, go)
    assert [s for s, _ in got] == [200, 200], got
    assert got[1][1]["version"]["version"] == 2
    [row] = _installs(world, pid)
    assert row["version"] == 2 and row["state"] == "active"
    assert _claims(world, row["id"]) == [
        ("nav", f"/apps/{pid}", _expected_payload(pid, "Site visits"), 0)]


def test_r8_two_publishes_at_once_leave_one_install_at_the_newest_version(world) -> None:
    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        return pid, list(await asyncio.gather(_publish(world.a, ANA, pid),
                                              _publish(world.a, ANA, pid)))

    pid, got = _run(world, go)
    assert [s for s, _ in got] == [200, 200], got
    assert _versions(world, pid) == [1, 2]
    [row] = _installs(world, pid)
    assert row["version"] == 2
    assert len(_claims(world, row["id"])) == 1


def test_r8_a_paused_install_stays_paused_at_the_new_version(world) -> None:
    async def first() -> str:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        assert (await _publish(world.a, ANA, pid))[0] == 200
        return pid

    pid = _run(world, first)
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE package_install SET state = 'paused' "
                       "WHERE app_id = CAST(:p AS uuid)"), {"p": pid})
    status, got = _run(world, lambda: _publish(world.a, ANA, pid))
    assert status == 200, got
    [row] = _installs(world, pid)
    assert (row["version"], row["state"]) == (2, "paused")


# ── 3. A refused install rolls the publish back ────────────────────────────


def test_r8_a_taken_key_is_409_with_the_detail_for_the_member_and_no_version(world) -> None:
    """Ana's private install of another app holds the key of the new app.
    The claims of one member overlap, so the store refuses, and the publish
    goes back with it."""
    async def setup() -> tuple[str, str, str]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        other = await _new(world.a, ANA, _manifest(_mid()))
        async with tenant_session(world.a) as db:
            held = await store.install(
                db, app_id=other, version=None, scope_subject=ANA, scope_kind="private",
                installed_by=ANA, ends_at=None,
                contributions=[store.Contribution(point="nav", key=f"/apps/{pid}")])
        return pid, other, held.id

    pid, other, held = _run(world, setup)
    status, got = _run(world, lambda: _publish(world.a, ANA, pid))
    assert status == 409, got
    assert got == {
        "code": "claim_taken", "point": "nav", "key": f"/apps/{pid}",
        "message": f"The nav key '/apps/{pid}' is taken by a private install.",
        "holder": {"source": "private", "package": other, "install_id": held,
                   "scope_subject": ANA_LOW}}
    assert _versions(world, pid) == []
    assert _app(world, pid) == ("draft", None)
    assert _installs(world, pid) == []
    [kept] = _installs(world, other)
    assert kept["id"] == held


def test_r8_a_refused_claim_is_422_with_the_detail_and_no_version(world, monkeypatch) -> None:
    def too_long(app_id: str, manifest: Any) -> list[store.Contribution]:
        return [store.Contribution(point="nav", key="k" * (store.MAX_KEY_BYTES + 1))]

    monkeypatch.setattr(studio_app, "claims_of", too_long)

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 422, got
    assert set(got) == {"code", "message"} and got["code"] == "bad_key"
    assert _versions(world, pid) == [] and _installs(world, pid) == []


# ── 4. The resolve shows the entry to the owner only ───────────────────────


def test_r8_the_shell_manifest_shows_the_entry_to_the_owner_only(world, monkeypatch) -> None:
    async def go() -> str:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        assert (await _publish(world.a, ANA, pid))[0] == 200
        return pid

    pid = _run(world, go)
    href = f"/apps/{pid}"
    mine = _view(world, ANA, world.a)
    assert mine.status == 200
    assert mine.nav[href] == {**_expected_payload(pid), "key": href, "source": "private",
                              "install_id": _installs(world, pid)[0]["id"],
                              "needs_setup": False}
    assert href not in _view(world, BEN, world.a).nav
    # Org B is off: the route answers 404 there.
    assert _view(world, ZED, world.b).status == 404
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{world.a},{world.b}")
    for who in (ZED, ANA):
        other = _view(world, who, world.b)
        assert other.status == 200 and href not in other.nav, who
    # A member who lacks the feature of the app page does not see the entry.
    assert href not in _view(world, ANA, world.a, ("feature:projects",)).nav
