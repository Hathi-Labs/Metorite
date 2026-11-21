"""WS-TOF TOF-3a F12a: the store of skill runs on a real database (R8).

Spec: ``project-docs/specs/ai_studio_rows/F12.md``, "The table
``studio_skill_runs`` (F12a)" and "Done when". Code:
``gateway/studio/skill_runs.py``. Migration: the file that creates
``studio_skill_runs`` (468 at build), found by its CONTENT, never by its
number (R1).

The store runs on a phase-4 promoted scratch database, as the NOBYPASSRLS
app role, so FORCE RLS binds. Two organizations, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_skill_runs_r8.py -q -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**What this proves.**

1. The table: its columns, ENABLE and FORCE RLS, one policy with USING and
   WITH CHECK, the two unique partial indexes and the trigger. A second run
   of the migration changes nothing.
2. The writers write each kind. A live row keeps the hash of the input and
   never the input. A proposal takes its version and its input hash from
   the failed row.
3. Org B reads none of the rows of org A, and the policy refuses a row
   stamped with org A from a session of org B. A writer of org B cannot
   name a skill or a failed row of org A.
4. The trigger refuses each UPDATE, as the app role and as the superuser.
5. A delete of the skill, and a delete of the org, delete the rows.
6. Each CHECK refuses its bad row.
7. One proposal for each failed row, and one failed live row for each run
   of a skill, by the unique indexes.
8. The member purge deletes the rows of the member in this org only.
9. The readers: the history, the repeat check, the newest passed live row.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. Drop the trigger: ``test_r8_a_stored_run_cannot_be_updated`` goes red.
* M2. Drop the index ``studio_skill_runs_one_repair``:
  ``test_r8_one_proposal_for_each_failed_row`` goes red.
* M3. Drop the index ``studio_skill_runs_one_failed_live``:
  ``test_r8_one_failed_live_row_for_each_run`` goes red.
* M4. Drop the policy: ``test_r8_org_b_reads_and_writes_nothing_of_org_a``
  goes red.
* M5. Make the foreign key ``skill_id`` alone to ``studio_skills (id)``:
  ``test_r8_a_writer_of_org_b_cannot_name_a_skill_of_org_a`` goes red on
  the raw insert.
* M6. Drop a CHECK: its case of ``test_r8_each_check_refuses_its_bad_row``
  goes red.
* M7. Drop the org predicate of the purge clause:
  ``test_r8_a_member_purge_removes_the_runs_of_this_org_only[superuser]``
  goes red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.studio import skill_runs as sr
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
_MARK = "CREATE TABLE IF NOT EXISTS studio_skill_runs ("

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@f12a.test"
BEN = f"ben.{TAG}@f12a.test"
_LIKE = f"%.{TAG}@f12a.test"
KEY = "ab" * 32
KEY2 = "cd" * 32
INPUT = "Call Asha on 555 0100"


def _migration() -> Path:
    """The migration of the table, found by its content and not its number."""
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    yield SimpleNamespace(p=p)
    # The runs go by the cascade of their foreign key.
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skill_runs WHERE created_by LIKE :t"), {"t": _LIKE})
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any, url: Any = None) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(url or world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _skill(org: str, member: str = ANA, name: str = "log") -> str:
    async with tenant_session(org) as db:
        made = await store.create_skill(db, member, name="Log a call",
                                        slug=f"{name}-{uuid.uuid4().hex[:8]}")
        await store.add_version(db, member, made.id, {
            "description": "Use this to log a call.", "instructions": "Make a task.",
            "actions": ["tool:create_task"]})
        return made.id


async def _live(org: str, skill_id: str, member: str = ANA, *, passed: bool = False,
                reason: str = "tool_error", run_id: str | None = None,
                body_key: str = KEY, text_: str = INPUT) -> sr.SkillRun:
    async with tenant_session(org) as db:
        return await sr.record_live(
            db, member, skill_id, version=1, passed=passed,
            reason="passed" if passed else reason, body_key=body_key, input_text=text_,
            run_id=run_id or f"run-{uuid.uuid4().hex}", thread_id="thread-1",
            detail="create_task: ToolError",
            writes=[{"action": "tool:create_task", "keys": ["title", "due"]}])


async def _test_row(org: str, skill_id: str, member: str = ANA, *,
                    reason: str = "missing_write", body_key: str = KEY) -> sr.SkillRun:
    async with tenant_session(org) as db:
        return await sr.record_test(db, member, skill_id, version=1, reason=reason,
                                    body_key=body_key, test_id="t1", credits=2)


async def _proposal(org: str, skill_id: str, repair_of: str, member: str = ANA, *,
                    passed: bool = True, reason: str = "passed",
                    body_key: str = KEY2) -> sr.SkillRun:
    async with tenant_session(org) as db:
        return await sr.record_proposal(
            db, member, skill_id, repair_of, passed=passed, reason=reason,
            body_key=body_key, credits=3, writes=[{"action": "tool:create_task",
                                                   "keys": ["title"]}])


def _admin_rows(world: Any, sql: str, params: dict[str, Any]) -> list[tuple]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(sql), params).all()]


_RAW = (
    "INSERT INTO studio_skill_runs (skill_id, version, kind, passed, reason, detail, "
    "body_key, input_hash, writes, repair_of, test_id, run_id, credits, created_by) "
    "VALUES (CAST(:sid AS uuid), :v, :kind, :passed, :reason, :detail, :bk, :ih, "
    "CAST(:writes AS jsonb), CAST(:rep AS uuid), :tid, :rid, CAST(:cr AS numeric), :by)"
)


def _raw(sid: str, **kw: Any) -> dict[str, Any]:
    return {"sid": sid, "v": 1, "kind": "live", "passed": False, "reason": "tool_error",
            "detail": "", "bk": KEY, "ih": KEY2, "writes": "[]", "rep": None,
            "tid": None, "rid": f"run-{uuid.uuid4().hex}", "cr": None, "by": ANA, **kw}


def _refused(world: Any, org: str, sql: str, params: dict[str, Any]) -> str:
    """Run *sql* as the app role in *org*. Answers the error text."""
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    return str(exc.value)


def _ran(world: Any, org: str, sql: str, params: dict[str, Any]) -> None:
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)
    _go(world, go)


# ── 1. The table ────────────────────────────────────────────────────────────


def test_r8_the_table_has_its_columns_rls_indexes_and_trigger(world) -> None:
    with world.p.admin_engine.connect() as c:
        cols = {r[0]: (r[1], r[2]) for r in c.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'studio_skill_runs'"))}
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'studio_skill_runs'::regclass")).one()
        policies = c.execute(text(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE tablename = 'studio_skill_runs'")).all()
        indexes = dict(c.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'studio_skill_runs'")).all())
        trigger = c.execute(text(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'studio_skill_runs'::regclass "
            "AND NOT tgisinternal")).scalars().all()
        fk = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'studio_skill_runs_skill_fk'")).scalar_one()
        checks = set(c.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = "
            "'studio_skill_runs'::regclass AND contype = 'c'")).scalars().all())
    assert set(cols) == {
        "id", "organization_id", "skill_id", "version", "kind", "passed", "reason",
        "detail", "body_key", "input_hash", "writes", "repair_of", "test_id", "run_id",
        "thread_id", "credits", "created_by", "created_at"}
    for name in ("organization_id", "skill_id", "version", "kind", "passed", "reason",
                 "detail", "body_key", "writes", "created_by", "created_at"):
        assert cols[name][1] == "NO", name
    for name in ("input_hash", "repair_of", "test_id", "run_id", "thread_id", "credits"):
        assert cols[name][1] == "YES", name
    assert cols["writes"][0] == "jsonb" and cols["credits"][0] == "numeric"
    assert tuple(rls) == (True, True)
    assert len(policies) == 1
    assert "app.tenant_id" in policies[0][0] and "app.tenant_id" in policies[0][1]
    one_repair = indexes["studio_skill_runs_one_repair"]
    assert "UNIQUE" in one_repair and "(organization_id, repair_of)" in one_repair
    assert "'proposal'" in one_repair
    one_live = indexes["studio_skill_runs_one_failed_live"]
    assert "UNIQUE" in one_live and "(organization_id, skill_id, run_id)" in one_live
    assert "'live'" in one_live and "NOT passed" in one_live
    assert "created_at DESC" in indexes["studio_skill_runs_skill_idx"]
    assert trigger == ["studio_skill_runs_immutable"]
    assert "(organization_id, skill_id) REFERENCES studio_skills(organization_id, id)" in fk
    assert "ON DELETE CASCADE" in fk
    assert checks == {f"studio_skill_runs_{n}" for n in (
        "version", "kind", "reason", "detail", "body_key", "input_hash", "writes_array",
        "writes_items", "writes_size", "proposal_repairs", "live_input", "live_run",
        "test_id", "created_by", "passed_reason", "credits")}


def test_r8_the_migration_runs_twice(world) -> None:
    sql = _migration().read_text(encoding="utf-8")
    auto = world.p.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
        cur.execute(sql)
    test_r8_the_table_has_its_columns_rls_indexes_and_trigger(world)


# ── 2. The writers ──────────────────────────────────────────────────────────


def test_r8_the_writers_write_each_kind_and_keep_no_input(world) -> None:
    a = world.p.org_a

    async def go() -> tuple[sr.SkillRun, sr.SkillRun, sr.SkillRun, sr.SkillRun]:
        sid = await _skill(a)
        live = await _live(a, sid, member=ANA.upper())
        test = await _test_row(a, sid)
        prop = await _proposal(a, sid, live.id)
        async with tenant_session(a) as db:
            again = await sr.get_run(db, live.id)
        assert again == live
        return live, test, prop, again

    live, test, prop, _ = _go(world, go)
    assert (live.kind, live.passed, live.reason, live.created_by) == (
        "live", False, "tool_error", ANA)
    assert live.input_hash == sr.input_hash(a, INPUT)
    assert live.writes == ({"action": "tool:create_task", "keys": ["due", "title"]},)
    assert live.run_id is not None and live.thread_id == "thread-1"
    assert (test.kind, test.passed, test.test_id, test.input_hash) == (
        "test", False, "t1", None)
    assert test.credits == 2
    assert (prop.kind, prop.passed, prop.reason, prop.repair_of) == (
        "proposal", True, "passed", live.id)
    assert prop.version == live.version and prop.input_hash == live.input_hash
    # No column of any row holds a word of the input.
    rows = _admin_rows(world, "SELECT to_jsonb(r)::text FROM studio_skill_runs r "
                       "WHERE created_by LIKE :t", {"t": _LIKE})
    assert len(rows) == 3
    for (blob,) in rows:
        for word in ("Asha", "555", "0100", "Call"):
            assert word not in blob, word


def test_r8_writes_at_the_byte_cap_pass_the_check(world) -> None:
    """The writer counts the bytes as jsonb writes them, so a list at the
    cap passes the CHECK, and the count is the octet length."""
    a = world.p.org_a
    # 10 items of 5 keys of 60 characters: 4010 bytes, under the cap.
    items = [{"action": "tool:" + "a" * 50, "keys": [f"k{n:02d}" + "k" * 57 for n in range(5)]}
             for _ in range(10)]
    assert sr.writes_bytes(items) < sr.MAX_WRITES_BYTES
    # Grow key names, at most 64 characters each, until the list is exactly
    # at the cap.
    for item in items:
        for n, key in enumerate(item["keys"]):
            grow = min(64 - len(key), sr.MAX_WRITES_BYTES - sr.writes_bytes(items))
            item["keys"][n] = key + "x" * grow
    assert sr.writes_bytes(items) == sr.MAX_WRITES_BYTES

    async def go() -> sr.SkillRun:
        sid = await _skill(a)
        async with tenant_session(a) as db:
            return await sr.record_test(db, ANA, sid, version=1, reason="extra_write",
                                        body_key=KEY, test_id="t9", writes=items)

    made = _go(world, go)
    [(size,)] = _admin_rows(world, "SELECT octet_length(writes::text) FROM studio_skill_runs "
                            "WHERE id = CAST(:i AS uuid)", {"i": made.id})
    assert size == sr.writes_bytes(sr.check_writes(items))


# ── 3. Two orgs ─────────────────────────────────────────────────────────────


def test_r8_org_b_reads_and_writes_nothing_of_org_a(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def seed() -> tuple[str, sr.SkillRun]:
        sid = await _skill(a)
        return sid, await _live(a, sid)

    sid, live = _go(world, seed)

    async def read_b() -> dict[str, Any]:
        async with tenant_session(b) as db:
            return {
                "get": await sr.get_run(db, live.id),
                "history": await sr.history(db, ANA, sid),
                "others": await sr.history(db, BEN, sid),
                "repeat": await sr.repeats_a_failed_body(db, sid, KEY),
                "proposal": await sr.proposal_for(db, live.id),
                "count": (await db.execute(text(
                    "SELECT count(*) FROM studio_skill_runs"))).scalar_one(),
            }

    out = _go(world, read_b)
    assert out["get"] is None and out["proposal"] is None and out["repeat"] is False
    assert out["history"].rows == () and dict(out["others"].others) == {}
    assert out["count"] == 0
    # The policy refuses a row stamped with org A from a session of org B.
    err = _refused(world, b, _RAW.replace("(skill_id,", "(organization_id, skill_id,")
                   .replace("VALUES (", "VALUES (CAST(:org AS uuid), "),
                   {**_raw(sid), "org": a})
    assert "row-level security" in err


def test_r8_a_writer_of_org_b_cannot_name_a_skill_of_org_a(world) -> None:
    """A foreign key check skips RLS. The writer selects the skill and the
    failed row under the bound org, and the key holds the org too."""
    a, b = world.p.org_a, world.p.org_b

    async def seed() -> tuple[str, sr.SkillRun]:
        sid = await _skill(a)
        return sid, await _live(a, sid)

    sid, live = _go(world, seed)

    async def write_b() -> None:
        with pytest.raises(sr.UnknownSkill):
            await _live(b, sid)
        with pytest.raises(sr.UnknownSkill):
            await _test_row(b, sid)
        with pytest.raises(sr.NotRepairable):
            await _proposal(b, sid, live.id)

    _go(world, write_b)
    err = _refused(world, b, _RAW, _raw(sid))
    assert "studio_skill_runs_skill_fk" in err
    [(n,)] = _admin_rows(world, "SELECT count(*) FROM studio_skill_runs "
                         "WHERE skill_id = CAST(:s AS uuid)", {"s": sid})
    assert n == 1


# ── 4. Immutable ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
@pytest.mark.parametrize("assignment", [
    "passed = true", "reason = 'passed'", "detail = 'x'", "body_key = repeat('e', 64)",
    "writes = '[]'::jsonb", "created_by = 'someone@f12a.test'",
])
def test_r8_a_stored_run_cannot_be_updated(world, role: str, assignment: str) -> None:
    a = world.p.org_a

    async def seed() -> sr.SkillRun:
        return await _live(a, await _skill(a))

    live = _go(world, seed)
    sql = f"UPDATE studio_skill_runs SET {assignment} WHERE id = CAST(:i AS uuid)"
    if role == "app":
        err = _refused(world, a, sql, {"i": live.id})
    else:
        with pytest.raises(DBAPIError) as exc, world.p.admin_engine.begin() as c:
            c.execute(text(sql), {"i": live.id})
        err = str(exc.value)
    assert "a stored run cannot change" in err
    [row] = _admin_rows(world, "SELECT passed, reason, detail, body_key, created_by "
                        "FROM studio_skill_runs WHERE id = CAST(:i AS uuid)", {"i": live.id})
    assert row == (False, "tool_error", "create_task: ToolError", KEY, ANA)


# ── 5. The cascades ─────────────────────────────────────────────────────────


def test_r8_a_delete_of_the_skill_deletes_its_runs(world) -> None:
    a = world.p.org_a

    async def seed() -> str:
        sid = await _skill(a)
        live = await _live(a, sid)
        await _test_row(a, sid)
        await _proposal(a, sid, live.id)
        return sid

    sid = _go(world, seed)
    count = "SELECT count(*) FROM studio_skill_runs WHERE skill_id = CAST(:s AS uuid)"
    assert _admin_rows(world, count, {"s": sid}) == [(3,)]
    _ran(world, a, "DELETE FROM studio_skills WHERE id = CAST(:s AS uuid)", {"s": sid})
    assert _admin_rows(world, count, {"s": sid}) == [(0,)]


def test_r8_a_delete_of_the_org_deletes_its_runs(world) -> None:
    org = str(uuid.uuid4())
    with world.p.admin_engine.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (CAST(:i AS uuid), :s, :s)"),
                  {"i": org, "s": f"f12a-{TAG}-{uuid.uuid4().hex[:6]}"})

    async def seed() -> str:
        sid = await _skill(org)
        await _live(org, sid)
        return sid

    sid = _go(world, seed)
    count = "SELECT count(*) FROM studio_skill_runs WHERE skill_id = CAST(:s AS uuid)"
    assert _admin_rows(world, count, {"s": sid}) == [(1,)]
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM organization WHERE id = CAST(:i AS uuid)"), {"i": org})
    assert _admin_rows(world, count, {"s": sid}) == [(0,)]


# ── 6. The CHECKs ───────────────────────────────────────────────────────────


_BIG = json.dumps([{"action": "tool:a", "keys": ["k" * 60] * 7}] * 10)
_ELEVEN = json.dumps([{"action": "tool:a", "keys": []}] * 11)


@pytest.mark.parametrize(("params", "constraint"), [
    ({"v": 0}, "studio_skill_runs_version"),
    ({"kind": "chat"}, "studio_skill_runs_kind"),
    ({"reason": "Tool_Error"}, "studio_skill_runs_reason"),
    ({"reason": "a" * 33}, "studio_skill_runs_reason"),
    ({"detail": "x" * 301}, "studio_skill_runs_detail"),
    ({"passed": True}, "studio_skill_runs_passed_reason"),
    ({"reason": "passed"}, "studio_skill_runs_passed_reason"),
    ({"cr": "-0.01"}, "studio_skill_runs_credits"),
    ({"bk": "x" * 64}, "studio_skill_runs_body_key"),
    ({"bk": "ab" * 31}, "studio_skill_runs_body_key"),
    ({"ih": "AB" * 32}, "studio_skill_runs_input_hash"),
    ({"writes": '{"action": "tool:a"}'}, "studio_skill_runs_writes_array"),
    ({"writes": _ELEVEN}, "studio_skill_runs_writes_items"),
    ({"writes": _BIG}, "studio_skill_runs_writes_size"),
    ({"kind": "proposal"}, "studio_skill_runs_proposal_repairs"),
    ({"rep": str(uuid.uuid4())}, "studio_skill_runs_proposal_repairs"),
    ({"ih": None}, "studio_skill_runs_live_input"),
    ({"rid": None}, "studio_skill_runs_live_run"),
    ({"kind": "test", "ih": None}, "studio_skill_runs_test_id"),
    ({"by": "Ana@f12a.test"}, "studio_skill_runs_created_by"),
    ({"by": ""}, "studio_skill_runs_created_by"),
])
def test_r8_each_check_refuses_its_bad_row(world, params, constraint) -> None:
    a = world.p.org_a
    sid = _go(world, lambda: _skill(a))
    _ran(world, a, _RAW, _raw(sid))  # the good row passes
    err = _refused(world, a, _RAW, _raw(sid, **params))
    assert constraint in err


# ── 7. The unique indexes ───────────────────────────────────────────────────


def test_r8_one_proposal_for_each_failed_row(world) -> None:
    a = world.p.org_a

    async def go() -> tuple[str, sr.SkillRun, sr.SkillRun]:
        sid = await _skill(a)
        live = await _live(a, sid)
        # A colleague cannot repair the failed run of Ana (defence in depth).
        with pytest.raises(sr.NotRepairable):
            await _proposal(a, sid, live.id, member=BEN)
        first = await _proposal(a, sid, live.id, passed=False, reason="cap_reached")
        with pytest.raises(sr.AlreadyRepaired):
            await _proposal(a, sid, live.id)
        passed = await _live(a, sid, passed=True)
        with pytest.raises(sr.NotRepairable):
            await _proposal(a, sid, passed.id)
        return sid, live, first

    sid, live, first = _go(world, go)
    err = _refused(world, a, _RAW, _raw(sid, kind="proposal", rep=live.id, rid=None))
    assert "studio_skill_runs_one_repair" in err
    assert _admin_rows(world, "SELECT id::text FROM studio_skill_runs "
                       "WHERE repair_of = CAST(:r AS uuid)", {"r": live.id}) == [(first.id,)]


def test_r8_one_failed_live_row_for_each_run(world) -> None:
    a = world.p.org_a
    run = f"run-{uuid.uuid4().hex}"

    async def go() -> str:
        sid = await _skill(a)
        other = await _skill(a, name="other")
        await _live(a, sid, run_id=run)
        with pytest.raises(sr.DuplicateFailure):
            await _live(a, sid, run_id=run, reason="member_said_wrong")
        # A passed row of the run, and a failed row of another skill, pass.
        await _live(a, sid, run_id=run, passed=True)
        await _live(a, other, run_id=run)
        return sid

    sid = _go(world, go)
    err = _refused(world, a, _RAW, _raw(sid, rid=run))
    assert "studio_skill_runs_one_failed_live" in err


# ── 8. The member purge ─────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_a_member_purge_removes_the_runs_of_this_org_only(world, role: str) -> None:
    """The clause deletes the runs that the member made in THIS org. The
    runs of a colleague stay, and so do the runs of the same address in
    another org. The superuser run bypasses RLS, so only the clause's own
    org predicate keeps org B's row safe there."""
    from gateway.routes.admin.members import _PURGE_DELETES, _purge_params

    [rows] = [r for r in _PURGE_DELETES if r.table == "studio_skill_runs"]
    a, b = world.p.org_a, world.p.org_b

    async def seed() -> dict[str, str]:
        sid_a = await _skill(a)
        sid_b = await _skill(b)
        return {"mine": (await _live(a, sid_a)).id,
                "mine_test": (await _test_row(a, sid_a)).id,
                "bens": (await _live(a, sid_a, member=BEN)).id,
                "theirs": (await _live(b, sid_b)).id}

    ids = _go(world, seed)
    engine = world.p.app_url if role == "app" else world.p.admin_engine.url

    async def purge() -> tuple[int, int]:
        params = _purge_params(rows, uid=str(uuid.uuid4()), email=ANA)
        async with tenant_engine_scope(_dsn(engine)), tenant_session(a) as db:
            n = (await db.execute(text(rows.count_sql), params)).scalar_one()
            gone = await db.execute(text(rows.delete_sql), params)
            return int(n), int(gone.rowcount)

    counted, deleted = asyncio.run(purge())
    assert counted == deleted == 2
    left = {r[0] for r in _admin_rows(
        world, "SELECT id::text FROM studio_skill_runs WHERE id = ANY(CAST(:i AS uuid[]))",
        {"i": list(ids.values())})}
    assert left == {ids["bens"], ids["theirs"]}


# ── 9. The readers ──────────────────────────────────────────────────────────


def test_r8_the_readers(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        sid = await _skill(a)
        made = [await _live(a, sid) for _ in range(12)]
        await _live(a, sid, member=BEN)
        await _live(a, sid, member=BEN, reason="card_declined")
        await _test_row(a, sid, member=BEN, reason="call_cap", body_key=KEY2)
        fresh = await _live(a, sid, passed=True)
        stop = await _proposal(a, sid, made[0].id, passed=False, reason="cap_reached",
                               body_key="ef" * 32)
        async with tenant_session(a) as db:
            return {
                "made": made, "fresh": fresh, "stop": stop,
                "history": await sr.history(db, ANA, sid),
                "short": await sr.history(db, ANA, sid, limit=3),
                "repeat_live": await sr.repeats_a_failed_body(db, sid, KEY),
                "repeat_test": await sr.repeats_a_failed_body(db, sid, KEY2),
                "repeat_cap": await sr.repeats_a_failed_body(db, sid, "ef" * 32),
                "repeat_none": await sr.repeats_a_failed_body(db, sid, "01" * 32),
                "passed": await sr.newest_passed_live(db, ANA, sid),
                "passed_ben": await sr.newest_passed_live(db, BEN, sid),
                "proposal": await sr.proposal_for(db, made[0].id),
            }

    out = _go(world, go)
    rows = out["history"].rows
    assert len(rows) == sr.HISTORY_ROWS
    assert all(r.created_by == ANA and not r.passed for r in rows)
    assert [r.created_at for r in rows] == sorted((r.created_at for r in rows), reverse=True)
    assert rows[0].id == out["stop"].id
    assert len(out["short"].rows) == 3
    assert dict(out["history"].others) == {"call_cap": 1, "card_declined": 1, "tool_error": 1}
    assert out["repeat_live"] is True and out["repeat_test"] is True
    # A stop for a cap is no failure of the skill (D-F12-13).
    assert out["repeat_cap"] is False and out["repeat_none"] is False
    assert out["passed"] is not None and out["passed"].id == out["fresh"].id
    assert out["passed_ben"] is None
    assert out["proposal"].id == out["stop"].id
