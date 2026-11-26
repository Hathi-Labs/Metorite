"""R8 - the task routes name the bound org on three member tables (H-312b).

The fault this fences. Ten statements in ``gateway/routes/tasks`` named the
member only (``WHERE user_id = :uid``): the day state of the Calendar, the
rollover log and the connected task accounts. Under the owner role RLS does not
apply, so a member of two organizations read and changed the rows of the other
one too. Each statement now also names ``settings._BOUND_ORG``, and each INSERT
names the org. Migration 504 puts ``organization_id`` on the numbered ladder
for the three tables.

The route halves run the REAL functions on the phase-4 catalog of
``test_h3_rls_promotion_rehearsal.py``, with two organizations. The owner half
points the shared engine at the SUPERUSER, which bypasses RLS as the owner role
of production does, so only the statement keeps the other org out. The RLS half
runs as the non-privileged ``acb_app_h3rls`` role. A hermetic fake would agree
with any SQL it is handed.

⚠️ The key of ``calendar_day_state`` is still ``(user_id, day)``. One address
holds one row for each day across its orgs. So the seeds put org A and org B on
different days, and ``test_a_save_of_a_day_that_another_org_holds_*`` pins what
a save in the second org does today: it changes nothing. The re-key is a later
contract step.

The replay half runs the body of 504 in a scratch schema, in a transaction that
rolls back, on a fresh connection with no tenant bound. A bound tenant would
fill the old rows through the column default and hide the backfill.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant, tenant_session
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

# The two-org phase-4 fixture and its DB gate. Used by name for injection.
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

#: The day of org A, and the next day for org B. The old key allows one row
#: for each address and day, so the two orgs cannot share a day.
DAY_A = date(2031, 3, 10)
DAY_B = DAY_A + timedelta(days=1)
DAY_NEW = DAY_A + timedelta(days=2)

_TABLES = ("calendar_day_state", "calendar_rollover_log", "task_accounts")


def _address() -> str:
    return f"two.orgs.{uuid.uuid4().hex[:8]}@h312b.test"


@pytest.fixture
def db(promoted):  # noqa: F811 - a fixture
    """The promoted catalog, with this module's member rows removed after."""
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            for table in _TABLES:
                c.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": email})


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


@contextlib.asynccontextmanager
async def _as_owner(ns, org: str):
    """Bind ``org``, and point the shared engine at the SUPERUSER. It bypasses
    RLS, as the owner role of production does, so only the statement filters."""
    with _tenant(org):
        async with tenant_engine_scope(
                ns.admin_engine.url.render_as_string(hide_password=False)):
            yield


@contextlib.asynccontextmanager
async def _as_member(ns, org: str):
    """Bind ``org`` as the non-privileged app role, under FORCE RLS."""
    with _tenant(org):
        async with tenant_engine_scope(
                ns.app_url.render_as_string(hide_password=False)):
            yield


def _user(email: str) -> SimpleNamespace:
    return SimpleNamespace(email=email)


def _plan_json(tag: str) -> str:
    """A fresh stored plan, in the shape that ``_store_pending_plan`` writes."""
    from gateway.routes.tasks.calendar import DayPlan

    plan = DayPlan(blocks=[], unplaced=[], used_mins=0, capacity_mins=60, notes=tag)
    return json.dumps({"kind": "plan", "at": datetime.now(UTC).isoformat(),
                       "plan": plan.model_dump()})


def _seed_day(c, *, email: str, org: str, day: date, one: str, seed: str,
              tag: str) -> None:
    c.execute(text(
        "INSERT INTO calendar_day_state (user_id, day, one_thing_id, seed_ids, "
        "pending_plan, organization_id) VALUES (:u, :d, CAST(:one AS uuid), "
        "CAST(:seeds AS jsonb), CAST(:p AS jsonb), CAST(:o AS uuid))"),
        {"u": email, "d": day, "one": one, "seeds": json.dumps([seed]),
         "p": _plan_json(tag), "o": org})


def _day_rows(ns, email: str) -> list[tuple]:
    """Each day row of ``email``, as the superuser sees it, ordered by day."""
    with ns.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT day, organization_id::text, one_thing_id::text, seed_ids, "
            "pending_plan->'plan'->>'notes' FROM calendar_day_state "
            "WHERE user_id = :u ORDER BY day"), {"u": email}).all()
    return [tuple(r) for r in rows]


# ── The day state: the reads ────────────────────────────────────────────────


async def test_the_day_state_reads_return_only_the_bound_orgs_row_under_the_owner_role(
        db) -> None:
    """Org A holds DAY_A and org B holds DAY_B. Under the owner role RLS does
    not help, so only the statement keeps the row of the other org out. Each
    negative read has a positive twin: a broken statement also reads nothing."""
    from gateway.routes.tasks.calendar import _one_thing_for, get_day_state

    email = _address()
    db.made.append(email)
    one_a, one_b = str(uuid.uuid4()), str(uuid.uuid4())
    seed_a, seed_b = str(uuid.uuid4()), str(uuid.uuid4())
    with db.admin_engine.begin() as c:
        _seed_day(c, email=email, org=db.org_a, day=DAY_A, one=one_a, seed=seed_a,
                  tag="plan-orga")
        _seed_day(c, email=email, org=db.org_b, day=DAY_B, one=one_b, seed=seed_b,
                  tag="plan-orgb")

    async with _as_owner(db, db.org_b):
        async with tenant_session() as s:
            b_on_a = await _one_thing_for(s, email, DAY_A)
            b_on_b = await _one_thing_for(s, email, DAY_B)
        state_b_on_a = await get_day_state(DAY_A.isoformat(), _user(email))
        state_b_on_b = await get_day_state(DAY_B.isoformat(), _user(email))
    async with _as_owner(db, db.org_a):
        async with tenant_session() as s:
            a_on_a = await _one_thing_for(s, email, DAY_A)
            a_on_b = await _one_thing_for(s, email, DAY_B)
        state_a_on_a = await get_day_state(DAY_A.isoformat(), _user(email))

    assert (b_on_a, b_on_b) == (None, one_b)
    assert (a_on_a, a_on_b) == (one_a, None)
    assert (state_b_on_a.one_thing_id, state_b_on_a.seed_ids) == (None, [])
    assert (state_b_on_b.one_thing_id, state_b_on_b.seed_ids) == (one_b, [seed_b])
    assert (state_a_on_a.one_thing_id, state_a_on_a.seed_ids) == (one_a, [seed_a])


async def test_a_pending_plan_is_taken_only_in_its_own_org_under_the_owner_role(
        db) -> None:
    """``_take_pending_plan`` reads the plan and clears it. In org B it must
    neither return nor clear the plan of org A."""
    from gateway.routes.tasks.calendar import _take_pending_plan

    email = _address()
    db.made.append(email)
    with db.admin_engine.begin() as c:
        _seed_day(c, email=email, org=db.org_a, day=DAY_A, one=str(uuid.uuid4()),
                  seed=str(uuid.uuid4()), tag="plan-orga")
        _seed_day(c, email=email, org=db.org_b, day=DAY_B, one=str(uuid.uuid4()),
                  seed=str(uuid.uuid4()), tag="plan-orgb")
    now = datetime.now(UTC)

    async with _as_owner(db, db.org_b), tenant_session() as s:
        b_on_a = await _take_pending_plan(s, email, DAY_A, "plan", now)
        b_on_b = await _take_pending_plan(s, email, DAY_B, "plan", now)
    # Org A's plan is still there: the org-B take neither read nor cleared it.
    assert [r[4] for r in _day_rows(db, email)] == ["plan-orga", None]

    async with _as_owner(db, db.org_a), tenant_session() as s:
        a_on_a = await _take_pending_plan(s, email, DAY_A, "plan", now)

    assert b_on_a is None
    assert b_on_b is not None and b_on_b.notes == "plan-orgb"
    assert a_on_a is not None and a_on_a.notes == "plan-orga"
    assert [r[4] for r in _day_rows(db, email)] == [None, None]


# ── The day state: the writes ───────────────────────────────────────────────


async def test_a_save_of_a_day_that_another_org_holds_changes_nothing(db) -> None:
    """⚠️ Pinned on purpose. Org A holds DAY_A on the old key `(user_id, day)`.
    A save of DAY_A in org B must not touch that row. Before H-312b the two
    UPDATEs named the member only, so under the owner role they overwrote it.
    The save in org B is lost, and the route answers the empty day. The
    re-key of the contract step changes this test."""
    from gateway.routes.tasks.calendar import DayStatePatch, put_day_state

    email = _address()
    db.made.append(email)
    one_a, seed_a = str(uuid.uuid4()), str(uuid.uuid4())
    with db.admin_engine.begin() as c:
        _seed_day(c, email=email, org=db.org_a, day=DAY_A, one=one_a, seed=seed_a,
                  tag="plan-orga")
    before = _day_rows(db, email)

    async with _as_owner(db, db.org_b):
        echoed = await put_day_state(
            DayStatePatch(day=DAY_A.isoformat(), one_thing_id=str(uuid.uuid4()),
                          seed_ids=[str(uuid.uuid4())]),
            _user(email))

    assert _day_rows(db, email) == before
    assert before == [(DAY_A, db.org_a, one_a, [seed_a], "plan-orga")]
    assert (echoed.one_thing_id, echoed.seed_ids) == (None, [])


async def test_a_save_of_a_free_day_writes_a_row_of_the_bound_org(db) -> None:
    """The positive twin: on a day that no org holds, the save of org B makes
    org B's row, with the org named by the INSERT, and the echo reads it."""
    from gateway.routes.tasks.calendar import DayStatePatch, put_day_state

    email = _address()
    db.made.append(email)
    one_b, seed_b = str(uuid.uuid4()), str(uuid.uuid4())
    async with _as_owner(db, db.org_b):
        echoed = await put_day_state(
            DayStatePatch(day=DAY_NEW.isoformat(), one_thing_id=one_b,
                          seed_ids=[seed_b]),
            _user(email))

    assert (echoed.one_thing_id, echoed.seed_ids) == (one_b, [seed_b])
    assert _day_rows(db, email) == [(DAY_NEW, db.org_b, one_b, [seed_b], None)]


async def test_a_pending_plan_store_never_writes_the_row_of_another_org(db) -> None:
    """``_store_pending_plan`` inserts the bare row with DO NOTHING, and then
    updates the row of the bound org. Org A holds DAY_A, and org B holds DAY_B.
    The store of org B leaves DAY_A as it was. It rewrites the plan of its own
    DAY_B and keeps the rest of that row. It makes its own row on DAY_NEW.
    Under the owner role only the org filter of the UPDATE keeps org B off the
    row of org A."""
    from gateway.routes.tasks.calendar import DayPlan, _store_pending_plan

    email = _address()
    db.made.append(email)
    with db.admin_engine.begin() as c:
        _seed_day(c, email=email, org=db.org_a, day=DAY_A, one=str(uuid.uuid4()),
                  seed=str(uuid.uuid4()), tag="plan-orga")
        _seed_day(c, email=email, org=db.org_b, day=DAY_B, one=str(uuid.uuid4()),
                  seed=str(uuid.uuid4()), tag="plan-orgb-old")
    before = _day_rows(db, email)
    plan = DayPlan(blocks=[], unplaced=[], used_mins=0, capacity_mins=60,
                   notes="plan-orgb")
    now = datetime.now(UTC)

    async with _as_owner(db, db.org_b), tenant_session() as s:
        for day in (DAY_A, DAY_B, DAY_NEW):
            await _store_pending_plan(s, email, day, "plan", plan, now)

    rows = _day_rows(db, email)
    assert rows[0] == before[0]
    assert rows[1][:4] == before[1][:4]
    assert [(r[0], r[1], r[4]) for r in rows[1:]] == [
        (DAY_B, db.org_b, "plan-orgb"), (DAY_NEW, db.org_b, "plan-orgb")]


async def test_the_writes_hold_under_force_rls_as_the_app_role(db) -> None:
    """The same writes as the non-privileged role. Each save stamps its own
    org. Org A holds DAY_A. In org B, a save of DAY_A and a plan store on DAY_A
    change nothing and raise no error. The store also rewrites the plan of
    org B's own DAY_B and makes org B's row on DAY_NEW.

    ⚠️ This test caught a fault that a local run missed. The old store was one
    ON CONFLICT DO UPDATE with a WHERE that named the org. Postgres 16.15 read
    that WHERE first and skipped the row of org A. Postgres 16.16 checks the
    RLS USING of the conflict row first, and it raised an error."""
    from gateway.routes.tasks.calendar import (
        DayPlan,
        DayStatePatch,
        _store_pending_plan,
        put_day_state,
    )

    email = _address()
    db.made.append(email)
    one_a, one_b = str(uuid.uuid4()), str(uuid.uuid4())
    async with _as_member(db, db.org_a):
        await put_day_state(DayStatePatch(day=DAY_A.isoformat(), one_thing_id=one_a),
                            _user(email))
    async with _as_member(db, db.org_b):
        await put_day_state(DayStatePatch(day=DAY_B.isoformat(), one_thing_id=one_b),
                            _user(email))
    before = _day_rows(db, email)
    async with _as_member(db, db.org_b):
        echoed = await put_day_state(
            DayStatePatch(day=DAY_A.isoformat(), one_thing_id=str(uuid.uuid4())),
            _user(email))
    plan = DayPlan(blocks=[], unplaced=[], used_mins=0, capacity_mins=60,
                   notes="plan-orgb")
    async with _as_member(db, db.org_b), tenant_session() as s:
        for day in (DAY_A, DAY_B, DAY_NEW):
            await _store_pending_plan(s, email, day, "plan", plan, datetime.now(UTC))

    assert before == [(DAY_A, db.org_a, one_a, [], None),
                      (DAY_B, db.org_b, one_b, [], None)]
    assert (echoed.one_thing_id, echoed.seed_ids) == (None, [])
    assert _day_rows(db, email) == [
        before[0],
        (DAY_B, db.org_b, one_b, [], "plan-orgb"),
        (DAY_NEW, db.org_b, None, [], "plan-orgb"),
    ]


# ── The rollover log ────────────────────────────────────────────────────────


async def test_the_rollover_log_reads_only_the_bound_orgs_rows_under_the_owner_role(
        db) -> None:
    from gateway.routes.tasks.calendar import rollover_log

    email = _address()
    db.made.append(email)
    with db.admin_engine.begin() as c:
        for org, tag in ((db.org_a, "moved-orga"), (db.org_b, "moved-orgb")):
            c.execute(text(
                "INSERT INTO calendar_rollover_log (user_id, item_id, title, "
                "rolled_from, organization_id) VALUES (:u, CAST(:i AS uuid), :t, "
                "now() - interval '1 day', CAST(:o AS uuid))"),
                {"u": email, "i": str(uuid.uuid4()), "t": tag, "o": org})

    async with _as_owner(db, db.org_b):
        log_b = await rollover_log(limit=30, user=_user(email))
    async with _as_owner(db, db.org_a):
        log_a = await rollover_log(limit=30, user=_user(email))

    assert [r["title"] for r in log_b] == ["moved-orgb"]
    assert [r["title"] for r in log_a] == ["moved-orga"]


# ── The connected task accounts ─────────────────────────────────────────────


def _seed_account(c, *, email: str, org: str, tag: str) -> str:
    account_id = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO task_accounts (id, user_id, provider, workspace_id, label, "
        "credentials_encrypted, schema_cache, organization_id) VALUES "
        "(CAST(:id AS uuid), :u, 'clickup', :ws, :label, '', "
        "CAST(:cache AS jsonb), CAST(:o AS uuid))"),
        {"id": account_id, "u": email, "ws": f"ws-{tag}", "label": f"label-{tag}",
         "cache": json.dumps({"members": [
             {"name": f"Person {tag}", "provider_user_id": f"pid-{tag}"}]}),
         "o": org})
    return account_id


async def test_the_three_account_reads_return_only_the_bound_orgs_rows_under_the_owner_role(
        db, monkeypatch) -> None:
    """``task_accounts`` is UNIQUE on ``(user_id, provider, workspace_id)``, so
    each org gets its own workspace. Org A's row goes in first, so a read that
    drops the org filter would most likely find it."""
    from gateway.routes.tasks import people as people_mod
    from gateway.routes.tasks.ai import _clarify_accounts, _enrich_people
    from gateway.routes.tasks.capture_email import _find_pm_account_for_person

    async def _no_directory(_db) -> list:
        # The people directory wins over the account members when it is not
        # empty, and the seeded owner of the fixture has a directory row.
        return []

    monkeypatch.setattr(people_mod, "fetch_people_for_clarify", _no_directory)
    email = _address()
    db.made.append(email)
    with db.admin_engine.begin() as c:
        acct_a = _seed_account(c, email=email, org=db.org_a, tag="orga")
        acct_b = _seed_account(c, email=email, org=db.org_b, tag="orgb")

    async def _reads(org: str) -> tuple:
        async with _as_owner(db, org), tenant_session() as s:
            accounts = await _clarify_accounts(s, email)
            people = await _enrich_people(s, email)
            find_a = await _find_pm_account_for_person(s, email, {"name": "Person orga"})
            find_b = await _find_pm_account_for_person(s, email, {"name": "Person orgb"})
        return ([(str(a.id), a.label) for a in accounts],
                [p["name"] for p in people],
                find_a and find_a[0], find_b and find_b[0])

    assert await _reads(db.org_b) == (
        [(acct_b, "label-orgb")], ["Person orgb"], None, acct_b)
    assert await _reads(db.org_a) == (
        [(acct_a, "label-orga")], ["Person orga"], acct_a, None)


# ── Migration 504, replayed in a scratch schema ─────────────────────────────


def _migration() -> Path:
    """504, found by content, never by number (R1)."""
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*_*.sql"))
            if "$h312b_backfill$" in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


def _body() -> str:
    """504 without its own BEGIN and COMMIT, so a test can roll it back."""
    lines = _migration().read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if ln.strip() not in ("BEGIN;", "COMMIT;")]
    assert len(kept) == len(lines) - 2, "504 lost its one transaction"
    return "\n".join(kept)


_PRE_TENANCY = """
CREATE TABLE organization (id UUID PRIMARY KEY);
CREATE TABLE app_user (email TEXT, organization_id UUID);
CREATE TABLE calendar_day_state (
    user_id TEXT NOT NULL, day DATE NOT NULL, one_thing_id UUID,
    PRIMARY KEY (user_id, day));
CREATE TABLE calendar_rollover_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id TEXT NOT NULL,
    item_id UUID NOT NULL DEFAULT gen_random_uuid(), title TEXT);
CREATE TABLE task_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'clickup',
    workspace_id TEXT NOT NULL DEFAULT gen_random_uuid()::text);
"""


@contextlib.contextmanager
def _scratch(ns):
    """A fresh connection (no tenant GUC at all) in a transaction that rolls
    back, with ``search_path`` on a new schema. The migration's unqualified
    names then reach the scratch tables, and the real catalog stays as it is.
    Yields the connection, the list of the notices it raised, and the schema."""
    schema = f"h312b_{uuid.uuid4().hex[:8]}"
    eng = create_engine(ns.admin_engine.url, poolclass=NullPool)
    conn = eng.connect()
    raw = conn.connection.dbapi_connection
    notices: list[tuple[str, str]] = []

    def _on_notice(diag) -> None:
        notices.append((diag.severity_nonlocalized or diag.severity,
                        diag.message_primary or ""))

    raw.add_notice_handler(_on_notice)
    trans = conn.begin()
    try:
        assert conn.execute(text(
            "SELECT current_setting('app.tenant_id', true)")).scalar() is None
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        conn.execute(text(f"SET LOCAL search_path = {schema}"))
        yield conn, notices, schema
    finally:
        trans.rollback()
        raw.remove_notice_handler(_on_notice)
        conn.close()
        eng.dispose()


def _run(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _orgs_of(conn, table: str) -> dict[str, str | None]:
    rows = conn.execute(text(
        f"SELECT user_id, organization_id::text FROM {table}")).all()
    return {r[0]: r[1] for r in rows}


def _seed_old_rows(conn, addresses: list[str]) -> None:
    for addr in addresses:
        conn.execute(text(
            "INSERT INTO calendar_day_state (user_id, day) VALUES (:u, :d)"),
            {"u": addr, "d": DAY_A})
        conn.execute(text(
            "INSERT INTO calendar_rollover_log (user_id, title) VALUES (:u, 't')"),
            {"u": addr})
        conn.execute(text("INSERT INTO task_accounts (user_id) VALUES (:u)"),
                     {"u": addr})


def _warnings(notices: list[tuple[str, str]]) -> list[str]:
    return [m for sev, m in notices if sev == "WARNING"]


def test_504_tenants_old_rows_from_app_user_and_leaves_the_rest_null(db) -> None:
    """Two orgs, a fresh ladder shape (no column). An address that `app_user`
    holds in one org gives its rows to that org, in any case and with spaces.
    An address in two orgs, and an address that no member has, stay NULL, and
    the WARNING says so for each table. A second run changes nothing."""
    o1, o2 = str(uuid.uuid4()), str(uuid.uuid4())
    with _scratch(db) as (conn, notices, _schema):
        _run(conn, _PRE_TENANCY)
        conn.execute(text("INSERT INTO organization (id) VALUES "
                          "(CAST(:a AS uuid)), (CAST(:b AS uuid))"), {"a": o1, "b": o2})
        for email, org in (("a@h312b.test", o1), (" B@H312B.test", o2),
                           ("two@h312b.test", o1), ("two@h312b.test", o2)):
            conn.execute(text("INSERT INTO app_user (email, organization_id) "
                              "VALUES (:e, CAST(:o AS uuid))"), {"e": email, "o": org})
        _seed_old_rows(conn, ["A@h312b.test", "b@h312b.test ", "two@h312b.test",
                              "ghost@h312b.test"])

        _run(conn, _body())
        expected = {"A@h312b.test": o1, "b@h312b.test ": o2,
                    "two@h312b.test": None, "ghost@h312b.test": None}
        for table in _TABLES:
            assert _orgs_of(conn, table) == expected, table
        first = _warnings(notices)
        assert len(first) == 3
        for table, warning in zip(_TABLES, first, strict=True):
            assert warning.startswith(f"504: {table} has 2 row(s)"), warning

        _run(conn, _body())
        for table in _TABLES:
            assert _orgs_of(conn, table) == expected, table

        # The column: nullable, the tenant default, and one cascade to the org.
        for table in _TABLES:
            null_ok, default = conn.execute(text(
                "SELECT NOT a.attnotnull, pg_get_expr(d.adbin, d.adrelid) "
                "FROM pg_attribute a LEFT JOIN pg_attrdef d "
                "  ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
                "WHERE a.attrelid = CAST(:t AS regclass) "
                "AND a.attname = 'organization_id'"), {"t": table}).one()
            assert null_ok, table
            assert "current_setting('app.tenant_id'" in default, default
            fks = conn.execute(text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'"),
                {"t": table}).scalars().all()
            assert fks == ["c"], (table, fks)


def test_504_gives_each_other_row_to_the_only_org(db) -> None:
    o1 = str(uuid.uuid4())
    with _scratch(db) as (conn, notices, _schema):
        _run(conn, _PRE_TENANCY)
        conn.execute(text("INSERT INTO organization (id) VALUES (CAST(:a AS uuid))"),
                     {"a": o1})
        conn.execute(text("INSERT INTO app_user (email, organization_id) "
                          "VALUES ('a@h312b.test', CAST(:o AS uuid))"), {"o": o1})
        _seed_old_rows(conn, ["a@h312b.test", "ghost@h312b.test"])

        _run(conn, _body())

        for table in _TABLES:
            assert _orgs_of(conn, table) == {
                "a@h312b.test": o1, "ghost@h312b.test": o1}, table
        assert _warnings(notices) == []


_PROD_SHAPE = """
CREATE TABLE organization (id UUID PRIMARY KEY);
CREATE TABLE app_user (email TEXT, organization_id UUID NOT NULL);
ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user FORCE ROW LEVEL SECURITY;
CREATE POLICY app_user_tenant_isolation ON app_user
    USING (organization_id = current_setting('app.tenant_id', true)::uuid);
CREATE TABLE calendar_day_state (
    user_id TEXT NOT NULL, day DATE NOT NULL,
    organization_id UUID NOT NULL
        DEFAULT current_setting('app.tenant_id', true)::uuid,
    PRIMARY KEY (user_id, day));
CREATE TABLE calendar_rollover_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id TEXT NOT NULL,
    item_id UUID NOT NULL DEFAULT gen_random_uuid(), title TEXT,
    organization_id UUID NOT NULL
        DEFAULT current_setting('app.tenant_id', true)::uuid);
CREATE TABLE task_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'clickup',
    workspace_id TEXT NOT NULL DEFAULT gen_random_uuid()::text,
    organization_id UUID NOT NULL
        DEFAULT current_setting('app.tenant_id', true)::uuid);
"""


def test_504_on_the_production_shape_changes_nothing(db) -> None:
    """Production: the column is there, NOT NULL, with its named foreign key,
    and RLS is forced. The deploy runs the file as an owner that RLS binds,
    with no tenant. The file changes no row and adds no second foreign key."""
    o1, o2 = str(uuid.uuid4()), str(uuid.uuid4())
    role = f"h312b_owner_{uuid.uuid4().hex[:8]}"
    with _scratch(db) as (conn, notices, schema):
        _run(conn, _PROD_SHAPE)
        conn.execute(text("INSERT INTO organization (id) VALUES "
                          "(CAST(:a AS uuid)), (CAST(:b AS uuid))"), {"a": o1, "b": o2})
        conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
        conn.execute(text(f"GRANT SELECT, REFERENCES ON organization TO {role}"))
        conn.execute(text(f"GRANT SELECT ON app_user TO {role}"))
        for i, table in enumerate(_TABLES):
            conn.execute(text(
                f"ALTER TABLE {table} ADD CONSTRAINT {table}_org_fk FOREIGN KEY "
                "(organization_id) REFERENCES organization (id) ON DELETE CASCADE"))
            conn.execute(text(f"ALTER TABLE {table} OWNER TO {role}"))
            conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            conn.execute(text(
                f"CREATE POLICY {table}_tenant_isolation ON {table} USING "
                "(organization_id = current_setting('app.tenant_id', true)::uuid) "
                "WITH CHECK "
                "(organization_id = current_setting('app.tenant_id', true)::uuid)"))
            for j, org in enumerate((o1, o2)):
                cols, vals = "user_id, organization_id", ":u, CAST(:o AS uuid)"
                params: dict[str, object] = {"u": f"m{i}{j}@h312b.test", "o": org}
                if table == "calendar_day_state":
                    cols, vals = f"{cols}, day", f"{vals}, :d"
                    params["d"] = DAY_A
                conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({vals})"),
                             params)

        def _state() -> dict:
            return {t: (_orgs_of(conn, t), conn.execute(text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'"),
                {"t": t}).scalar()) for t in _TABLES}

        before = _state()
        conn.execute(text(f"SET LOCAL ROLE {role}"))
        _run(conn, _body())
        _run(conn, _body())
        conn.execute(text("RESET ROLE"))

        assert _state() == before
        assert all(fk_count == 1 for _rows, fk_count in before.values())
        assert all(len(rows) == 2 for rows, _fk in before.values())
        assert _warnings(notices) == []
