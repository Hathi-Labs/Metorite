"""The skill routes of AI Studio on a real database (STU-F2a, R8).

Spec: ``project-docs/specs/ai_studio.md`` §7 and §14 F2a. Code:
``gateway/routes/studio/skills.py`` over ``gateway/studio/skills_store.py``.

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Each call binds its org as the
auth layer does, and the handler opens ``tenant_session()`` with no org.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_skills_routes_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (F2a).**

a. Org B gets 404 on org A's skill by id on every route, and its list is
   empty. That holds for a member of org B with the same email too.
b. Member 2 of org A gets 404 on member 1's skill, and does not see it in
   the list.
c. Two ``POST .../versions`` calls at once give versions 1 and 2.
d. The catalog check reads the bound org: an action that the catalog does
   not serve writes nothing, and a workflow of org B is not an action of
   org A.
e. A publish needs a version, and a new version of a published skill
   returns it to ``draft``.
f. Two moves at once give one 200 and one 409, and the skill ends in a state
   that ``skills.TRANSITIONS`` allows. The move reads the row ``FOR UPDATE``,
   so the second move reads the state that the first move wrote.
g. A slug, a name or a description that Postgres refuses (a final newline,
   a NUL, a lone surrogate) is 422, never 500. It writes no row, and the
   text does not echo the value.

**The mutations (R7).**

* M1. In ``skills._check_body``, comment out the catalog check (the
  ``_catalog_problem`` call). ``test_r8_an_unknown_action_writes_nothing``
  and ``test_r8_a_workflow_of_another_org_is_not_an_action`` go red, and so
  does ``test_an_unknown_action_is_422_with_its_path`` of the hermetic suite.
* M2. In ``skills.set_skill_state``, drop ``for_update=True`` from the first
  read. ``test_r8_two_moves_at_once_give_one_200_and_one_409`` goes red, and
  so does ``test_a_move_reads_the_skill_with_its_row_lock`` of the hermetic
  suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.studio import actions, skills
from gateway.studio import skills_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
#: The two members of this suite. The tag lets teardown find their rows.
ANA = f"ana.{TAG}@stuf2a.test"
BEN = f"ben.{TAG}@stuf2a.test"
_LIKE = f"%.{TAG}@stuf2a.test"

_DOORS = sorted(r["id"] for r in actions._manifest_rows())
A1, A2 = _DOORS[0], _DOORS[1]


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    yield promoted
    # The versions go by the cascade of their foreign key.
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})
        c.execute(text("DELETE FROM workflows WHERE name LIKE :p"), {"p": f"% {TAG}"})


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:workflows"]), organization_id=org)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _call(org: str, email: str, route: Callable[..., Awaitable[Any]],
                *args: Any, **kw: Any) -> tuple[int, Any]:
    """Call a real handler with *org* bound, as the auth layer binds it.
    Answers the status and the answer, or the detail of a refusal."""
    token = bind_tenant(org)
    try:
        found = await route(*args, user=_user(org, email), **kw)
        return 200, found
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _body(actions_: list[str] | None = None) -> dict[str, Any]:
    return {"description": "Use this when someone asks for a quote.",
            "instructions": "Read the price list, then fill the template.",
            "actions": [A1, A2] if actions_ is None else actions_}


async def _create(org: str, email: str = ANA, body: dict[str, Any] | None = None,
                  name: str = "Quotes") -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "slug": f"quotes-{TAG}"}
    if body is not None:
        payload["body"] = body
    status, got = await _call(org, email, skills.create_skill, payload=payload)
    assert status == 200, got
    return got


async def _every_id_route(org: str, email: str, sid: str) -> list[tuple[int, Any]]:
    return [
        await _call(org, email, skills.get_skill, sid),
        await _call(org, email, skills.get_skill_version, sid, "1"),
        await _call(org, email, skills.add_skill_version, sid, payload={"body": _body()}),
        await _call(org, email, skills.set_skill_state, sid, payload={"state": "archived"}),
    ]


def _row(world: Any, sid: str) -> tuple[Any, ...]:
    """The skill and its version count, read as the superuser."""
    with world.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT organization_id::text, owner_email, state, visibility, current_version, "
            "(SELECT count(*) FROM studio_skill_versions v WHERE v.skill_id = s.id) "
            "FROM studio_skills s WHERE s.id = CAST(:id AS uuid)"), {"id": sid}).first()
    return tuple(row) if row else ()


def _count(world: Any) -> int:
    with world.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_skills WHERE owner_email LIKE :t"),
            {"t": _LIKE}).scalar_one())


def _seed_workflow(world: Any, org: str) -> str:
    wid = str(uuid.uuid4())
    with world.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO workflows (id, name, owner_email, status, organization_id) "
            "VALUES (:id, :n, :o, 'published', :org)"),
            {"id": wid, "n": f"Flow {TAG}", "o": ANA, "org": org})
    return wid


# ── a. The org wall ────────────────────────────────────────────────────────


def test_r8_org_b_gets_404_on_org_a_skill_on_every_route(world) -> None:
    a, b = world.org_a, world.org_b

    async def go() -> tuple[str, list[list[tuple[int, Any]]], list[Any]]:
        sid = (await _create(a, body=_body()))["skill"]["id"]
        # The same email in org B, and another member of org B.
        answers = [await _every_id_route(b, email, sid) for email in (ANA, BEN)]
        lists = [(await _call(b, email, skills.list_skills))[1]["skills"]
                 for email in (ANA, BEN)]
        return sid, answers, lists

    sid, answers, lists = _run(world, go)
    for per_member in answers:
        assert per_member == [(404, "Not found")] * 4
    assert lists == [[], []]
    # Nothing of org A moved: still a draft with one version.
    assert _row(world, sid) == (world.org_a, ANA, "draft", "private", 1, 1)


def test_r8_org_a_still_reads_its_own_skill(world) -> None:
    """The control of the wall test: the same calls in org A succeed."""
    a = world.org_a

    async def go() -> tuple[str, list[int], list[str]]:
        sid = (await _create(a, body=_body()))["skill"]["id"]
        statuses = [s for s, _ in await _every_id_route(a, ANA, sid)]
        listed = [s["id"] for s in (await _call(a, ANA, skills.list_skills))[1]["skills"]]
        return sid, statuses, listed

    sid, statuses, listed = _run(world, go)
    assert statuses == [200, 200, 200, 200]
    assert sid in listed
    assert _row(world, sid) == (world.org_a, ANA, "archived", "private", 2, 2)


def test_r8_a_body_that_names_org_b_stays_in_org_a(world) -> None:
    a, b = world.org_a, world.org_b

    async def go() -> str:
        status, got = await _call(a, ANA, skills.create_skill, payload={
            "name": "Quotes", "slug": f"named-{TAG}", "organization_id": b,
            "owner_email": BEN, "visibility": "org", "body": _body()})
        assert status == 200, got
        return got["skill"]["id"]

    sid = _run(world, go)
    assert _row(world, sid) == (world.org_a, ANA, "draft", "private", 1, 1)


# ── b. The member wall ─────────────────────────────────────────────────────


def test_r8_member_2_gets_404_on_member_1_skill(world) -> None:
    a = world.org_a

    async def go() -> tuple[str, list[tuple[int, Any]], list[str]]:
        sid = (await _create(a, body=_body()))["skill"]["id"]
        answers = await _every_id_route(a, BEN, sid)
        listed = [s["id"] for s in (await _call(a, BEN, skills.list_skills))[1]["skills"]]
        return sid, answers, listed

    sid, answers, listed = _run(world, go)
    assert answers == [(404, "Not found")] * 4
    assert sid not in listed
    assert _row(world, sid) == (world.org_a, ANA, "draft", "private", 1, 1)


def test_r8_the_slug_is_unique_for_each_member(world) -> None:
    a, b = world.org_a, world.org_b

    async def go() -> list[tuple[int, Any]]:
        payload = {"name": "Quotes", "slug": f"quotes-{TAG}"}
        return [
            await _call(a, ANA, skills.create_skill, payload=payload),
            await _call(a, ANA, skills.create_skill, payload=payload),
            await _call(a, BEN, skills.create_skill, payload=payload),
            await _call(b, ANA, skills.create_skill, payload=payload),
        ]

    got = _run(world, go)
    assert [s for s, _ in got] == [200, 409, 200, 200]
    assert got[1][1] == "You already have a skill with this name. Choose another name."
    assert TAG not in got[1][1]


# ── c. Two writers ─────────────────────────────────────────────────────────


def test_r8_two_new_versions_at_once_get_1_and_2(world) -> None:
    a = world.org_a

    async def go() -> tuple[str, list[tuple[int, Any]]]:
        sid = (await _create(a))["skill"]["id"]
        got = await asyncio.gather(*(
            _call(a, ANA, skills.add_skill_version, sid, payload={"body": _body([action])})
            for action in (A1, A2)))
        return sid, list(got)

    sid, got = _run(world, go)
    assert [s for s, _ in got] == [200, 200]
    assert sorted(v["version"]["version"] for _, v in got) == [1, 2]
    assert _row(world, sid) == (world.org_a, ANA, "draft", "private", 2, 2)


# ── d. The catalog check reads the bound org ───────────────────────────────


def test_r8_an_unknown_action_writes_nothing(world) -> None:
    a = world.org_a

    async def go() -> tuple[int, Any]:
        return await _call(a, ANA, skills.create_skill, payload={
            "name": "Quotes", "slug": f"unknown-{TAG}",
            "body": _body([A1, "crm.no_such_op"])})

    status, detail = _run(world, go)
    assert status == 422
    assert detail.startswith("$.actions[1]:") and "crm.no_such_op" in detail
    assert _count(world) == 0


def test_r8_a_workflow_of_another_org_is_not_an_action(world) -> None:
    a, b = world.org_a, world.org_b
    mine, theirs = _seed_workflow(world, a), _seed_workflow(world, b)

    async def go() -> list[tuple[int, Any]]:
        return [
            await _call(a, ANA, skills.create_skill, payload={
                "name": "Theirs", "slug": f"theirs-{TAG}",
                "body": _body([f"workflow:{theirs}"])}),
            await _call(a, ANA, skills.create_skill, payload={
                "name": "Mine", "slug": f"mine-{TAG}",
                "body": _body([f"workflow:{mine}"])}),
        ]

    (s1, d1), (s2, _) = _run(world, go)
    assert s1 == 422 and d1.startswith("$.actions[0]:")
    assert s2 == 200
    assert _count(world) == 1


# ── e. The states ──────────────────────────────────────────────────────────


def test_r8_a_publish_with_no_version_is_409(world) -> None:
    a = world.org_a

    async def go() -> tuple[str, tuple[int, Any]]:
        sid = (await _create(a))["skill"]["id"]
        return sid, await _call(a, ANA, skills.set_skill_state, sid,
                                payload={"state": "published"})

    sid, got = _run(world, go)
    assert got == (409, "Add a version first")
    assert _row(world, sid) == (world.org_a, ANA, "draft", "private", None, 0)


def test_r8_a_new_version_of_a_published_skill_returns_it_to_draft(world) -> None:
    a = world.org_a

    async def go() -> tuple[str, list[Any]]:
        sid = (await _create(a, body=_body()))["skill"]["id"]
        published = await _call(a, ANA, skills.set_skill_state, sid,
                                payload={"state": "published"})
        added = await _call(a, ANA, skills.add_skill_version, sid,
                            payload={"body": _body([A2])})
        read = await _call(a, ANA, skills.get_skill, sid)
        return sid, [published, added, read]

    sid, (published, added, read) = _run(world, go)
    assert published[0] == 200 and published[1]["skill"]["state"] == "published"
    assert added[0] == 200 and added[1]["skill"]["state"] == "draft"
    assert added[1]["version"]["version"] == 2
    assert read[1]["skill"]["state"] == "draft"
    assert read[1]["version"]["body"]["actions"] == [A2]
    assert _row(world, sid) == (world.org_a, ANA, "draft", "private", 2, 2)


# ── f. Two moves at once ───────────────────────────────────────────────────


def test_r8_two_moves_at_once_give_one_200_and_one_409(world, monkeypatch) -> None:
    """A paused skill gets an archive and a publish at once. The archive moves
    first, and it holds the row until the publish has read the row, or for one
    second. With the row lock, the publish read waits for the archive to
    commit, reads ``archived`` and gets 409. Without the lock, the publish
    reads ``paused``, and its UPDATE writes ``published`` over the archive."""
    a = world.org_a
    real_get, real_set = store.get_skill, store.set_state

    async def go() -> tuple[str, tuple[int, Any], tuple[int, Any]]:
        sid = (await _create(a, body=_body()))["skill"]["id"]
        for state in ("published", "paused"):
            status, got = await _call(a, ANA, skills.set_skill_state, sid,
                                      payload={"state": state})
            assert status == 200, got
        archive_holds = asyncio.Event()
        read_starts = asyncio.Event()
        read_ends = asyncio.Event()

        def _in_publish() -> bool:
            task = asyncio.current_task()
            return task is not None and task.get_name() == "publish"

        async def get_skill(db: Any, member: str, skill_id: str, **kw: Any) -> Any:
            if _in_publish():
                read_starts.set()
            found = await real_get(db, member, skill_id, **kw)
            if _in_publish():
                read_ends.set()
            return found

        async def set_state(db: Any, member: str, skill_id: str, state: str) -> bool:
            moved = await real_set(db, member, skill_id, state)
            if state == "archived":
                archive_holds.set()
                # Hold the row until the publish has read it. With the lock,
                # that read waits for this commit, so stop after one second.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(read_starts.wait(), timeout=10)
                    await asyncio.wait_for(read_ends.wait(), timeout=1)
            return moved

        monkeypatch.setattr(store, "get_skill", get_skill)
        monkeypatch.setattr(store, "set_state", set_state)

        async def publish() -> tuple[int, Any]:
            await archive_holds.wait()
            return await _call(a, ANA, skills.set_skill_state, sid,
                               payload={"state": "published"})

        archived, published = await asyncio.gather(
            asyncio.create_task(_call(a, ANA, skills.set_skill_state, sid,
                                      payload={"state": "archived"}), name="archive"),
            asyncio.create_task(publish(), name="publish"))
        return sid, archived, published

    sid, archived, published = _run(world, go)
    assert archived[0] == 200 and archived[1]["skill"]["state"] == "archived"
    assert published == (409, "The skill is archived. It cannot move to published.")
    # The skill ends in a state that the table allows from paused.
    assert _row(world, sid) == (world.org_a, ANA, "archived", "private", 1, 1)
    assert "archived" in skills.TRANSITIONS["paused"]


# ── g. Text that Postgres refuses ──────────────────────────────────────────

#: The bad texts of the review. Each value holds the suite tag, so a test can
#: prove that the refusal does not echo it.
_BAD_TEXT = {
    "slug_final_newline": ("slug", f"quotes-{TAG}\n"),
    "name_nul": ("name", f"Quotes {TAG}\x00"),
    "description_nul": ("description", f"Prices {TAG}\x00 and terms"),
    "name_lone_surrogate": ("name", f"Quotes {TAG} \ud83d"),
    "description_lone_surrogate": ("description", f"Half {TAG} \ud83d of a pair"),
}


@pytest.mark.parametrize(("field", "value"), list(_BAD_TEXT.values()), ids=list(_BAD_TEXT))
def test_r8_text_that_postgres_refuses_is_422_and_writes_nothing(world, field, value) -> None:
    """Python's ``$`` matches before a final newline, and Postgres refuses a
    NUL and a lone surrogate. The store refuses each first. So the route
    answers 422, and no DBAPIError gives a 500 with the value in its log."""
    a = world.org_a

    async def go() -> tuple[int, Any]:
        payload: dict[str, Any] = {"name": "Quotes", "slug": f"text-{TAG}",
                                   "description": "", "body": _body()}
        payload[field] = value
        return await _call(a, ANA, skills.create_skill, payload=payload)

    status, detail = _run(world, go)
    assert status == 422, detail
    assert TAG not in detail
    assert not any(ch in detail for ch in ("\x00", "\ud83d", "\n"))
    assert _count(world) == 0
