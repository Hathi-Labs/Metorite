"""WS-STU J2c (WS-TOF TOF-4a) — the share of a skill with the org, on a real
database (R8).

Spec: ``project-docs/specs/ai_studio_rows/J2c.md``. Code: the share routes
of ``gateway/routes/studio/skills.py`` over ``gateway/studio/skills_store.py``,
and the loadable reads of the same store (``list_loadable`` and
``loadable_versions``). Migration: the file that adds
``studio_skills_shared_version``, found by CONTENT (R1). The hermetic twin is
``tests/unit/test_studio_skill_share.py``.

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds each statement. Each call binds
its org as the auth layer does. Org A has Owen (the owner of each skill),
Ada (a holder of ``studio:publish``) and Mel (a member with
``feature:studio`` only). Org B has Zed, a holder.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_skill_share_r8.py -v -rs -p no:randomly

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J2c).**

a. Owen asks (202 ``pending``), a second ask writes nothing, and Ada
   approves. Mel then loads the skill.
b. A later version of Owen reaches no other member: Mel loads the pinned
   version, and Owen loads the newest one.
c. Owen cannot approve his own share (403), also as a holder. Mel, with no
   right, cannot approve or read the list (403). Nothing changes.
d. An approved or a rejected version cannot be asked again (409).
e. An unshare returns the skill to ``private``, and Mel loads it no more on
   the next read.
f. Org B never sees org A's skill, ask or share: the list, the approve, the
   unshare and the loadable reads.
g. The CHECK of the table refuses ``system``, and a shared version needs
   ``org``.
h. The holder's list of shared skills (J2d follow-up) gives only the shared
   skills of the bound org. Mel gets 403, and another state is 422.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.routes.studio import skills
from gateway.studio import skills_store as store
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "ADD CONSTRAINT studio_skills_shared_version" in p.read_text(encoding="utf-8"))

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@j2c.test"
ADA = f"ada.{TAG}@j2c.test"
MEL = f"mel.{TAG}@j2c.test"
ZED = f"zed.{TAG}@j2c.test"
_LIKE = f"%.{TAG}@j2c.test"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


def _user(org: str, email: str, grants: tuple[str, ...]) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


async def _call(route: str, org: str, email: str, grants: tuple[str, ...] = STUDIO,
                **kwargs: Any) -> tuple[int, Any]:
    """Call the real handler *route* with *org* bound, as the auth layer
    binds it. Answers the status and the answer, or the detail of a refusal."""
    handler = getattr(skills, route)
    token = bind_tenant(org)
    try:
        got = await handler(user=_user(org, email, grants), **kwargs)
        return (202 if route == "request_skill_share" else 200), got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _body(n: int) -> dict[str, Any]:
    return {"description": f"Use this for quotes, round {n}.",
            "instructions": f"Rules of round {n}: read the price list, then fill the template.",
            "actions": ["knowledge.get_page"]}


async def _published(org: str, email: str = OWEN, versions: int = 1) -> str:
    """A published skill of *email* with *versions* versions."""
    async with tenant_session(org) as db:
        skill = await store.create_skill(
            db, email, name="Quotes", slug=f"quotes-{TAG}-{next(_COUNTER)}")
        for n in range(versions):
            await store.add_version(db, email, skill.id, _body(n + 1))
        await store.set_state(db, email, skill.id, "published")
    return skill.id


async def _add_and_publish(org: str, sid: str) -> int:
    """A new version of Owen, published again (the route of a new version
    returns the skill to draft first)."""
    async with tenant_session(org) as db:
        got = await store.add_version(db, OWEN, sid, _body(99))
        await store.set_state(db, OWEN, sid, "published")
    assert got is not None
    return got.version


async def _loads(org: str, member: str, sid: str) -> tuple[list[tuple[str, int | None]],
                                                          list[tuple[str, int]]]:
    """What a turn of *member* loads: the heads and the version pairs."""
    async with tenant_session(org) as db:
        heads = [(s.id, s.current_version) for s in await store.list_loadable(db, member)
                 if s.id == sid]
        pairs = [(s.id, v.version) for s, v in await store.loadable_versions(db, member, [sid])]
    return heads, pairs


def _row(world: Any, sid: str) -> dict[str, Any]:
    with world.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT visibility, shared_version, share_state, share_version, "
            "share_requested_by, share_requested_at, share_decided_by "
            "FROM studio_skills WHERE id = CAST(:i AS uuid)"), {"i": sid}).mappings().first()
    return dict(row) if row else {}


# ── a, b. Ask, approve, load the pinned version ────────────────────────────


def test_r8_an_approved_share_loads_the_pinned_version_for_another_member(world) -> None:
    a = world.org_a

    async def go() -> dict[str, Any]:
        sid = await _published(a, versions=2)
        out: dict[str, Any] = {"sid": sid, "before": await _loads(a, MEL, sid)}
        out["ask"] = await _call("request_skill_share", a, OWEN, skill_id=sid, version="1")
        out["again"] = await _call("request_skill_share", a, OWEN, skill_id=sid, version="1")
        out["approve"] = await _call("approve_skill_share", a, ADA, PUBLISHER,
                                     skill_id=sid, version="1")
        out["mel"] = await _loads(a, MEL, sid)
        out["owen"] = await _loads(a, OWEN, sid)
        out["newest"] = await _add_and_publish(a, sid)
        out["mel_after"] = await _loads(a, MEL, sid)
        out["owen_after"] = await _loads(a, OWEN, sid)
        return out

    got = _run(world, go)
    sid = got["sid"]
    assert got["before"] == ([], [])
    assert got["ask"][0] == 202 and got["ask"][1]["share_state"] == "pending"
    # A second ask writes nothing: the same time of the ask.
    assert got["again"] == got["ask"]
    assert got["approve"][0] == 200, got["approve"]
    assert got["approve"][1]["visibility"] == "org"
    assert got["approve"][1]["shared_version"] == 1
    assert got["approve"][1]["decided_by"] == ADA
    # Mel loads version 1, the pinned one. Owen loads his newest, version 2.
    assert got["mel"] == ([(sid, 1)], [(sid, 1)])
    assert got["owen"] == ([(sid, 2)], [(sid, 2)])
    # b. A later version of Owen reaches no other member.
    assert got["newest"] == 3
    assert got["mel_after"] == ([(sid, 1)], [(sid, 1)])
    assert got["owen_after"] == ([(sid, 3)], [(sid, 3)])


# ── c. The right and the owner ─────────────────────────────────────────────


def test_r8_the_owner_and_a_member_without_the_right_cannot_approve(world) -> None:
    a = world.org_a

    async def go() -> dict[str, Any]:
        sid = await _published(a)
        await _call("request_skill_share", a, OWEN, skill_id=sid, version="1")
        return {
            "sid": sid,
            "self": await _call("approve_skill_share", a, OWEN, PUBLISHER,
                                skill_id=sid, version="1"),
            "mel": await _call("approve_skill_share", a, MEL, skill_id=sid, version="1"),
            "mel_reject": await _call("reject_skill_share", a, MEL, skill_id=sid, version="1"),
            "mel_list": await _call("list_skill_shares", a, MEL, state="pending"),
            "ada_list": await _call("list_skill_shares", a, ADA, PUBLISHER, state="pending"),
            "mel_loads": await _loads(a, MEL, sid),
        }

    got = _run(world, go)
    sid = got["sid"]
    assert got["self"][0] == 403 and got["self"][1]["code"] == "forbidden"
    assert got["mel"][0] == 403 and got["mel_reject"][0] == 403
    assert got["mel_list"][0] == 403
    row = _row(world, sid)
    assert (row["visibility"], row["share_state"], row["shared_version"]) == (
        "private", "pending", None)
    assert got["mel_loads"] == ([], [])
    # Ada reads the ask, with the body of the asked version.
    assert got["ada_list"][0] == 200
    mine = [s for s in got["ada_list"][1]["shares"] if s["skill_id"] == sid]
    assert len(mine) == 1 and mine[0]["version"] == 1 and mine[0]["owner_email"] == OWEN
    assert mine[0]["body"]["instructions"].startswith("Rules of round 1")


def test_r8_a_version_that_nobody_asked_for_is_404(world) -> None:
    a = world.org_a

    async def go() -> list[tuple[int, Any]]:
        sid = await _published(a, versions=2)
        out = [await _call("approve_skill_share", a, ADA, PUBLISHER, skill_id=sid,
                           version="1"),
               await _call("unshare_skill", a, ADA, PUBLISHER, skill_id=sid)]
        await _call("request_skill_share", a, OWEN, skill_id=sid, version="2")
        out.append(await _call("approve_skill_share", a, ADA, PUBLISHER, skill_id=sid,
                               version="1"))
        # Mel asks for a skill of Owen, and Owen for a version that is not there.
        out.append(await _call("request_skill_share", a, MEL, skill_id=sid, version="2"))
        out.append(await _call("request_skill_share", a, OWEN, skill_id=sid, version="9"))
        return out

    got = _run(world, go)
    assert [s for s, _ in got] == [404, 404, 404, 404, 404], got


# ── d. A decided version cannot be asked again ─────────────────────────────


@pytest.mark.parametrize("move", ["approve_skill_share", "reject_skill_share"])
def test_r8_a_decided_version_cannot_be_asked_again(world, move: str) -> None:
    a = world.org_a

    async def go() -> dict[str, Any]:
        sid = await _published(a, versions=2)
        await _call("request_skill_share", a, OWEN, skill_id=sid, version="1")
        decided = await _call(move, a, ADA, PUBLISHER, skill_id=sid, version="1")
        return {
            "sid": sid, "decided": decided,
            "again": await _call("request_skill_share", a, OWEN, skill_id=sid, version="1"),
            "twice": await _call(move, a, ADA, PUBLISHER, skill_id=sid, version="1"),
            "newer": await _call("request_skill_share", a, OWEN, skill_id=sid, version="2"),
        }

    got = _run(world, go)
    assert got["decided"][0] == 200
    assert got["again"][0] == 409 and got["again"][1]["code"] == "share_conflict"
    assert got["twice"][0] == 409
    assert got["newer"][0] == 202 and got["newer"][1]["share_version"] == 2
    row = _row(world, got["sid"])
    if move == "reject_skill_share":
        assert (row["visibility"], row["shared_version"]) == ("private", None)
    else:
        # The older approved version stays shared while version 2 waits.
        assert (row["visibility"], row["shared_version"]) == ("org", 1)


# ── e. An unshare stops the load at the next read ──────────────────────────


@pytest.mark.parametrize(("who", "grants"), [(OWEN, STUDIO), (ADA, PUBLISHER)])
def test_r8_an_unshare_stops_the_load_at_the_next_read(world, who: str,
                                                        grants: tuple[str, ...]) -> None:
    a = world.org_a

    async def go() -> dict[str, Any]:
        sid = await _published(a)
        await _call("request_skill_share", a, OWEN, skill_id=sid, version="1")
        await _call("approve_skill_share", a, ADA, PUBLISHER, skill_id=sid, version="1")
        loaded = await _loads(a, MEL, sid)
        refused = await _call("unshare_skill", a, MEL, skill_id=sid)
        gone = await _call("unshare_skill", a, who, grants, skill_id=sid)
        return {"sid": sid, "loaded": loaded, "refused": refused, "gone": gone,
                "after": await _loads(a, MEL, sid), "owen": await _loads(a, OWEN, sid)}

    got = _run(world, go)
    sid = got["sid"]
    assert got["loaded"] == ([(sid, 1)], [(sid, 1)])
    assert got["refused"][0] == 403
    assert got["gone"][0] == 200 and got["gone"][1]["visibility"] == "private"
    assert got["after"] == ([], [])
    # The owner still loads his own skill.
    assert got["owen"] == ([(sid, 1)], [(sid, 1)])
    row = _row(world, sid)
    assert (row["visibility"], row["shared_version"], row["share_state"]) == (
        "private", None, None)


# ── f. The org wall ────────────────────────────────────────────────────────


def test_r8_org_b_never_sees_org_a_skill_ask_or_share(world) -> None:
    a, b = world.org_a, world.org_b

    async def go() -> dict[str, Any]:
        pending = await _published(a)
        shared = await _published(a)
        await _call("request_skill_share", a, OWEN, skill_id=pending, version="1")
        await _call("request_skill_share", a, OWEN, skill_id=shared, version="1")
        await _call("approve_skill_share", a, ADA, PUBLISHER, skill_id=shared, version="1")
        return {
            "pending": pending, "shared": shared,
            "list": await _call("list_skill_shares", b, ZED, PUBLISHER, state="pending"),
            "approve": await _call("approve_skill_share", b, ZED, PUBLISHER,
                                   skill_id=pending, version="1"),
            "reject": await _call("reject_skill_share", b, ZED, PUBLISHER,
                                  skill_id=pending, version="1"),
            "unshare": await _call("unshare_skill", b, ZED, PUBLISHER, skill_id=shared),
            "ask": await _call("request_skill_share", b, OWEN, skill_id=pending, version="1"),
            "loads": await _loads(b, ZED, shared),
            "owen_b": await _loads(b, OWEN, shared),
            "mel_a": await _loads(a, MEL, shared),
        }

    got = _run(world, go)
    assert got["list"][0] == 200
    ids = {s["skill_id"] for s in got["list"][1]["shares"]}
    assert got["pending"] not in ids and got["shared"] not in ids
    assert got["approve"][0] == 404 and got["reject"][0] == 404
    assert got["unshare"][0] == 404 and got["ask"][0] == 404
    assert got["loads"] == ([], []) and got["owen_b"] == ([], [])
    # Non-vacuity: org A's member loads the shared skill.
    assert got["mel_a"] == ([(got["shared"], 1)], [(got["shared"], 1)])
    assert _row(world, got["pending"])["share_state"] == "pending"
    assert _row(world, got["shared"])["visibility"] == "org"


# ── g. The checks of the table ─────────────────────────────────────────────


def test_r8_the_migration_is_in_place_and_the_check_refuses_system(world) -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS shared_version" in sql
    assert "SET LOCAL lock_timeout" in sql
    a = world.org_a

    async def make() -> str:
        return await _published(a)

    sid = _run(world, make)

    def refused(stmt: str) -> bool:
        async def go() -> None:
            async with tenant_session(a) as db:
                await db.execute(text(stmt), {"i": sid})
        try:
            _run(world, go)
        except DBAPIError:
            return True
        return False

    assert refused("UPDATE studio_skills SET visibility = 'system' WHERE id = CAST(:i AS uuid)")
    # A shared version needs the org visibility, and a version that exists.
    assert refused("UPDATE studio_skills SET shared_version = 1 WHERE id = CAST(:i AS uuid)")
    assert refused("UPDATE studio_skills SET visibility = 'org', shared_version = 5 "
                   "WHERE id = CAST(:i AS uuid)")
    assert refused("UPDATE studio_skills SET share_state = 'pending' "
                   "WHERE id = CAST(:i AS uuid)")
    assert refused("UPDATE studio_skills SET share_state = 'maybe', share_version = 1 "
                   "WHERE id = CAST(:i AS uuid)")
    row = _row(world, sid)
    assert (row["visibility"], row["shared_version"], row["share_state"]) == (
        "private", None, None)


# ── h. The holder's list of shared skills (J2d follow-up) ──────────────────


def test_r8_the_holder_lists_only_the_shared_skills_of_the_bound_org(world) -> None:
    """``GET /skill-shares?state=approved`` gives each skill that org A
    shares, and never a private, a pending-only or a rejected-only skill, an
    unshared skill, or a skill of org B. A shared skill whose newest ask
    waits still shows, with no approver. Mel gets 403. An unknown state is
    422."""
    a, b = world.org_a, world.org_b

    async def decided(org: str, approver: str, approve: bool | None, versions: int = 1) -> str:
        sid = await _published(org, OWEN, versions)
        await _call("request_skill_share", org, OWEN, skill_id=sid, version="1")
        if approve is not None:
            move = "approve_skill_share" if approve else "reject_skill_share"
            got = await _call(move, org, approver, PUBLISHER, skill_id=sid, version="1")
            assert got[0] == 200, got
        return sid

    async def go() -> dict[str, Any]:
        ids = {"private": await _published(a),
               "pending": await decided(a, ADA, None),
               "rejected": await decided(a, ADA, False),
               "shared": await decided(a, ADA, True),
               "newer": await decided(a, ADA, True, versions=2),
               "unshared": await decided(a, ADA, True),
               "b_shared": await decided(b, ZED, True)}
        await _call("request_skill_share", a, OWEN, skill_id=ids["newer"], version="2")
        await _call("unshare_skill", a, OWEN, skill_id=ids["unshared"])
        return {
            "ids": ids,
            "ada": await _call("list_skill_shares", a, ADA, PUBLISHER, state="approved"),
            "zed": await _call("list_skill_shares", b, ZED, PUBLISHER, state="approved"),
            "mel": await _call("list_skill_shares", a, MEL, state="approved"),
            "bad": await _call("list_skill_shares", a, ADA, PUBLISHER, state="rejected"),
        }

    got = _run(world, go)
    ids = got["ids"]
    assert got["ada"][0] == 200 and got["ada"][1]["state"] == "approved"
    mine = {s["skill_id"]: s for s in got["ada"][1]["shares"]
            if s["skill_id"] in set(ids.values())}
    assert set(mine) == {ids["shared"], ids["newer"]}
    one = mine[ids["shared"]]
    assert (one["owner_email"], one["shared_version"], one["approved_by"]) == (OWEN, 1, ADA)
    assert one["approved_at"] is not None and "body" not in one
    newer = mine[ids["newer"]]
    assert (newer["shared_version"], newer["share_state"], newer["share_version"]) == (
        1, "pending", 2)
    assert newer["approved_by"] is None
    # Org B reads its own shared skill, and nothing of org A.
    theirs = {s["skill_id"] for s in got["zed"][1]["shares"]}
    assert ids["b_shared"] in theirs and not theirs & set(mine)
    assert ids["b_shared"] not in {s["skill_id"] for s in got["ada"][1]["shares"]}
    assert got["mel"][0] == 403 and got["mel"][1]["message"] == skills.NO_SHARED_LIST_RIGHT
    assert got["bad"][0] == 422
