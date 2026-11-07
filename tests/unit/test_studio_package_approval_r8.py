"""WS-STU J2 / WS-PKG PKG-11b-2 — the approval writer, on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §6.2 ("The approval record") and
the PKG-11b-2 row. Code: ``gateway/studio/packages_store.py``
(``request_org_approval``, ``approve_version``, ``reject_version``). The
hermetic twin is ``tests/unit/test_studio_package_approval.py``. No migration:
``app_versions.review_status`` and its CHECK exist since 114.

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL="postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>"
    uv run pytest tests/unit/test_studio_package_approval_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-11b-2).**

a. A request then an approval moves ``auto`` to ``pending`` to ``approved``,
   with one audit row for each move.
b. Org B cannot approve, reject or ask for a version of org A.
c. A second approve on a stale state raises ``ApprovalConflict`` and writes no
   second audit row.
d. The manifest, the bundle and the hash columns of the row do not change,
   and another version of the package does not change.
e. Two approvals at once give one winner.
f. The publisher and the owner cannot approve. A member without the right
   cannot approve.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import get_session_factory, tenant_session
from gateway.studio import packages_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_studio_packages_store import MANIFEST

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OLGA = f"olga.{TAG}@pkg11b.test"
ABE = f"abe.{TAG}@pkg11b.test"
CY = f"cy.{TAG}@pkg11b.test"
_LIKE = f"%.{TAG}@pkg11b.test"
MID = f"quotes-{TAG}"

CATALOG: dict[str, str] = {}
_ROW_COLUMNS = (
    "version, manifest::text, bundle_html, bundle_sha256, release_notes, scope_set_hash, "
    "published_by, published_at::text, review_status")


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _manifest(**patch: Any) -> dict[str, Any]:
    d = copy.deepcopy(MANIFEST)
    d["id"] = MID
    d.update(patch)
    return d


@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    yield SimpleNamespace(p=p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_audit WHERE user_email LIKE :t"), {"t": _LIKE})
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _package_with_versions(org: str, count: int = 1) -> str:
    """A package of OLGA with *count* published versions."""
    async with tenant_session(org) as db:
        pkg = await store.create_package(db, OLGA, _manifest())
    for i in range(count):
        if i:
            async with tenant_session(org) as db:
                await store.update_manifest(db, OLGA, pkg.id, _manifest(purpose=f"Purpose {i}."))
        async with tenant_session(org) as db:
            assert await store.publish_version(db, OLGA, pkg.id, f"v{i + 1}", catalog=CATALOG)
    return pkg.id


async def _request(org: str, pid: str, v: int = 1, member: str = OLGA) -> Any:
    async with tenant_session(org) as db:
        return await store.request_org_approval(db, member, pid, v)


async def _approve(org: str, pid: str, v: int = 1, who: str = ABE, can: bool = True) -> Any:
    async with tenant_session(org) as db:
        return await store.approve_version(db, who, pid, v, can_publish=can)


def _rows(world: Any, pid: str) -> list[tuple[Any, ...]]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            f"SELECT {_ROW_COLUMNS} FROM app_versions WHERE app_id = CAST(:i AS uuid) "
            "ORDER BY version"), {"i": pid})]


def _reviews(world: Any, pid: str) -> list[tuple[Any, ...]]:
    with world.p.admin_engine.connect() as c:
        return [(r[0], r[1], r[2], r[3], r[4]) for r in c.execute(text(
            "SELECT kind, app_version, user_email, detail, organization_id::text "
            "FROM app_audit WHERE app_id = CAST(:i AS uuid) AND detail->>'op' = :op "
            "ORDER BY id"), {"i": pid, "op": store.REVIEW_OP})]


def _states(world: Any, pid: str) -> list[str]:
    return [r[-1] for r in _rows(world, pid)]


# ── a. The round trip ──────────────────────────────────────────────────────


def test_r8_request_then_approve_round_trip(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pid = await _package_with_versions(a)
        asked = await _request(a, pid)
        again = await _request(a, pid)
        approved = await _approve(a, pid)
        return pid, asked, again, approved

    pid, asked, again, approved = _go(world, go)
    assert asked.review_status == "pending" and again.review_status == "pending"
    assert approved.review_status == "approved" and approved.version == 1
    assert _states(world, pid) == ["approved"]
    reviews = _reviews(world, pid)
    # The second ask wrote nothing.
    assert [(k, v, e) for k, v, e, _d, _o in reviews] == [
        ("action", 1, OLGA), ("action", 1, ABE)]
    assert reviews[0][3] == {"op": store.REVIEW_OP, "review": "pending", "by": OLGA,
                             "requested_by": OLGA}
    assert reviews[1][3] == {"op": store.REVIEW_OP, "review": "approved", "by": ABE,
                             "requested_by": OLGA}
    assert {o for *_r, o in reviews} == {a}


def test_r8_a_reject_moves_pending_to_rejected_and_a_new_ask_is_a_conflict(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pid = await _package_with_versions(a)
        await _request(a, pid)
        async with tenant_session(a) as db:
            got = await store.reject_version(db, OLGA, pid, 1, can_publish=True)
        with pytest.raises(store.ApprovalConflict):
            await _request(a, pid)
        return pid, got

    pid, got = _go(world, go)
    assert got.review_status == "rejected"
    assert _states(world, pid) == ["rejected"]
    assert [d["review"] for _k, _v, _e, d, _o in _reviews(world, pid)] == ["pending", "rejected"]


# ── b. Org B ───────────────────────────────────────────────────────────────


def test_r8_org_b_cannot_approve_reject_or_ask_for_a_version_of_org_a(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> Any:
        pid = await _package_with_versions(a)
        await _request(a, pid)
        before = _rows(world, pid)
        got_approve = await _approve(b, pid)
        async with tenant_session(b) as db:
            got_reject = await store.reject_version(db, ABE, pid, 1, can_publish=True)
        got_ask = await _request(b, pid)
        return pid, before, got_approve, got_reject, got_ask

    pid, before, got_approve, got_reject, got_ask = _go(world, go)
    assert (got_approve, got_reject, got_ask) == (None, None, None)
    assert _rows(world, pid) == before and _states(world, pid) == ["pending"]
    assert len(_reviews(world, pid)) == 1


# ── c. A stale second approve ──────────────────────────────────────────────


def test_r8_a_stale_second_approve_is_a_conflict_with_no_second_audit_row(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pid = await _package_with_versions(a)
        await _request(a, pid)
        await _approve(a, pid, who=ABE)
        with pytest.raises(store.ApprovalConflict) as exc:
            await _approve(a, pid, who=CY)
        return pid, exc.value

    pid, err = _go(world, go)
    assert err.current == "approved"
    assert [d["review"] for _k, _v, _e, d, _o in _reviews(world, pid)] == ["pending", "approved"]
    assert _states(world, pid) == ["approved"]


# ── d. One version, and no other column ────────────────────────────────────


def test_r8_an_approval_writes_review_status_of_one_version_only(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pid = await _package_with_versions(a, count=3)
        before = _rows(world, pid)
        await _request(a, pid, v=2)
        await _approve(a, pid, v=2)
        return pid, before

    pid, before = _go(world, go)
    after = _rows(world, pid)
    assert [r[-1] for r in after] == ["auto", "approved", "auto"]
    # Each column but review_status is the same, on each row.
    assert [r[:-1] for r in after] == [r[:-1] for r in before]
    assert after[0] == before[0] and after[2] == before[2]


# ── e. Two approvals at once ───────────────────────────────────────────────


def test_r8_two_approvals_at_once_give_one_winner(world) -> None:
    """Approver 1 holds the row lock in an open transaction. Approver 2, on a
    second connection, waits for it, then reads ``approved`` and raises."""
    a = world.p.org_a

    async def second(pid: str) -> Any:
        try:
            return await _approve(a, pid, who=CY)
        except store.ApprovalConflict as exc:
            return exc

    async def go() -> Any:
        pid = await _package_with_versions(a)
        await _request(a, pid)
        async with tenant_session(a) as db1:
            first = await store.approve_version(db1, ABE, pid, 1, can_publish=True)
            task = asyncio.create_task(second(pid))
            await asyncio.sleep(1.0)
            blocked = not task.done()
        other = await task
        return pid, first, other, blocked

    pid, first, other, blocked = _go(world, go)
    assert blocked, "approver 2 did not wait for the row lock"
    assert first.review_status == "approved"
    assert isinstance(other, store.ApprovalConflict) and other.current == "approved"
    reviews = _reviews(world, pid)
    assert [(d["review"], d["by"]) for _k, _v, _e, d, _o in reviews] == [
        ("pending", OLGA), ("approved", ABE)]


# ── f. Who may approve ─────────────────────────────────────────────────────


def test_r8_the_publisher_and_the_owner_cannot_approve(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pid = await _package_with_versions(a)
        await _request(a, pid)
        refusals = []
        for who, can in ((OLGA, True), (OLGA.upper(), True), (ABE, False)):
            with pytest.raises(store.ApprovalRefused) as exc:
                await _approve(a, pid, who=who, can=can)
            refusals.append(str(exc.value))
        return pid, refusals

    pid, refusals = _go(world, go)
    assert refusals == [store.SELF_APPROVAL, store.SELF_APPROVAL, store.NO_PUBLISH_RIGHT]
    assert _states(world, pid) == ["pending"]
    assert len(_reviews(world, pid)) == 1


def test_r8_an_unbound_session_reads_no_version(world) -> None:
    """With no tenant bound, the RLS policy refuses the locked read (or it
    finds nothing). Either way the call moves nothing and writes no row."""
    a = world.p.org_a

    async def go() -> Any:
        pid = await _package_with_versions(a)
        await _request(a, pid)
        session = get_session_factory()()
        try:
            got: Any = await store.approve_version(session, ABE, pid, 1, can_publish=True)
        except Exception as exc:  # the RLS refusal is a driver error
            got = exc
        finally:
            await session.rollback()
            await session.close()
        return pid, got

    pid, got = _go(world, go)
    assert got is None or "uuid" in str(got)
    assert _states(world, pid) == ["pending"]
    assert len(_reviews(world, pid)) == 1
