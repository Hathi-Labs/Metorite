"""WS-WEB WEB-10 — the goal record of a long job on a real database (R8).

Spec: ``project-docs/specs/web_research.md`` §6 and the §11 WEB-10 row. Code:
``packages/acb_common/acb_common/goal_store.py``. Migration: the file that
creates ``goal_records`` (324 at build, 325 at merge).

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_goal_record_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (WEB-10).**

a. The migration applies, and the table has ENABLE and FORCE RLS.
b. Org B reads and writes none of org A's goals. A member reads only their
   own goals. The store filters on the bound org too, so RLS is not the
   only guard.
c. A stale update is refused, and the row does not change. Two racing
   updates give one winner, and so do two racing rounds.
d. The cap stops a job with ``cap_reached``, for rounds and for cost. An
   active goal is always under both caps, on the database too.
e. The CHECKs refuse an unknown block code and each other bad row. The
   store refuses each bad value before the database.
f. The objective never changes. Each UPDATE raises the version by one. No
   column holds a permission to continue.
g. The member purge deletes the member's goals of the bound org only.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. The migration and ``generated/04_policies.sql``: drop the
  ``FORCE  ROW LEVEL SECURITY`` line. ``test_the_migration_holds_the_checks_
  the_rls_and_the_trigger`` and ``test_r8_the_table_has_enable_and_force_rls``
  go red.
* M1b. Drop the ENABLE and the FORCE lines, in both files.
  ``test_r8_org_b_reads_and_writes_nothing_of_org_a`` and
  ``test_r8_the_table_has_enable_and_force_rls`` go red.
* M2. ``goal_store._UPDATE_SQL``: drop ``AND version = :v``.
  ``test_r8_two_racing_updates_give_one_winner`` and
  ``test_r8_many_racing_updates_give_one_winner`` go red.
* M2b. ``goal_store._ROUND_SQL``: drop ``AND version = :v``.
  ``test_r8_two_racing_rounds_give_one_winner`` goes red. The trigger does
  not catch it, because ``version = version + 1`` still raises the version
  by one.
* M3. ``goal_store.cap_block``: use ``>`` in place of ``>=``.
  ``test_cap_block_is_the_one_cap_rule``,
  ``test_r8_the_round_cap_stops_the_job_with_cap_reached`` and
  ``test_r8_a_round_that_meets_the_cost_cap_exactly_stops_the_job`` go red.
* M4. The migration: drop the CHECK ``goal_records_active_under_caps``.
  The ``active_at_round_cap`` and ``active_at_cost_cap`` cases of
  ``test_r8_the_checks_refuse_a_bad_row`` go red.
* M4b. The migration: drop ``cost_used_credits <> 'NaN'``. The
  ``cost_used_nan`` case of ``test_r8_the_checks_refuse_a_bad_row`` goes
  red.
* M5. The migration: drop the trigger ``goal_records_guard``.
  ``test_r8_the_identity_of_a_goal_cannot_change`` and
  ``test_r8_an_update_must_raise_the_version_by_one`` go red.
* M6. ``routes/admin/members.py``: drop the ``goal_records`` row of
  ``_PURGE_DELETES``. ``test_r8_a_member_purge_removes_the_goals_of_this_org_only``
  goes red, and so do two tests of ``test_admin_member_purge.py``.
* M7. ``goal_store._check_changes``: drop the ``objective`` refusal. The
  general refusal of an unknown field still refuses it, with another text.
  ``test_r8_the_store_refuses_an_objective_change_and_the_row_stays`` goes
  red.
* M8. ``goal_store._LIST_SQL``: drop ``organization_id = {_BOUND_ORG} AND``.
  ``test_r8_the_store_filters_on_the_bound_org_without_rls`` and
  ``test_the_migration_holds_the_checks_the_rls_and_the_trigger`` go red.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import goal_store as store
from acb_common.db import tenant_session
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
    if "CREATE TABLE IF NOT EXISTS goal_records (" in p.read_text(encoding="utf-8"))
_POLICIES = _ROOT / "infra" / "postgres" / "generated" / "04_policies.sql"
_STORE = _ROOT / "packages" / "acb_common" / "acb_common" / "goal_store.py"

TAG = uuid.uuid4().hex[:8]
#: The two members of this suite. The tag lets teardown find their rows.
ANA = f"ana.{TAG}@web10.test"
BEN = f"ben.{TAG}@web10.test"
_LIKE = f"%.{TAG}@web10.test"
_OBJECTIVE = "Find the list price of three 3D printers for a school lab."

#: Every column of the table, in order. A column of permission, consent or
#: approval would break web_research.md §6: permission is not durable.
_COLUMNS = [
    "organization_id", "id", "member_email", "kind", "objective", "state",
    "version", "round_count", "round_cap", "cost_used_credits",
    "cost_cap_credits", "block_code", "job_id", "created_at", "updated_at",
    "finished_at",
]


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    yield SimpleNamespace(p=p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM goal_records WHERE member_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _create(org: str, member: str = ANA, **kw: Any) -> store.Goal:
    args: dict[str, Any] = {"objective": _OBJECTIVE, "cost_cap_credits": 10, **kw}
    async with tenant_session(org) as db:
        return await store.create_goal(db, member, **args)


def _row(world: Any, goal_id: str) -> dict[str, Any]:
    """The row of *goal_id*, read as the superuser."""
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT * FROM goal_records WHERE id = CAST(:i AS uuid)"),
            {"i": goal_id}).mappings().one()
    return dict(row)


def _count(world: Any) -> int:
    """The rows of this suite, read as the superuser."""
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM goal_records WHERE member_email LIKE :t"),
            {"t": _LIKE}).scalar_one())


def _refused(world: Any, org: str, sql: str, params: dict[str, Any]) -> str:
    """Run *sql* as the app role in *org*. Answers the error text."""
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    return str(exc.value)


_RAW_GOAL = (
    "INSERT INTO goal_records (member_email, kind, objective, state, version, "
    "round_count, round_cap, cost_used_credits, cost_cap_credits, block_code, "
    "finished_at) VALUES (:m, :kind, :obj, :state, :ver, :rc, :cap, :used, :ccap, "
    ":block, :fin)")


def _goal_params(**kw: Any) -> dict[str, Any]:
    return {"m": ANA, "kind": "research", "obj": _OBJECTIVE, "state": "active",
            "ver": 1, "rc": 0, "cap": 40, "used": Decimal("0"), "ccap": Decimal("10"),
            "block": None, "fin": None, **kw}


# ── The schema and the wiring ──────────────────────────────────────────────


def test_the_migration_holds_the_checks_the_rls_and_the_trigger() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    policies = _POLICIES.read_text(encoding="utf-8")
    assert "ALTER TABLE goal_records ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE goal_records FORCE  ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE goal_records FORCE  ROW LEVEL SECURITY" in policies
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    for value in (*store.STATES, *store.BLOCK_CODES):
        assert f"'{value}'" in sql, value
    assert "UNIQUE (organization_id, id)" in sql
    assert "BEFORE UPDATE ON goal_records" in sql
    assert "NUMERIC(14, 4)" in sql
    assert f"round_cap BETWEEN 1 AND {store.MAX_ROUND_CAP}" in sql
    assert f"char_length(objective) <= {store.MAX_OBJECTIVE_CHARS}" in sql
    assert f"cost_cap_credits <= {int(store.MAX_CREDITS)}" in sql
    # One generic table: WS-43 CA runs and Studio builds use it later.
    assert re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql) == ["goal_records"]
    assert "gtd_" not in sql
    # No statement names an org as a value: the DEFAULT and RLS bind it.
    for name in dir(store):
        if name.endswith("_SQL"):
            stmt = getattr(store, name)
            assert ":org" not in stmt, name
            if "INSERT INTO" in stmt:
                columns = stmt.split("INSERT INTO", 1)[1].split(")", 1)[0]
                assert "organization_id" not in columns, name
            else:
                # Each read and each write filters on the bound org, not on RLS only.
                assert f"WHERE organization_id = {store._BOUND_ORG}" in stmt, name


def test_the_store_opens_no_session_and_holds_no_route() -> None:
    """The store takes the caller's session. It builds no engine, opens no
    session and holds no route, so the orchestrator and the gateway can both
    import it."""
    src = _STORE.read_text(encoding="utf-8")
    for banned in ("tenant_session(", "get_db(", "get_session_factory(",
                   "create_async_engine", "create_engine", "APIRouter", "gateway"):
        assert banned not in src.split('"""', 2)[2], banned
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in imports if "gateway" in ln or "orchestrator" in ln], imports


def test_only_the_store_writes_the_table() -> None:
    """``goal_store.py`` is the one writer. The member purge builds its
    DELETE from ``_PersonRows``, so no statement text of it names the table."""
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+goal_records\b", re.I)
    found = []
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if path == _STORE or "node_modules" in path.parts:
                continue
            if write.search(path.read_text(encoding="utf-8", errors="replace")):
                found.append(str(path.relative_to(_ROOT)))
    assert found == [], found


def test_the_block_codes_and_the_states_are_closed() -> None:
    assert tuple(store.BLOCK_CODES) == ("cap_reached", "needs_member", "source_unreachable")
    assert store.STATES == ("active", "blocked", "done", "failed", "cancelled")
    assert set(store.FINAL_STATES) < set(store.STATES)
    for code, sentence in store.BLOCK_CODES.items():
        # One sentence for the member: it ends with a full stop, and it holds
        # no other one, no semicolon and no code name.
        assert sentence.endswith(".") and sentence.count(".") == 1, code
        assert ";" not in sentence and "_" not in sentence, code
        assert len(sentence.split()) <= 25, code


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_goal_record_r8\.py \\?$", workflow, re.M)


def test_cap_block_is_the_one_cap_rule() -> None:
    d = Decimal
    assert store.cap_block(39, 40, d("0"), d("10")) is None
    assert store.cap_block(40, 40, d("0"), d("10")) == "cap_reached"
    assert store.cap_block(0, 40, d("9.9999"), d("10")) is None
    assert store.cap_block(0, 40, d("10"), d("10")) == "cap_reached"
    assert store.cap_block(41, 40, d("11"), d("10")) == "cap_reached"


def test_the_store_refuses_a_bad_write_before_the_db() -> None:
    """The session is None, so a call that reached the database fails on it.
    Each refusal is a GoalStoreError, never an AttributeError or a DBAPIError."""
    gid = str(uuid.uuid4())

    async def go() -> None:
        bad_creates: list[dict[str, Any]] = [
            {"objective": "", "cost_cap_credits": 10},
            {"objective": "   ", "cost_cap_credits": 10},
            {"objective": "x" * (store.MAX_OBJECTIVE_CHARS + 1), "cost_cap_credits": 10},
            {"objective": 3, "cost_cap_credits": 10},
            {"objective": "o", "cost_cap_credits": 0},
            {"objective": "o", "cost_cap_credits": Decimal("0.00004")},
            {"objective": "o", "cost_cap_credits": -1},
            {"objective": "o", "cost_cap_credits": float("nan")},
            {"objective": "o", "cost_cap_credits": float("inf")},
            {"objective": "o", "cost_cap_credits": Decimal("NaN")},
            {"objective": "o", "cost_cap_credits": Decimal("sNaN")},
            {"objective": "o", "cost_cap_credits": Decimal("Infinity")},
            {"objective": "o", "cost_cap_credits": True},
            {"objective": "o", "cost_cap_credits": "10"},
            {"objective": "o", "cost_cap_credits": store.MAX_CREDITS + 1},
            {"objective": "o", "cost_cap_credits": 10, "round_cap": 0},
            {"objective": "o", "cost_cap_credits": 10, "round_cap": store.MAX_ROUND_CAP + 1},
            {"objective": "o", "cost_cap_credits": 10, "round_cap": True},
            {"objective": "o", "cost_cap_credits": 10, "round_cap": 4.0},
            {"objective": "o", "cost_cap_credits": 10, "kind": "Research"},
            {"objective": "o", "cost_cap_credits": 10, "kind": "research\n"},
            {"objective": "o", "cost_cap_credits": 10, "kind": "a" * 65},
            {"objective": "o", "cost_cap_credits": 10, "job_id": "not-a-uuid"},
        ]
        for args in bad_creates:
            with pytest.raises(store.GoalStoreError):
                await store.create_goal(None, ANA, **args)
        with pytest.raises(store.GoalStoreError):
            await store.create_goal(None, " ", objective="o", cost_cap_credits=10)
        with pytest.raises(store.GoalStoreError):
            await store.get_goal(None, ANA, "not-a-uuid")
        for limit in (0, store.MAX_LIST + 1, True, "5"):
            with pytest.raises(store.GoalStoreError):
                await store.list_goals(None, ANA, limit=limit)
        bad_updates: list[dict[str, Any]] = [
            {}, {"objective": "Something else"}, {"objective": _OBJECTIVE},
            {"version": 5}, {"round_count": 0}, {"cost_used_credits": 0},
            {"member_email": BEN}, {"kind": "coding"}, {"permitted": True},
            {"state": "live"}, {"state": "blocked"},
            {"state": "blocked", "block_code": None},
            {"state": "blocked", "block_code": "paused"},
            {"block_code": "needs_member"}, {"state": "active", "block_code": "cap_reached"},
            {"round_cap": 0}, {"cost_cap_credits": float("nan")}, {"job_id": "x"},
        ]
        for changes in bad_updates:
            with pytest.raises(store.GoalStoreError):
                await store.update_goal(None, ANA, gid, 1, **changes)
        for version in (0, -1, True, "1", 1.0):
            with pytest.raises(store.GoalStoreError):
                await store.update_goal(None, ANA, gid, version, state="done")
            with pytest.raises(store.GoalStoreError):
                await store.record_round(None, ANA, gid, version, 1)
        for cost in (-1, float("nan"), float("-inf"), Decimal("NaN"), True, None,
                     store.MAX_CREDITS + 1):
            with pytest.raises(store.GoalStoreError):
                await store.record_round(None, ANA, gid, 1, cost)

    asyncio.run(go())


def test_an_objective_refusal_names_the_field_and_not_the_value() -> None:
    with pytest.raises(store.GoalStoreError) as refused:
        asyncio.run(store.create_goal(
            None, ANA, objective="Secret plan " * 300, cost_cap_credits=10))
    assert "objective" in str(refused.value)
    assert "Secret" not in str(refused.value)


# ── a. The table ───────────────────────────────────────────────────────────


def test_r8_the_table_has_enable_and_force_rls(world) -> None:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'goal_records'")).one()
        policies = c.execute(text(
            "SELECT policyname FROM pg_policies WHERE tablename = 'goal_records'")
        ).scalars().all()
    assert tuple(row) == (True, True)
    assert "goal_records_tenant_isolation" in policies


def test_r8_no_column_holds_a_permission_to_continue(world) -> None:
    """Permission to continue is not durable (web_research.md §6). A restart
    asks the member again, so no column may hold a permission."""
    with world.p.admin_engine.connect() as c:
        columns = c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'goal_records' ORDER BY ordinal_position")).scalars().all()
    assert list(columns) == _COLUMNS
    assert not [c for c in columns if re.search(r"permi|consent|approv|allow|grant", c)]


def test_r8_a_new_goal_is_active_at_version_1(world) -> None:
    a = world.p.org_a
    job = str(uuid.uuid4())

    async def go() -> tuple[store.Goal, store.Goal | None, list[store.Goal]]:
        made = await _create(a, member=ANA.upper(), job_id=job)
        async with tenant_session(a) as db:
            return made, await store.get_goal(db, ANA, made.id), await store.list_goals(db, ANA)

    made, got, listed = _go(world, go)
    assert made.state == "active" and made.version == 1 and made.kind == "research"
    assert made.member_email == ANA and made.objective == _OBJECTIVE
    assert made.round_count == 0 and made.round_cap == store.DEFAULT_ROUND_CAP == 40
    assert made.cost_used_credits == 0 and made.cost_cap_credits == Decimal("10.0000")
    assert made.block_code is None and made.block_message is None
    assert made.finished_at is None and made.job_id == job
    assert got == made and [g.id for g in listed] == [made.id]
    row = _row(world, made.id)
    assert str(row["organization_id"]) == a


def test_r8_credits_are_stored_at_four_places(world) -> None:
    async def go() -> tuple[store.Goal, store.Goal | None]:
        made = await _create(world.p.org_a, cost_cap_credits=0.1)
        async with tenant_session(world.p.org_a) as db:
            first = await store.record_round(db, ANA, made.id, 1, Decimal("0.00005"))
            assert first is not None
            return made, await store.record_round(db, ANA, made.id, 2, 0.00004)

    made, after = _go(world, go)
    assert made.cost_cap_credits == Decimal("0.1000")
    # 0.00005 rounds up to 0.0001, as NUMERIC(14, 4) rounds it. 0.00004
    # rounds to 0, so the second round adds nothing.
    assert after is not None and after.cost_used_credits == Decimal("0.0001")
    assert _row(world, made.id)["cost_used_credits"] == Decimal("0.0001")


def test_r8_an_objective_with_a_nul_is_stored_with_a_replacement(world) -> None:
    made = _go(world, lambda: _create(world.p.org_a, objective="  Prices\x00 of \ud83d  "))
    assert made.objective == "Prices� of �"
    assert _row(world, made.id)["objective"] == "Prices� of �"


def test_r8_the_list_is_newest_first_and_bounded(world) -> None:
    a = world.p.org_a

    async def go() -> tuple[list[str], list[store.Goal], list[store.Goal]]:
        ids = [(await _create(a, objective=f"Goal {i}")).id for i in range(3)]
        async with tenant_session(a) as db:
            # Touch the first, so it is the newest change.
            await store.update_goal(db, ANA, ids[0], 1, round_cap=41)
        async with tenant_session(a) as db:
            return ids, await store.list_goals(db, ANA), await store.list_goals(db, ANA, limit=2)

    ids, listed, two = _go(world, go)
    assert [g.id for g in listed] == [ids[0], ids[2], ids[1]]
    assert [g.id for g in two] == [ids[0], ids[2]]


# ── b. Two orgs and two members ────────────────────────────────────────────


def test_r8_org_b_reads_and_writes_nothing_of_org_a(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, Any]:
        goal = await _create(a)
        out: dict[str, Any] = {"goal": goal}
        async with tenant_session(b) as db:
            out["get"] = await store.get_goal(db, ANA, goal.id)
            out["list"] = await store.list_goals(db, ANA)
            out["update"] = await store.update_goal(db, ANA, goal.id, 1, state="cancelled")
            out["round"] = await store.record_round(db, ANA, goal.id, 1, 5)
            out["raw"] = (await db.execute(text(
                "SELECT count(*) FROM goal_records WHERE member_email = :m"),
                {"m": ANA})).scalar_one()
            out["raw_update"] = (await db.execute(text(
                "UPDATE goal_records SET state = 'cancelled', finished_at = now(), "
                "version = version + 1 WHERE id = CAST(:i AS uuid)"),
                {"i": goal.id})).rowcount
        return out

    out = _go(world, go)
    assert out["get"] is None and out["list"] == []
    assert out["update"] is None and out["round"] is None
    assert out["raw"] == 0 and out["raw_update"] == 0
    row = _row(world, out["goal"].id)
    assert (row["state"], row["version"], row["round_count"]) == ("active", 1, 0)
    assert str(row["organization_id"]) == a


def test_r8_org_b_cannot_write_a_row_into_org_a(world) -> None:
    """WITH CHECK: a session of org B cannot stamp a row with org A."""
    params = _goal_params()
    sql = _RAW_GOAL.replace("(member_email,", "(organization_id, member_email,").replace(
        "VALUES (:m,", "VALUES (CAST(:org AS uuid), :m,")
    err = _refused(world, world.p.org_b, sql, {**params, "org": world.p.org_a})
    assert "row-level security" in err
    assert _count(world) == 0


def test_r8_a_member_reads_and_writes_only_their_own_goals(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        goal = await _create(a, member=ANA)
        out: dict[str, Any] = {"goal": goal}
        async with tenant_session(a) as db:
            out["get"] = await store.get_goal(db, BEN, goal.id)
            out["list"] = await store.list_goals(db, BEN)
            out["update"] = await store.update_goal(db, BEN, goal.id, 1, state="cancelled")
            out["round"] = await store.record_round(db, BEN, goal.id, 1, 5)
            # The positive control: Ana reads her own goal.
            out["own"] = await store.get_goal(db, ANA, goal.id)
        return out

    out = _go(world, go)
    assert out["get"] is None and out["list"] == []
    assert out["update"] is None and out["round"] is None
    assert out["own"] == out["goal"]
    assert _row(world, out["goal"].id)["version"] == 1


def test_r8_the_store_filters_on_the_bound_org_without_rls(world) -> None:
    """Each statement of the store filters on ``_BOUND_ORG`` too, not on RLS
    only. The superuser bypasses RLS, so here only the store's own filter
    keeps org A's goal away from a session bound to org B."""
    a, b = world.p.org_a, world.p.org_b
    goal = _go(world, lambda: _create(a))

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with tenant_engine_scope(_dsn(world.p.admin_engine.url)), tenant_session(b) as db:
            # The positive control: RLS does not hide the row here.
            out["raw"] = (await db.execute(text(
                "SELECT count(*) FROM goal_records WHERE member_email = :m"),
                {"m": ANA})).scalar_one()
            out["get"] = await store.get_goal(db, ANA, goal.id)
            out["list"] = await store.list_goals(db, ANA)
            out["update"] = await store.update_goal(db, ANA, goal.id, 1, state="cancelled")
            out["round"] = await store.record_round(db, ANA, goal.id, 1, 5)
        return out

    out = asyncio.run(go())
    assert out["raw"] == 1
    assert out["get"] is None and out["list"] == []
    assert out["update"] is None and out["round"] is None
    row = _row(world, goal.id)
    assert (row["state"], row["version"], row["round_count"]) == ("active", 1, 0)
    assert str(row["organization_id"]) == a


# ── c. Compare-and-set ─────────────────────────────────────────────────────


def test_r8_a_stale_update_is_refused_and_the_row_does_not_change(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        goal = await _create(a)
        out: dict[str, Any] = {"goal": goal}
        async with tenant_session(a) as db:
            out["first"] = await store.update_goal(db, ANA, goal.id, 1, round_cap=50)
        for name, call in (
                ("update", lambda db: store.update_goal(db, ANA, goal.id, 1, state="cancelled")),
                ("round", lambda db: store.record_round(db, ANA, goal.id, 1, 3))):
            with pytest.raises(store.StaleGoal) as stale:
                async with tenant_session(a) as db:
                    await call(db)
            out[name] = stale.value
        return out

    out = _go(world, go)
    assert out["first"].version == 2 and out["first"].round_cap == 50
    for name in ("update", "round"):
        assert out[name].current_version == 2, name
        assert "Read it again" in str(out[name])
    row = _row(world, out["goal"].id)
    assert (row["version"], row["state"], row["round_cap"], row["round_count"]) == (
        2, "active", 50, 0)
    assert row["cost_used_credits"] == 0


def test_r8_two_racing_updates_give_one_winner(world) -> None:
    """Both writers read version 1. The first holds the row lock. The second
    waits for the commit, then finds version 2, and changes nothing."""
    a = world.p.org_a
    hold = 0.6

    async def go() -> dict[str, Any]:
        goal = await _create(a)
        locked = asyncio.Event()
        read = asyncio.Event()

        async def first() -> store.Goal | None:
            async with tenant_session(a) as db:
                # Both read version 1 before either writes.
                await read.wait()
                won = await store.update_goal(db, ANA, goal.id, 1, round_cap=55)
                locked.set()
                await asyncio.sleep(hold)
            return won

        async def second() -> tuple[BaseException | None, float]:
            async with tenant_session(a) as db:
                seen = await store.get_goal(db, ANA, goal.id)
                assert seen is not None and seen.version == 1
            read.set()
            await locked.wait()
            started = time.monotonic()
            try:
                async with tenant_session(a) as db:
                    await store.update_goal(db, ANA, goal.id, seen.version, state="cancelled")
            except store.StaleGoal as exc:
                return exc, time.monotonic() - started
            return None, time.monotonic() - started

        won, (lost, waited) = await asyncio.gather(first(), second())
        return {"goal": goal, "won": won, "lost": lost, "waited": waited}

    out = _go(world, go)
    assert out["won"] is not None and out["won"].version == 2
    assert isinstance(out["lost"], store.StaleGoal)
    # The second statement waited on the row lock of the first.
    assert out["waited"] >= hold * 0.5, out["waited"]
    row = _row(world, out["goal"].id)
    assert (row["version"], row["state"], row["round_cap"]) == (2, "active", 55)
    assert row["finished_at"] is None


def test_r8_many_racing_updates_give_one_winner(world) -> None:
    a = world.p.org_a

    async def go() -> tuple[str, list[Any]]:
        goal = await _create(a)

        async def one(cap: int) -> Any:
            async with tenant_session(a) as db:
                return await store.update_goal(db, ANA, goal.id, 1, round_cap=cap)

        got = await asyncio.gather(*(one(41 + i) for i in range(5)), return_exceptions=True)
        return goal.id, list(got)

    goal_id, got = _go(world, go)
    winners = [g for g in got if isinstance(g, store.Goal)]
    losers = [g for g in got if isinstance(g, store.StaleGoal)]
    assert len(winners) == 1 and len(losers) == 4, got
    row = _row(world, goal_id)
    assert row["version"] == 2 and row["round_cap"] == winners[0].round_cap


def test_r8_two_racing_rounds_give_one_winner(world, monkeypatch) -> None:
    """Both writers pass the pre-read at version 1, then race. A barrier
    holds each writer after its pre-read until the other writer read too, so
    no sleep sets the order. Then the UPDATE of ``_ROUND_SQL`` is the only
    guard. The first UPDATE holds the row lock. The second waits for the
    commit, finds version 2, and changes nothing. So no round and no cost is
    lost, and a job cannot run past its cost cap."""
    a = world.p.org_a
    real_read = store._read_for_write
    costs = (2, 3)

    async def go() -> dict[str, Any]:
        goal = await _create(a, cost_cap_credits=10)
        both_read = asyncio.Barrier(len(costs))
        seen: list[int] = []

        async def read_then_wait(*args: Any) -> store.Goal | None:
            got = await real_read(*args)
            assert got is not None
            seen.append(got.version)
            await asyncio.wait_for(both_read.wait(), timeout=30)
            return got

        monkeypatch.setattr(store, "_read_for_write", read_then_wait)

        async def one(cost: int) -> Any:
            async with tenant_session(a) as db:
                return await store.record_round(db, ANA, goal.id, 1, cost)

        got = await asyncio.gather(*(one(c) for c in costs), return_exceptions=True)
        return {"goal": goal, "got": list(got), "seen": seen}

    out = _go(world, go)
    got = out["got"]
    assert out["seen"] == [1, 1], got
    winners = [(c, g) for c, g in zip(costs, got, strict=True) if isinstance(g, store.Goal)]
    losers = [g for g in got if isinstance(g, store.StaleGoal)]
    assert len(winners) == 1 and len(losers) == 1, got
    [(cost, won)] = winners
    # The loser passed the pre-read, so the UPDATE refused it, not the read.
    assert losers[0].current_version is None
    assert (won.version, won.round_count, won.cost_used_credits) == (2, 1, Decimal(cost))
    row = _row(world, out["goal"].id)
    assert (row["version"], row["round_count"], row["state"]) == (2, 1, "active")
    assert row["cost_used_credits"] == Decimal(cost)


# ── d. The caps ────────────────────────────────────────────────────────────


def test_r8_the_round_cap_stops_the_job_with_cap_reached(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        goal = await _create(a, round_cap=2, cost_cap_credits=100)
        out: dict[str, Any] = {"goal": goal}
        async with tenant_session(a) as db:
            out["one"] = await store.record_round(db, ANA, goal.id, 1, 1)
            out["two"] = await store.record_round(db, ANA, goal.id, 2, 1)
        with pytest.raises(store.GoalStateError):
            async with tenant_session(a) as db:
                await store.record_round(db, ANA, goal.id, 3, 1)
        return out

    out = _go(world, go)
    one, two = out["one"], out["two"]
    assert (one.state, one.round_count, one.block_code, one.version) == ("active", 1, None, 2)
    assert (two.state, two.round_count, two.block_code, two.version) == (
        "blocked", 2, "cap_reached", 3)
    assert two.block_message == store.BLOCK_CODES["cap_reached"]
    assert two.cost_used_credits == Decimal("2.0000") and two.finished_at is None
    row = _row(world, out["goal"].id)
    assert (row["state"], row["round_count"], row["version"]) == ("blocked", 2, 3)


def test_r8_the_cost_cap_stops_the_job_with_cap_reached(world) -> None:
    a = world.p.org_a

    async def go() -> list[store.Goal | None]:
        goal = await _create(a, cost_cap_credits=Decimal("5"))
        async with tenant_session(a) as db:
            one = await store.record_round(db, ANA, goal.id, 1, Decimal("3"))
            two = await store.record_round(db, ANA, goal.id, 2, Decimal("2.5"))
        return [one, two]

    one, two = _go(world, go)
    assert one is not None and one.state == "active" and one.cost_used_credits == 3
    # The last round goes past the cap: its cost is known only at its end.
    assert two is not None and (two.state, two.block_code) == ("blocked", "cap_reached")
    assert two.cost_used_credits == Decimal("5.5000") and two.round_count == 2


def test_r8_a_round_that_meets_the_cost_cap_exactly_stops_the_job(world) -> None:
    async def go() -> store.Goal | None:
        goal = await _create(world.p.org_a, cost_cap_credits=Decimal("4"))
        async with tenant_session(world.p.org_a) as db:
            return await store.record_round(db, ANA, goal.id, 1, 4)

    got = _go(world, go)
    assert got is not None and (got.state, got.block_code) == ("blocked", "cap_reached")


def test_r8_a_capped_goal_resumes_only_under_a_raised_cap(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        goal = await _create(a, round_cap=1)
        out: dict[str, Any] = {}
        async with tenant_session(a) as db:
            capped = await store.record_round(db, ANA, goal.id, 1, 1)
            assert capped is not None and capped.state == "blocked"
            # A resume at the cap stays blocked with cap_reached.
            out["same"] = await store.update_goal(db, ANA, goal.id, 2, state="active")
            out["raised"] = await store.update_goal(
                db, ANA, goal.id, 3, state="active", round_cap=3)
            out["round"] = await store.record_round(db, ANA, goal.id, 4, 1)
        return out

    out = _go(world, go)
    assert (out["same"].state, out["same"].block_code, out["same"].version) == (
        "blocked", "cap_reached", 3)
    assert (out["raised"].state, out["raised"].block_code) == ("active", None)
    assert out["raised"].round_cap == 3 and out["raised"].round_count == 1
    assert out["round"].state == "active" and out["round"].round_count == 2


def test_r8_a_lowered_cap_blocks_an_active_goal(world) -> None:
    a = world.p.org_a

    async def go() -> tuple[store.Goal | None, store.Goal | None]:
        goal = await _create(a, cost_cap_credits=10)
        async with tenant_session(a) as db:
            await store.record_round(db, ANA, goal.id, 1, 6)
            await store.record_round(db, ANA, goal.id, 2, 1)
            by_rounds = await store.update_goal(db, ANA, goal.id, 3, round_cap=2)
        other = await _create(a, cost_cap_credits=10)
        async with tenant_session(a) as db:
            await store.record_round(db, ANA, other.id, 1, 6)
            by_cost = await store.update_goal(db, ANA, other.id, 2, cost_cap_credits=6)
        return by_rounds, by_cost

    by_rounds, by_cost = _go(world, go)
    for got in (by_rounds, by_cost):
        assert got is not None and (got.state, got.block_code) == ("blocked", "cap_reached")


def test_r8_a_blocked_goal_takes_no_round_and_keeps_its_code(world) -> None:
    a = world.p.org_a

    async def go() -> store.Goal | None:
        goal = await _create(a)
        async with tenant_session(a) as db:
            blocked = await store.update_goal(
                db, ANA, goal.id, 1, state="blocked", block_code="needs_member")
            assert blocked is not None
        with pytest.raises(store.GoalStateError):
            async with tenant_session(a) as db:
                await store.record_round(db, ANA, goal.id, 2, 1)
        async with tenant_session(a) as db:
            # A cap change of a blocked goal keeps its code.
            return await store.update_goal(db, ANA, goal.id, 2, round_cap=60)

    got = _go(world, go)
    assert got is not None and (got.state, got.block_code, got.round_cap) == (
        "blocked", "needs_member", 60)
    assert got.block_message == store.BLOCK_CODES["needs_member"]


@pytest.mark.parametrize("final", store.FINAL_STATES)
def test_r8_a_final_goal_never_changes(world, final: str) -> None:
    a = world.p.org_a

    async def go() -> store.Goal | None:
        goal = await _create(a)
        async with tenant_session(a) as db:
            ended = await store.update_goal(db, ANA, goal.id, 1, state=final)
        for call in (lambda db: store.update_goal(db, ANA, goal.id, 2, state="active"),
                     lambda db: store.record_round(db, ANA, goal.id, 2, 1)):
            with pytest.raises(store.GoalStateError):
                async with tenant_session(a) as db:
                    await call(db)
        return ended

    ended = _go(world, go)
    assert ended is not None and ended.state == final and ended.block_code is None
    assert ended.finished_at is not None and ended.version == 2
    row = _row(world, ended.id)
    assert (row["state"], row["version"]) == (final, 2)


# ── e. The CHECKs ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("params", "constraint"), [
    ({"state": "blocked", "block": "paused"}, "goal_records_block_code"),
    ({"state": "blocked"}, "goal_records_blocked_has_code"),
    ({"block": "needs_member"}, "goal_records_blocked_has_code"),
    ({"state": "done"}, "goal_records_finished"),
    ({"fin": datetime(2026, 11, 1, tzinfo=UTC)}, "goal_records_finished"),
    ({"state": "live"}, "goal_records_state"),
    ({"kind": "research\n"}, "goal_records_kind"),
    ({"kind": "Research"}, "goal_records_kind"),
    ({"m": "Ana@WEB10.test"}, "goal_records_member_email"),
    ({"m": ""}, "goal_records_member_email"),
    ({"obj": "   "}, "goal_records_objective"),
    ({"obj": "x" * 2001}, "goal_records_objective"),
    ({"ver": 0}, "goal_records_version"),
    ({"state": "blocked", "block": "needs_member", "cap": 0}, "goal_records_round_cap"),
    ({"cap": 10001}, "goal_records_round_cap"),
    ({"rc": -1}, "goal_records_round_count"),
    ({"state": "blocked", "block": "needs_member", "ccap": Decimal("0")},
     "goal_records_cost_cap"),
    ({"ccap": Decimal("1000000000.0001")}, "goal_records_cost_cap"),
    ({"used": Decimal("-1")}, "goal_records_cost_used"),
    # Postgres sorts NaN above every number. The goal is blocked, so the CHECK
    # of the caps does not refuse the row first.
    ({"state": "blocked", "block": "needs_member", "used": Decimal("NaN")},
     "goal_records_cost_used"),
    ({"state": "blocked", "block": "needs_member", "ccap": Decimal("NaN")},
     "goal_records_cost_cap"),
    ({"rc": 40, "cap": 40}, "goal_records_active_under_caps"),
    ({"used": Decimal("10"), "ccap": Decimal("10")}, "goal_records_active_under_caps"),
], ids=["unknown_block_code", "blocked_with_no_code", "active_with_a_code",
        "done_with_no_finish", "active_with_a_finish", "unknown_state", "kind_final_newline",
        "kind_upper", "member_upper", "member_empty", "objective_blank", "objective_long",
        "version_0", "round_cap_0", "round_cap_over", "round_count_negative", "cost_cap_0",
        "cost_cap_over", "cost_used_negative", "cost_used_nan", "cost_cap_nan",
        "active_at_round_cap", "active_at_cost_cap"])
def test_r8_the_checks_refuse_a_bad_row(world, params, constraint) -> None:
    err = _refused(world, world.p.org_a, _RAW_GOAL, _goal_params(**params))
    assert constraint in err
    assert _count(world) == 0


def test_r8_the_raw_insert_of_a_good_row_passes(world) -> None:
    """The positive control of the cases above."""
    async def go() -> None:
        async with tenant_session(world.p.org_a) as db:
            await db.execute(text(_RAW_GOAL), _goal_params())

    _go(world, go)
    assert _count(world) == 1


# ── f. The trigger ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
@pytest.mark.parametrize("assignment", [
    "objective = 'Something else'",
    "member_email = 'ben.other@web10.test'",
    "kind = 'coding'",
    "created_at = now() - interval '1 day'",
    "id = gen_random_uuid()",
])
def test_r8_the_identity_of_a_goal_cannot_change(world, role: str, assignment: str) -> None:
    a = world.p.org_a
    goal = _go(world, lambda: _create(a))
    sql = (f"UPDATE goal_records SET {assignment}, version = version + 1 "
           "WHERE id = CAST(:i AS uuid)")
    if role == "app":
        err = _refused(world, a, sql, {"i": goal.id})
    else:
        with pytest.raises(DBAPIError) as exc, world.p.admin_engine.begin() as c:
            c.execute(text(sql), {"i": goal.id})
        err = str(exc.value)
    assert "the objective and the identity of a goal cannot change" in err
    row = _row(world, goal.id)
    assert (row["objective"], row["member_email"], row["kind"], row["version"]) == (
        _OBJECTIVE, ANA, "research", 1)


def test_r8_the_store_refuses_an_objective_change_and_the_row_stays(world) -> None:
    a = world.p.org_a
    goal = _go(world, lambda: _create(a))

    async def go() -> None:
        async with tenant_session(a) as db:
            await store.update_goal(db, ANA, goal.id, 1, objective="Something else")

    with pytest.raises(store.GoalStoreError, match="objective cannot change"):
        _go(world, go)
    row = _row(world, goal.id)
    assert (row["objective"], row["version"]) == (_OBJECTIVE, 1)


@pytest.mark.parametrize("role", ["app", "superuser"])
@pytest.mark.parametrize("version", ["version", "version + 2", "1"])
def test_r8_an_update_must_raise_the_version_by_one(world, role: str, version: str) -> None:
    """A writer that forgets the version would let a stale view win. The
    trigger refuses it, for each role."""
    a = world.p.org_a
    goal = _go(world, lambda: _create(a))
    sql = (f"UPDATE goal_records SET round_cap = 41, version = {version} "
           "WHERE id = CAST(:i AS uuid)")
    if role == "app":
        err = _refused(world, a, sql, {"i": goal.id})
    else:
        with pytest.raises(DBAPIError) as exc, world.p.admin_engine.begin() as c:
            c.execute(text(sql), {"i": goal.id})
        err = str(exc.value)
    assert "each update raises the version by one" in err
    row = _row(world, goal.id)
    assert (row["round_cap"], row["version"]) == (40, 1)


def test_r8_an_update_that_raises_the_version_by_one_passes(world) -> None:
    """The positive control of the two cases above."""
    a = world.p.org_a
    goal = _go(world, lambda: _create(a))

    async def go() -> int:
        async with tenant_session(a) as db:
            return int((await db.execute(text(
                "UPDATE goal_records SET round_cap = 41, version = version + 1 "
                "WHERE id = CAST(:i AS uuid)"), {"i": goal.id})).rowcount)

    assert _go(world, go) == 1
    assert _row(world, goal.id)["version"] == 2


# ── g. The member purge ────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_a_member_purge_removes_the_goals_of_this_org_only(world, role: str) -> None:
    """The purge clause of ``goal_records`` deletes the member's goals of
    THIS org. A colleague's goals stay, and so do the goals of the same
    address in another org. The superuser run bypasses RLS, so only the
    clause's own org predicate keeps org B's row safe there."""
    from gateway.routes.admin.members import _PURGE_DELETES, _purge_params

    [rows] = [r for r in _PURGE_DELETES if r.table == "goal_records"]
    a, b = world.p.org_a, world.p.org_b

    async def seed() -> dict[str, str]:
        return {"mine": (await _create(a, member=ANA)).id,
                "mine_too": (await _create(a, member=ANA, objective="Second")).id,
                "bens": (await _create(a, member=BEN)).id,
                "theirs": (await _create(b, member=ANA)).id}

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
    with world.p.admin_engine.connect() as c:
        left = set(c.execute(text(
            "SELECT id::text FROM goal_records WHERE id = ANY(CAST(:i AS uuid[]))"),
            {"i": list(ids.values())}).scalars().all())
    assert left == {ids["bens"], ids["theirs"]}
