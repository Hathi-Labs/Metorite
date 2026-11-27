"""WS-STU J2b-1 — the routes of the approval for the org, on a real database
(R8).

Spec: ``project-docs/specs/ai_studio.md`` §9 item 3 and the J2b-1 row of
§14, and ``package_host.md`` §6.1 and §6.2. The owner chose path A on
2026-11-13 (task 1200). Code: ``gateway/routes/studio/packages.py``
(``request_approval``, ``list_approvals``, ``approve_approval``,
``reject_approval`` and the org branch of ``install_package``) over
``gateway/studio/packages_store.py``. The hermetic twin is
``tests/unit/test_studio_package_approval_routes.py``.

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds each statement. Each call binds
its org as the auth layer does. Org A has Owen (the owner of each app, with
``studio:publish``), Ada (an admin, with ``studio:publish``) and Mel (a member
with ``feature:studio`` only). Org B has Zed, with ``studio:publish``.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_package_approval_routes_r8.py -v -rs -p no:randomly

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J2b-1).**

a. Owen asks (202 ``pending``). Ada approves exactly one version. A second
   version stays ``pending``.
b. A second ask writes nothing.
c. Owen cannot approve his own version (403). Mel, with no right, cannot
   approve or read the list (403). Nothing changes.
d. A version that nobody asked for gives 404 to the approve and the reject.
e. Org B cannot see the pending list of org A, and cannot approve its
   version (404).
f. The org install of PKG-8b answers 202 and writes no ``package_install``
   row without an approval. After the approval it installs (201).
g. An approval survives a pause, and the org install refuses the paused
   package (423 ``package_paused``). A rejected live version is 409.
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
from fastapi import HTTPException, Response
from gateway.routes.studio import packages
from gateway.studio import packages_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@j2b1.test"
ADA = f"ada.{TAG}@j2b1.test"
MEL = f"mel.{TAG}@j2b1.test"
ZED = f"zed.{TAG}@j2b1.test"
_EMAIL_LIKE = f"%.{TAG}@j2b1.test"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")


def _manifest(mid: str, purpose: str = "Track the visits to a site.") -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": "Site visits", "purpose": purpose,
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


def _user(org: str, email: str, grants: tuple[str, ...]) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The body stream that a package handler reads."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def stream(self) -> Any:
        yield self._raw


_DEFAULT = {"request_approval": 202, "install_package": 201}
_BODY = ("create_package", "save_draft", "publish", "install_package")


async def _call(route: str, org: str, email: str, grants: tuple[str, ...] = PUBLISHER,
                **kwargs: Any) -> tuple[int, Any]:
    """Call the real handler *route* with *org* bound. Answers the status and
    the answer, or the status and the detail of a refusal."""
    handler = getattr(packages, route)
    kwargs["user"] = _user(org, email, grants)
    response = Response()
    response.status_code = None  # type: ignore[assignment]
    if route in _BODY:
        kwargs["request"] = _Request(kwargs.pop("payload", None))
    if route == "install_package":
        kwargs["response"] = response
    token = bind_tenant(org)
    try:
        got = await handler(**kwargs)
        return response.status_code or _DEFAULT.get(route, 200), got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _live(org: str, email: str, versions: int = 1) -> str:
    """A new package of *email* with *versions* published versions."""
    mid = f"j2b1{TAG}-app{next(_COUNTER)}"
    status, got = await _call("create_package", org, email, payload=_manifest(mid))
    assert status == 200, got
    pid = str(got["package"]["id"])
    for n in range(versions):
        if n:
            saved = await _call("save_draft", org, email, package_id=pid,
                                payload=_manifest(mid, f"Purpose {n}."))
            assert saved[0] == 200, saved
        published = await _call("publish", org, email, package_id=pid, payload={})
        assert published[0] == 200, published
    return pid


async def _ask(org: str, pid: str, v: int, who: str = OWEN) -> tuple[int, Any]:
    return await _call("request_approval", org, who, package_id=pid, version=str(v))


async def _decide(move: str, org: str, pid: str, v: int, who: str = ADA,
                  grants: tuple[str, ...] = PUBLISHER) -> tuple[int, Any]:
    route = "approve_approval" if move == "approve" else "reject_approval"
    return await _call(route, org, who, grants, package_id=pid, version=str(v))


async def _list(org: str, who: str, grants: tuple[str, ...] = PUBLISHER) -> tuple[int, Any]:
    return await _call("list_approvals", org, who, grants, state="pending")


async def _install_org(org: str, pid: str) -> tuple[int, Any]:
    return await _call("install_package", org, OWEN, package_id=pid, payload={"scope": "org"})


def _run(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the package host on for both. The
    members are seeded as the superuser. The teardown removes each package
    of the members, with its versions, objects and audit rows."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        for email, org in ((OWEN, p.org_a), (ADA, p.org_a), (MEL, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"), {"e": email, "org": org})
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE owner_email LIKE :t"), {"t": _EMAIL_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _EMAIL_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


# ── Readers (as the superuser, so RLS hides nothing) ───────────────────────


def _states(world: Any, pid: str) -> dict[int, str]:
    with world.p.admin_engine.connect() as c:
        return {int(r[0]): str(r[1]) for r in c.execute(text(
            "SELECT version, review_status FROM app_versions WHERE app_id = CAST(:p AS uuid)"),
            {"p": pid}).all()}


def _reviews(world: Any, pid: str) -> list[tuple[int, str, str]]:
    with world.p.admin_engine.connect() as c:
        return [(int(r[0]), str(r[1]), str(r[2])) for r in c.execute(text(
            "SELECT app_version, user_email, detail->>'review' FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) AND detail->>'op' = :op ORDER BY id"),
            {"p": pid, "op": store.REVIEW_OP}).all()]


def _org_installs(world: Any, pid: str) -> list[tuple[int, str]]:
    with world.p.admin_engine.connect() as c:
        return [(int(r[0]), str(r[1])) for r in c.execute(text(
            "SELECT version, state FROM package_install "
            "WHERE app_id = CAST(:p AS uuid) AND scope_kind = 'org'"), {"p": pid}).all()]


# ── a, b. The owner asks, and another holder approves one version ─────────


def test_r8_the_owner_asks_and_another_holder_approves_one_version(world) -> None:
    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _live(world.a, OWEN, versions=2)
        return pid, [await _ask(world.a, pid, 1), await _ask(world.a, pid, 1),
                     await _ask(world.a, pid, 2), await _decide("approve", world.a, pid, 1)]

    pid, (first, repeat, second, approved) = _run(world, go)
    assert first == (202, {"package_id": pid, "version": 1, "review_status": "pending"})
    assert repeat == first
    assert second[0] == 202 and second[1]["review_status"] == "pending"
    assert approved == (200, {"package_id": pid, "version": 1, "review_status": "approved"})
    # One version only: version 2 stays pending.
    assert _states(world, pid) == {1: "approved", 2: "pending"}
    # b. The repeat wrote no row.
    assert _reviews(world, pid) == [(1, OWEN, "pending"), (2, OWEN, "pending"),
                                    (1, ADA, "approved")]


# ── c. The owner and a member without the right ────────────────────────────


def test_r8_the_owner_and_a_member_without_the_right_get_403(world) -> None:
    async def go() -> tuple[str, dict[str, tuple[int, Any]]]:
        pid = await _live(world.a, OWEN)
        assert (await _ask(world.a, pid, 1))[0] == 202
        return pid, {
            "owner approves": await _decide("approve", world.a, pid, 1, who=OWEN),
            "member approves": await _decide("approve", world.a, pid, 1, MEL, STUDIO),
            "member rejects": await _decide("reject", world.a, pid, 1, MEL, STUDIO),
            "member lists": await _list(world.a, MEL, STUDIO),
        }

    pid, got = _run(world, go)
    assert {k: s for k, (s, _) in got.items()} == dict.fromkeys(got, 403), got
    assert got["owner approves"][1]["message"] == store.SELF_APPROVAL
    assert _states(world, pid) == {1: "pending"}
    assert _reviews(world, pid) == [(1, OWEN, "pending")]


# ── d. A version that nobody asked for ─────────────────────────────────────


def test_r8_a_version_that_nobody_asked_for_is_404(world) -> None:
    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _live(world.a, OWEN)
        return pid, [await _decide("approve", world.a, pid, 1),
                     await _decide("reject", world.a, pid, 1),
                     await _decide("approve", world.a, pid, 9),
                     await _decide("approve", world.a, str(uuid.uuid4()), 1)]

    pid, answers = _run(world, go)
    assert answers == [(404, "Not found")] * 4
    assert _states(world, pid) == {1: "auto"}
    assert _reviews(world, pid) == []


def test_r8_a_decided_version_is_409(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        pid = await _live(world.a, OWEN)
        assert (await _ask(world.a, pid, 1))[0] == 202
        assert (await _decide("reject", world.a, pid, 1))[0] == 200
        return [await _decide("approve", world.a, pid, 1), await _ask(world.a, pid, 1)]

    for status, detail in _run(world, go):
        assert status == 409, detail
        assert (detail["code"], detail["review_status"]) == ("review_conflict", "rejected")


# ── The list of an approver, and e. org B ──────────────────────────────────


def test_r8_a_holder_reads_the_pending_list_and_org_b_sees_none_of_it(world) -> None:
    async def go() -> tuple[str, str, dict[str, tuple[int, Any]]]:
        pid = await _live(world.a, OWEN, versions=2)
        hidden = await _live(world.a, MEL)   # never asked for, so never listed
        assert (await _ask(world.a, pid, 2))[0] == 202
        return pid, hidden, {
            "ada": await _list(world.a, ADA),
            "zed": await _list(world.b, ZED),
            "zed approves": await _decide("approve", world.b, pid, 2, who=ZED),
            "zed asks": await _ask(world.b, pid, 2, who=ZED),
        }

    pid, hidden, got = _run(world, go)
    status, body = got["ada"]
    assert status == 200, body
    rows = [r for r in body["approvals"] if r["owner_email"].endswith(f".{TAG}@j2b1.test")]
    assert [(r["package_id"], r["version"]) for r in rows] == [(pid, 2)]
    [row] = rows
    assert row["manifest"]["purpose"] == "Purpose 1."
    assert (row["owner_email"], row["requested_by"]) == (OWEN, OWEN)
    assert row["requested_at"] is not None
    assert hidden not in json.dumps(body, default=str)
    zed_status, zed_body = got["zed"]
    assert zed_status == 200 and pid not in json.dumps(zed_body, default=str)
    assert got["zed approves"] == (404, "Not found")
    assert got["zed asks"] == (404, "Not found")
    assert _states(world, pid) == {1: "auto", 2: "pending"}


# ── f. The org install of PKG-8b, gated on an approval ─────────────────────


def test_r8_the_org_install_asks_first_and_installs_after_the_approval(world) -> None:
    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _live(world.a, OWEN)
        first = await _install_org(world.a, pid)
        again = await _install_org(world.a, pid)
        installs_before = _org_installs(world, pid)
        approved = await _decide("approve", world.a, pid, 1)
        after = await _install_org(world.a, pid)
        return pid, [first, again, installs_before, approved, after]

    pid, (first, again, before, approved, after) = _run(world, go)
    assert first == (202, {"install": None, "created": False, "review_status": "pending",
                           "version": 1})
    assert again == first
    assert before == []
    assert approved[0] == 200, approved
    assert after[0] == 201 and after[1]["install"]["scope_kind"] == "org", after
    assert _org_installs(world, pid) == [(1, "active")]
    assert _reviews(world, pid) == [(1, OWEN, "pending"), (1, ADA, "approved")]


def test_r8_a_new_live_version_asks_again_and_leaves_the_org_install(world) -> None:
    """The approval names one version. A publish makes a new live version,
    and the org install stays at the approved one until a holder approves
    the new one."""
    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, OWEN)
        assert (await _ask(world.a, pid, 1))[0] == 202
        assert (await _decide("approve", world.a, pid, 1))[0] == 200
        assert (await _install_org(world.a, pid))[0] == 201
        assert (await _call("publish", world.a, OWEN, package_id=pid, payload={}))[0] == 200
        return pid, await _install_org(world.a, pid)

    pid, (status, got) = _run(world, go)
    assert status == 202 and got["version"] == 2, got
    assert _states(world, pid) == {1: "approved", 2: "pending"}
    assert _org_installs(world, pid) == [(1, "active")]


# ── g. A pause, and a rejected version ─────────────────────────────────────


def test_r8_an_approval_survives_a_pause_and_the_org_install_refuses_it(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, OWEN)
        assert (await _ask(world.a, pid, 1))[0] == 202
        assert (await _decide("approve", world.a, pid, 1))[0] == 200
        async with tenant_session(world.a) as db:
            assert await store.pause_package(db, ADA, pid) is not None
        return pid, await _install_org(world.a, pid)

    pid, (status, got) = _run(world, go)
    assert (status, got) == (423, {"code": "package_paused", "message": packages.PAUSED_TEXT})
    assert _states(world, pid) == {1: "approved"}
    assert _org_installs(world, pid) == []


def test_r8_a_rejected_live_version_is_409_and_installs_nothing(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, OWEN)
        assert (await _ask(world.a, pid, 1))[0] == 202
        assert (await _decide("reject", world.a, pid, 1, who=OWEN))[0] == 200
        return pid, await _install_org(world.a, pid)

    pid, (status, got) = _run(world, go)
    assert status == 409 and got["code"] == "review_conflict", got
    assert _org_installs(world, pid) == []
