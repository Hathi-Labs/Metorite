"""WS-BRIEF BR-4 — the brief runs and claims on a real database (R8).

Spec: ``project-docs/specs/core_brief.md`` §3 and §10 row 1. Code:
``gateway/brief/store.py``. Migration: the file that creates ``brief_runs``
(315 at build).

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members, a frozen clock.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_brief_store_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (BR-4).**

a. The migration applies, and both tables have ENABLE and FORCE RLS.
b. A second create of one slot answers the same row and makes no second row.
c. The CHECKs hold the kinds, the states, the lead source, the sections, a
   model lead that cites a claim, and a value with its path.
d. A claim with an empty ``source_app``, ``source_kind``, ``source_id`` or
   ``href`` fails its CHECK. ``source_id`` has the ``<app>:<ref>`` shape.
   ``source_ids`` holds at most 50. The foreign key on the run cascades.
g. Org B reads nothing of org A, and a member reads only their own runs.
h. A retry of ``add_claims`` with the same positions makes one set, not two.
i. ``prune_runs`` deletes the runs of the bound org before the cut-off, and
   their claims. A later run and a run of another org stay.
j. The member purge deletes the member's runs and claims of this org. The
   runs of another member and of another org stay, also as the superuser.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. The migration and ``generated/04_policies.sql``: drop the two
  ``FORCE  ROW LEVEL SECURITY`` lines of ``brief_runs``.
  ``test_the_migration_holds_the_checks_and_the_rls`` and
  ``test_r8_both_tables_have_enable_and_force_rls`` go red.
* M2. The migration: drop the CHECK ``brief_claims_source_present``.
  ``test_r8_a_claim_with_no_source_is_refused`` goes red.
* M3. The migration: drop the UNIQUE ``brief_runs_slot_key``.
  ``test_r8_a_second_create_answers_the_same_row`` goes red.
* M4. The migration: make ``brief_claims_run_position_key`` a plain index.
  ``test_r8_a_retry_of_the_same_claims_makes_one_set`` goes red.
* M5. ``routes/admin/members.py``: drop the org predicate of ``brief_runs``.
  ``test_r8_a_member_purge_leaves_no_brief_behind[superuser]`` goes red.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.brief import store
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
    if "CREATE TABLE IF NOT EXISTS brief_runs (" in p.read_text(encoding="utf-8"))
_POLICIES = _ROOT / "infra" / "postgres" / "generated" / "04_policies.sql"

TAG = uuid.uuid4().hex[:8]
#: The two members of this suite. The tag lets teardown find their rows.
ANA = f"ana.{TAG}@br4.test"
BEN = f"ben.{TAG}@br4.test"

#: The frozen clock: Tuesday 10 March 2099, 07:30 in Asia/Kolkata.
T = datetime(2099, 3, 10, 2, 0, tzinfo=UTC)
D = date(2099, 3, 10)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    yield SimpleNamespace(p=p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM brief_runs WHERE member_email LIKE :t"),
                  {"t": f"%.{TAG}@br4.test"})


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _create(org: str, member: str = ANA, kind: str = "brief") -> store.Created:
    async with tenant_session(org) as db:
        return await store.create_run(db, member, kind=kind, local_date=D, slot_at=T)


def _claim(**kw: Any) -> store.ClaimIn:
    base: dict[str, Any] = {
        "position": 0, "section": "now", "text": "Reply to Ravi about the quote.",
        "source_app": "email", "source_kind": "email_reply",
        "source_id": "email:thread:42", "href": "/email?thread=42"}
    return store.ClaimIn(**{**base, **kw})


_COUNT_SQL = {
    "brief_runs": "SELECT count(*) FROM brief_runs r WHERE r.member_email LIKE :t",
    "brief_claims": ("SELECT count(*) FROM brief_claims c JOIN brief_runs r "
                     "ON r.id = c.run_id WHERE r.member_email LIKE :t"),
}


def _count(world: Any, table: str) -> int:
    """The rows of this suite in *table*, read as the superuser."""
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(_COUNT_SQL[table]),
                             {"t": f"%.{TAG}@br4.test"}).scalar_one())


def _refused(world: Any, org: str, sql: str, params: dict[str, Any]) -> str:
    """Run *sql* as the app role in *org*. Answers the error text."""
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    return str(exc.value)


_RAW_RUN = (
    "INSERT INTO brief_runs (member_email, kind, local_date, slot_at, state, "
    "lead_source, lead_claim_ids) VALUES (:m, :k, :d, :slot, :st, :ls, "
    "CAST(:ids AS uuid[]))")

_RAW_CLAIM = (
    "INSERT INTO brief_claims (run_id, position, section, text, source_app, "
    "source_kind, source_id, source_ids, href, value, value_path) VALUES "
    "(CAST(:run AS uuid), 0, :sec, 'x', 'email', 'email_reply', :sid, "
    "CAST(:sids AS text[]), '/email', CAST(:val AS numeric), :vp)")


def _run_params(**kw: Any) -> dict[str, Any]:
    return {"m": ANA, "k": "brief", "d": D, "slot": T, "st": "planned", "ls": None,
            "ids": [], **kw}


def _claim_params(run: str, **kw: Any) -> dict[str, Any]:
    return {"run": run, "sec": "now", "sid": "email:1", "sids": [], "val": None,
            "vp": None, **kw}


# ── The schema and the wiring ──────────────────────────────────────────────


def test_the_migration_holds_the_checks_and_the_rls() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    policies = _POLICIES.read_text(encoding="utf-8")
    for table in ("brief_runs", "brief_claims"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    for value in store.KINDS + store.STATES + store.SECTIONS:
        assert f"'{value}'" in sql, value
    assert "UNIQUE (organization_id, member_email, kind, local_date)" in sql
    assert "source_id ~ '^[a-z_]+:.+'" in sql
    assert "gtd_" not in sql
    # No statement names an org as a value: the DEFAULT and RLS bind it.
    for name in dir(store):
        if name.endswith("_SQL"):
            stmt = getattr(store, name)
            assert ":org" not in stmt, name
            if stmt.startswith("INSERT"):
                assert "organization_id" not in stmt.split("VALUES")[0].split("SELECT")[0]


def test_the_store_adds_no_second_grammar_and_no_href_builder() -> None:
    """Acceptance e and f. The CHECK is the fence of ``<app>:<ref>``."""
    src = (_ROOT / "apps" / "services" / "gateway" / "gateway" / "brief" / "store.py").read_text(
        encoding="utf-8")
    assert "normalize_grant_subject" not in src
    assert "import re" not in src
    assert "def build_href" not in src and "def href" not in src


def test_r8_both_tables_have_enable_and_force_rls(world) -> None:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('brief_runs', 'brief_claims') ORDER BY relname")).all()
    assert [tuple(r) for r in rows] == [
        ("brief_claims", True, True), ("brief_runs", True, True)]


# ── b. One run for each slot ───────────────────────────────────────────────


def test_r8_a_second_create_answers_the_same_row(world) -> None:
    a = world.p.org_a

    async def go() -> tuple[store.Created, store.Created, store.Created]:
        first = await _create(a)
        second = await _create(a, member=ANA.upper())
        other_kind = await _create(a, kind="closeout")
        return first, second, other_kind

    first, second, other_kind = _go(world, go)
    assert first.created is True and second.created is False
    assert second.run.id == first.run.id
    assert first.run.state == "planned" and first.run.member_email == ANA
    assert first.run.slot_at == T and first.run.local_date == D
    assert other_kind.created is True and other_kind.run.id != first.run.id
    assert _count(world, "brief_runs") == 2
    # The table refuses a second row on its own, not only the store.
    err = _refused(world, a, _RAW_RUN, _run_params())
    assert "brief_runs_slot_key" in err


# ── c. The CHECKs of a run ─────────────────────────────────────────────────


@pytest.mark.parametrize(("params", "constraint"), [
    ({"k": "weekly"}, "brief_runs_kind"),
    ({"st": "sent"}, "brief_runs_state"),
    ({"ls": "guess"}, "brief_runs_lead_source"),
    ({"ls": "model"}, "brief_runs_model_lead_cites"),
    ({"m": "Ana@BR4.test"}, "brief_runs_member_email"),
    ({"m": ""}, "brief_runs_member_email"),
])
def test_r8_the_run_checks_refuse_a_bad_row(world, params, constraint) -> None:
    err = _refused(world, world.p.org_a, _RAW_RUN, _run_params(**params))
    assert constraint in err


def test_r8_a_model_lead_that_cites_a_claim_is_legal(world) -> None:
    async def go() -> None:
        async with tenant_session(world.p.org_a) as db:
            await db.execute(text(_RAW_RUN), _run_params(
                ls="model", ids=[str(uuid.uuid4())]))

    _go(world, go)
    assert _count(world, "brief_runs") == 1


@pytest.mark.parametrize("state", store.STATES)
def test_r8_set_state_moves_the_run_and_stamps_the_time(world, state) -> None:
    async def go() -> store.Run | None:
        run = (await _create(world.p.org_a)).run
        async with tenant_session(world.p.org_a) as db:
            assert await store.set_state(db, ANA, run.id, state, now=T) is True
            return await store.read_run(db, ANA, kind="brief", local_date=D)

    run = _go(world, go)
    assert run is not None and run.state == state
    assert run.ready_at == (T if state == "ready" else None)
    assert run.delivered_at == (T if state == "delivered" else None)


def test_the_store_refuses_a_bad_state_kind_or_member_before_the_db() -> None:
    async def go() -> None:
        with pytest.raises(store.BriefStoreError):
            await store.set_state(None, ANA, str(uuid.uuid4()), "sent", now=T)
        with pytest.raises(store.BriefStoreError):
            await store.create_run(None, ANA, kind="weekly", local_date=D, slot_at=T)
        with pytest.raises(store.BriefStoreError):
            await store.create_run(None, "  ", kind="brief", local_date=D, slot_at=T)
        with pytest.raises(store.BriefStoreError):
            await store.create_run(None, ANA, kind="brief", local_date=D,
                                   slot_at=T.replace(tzinfo=None))

    asyncio.run(go())


# ── d. The claims ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("column", ["source_app", "source_kind", "source_id", "href"])
def test_r8_a_claim_with_no_source_is_refused(world, column) -> None:
    async def go() -> None:
        run = (await _create(world.p.org_a)).run
        async with tenant_session(world.p.org_a) as db:
            await store.add_claims(db, ANA, run.id, [_claim(**{column: ""})])

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    # An empty source_id also fails the shape CHECK, and Postgres names the
    # first CHECK that fails. The other three columns name the source CHECK.
    names = ("brief_claims_source_present",) + (
        ("brief_claims_source_id_shape",) if column == "source_id" else ())
    assert any(n in str(exc.value) for n in names), str(exc.value)
    assert _count(world, "brief_claims") == 0


@pytest.mark.parametrize("source_id", ["email", "Email:1", ":1", "email:", "e-mail:1", " email:1"])
def test_r8_a_source_id_with_no_app_ref_shape_is_refused(world, source_id) -> None:
    async def go() -> None:
        run = (await _create(world.p.org_a)).run
        async with tenant_session(world.p.org_a) as db:
            await store.add_claims(db, ANA, run.id, [_claim(source_id=source_id)])

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    assert "brief_claims_source_id_shape" in str(exc.value)


@pytest.mark.parametrize(("params", "constraint"), [
    ({"sec": "later"}, "brief_claims_section"),
    ({"sids": [f"email:{i}" for i in range(51)]}, "brief_claims_source_ids_max"),
    ({"val": "3"}, "brief_claims_value_path"),
])
def test_r8_the_claim_checks_refuse_a_bad_row(world, params, constraint) -> None:
    run = _go(world, lambda: _create(world.p.org_a)).run
    err = _refused(world, world.p.org_a, _RAW_CLAIM, _claim_params(run.id, **params))
    assert constraint in err


def test_r8_claims_round_trip_in_order_and_split_on_the_first_colon(world) -> None:
    claims = [
        _claim(position=1, section="numbers", text="3 replies waited over a day.",
               source_kind="count", source_id="email:count:waited",
               source_ids=tuple(f"email:t{i}" for i in range(50)), value=Decimal(3),
               value_path="facts.waited_hours", fingerprint="fp1"),
        _claim(position=0, source_app="calendar", source_kind="meeting",
               source_id="calendar:2099-03-10", href="/calendar?date=2099-03-10"),
    ]

    async def go() -> list[store.Claim]:
        run = (await _create(world.p.org_a)).run
        async with tenant_session(world.p.org_a) as db:
            ids = await store.add_claims(db, ANA, run.id, claims)
            assert len(ids) == 2
            return await store.read_claims(db, ANA, run.id)

    got = _go(world, go)
    assert [c.position for c in got] == [0, 1]
    assert got[0].source_id.split(":", 1) == ["calendar", "2099-03-10"]
    assert got[1].source_id.split(":", 1) == ["email", "count:waited"]
    assert len(got[1].source_ids) == 50 and got[1].value == Decimal(3)
    assert got[1].value_path == "facts.waited_hours" and got[1].fingerprint == "fp1"
    assert got[0].href == "/calendar?date=2099-03-10"


def test_the_store_refuses_more_than_fifty_source_ids_before_the_db() -> None:
    async def go() -> None:
        with pytest.raises(store.BriefStoreError):
            await store.add_claims(None, ANA, str(uuid.uuid4()), [
                _claim(source_ids=tuple(f"email:{i}" for i in range(51)))])

    asyncio.run(go())


def test_r8_deleting_a_run_deletes_its_claims(world) -> None:
    async def go() -> str:
        run = (await _create(world.p.org_a)).run
        async with tenant_session(world.p.org_a) as db:
            await store.add_claims(db, ANA, run.id, [_claim(), _claim(position=1)])
        return run.id

    run_id = _go(world, go)
    assert _count(world, "brief_claims") == 2
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM brief_runs WHERE id = CAST(:i AS uuid)"), {"i": run_id})
    with world.p.admin_engine.connect() as c:
        left = c.execute(text("SELECT count(*) FROM brief_claims WHERE run_id = "
                              "CAST(:i AS uuid)"), {"i": run_id}).scalar_one()
    assert left == 0


# ── g. Two orgs and two members ────────────────────────────────────────────


def test_r8_org_b_reads_and_writes_nothing_of_org_a(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, Any]:
        run = (await _create(a)).run
        async with tenant_session(a) as db:
            await store.add_claims(db, ANA, run.id, [_claim()])
        out: dict[str, Any] = {"run": run}
        async with tenant_session(b) as db:
            out["read"] = await store.read_run(db, ANA, kind="brief", local_date=D)
            out["claims"] = await store.read_claims(db, ANA, run.id)
            out["set"] = await store.set_state(db, ANA, run.id, "failed", now=T)
            out["add"] = await store.add_claims(db, ANA, run.id, [_claim(position=5)])
            out["raw"] = (await db.execute(text(
                "SELECT count(*) FROM brief_runs"))).scalar_one()
        # Org B makes its own run for the same member and slot: a new row.
        out["b_run"] = await _create(b)
        return out

    out = _go(world, go)
    assert out["read"] is None and out["claims"] == []
    assert out["set"] is False and out["add"] == []
    assert out["raw"] == 0
    assert out["b_run"].created is True and out["b_run"].run.id != out["run"].id
    with world.p.admin_engine.connect() as c:
        state = c.execute(text("SELECT state FROM brief_runs WHERE id = CAST(:i AS uuid)"),
                          {"i": out["run"].id}).scalar_one()
        orgs = c.execute(text(
            "SELECT DISTINCT c.organization_id::text FROM brief_claims c JOIN brief_runs r "
            "ON r.id = c.run_id WHERE r.member_email = :m"), {"m": ANA}).scalars().all()
    assert state == "planned"
    assert orgs == [a]


def test_r8_a_claim_cannot_point_at_a_run_of_another_org(world) -> None:
    """A foreign key check skips RLS, so the pair (org, run) is the fence."""
    run = _go(world, lambda: _create(world.p.org_b)).run
    err = _refused(world, world.p.org_a, _RAW_CLAIM, _claim_params(run.id))
    assert "brief_claims_run_fk" in err


def test_r8_a_member_reads_and_writes_only_their_own_runs(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        run = (await _create(a, member=ANA)).run
        async with tenant_session(a) as db:
            await store.add_claims(db, ANA, run.id, [_claim()])
        out: dict[str, Any] = {}
        async with tenant_session(a) as db:
            out["read"] = await store.read_run(db, BEN, kind="brief", local_date=D)
            out["claims"] = await store.read_claims(db, BEN, run.id)
            out["set"] = await store.set_state(db, BEN, run.id, "failed", now=T)
            out["add"] = await store.add_claims(db, BEN, run.id, [_claim(position=5)])
            # The positive control: Ana reads her own run and its one claim.
            out["own"] = await store.read_run(db, ANA, kind="brief", local_date=D)
            out["own_claims"] = await store.read_claims(db, ANA, run.id)
        return out

    out = _go(world, go)
    assert out["read"] is None and out["claims"] == []
    assert out["set"] is False and out["add"] == []
    assert out["own"] is not None and out["own"].state == "planned"
    assert len(out["own_claims"]) == 1


# ── h. A retry ─────────────────────────────────────────────────────────────


def test_r8_a_retry_of_the_same_claims_makes_one_set(world) -> None:
    claims = [_claim(), _claim(position=1, text="Call the bank.")]

    async def go() -> tuple[list[str], list[str], list[store.Claim]]:
        run = (await _create(world.p.org_a)).run
        async with tenant_session(world.p.org_a) as db:
            first = await store.add_claims(db, ANA, run.id, claims)
        async with tenant_session(world.p.org_a) as db:
            second = await store.add_claims(db, ANA, run.id, [*claims, _claim(position=2)])
            got = await store.read_claims(db, ANA, run.id)
        return first, second, got

    first, second, got = _go(world, go)
    assert len(first) == 2
    # Only the new position takes a row. The two old ones give no id.
    assert len(second) == 1 and second[0] not in first
    assert [c.position for c in got] == [0, 1, 2]
    assert _count(world, "brief_claims") == 3


# ── i. Retention ───────────────────────────────────────────────────────────


def test_r8_prune_runs_deletes_old_runs_of_the_bound_org_only(world) -> None:
    from datetime import timedelta

    old, cut, kept = D - timedelta(days=100), D - timedelta(days=90), D - timedelta(days=89)

    async def make(org: str, member: str, day: date) -> str:
        async with tenant_session(org) as db:
            run = (await store.create_run(db, member, kind="brief", local_date=day,
                                          slot_at=T)).run
            await store.add_claims(db, member, run.id, [_claim()])
            return run.id

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {
            "a_old": await make(world.p.org_a, ANA, old),
            "a_ben_old": await make(world.p.org_a, BEN, old),
            "a_edge": await make(world.p.org_a, ANA, cut),
            "a_new": await make(world.p.org_a, ANA, kept),
            "b_old": await make(world.p.org_b, ANA, old),
        }
        async with tenant_session(world.p.org_a) as db:
            out["n"] = await store.prune_runs(db, cut)
        return out

    out = _go(world, go)
    assert out["n"] == 2
    with world.p.admin_engine.connect() as c:
        runs = set(c.execute(text(
            "SELECT id::text FROM brief_runs WHERE member_email LIKE :t"),
            {"t": f"%.{TAG}@br4.test"}).scalars().all())
        claims = set(c.execute(text(
            "SELECT run_id::text FROM brief_claims WHERE run_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [out[k] for k in ("a_old", "a_ben_old", "a_edge", "a_new", "b_old")]},
        ).scalars().all())
    assert runs == {out["a_edge"], out["a_new"], out["b_old"]}
    assert claims == runs


def test_the_store_refuses_a_prune_with_no_date_before_the_db() -> None:
    async def go() -> None:
        with pytest.raises(store.BriefStoreError):
            await store.prune_runs(None, T)
        with pytest.raises(store.BriefStoreError):
            await store.prune_runs(None, "2099-01-01")

    asyncio.run(go())


# ── j. The member purge ────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_a_member_purge_leaves_no_brief_behind(world, role: str) -> None:
    """The purge clause of ``brief_runs`` deletes the member's runs of THIS
    org, and their claims by cascade. A colleague's runs stay, and so do the
    runs of the same address in another org. The superuser run bypasses RLS,
    so only the clause's own org predicate keeps org B's row safe there."""
    from gateway.routes.admin.members import _PURGE_DELETES, _purge_params

    [rows] = [r for r in _PURGE_DELETES if r.table == "brief_runs"]
    a, b = world.p.org_a, world.p.org_b

    async def make(org: str, member: str) -> str:
        run = (await _create(org, member=member)).run
        async with tenant_session(org) as db:
            await store.add_claims(db, member, run.id, [_claim(), _claim(position=1)])
        return run.id

    async def seed() -> dict[str, str]:
        return {"mine": await make(a, ANA), "bens": await make(a, BEN),
                "theirs": await make(b, ANA)}

    ids = _go(world, seed)
    engine = world.p.app_url if role == "app" else world.p.admin_engine.url

    async def purge() -> tuple[int, int]:
        params = _purge_params(rows, uid=str(uuid.uuid4()), email=ANA)
        async with tenant_engine_scope(_dsn(engine)), tenant_session(a) as db:
            n = (await db.execute(text(rows.count_sql), params)).scalar_one()
            gone = await db.execute(text(rows.delete_sql), params)
            return int(n), int(gone.rowcount)

    counted, deleted = asyncio.run(purge())
    assert counted == deleted == 1
    with world.p.admin_engine.connect() as c:
        runs = set(c.execute(text(
            "SELECT id::text FROM brief_runs WHERE id = ANY(CAST(:i AS uuid[]))"),
            {"i": list(ids.values())}).scalars().all())
        claims = c.execute(text(
            "SELECT run_id::text, count(*) FROM brief_claims "
            "WHERE run_id = ANY(CAST(:i AS uuid[])) GROUP BY run_id"),
            {"i": list(ids.values())}).all()
    assert runs == {ids["bens"], ids["theirs"]}
    assert dict(claims) == {ids["bens"]: 2, ids["theirs"]: 2}


# ── The guarded move, the lead and the link (BR-2a-1) ──────────────────────


def test_r8_a_guarded_move_changes_only_a_row_in_the_from_state(world) -> None:
    """``set_state`` with ``from_state`` is ``UPDATE ... WHERE state = :from``.
    A row that moved on does not move again, and a pair outside
    ``TRANSITIONS`` is refused before any statement."""
    org = world.p.org_a

    async def go() -> list:
        out = []
        async with tenant_session(org) as db:
            run = (await store.create_run(db, ANA, kind="brief", local_date=D, slot_at=T)).run
            out.append(await store.set_state(db, ANA, run.id, "gathered", now=T,
                                             from_state="planned"))
            out.append(await store.set_state(db, ANA, run.id, "gathered", now=T,
                                             from_state="planned"))  # moved on
            out.append(await store.set_state(db, ANA, run.id, "empty", now=T,
                                             from_state="planned"))  # a legal pair, wrong row
            out.append(await store.set_state(db, ANA, run.id, "ready", now=T,
                                             from_state="gathered"))
            out.append((await store.read_run(db, ANA, kind="brief", local_date=D)).state)
            with pytest.raises(store.BriefStoreError):
                await store.set_state(db, ANA, run.id, "ready", now=T, from_state="planned")
            with pytest.raises(store.BriefStoreError):
                await store.set_state(db, ANA, run.id, "delivered", now=T, from_state="ready")
            # Another member moves nothing.
            out.append(await store.set_state(db, BEN, run.id, "empty", now=T,
                                             from_state="planned"))
        return out

    assert _go(world, go) == [True, False, False, True, "ready", False]


def test_r8_the_lead_and_the_ledger_link_write_once_for_the_member(world) -> None:
    org = world.p.org_a
    ledger_id = str(uuid.uuid4())

    async def go() -> tuple:
        async with tenant_session(org) as db:
            run = (await store.create_run(db, ANA, kind="brief", local_date=D, slot_at=T)).run
            other = await store.set_lead(db, BEN, run.id, "Not yours.", source="template")
            lead = await store.set_lead(db, ANA, run.id, "Your day. 2 things need you.",
                                        source="template")
            with pytest.raises(store.BriefStoreError):
                await store.set_lead(db, ANA, run.id, "x", source="human")
            with pytest.raises(store.BriefStoreError):
                await store.set_lead(db, ANA, run.id, " ", source="template")
            first = await store.link_run(db, ANA, run.id, ledger_id)
            second = await store.link_run(db, ANA, run.id, str(uuid.uuid4()))
            got = await store.read_run(db, ANA, kind="brief", local_date=D)
        return other, lead, first, second, got

    other, lead, first, second, got = _go(world, go)
    assert (other, lead, first, second) == (False, True, True, False)
    assert got.lead == "Your day. 2 things need you." and got.lead_source == "template"
    assert got.lead_claim_ids == () and got.unattended_run_id == ledger_id
