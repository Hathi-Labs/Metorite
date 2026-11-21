"""WS-TOF TOF-3a F12c: the repair, the approve and the undo on a real database (R8).

Spec: ``project-docs/specs/ai_studio_rows/F12.md``, "The approve and the
undo" and "Done when". Code: the routes ``repair_skill``,
``approve_skill_repair``, ``skill_run_history`` and ``undo_skill_version``
of ``gateway/routes/studio/skills.py``, and ``gateway/studio/skill_repair.py``.
The hermetic half is ``tests/unit/test_skill_repair.py``.

The routes run on a phase-4 promoted scratch database, as the NOBYPASSRLS
app role, so FORCE RLS binds. ``skill_tests.run_suite`` is real, and it reads
the skill from the database. The runner and the meter are fakes, so no model
call and no credit read happen. Run it on a PRIVATE ladder database, one R8
suite at a time::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_skill_repair_r8.py -q -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**What this proves.** The repair reads the failed input from the chat of the
member by its hash, and writes one ``proposal`` row with no word of it. The
unique index refuses a second proposal for one failed row. The approve of a
proposal that passed writes version N+1 with the proposed body and the base
tests, and the skill keeps its state and its ``shared_version``. A stale
``expected_version`` and a body that is not the proposal get 409. Two approves
at once give one 201 and one 409. The F7 undo then writes N+2 with the body
and the hash of N. Member 2, org B and an org outside the flag get 404.

**The mutations (R7).** Drop ``FOR UPDATE`` from the approve, and
``test_r8_two_approves_at_once_give_one_version`` can write two versions.
Drop the ``created_by`` filter of ``failed_input``, and
``test_r8_the_input_is_the_members_own`` goes red.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_auth import UserContext, UserRole
from acb_auth.access import build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.tenant_redis import TenantRedis
from acb_skills.skill_dry_run import HoldAndRecord, current_dry_run
from agent_framework import FunctionTool
from fastapi import HTTPException
from gateway.studio import skill_repair, skill_tests
from gateway.studio import skill_runs as sr
from gateway.studio import skills_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_skill_tests import SlotRedis

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@f12c.test"
BEN = f"ben.{TAG}@f12c.test"
_LIKE = f"%.{TAG}@f12c.test"
WORDS = "Call Asha on 555 0100 about the Acme quote"
BITS = ("Asha", "555", "0100")
BODY = {"description": "Use this to log a call.", "instructions": "Make a task.",
        "actions": ["tool:create_task"],
        "tests": [{"id": "t1", "input": "Log a call with Ravi",
                   "expect": [{"action": "tool:create_task", "fields": {"title": "Call Ravi"}}]}]}
FIX = "Make a task. Put the name of the person in the title."
CATALOG = [{"id": "tool:create_task", "class": "B"}]


def _create_task_tool() -> FunctionTool:
    async def create_task(title: str = "") -> str:
        raise AssertionError("a held write ran")

    return FunctionTool(func=create_task, name="create_task", description="d")


class _Runner:
    """A fake turn: it arms the record and holds one ``create_task`` call."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, message: str, *, run_id: str, thread_id: str, org: str,
                       member: str) -> None:
        record = current_dry_run()
        assert record is not None
        self.messages.append(message)
        record.armed = record.loaded = True

        async def never() -> None:
            raise AssertionError("a held call ran")

        ctx = SimpleNamespace(result=None, function=_create_task_tool(),
                              arguments={"title": "Call Ravi"})
        await HoldAndRecord().process(ctx, never)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    from gateway.jobs import headless_turn
    from gateway.routes.studio import actions

    p = promoted
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "studio_chat_tools_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "studio_skill_tests_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "studio_skill_repair_orgs", p.org_a, raising=False)
    monkeypatch.setattr(settings, "studio_skill_tests_cap_credits", "5", raising=False)
    monkeypatch.setattr("acb_skills.subagent_contract.annotated_read_only",
                        lambda _name: False)
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
    runner = _Runner()

    async def run(message: str, **kw: Any) -> None:
        await runner(message, **kw)

    async def meter(org: str, member: str, run_id: str) -> Decimal:
        return Decimal("0.25")

    async def catalog_rows() -> list[dict[str, Any]]:
        return [dict(r) for r in CATALOG]

    monkeypatch.setattr(skill_tests, "agent_runner", run)
    monkeypatch.setattr(headless_turn, "console_meter", meter)
    monkeypatch.setattr(actions, "catalog_rows", catalog_rows)
    slot = SlotRedis()
    monkeypatch.setattr(skill_tests, "get_tenant_redis", lambda: TenantRedis(slot))
    yield SimpleNamespace(p=p, runner=runner, threads=[])
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})
        c.execute(text("DELETE FROM chat_session WHERE user_id LIKE :t"), {"t": _LIKE})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


@contextlib.contextmanager
def _bound(org: str) -> Any:
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _user(org: str, email: str = ANA) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


def _chat(world: Any, org: str, member: str, content: str) -> str:
    """One chat of *member* with one typed message, seeded as the superuser."""
    thread = f"thread-{uuid.uuid4().hex}"
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, organization_id) "
            "VALUES (:id, :u, 'orchestrator', CAST(:o AS uuid))"),
            {"id": thread, "u": member, "o": org})
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "author_email, author_kind, organization_id) VALUES "
            "(:id, :s, 'user', :c, 1, :m, 'human', CAST(:o AS uuid))"),
            {"id": f"m-{uuid.uuid4().hex}", "s": thread, "c": content, "m": member, "o": org})
    return thread


async def _skill(org: str, *, publish: bool = True) -> str:
    async with tenant_session(org) as db:
        made = await store.create_skill(db, ANA, name="Log a call",
                                        slug=f"log-{uuid.uuid4().hex[:8]}")
        await store.add_version(db, ANA, made.id, dict(BODY))
        if publish:
            await store.set_state(db, ANA, made.id, "published")
        return made.id


async def _failure(org: str, sid: str, thread: str, member: str = ANA) -> sr.SkillRun:
    async with tenant_session(org) as db:
        return await sr.record_live(
            db, member, sid, version=1, passed=False, reason="tool_error",
            body_key=skill_repair.body_key(BODY), input_text=WORDS,
            run_id=f"run-{uuid.uuid4().hex}", thread_id=thread, detail="create_task: Boom")


async def _repair(org: str, sid: str, failure: str, email: str = ANA, **body: Any) -> Any:
    from gateway.routes.studio import skills as route

    with _bound(org):
        return await route.repair_skill(sid, {"failure_id": failure, "instructions": FIX, **body},
                                        user=_user(org, email))


async def _approve(org: str, sid: str, out: dict[str, Any], email: str = ANA,
                   **over: Any) -> Any:
    from gateway.routes.studio import skills as route

    body = {"expected_version": out["base_version"], "instructions": out["instructions"],
            "remove_actions": out["remove_actions"], **over}
    with _bound(org):
        return await route.approve_skill_repair(sid, out["proposal_id"], body,
                                                user=_user(org, email))


async def _code(coro: Any) -> tuple[int, Any]:
    try:
        await coro
    except HTTPException as exc:
        return exc.status_code, exc.detail
    return 200, None


def _rows(world: Any, org: str) -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        async with tenant_session(org) as db:
            return [dict(r._mapping) for r in (await db.execute(text(
                "SELECT kind, passed, reason, repair_of::text AS repair_of, version, "
                "to_jsonb(r)::text AS whole FROM studio_skill_runs r "
                "WHERE created_by LIKE :t ORDER BY created_at, kind"), {"t": _LIKE})).all()]
    return _go(world, go)


def test_r8_the_repair_writes_one_proposal_and_no_input(world) -> None:
    a = world.p.org_a
    thread = _chat(world, a, ANA, WORDS)

    async def go() -> tuple[Any, Any, Any]:
        sid = await _skill(a)
        failed = await _failure(a, sid, thread)
        out = await _repair(a, sid, failed.id)
        again = await _code(_repair(a, sid, failed.id, instructions=FIX + " Then stop."))
        return failed, out, again

    failed, out, again = _go(world, go)
    assert out["passed"] is True and out["next_version"] == 2 and out["tests"] == 1, out
    assert world.runner.messages == ["Log a call with Ravi", WORDS]
    assert again[0] == 409 and again[1]["code"] == "already_repaired"
    assert len(world.runner.messages) == 2, "the second repair spent nothing"
    rows = _rows(world, a)
    [proposal] = [r for r in rows if r["kind"] == "proposal"]
    assert proposal["passed"] is True and proposal["repair_of"] == failed.id
    for row in rows:
        for bit in BITS:
            assert bit not in row["whole"], bit
    assert _rows(world, world.p.org_b) == []


def test_r8_the_index_refuses_a_second_proposal(world) -> None:
    a = world.p.org_a
    thread = _chat(world, a, ANA, WORDS)

    async def go() -> None:
        sid = await _skill(a)
        failed = await _failure(a, sid, thread)
        key = "ab" * 32
        async with tenant_session(a) as db:
            await sr.record_proposal(db, ANA, sid, failed.id, passed=False,
                                     reason="extra_write", body_key=key)
        async with tenant_session(a) as db:
            with pytest.raises(sr.AlreadyRepaired):
                await sr.record_proposal(db, ANA, sid, failed.id, passed=True,
                                         reason="passed", body_key=key)

    _go(world, go)


def test_r8_the_input_is_the_members_own(world) -> None:
    a = world.p.org_a
    mine = _chat(world, a, ANA, WORDS)
    theirs = _chat(world, a, BEN, WORDS)
    want = sr.input_hash(a, WORDS)

    async def go() -> tuple[Any, ...]:
        async with tenant_session(a) as db:
            return (await skill_repair.failed_input(db, ANA, mine, want),
                    await skill_repair.failed_input(db, ANA, theirs, want),
                    await skill_repair.failed_input(db, BEN, mine, want),
                    await skill_repair.failed_input(db, ANA, mine, "cd" * 32))

    assert _go(world, go) == (WORDS, None, None, None)

    async def go_b() -> Any:
        async with tenant_session(world.p.org_b) as db:
            return await skill_repair.failed_input(db, ANA, mine, want)

    assert _go(world, go_b) is None, "org B reads no message of org A"


def test_r8_approve_writes_the_next_version_and_undo_restores(world) -> None:
    from gateway.routes.studio import skills as route

    a = world.p.org_a
    thread = _chat(world, a, ANA, WORDS)

    async def go() -> tuple[Any, ...]:
        sid = await _skill(a)
        failed = await _failure(a, sid, thread)
        out = await _repair(a, sid, failed.id)
        stale = await _code(_approve(a, sid, out, expected_version=2))
        other = await _code(_approve(a, sid, out, instructions="Another text."))
        saved = await _approve(a, sid, out)
        second = await _code(_approve(a, sid, out))
        with _bound(a):
            undone = await route.undo_skill_version(sid, {"expected_version": 2},
                                                    user=_user(a))
        async with tenant_session(a) as db:
            versions = {v.version: v for v in [
                await store.get_version(db, ANA, sid, n) for n in (1, 2, 3)]}
        return stale, other, saved, second, undone, versions

    stale, other, saved, second, undone, versions = _go(world, go)
    assert stale[0] == 409 and other[0] == 409 and second[0] == 409
    assert saved["version"]["version"] == 2 and saved["skill"]["state"] == "published"
    assert saved["message"].startswith("I saved version 2. The skill stays published.")
    v1, v2, v3 = versions[1], versions[2], versions[3]
    assert v2.body["instructions"] == FIX and v2.body["tests"] == BODY["tests"]
    assert v2.created_by == ANA
    assert undone["version"]["version"] == 3 and undone["skill"]["state"] == "draft"
    assert v3.body == v1.body and v3.content_hash == v1.content_hash


def test_r8_two_approves_at_once_give_one_version(world) -> None:
    a = world.p.org_a
    thread = _chat(world, a, ANA, WORDS)

    async def go() -> tuple[Any, Any]:
        sid = await _skill(a)
        failed = await _failure(a, sid, thread)
        out = await _repair(a, sid, failed.id)
        both = await asyncio.gather(_code(_approve(a, sid, out)), _code(_approve(a, sid, out)))
        async with tenant_session(a) as db:
            return both, await store.list_versions(db, ANA, sid)

    both, versions = _go(world, go)
    assert sorted(status for status, _ in both) == [200, 409]
    assert [v.version for v in versions] == [2, 1]


def test_r8_a_shared_skill_keeps_its_shared_version(world) -> None:
    a = world.p.org_a
    thread = _chat(world, a, ANA, WORDS)

    async def go() -> str:
        sid = await _skill(a)
        async with tenant_session(a) as db:
            await store.request_share(db, ANA, sid, 1)
            await store.approve_share(db, BEN, sid, 1, can_publish=True)
        failed = await _failure(a, sid, thread)
        out = await _repair(a, sid, failed.id)
        await _approve(a, sid, out)
        return sid

    sid = _go(world, go)
    with world.p.admin_engine.connect() as c:
        row = c.execute(text("SELECT shared_version, current_version, state FROM studio_skills "
                             "WHERE id = CAST(:s AS uuid)"), {"s": sid}).first()
    assert tuple(row) == (1, 2, "published")


def test_r8_another_member_another_org_and_a_dark_org_get_404(world) -> None:
    from gateway.routes.studio import skills as route

    a, b = world.p.org_a, world.p.org_b
    thread = _chat(world, a, ANA, WORDS)

    async def go() -> list[Any]:
        sid = await _skill(a)
        failed = await _failure(a, sid, thread)
        got = [await _code(_repair(a, sid, failed.id, email=BEN))]
        with _bound(a):
            got.append(await _code(route.skill_run_history(sid, user=_user(a, BEN))))
        # Org B is off the flag, so each route is 404 before any read.
        got.append(await _code(_repair(b, sid, failed.id)))
        with _bound(b):
            got.append(await _code(route.skill_run_history(sid, user=_user(b))))
        # Org B on the flag still sees no skill and no failed row of org A.
        settings = get_settings()
        settings.studio_skill_repair_orgs = f"{a},{b}"
        try:
            got.append(await _code(_repair(b, sid, failed.id)))
            with _bound(b):
                got.append(await _code(route.skill_run_history(sid, user=_user(b))))
        finally:
            settings.studio_skill_repair_orgs = a
        out = await _repair(a, sid, failed.id)
        got.append(await _code(_approve(a, sid, out, email=BEN)))
        got.append(await _code(_approve(b, sid, out)))
        with _bound(a):
            history = await route.skill_run_history(sid, user=_user(a))
        got.append(history)
        return got

    got = _go(world, go)
    assert [g[0] for g in got[:-1]] == [404] * 8, got[:-1]
    history = got[-1]
    # The history holds failed rows only, so the proposal that passed is not in it.
    assert [r["reason"] for r in history["runs"]] == ["tool_error"]
    assert history["others"] == {}
    assert world.runner.messages == ["Log a call with Ravi", WORDS]


def test_r8_it_was_wrong_writes_one_failed_row_for_the_run(world) -> None:
    """The newest passed live row of the member, then one ``member_said_wrong``
    row with its pointers. A second "it was wrong" for that run gets 409."""
    from gateway.routes.studio import skills as route

    a = world.p.org_a
    thread = _chat(world, a, ANA, WORDS)

    async def go() -> tuple[Any, Any, Any]:
        sid = await _skill(a)
        async with tenant_session(a) as db:
            passed = await sr.record_live(
                db, ANA, sid, version=1, passed=True, reason="passed",
                body_key=skill_repair.body_key(BODY), input_text=WORDS,
                run_id=f"run-{uuid.uuid4().hex}", thread_id=thread)
        with _bound(a):
            out = await route.repair_skill(sid, {"said_wrong": True, "instructions": FIX},
                                           user=_user(a))
            again = await _code(route.repair_skill(
                sid, {"said_wrong": True, "instructions": FIX + " Then stop."}, user=_user(a)))
        return passed, out, again

    passed, out, again = _go(world, go)
    assert out["passed"] is True, out
    assert again[0] == 409 and again[1]["code"] == "already_repaired"
    rows = _rows(world, a)
    wrong = [r for r in rows if r["kind"] == "live" and r["reason"] == "member_said_wrong"]
    assert len(wrong) == 1 and passed.run_id in wrong[0]["whole"]
    [proposal] = [r for r in rows if r["kind"] == "proposal"]
    assert proposal["passed"] is True
    for row in rows:
        for bit in BITS:
            assert bit not in row["whole"], bit


def test_r8_a_refused_proposal_and_a_row_that_cannot_start_a_repair(world) -> None:
    """Review round 1: a proposal that the pack builder refuses gets 422 and
    writes no row, so the one repair stays free. A passed live row, a row of
    another member and an id that does not exist give one 404
    ``no_failed_run``."""
    a = world.p.org_a
    thread = _chat(world, a, ANA, WORDS)

    async def go() -> tuple[Any, ...]:
        sid = await _skill(a)
        failed = await _failure(a, sid, thread)
        bad = await _code(_repair(a, sid, failed.id, instructions="Make a task for {{name}}."))
        async with tenant_session(a) as db:
            passed = await sr.record_live(
                db, ANA, sid, version=1, passed=True, reason="passed",
                body_key=skill_repair.body_key(BODY), input_text=WORDS,
                run_id=f"run-{uuid.uuid4().hex}", thread_id=thread)
        theirs = await _failure(a, sid, thread, member=BEN)
        answers = [await _code(_repair(a, sid, rid))
                   for rid in (passed.id, theirs.id, str(uuid.uuid4()))]
        out = await _repair(a, sid, failed.id)
        return bad, answers, out

    bad, answers, out = _go(world, go)
    assert bad[0] == 422 and bad[1]["code"] == "unresolved_variable"
    assert answers[0][0] == 404 and answers[0][1]["code"] == "no_failed_run"
    assert answers[0] == answers[1] == answers[2]
    assert out["passed"] is True, "the refusal left the one repair free"
    assert len([r for r in _rows(world, a) if r["kind"] == "proposal"]) == 1
