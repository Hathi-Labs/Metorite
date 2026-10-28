"""R8 — WS-KG KG-3a, the stored search vector of email, on a REAL Postgres.

Migration 314 adds ``email_messages.fts`` and a trigger that fills it. The
search reads ``COALESCE(em.fts, <the expression>)``. Under FORCE RLS no index
serves the app role (``docs/rls_and_indexes.md``), so the stored vector is what
makes the search fast. Each test runs the app code as the non-privileged role
of the phase-4 rehearsal (NOSUPERUSER, NOBYPASSRLS).

* (a) On 20 000 rows with the vector filled, the email source of company
  retrieval answers in under 0.75 s, the median of 5 runs. The plan of the
  statement that the route runs tests the stored column first.
* (b) The trigger fills the vector on an insert and on a change of the subject,
  the body or the sender. An update of another column keeps it.
* (c) The Email app gets the same rows, ranks, snippets and total with the
  vector filled and with it NULL.
* (d) The backfill is idempotent and resumable, and it fills no row of
  another org.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import re
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from psycopg import sql
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_keep_separate import _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

ROWS = 20_000
#: Half of ``company.SOURCE_TIMEOUT_S``, the real budget of one source.
BUDGET_S = 0.75
OWNER = "kg3a-owner@kg3a.test"
RARE = "kingfisher"

#: The trigger's text, read from the migration by CONTENT (R1).
_EXPR = (
    "to_tsvector('english', coalesce(subject,'') || ' ' || coalesce(body_text,'')"
    " || ' ' || coalesce(from_address->>'name','') || ' ' ||"
    " coalesce(from_address->>'email',''))"
)

_WORDS = (
    "order", "printer", "filament", "nozzle", "shipment", "quote", "payment",
    "delivery", "meeting", "schedule", "review", "design", "prototype", "support",
    "warranty", "service", "account", "report", "update", "project", "customer",
    "vendor", "supply", "stock", "price", "discount", "contract", "renewal",
    "training", "install", "calibration", "firmware", "resin", "extruder", "sensor",
    "board", "cable", "spare", "dispatch", "courier", "tracking", "receipt", "credit",
    "balance", "ledger", "audit",
)

_SEARCH: dict[str, Any] = {
    "account_id": None, "folder": "all", "label": None, "labels": None,
    "uncategorized": False, "from_addr": None, "to_addr": None,
    "received_after": None, "received_before": None, "is_read": None,
    "is_starred": None, "has_attachments": None, "sender_category": None,
    "importance": None, "hybrid": False, "light": False, "page": 1,
    "page_size": 25, "with_total": True,
}


def _member(org: str) -> UserContext:
    return UserContext(email=OWNER, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:email"]))


@asynccontextmanager
async def _as_member(p: Any, org: str) -> AsyncIterator[None]:
    """Bind *org* and point the shared engine at the app role."""
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


def _null_count(admin: Engine, org: str) -> int:
    with admin.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM email_messages WHERE fts IS NULL "
            "AND organization_id = CAST(:o AS uuid)"), {"o": org}).scalar_one())


def _clear(admin: Engine, org: str) -> None:
    """NULL the vector of *org*, as rows from before migration 314 hold it."""
    with admin.begin() as c:
        c.execute(text("UPDATE email_messages SET fts = NULL "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """20 000 mails for one mailbox of org A, and 50 of org B, all filled."""
    p = promoted
    vocab = "ARRAY[" + ",".join(f"'{w}'" for w in _WORDS) + "]"
    boxes = {}
    with p.admin_engine.begin() as c:
        for org, n in ((p.org_a, ROWS), (p.org_b, 50)):
            box = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, organization_id) VALUES (:u, 'microsoft', "
                ":m, 'x', CAST(:o AS uuid)) RETURNING id"),
                {"u": OWNER, "m": f"box-{org[:8]}@kg3a.test", "o": org}).scalar_one())
            boxes[org] = box
            # Each body holds about 3 200 characters, the mean on staging. One
            # mail in 100 also holds the rare word.
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, folder, "
                "from_address, to_addresses, subject, body_text, snippet, received_at, "
                "organization_id) "
                "SELECT CAST(:a AS uuid), 'kg3a-' || g, "
                "CASE WHEN g % 10 = 0 THEN 'sent' ELSE 'inbox' END, "
                "jsonb_build_object('name', 'Sender ' || (g % 300), "
                "'email', 's' || (g % 300) || '@vendor.test'), '[]'::jsonb, "
                f"'Subject ' || ({vocab})[1 + (g * 7) % {len(_WORDS)}] || ' ' || g, "
                f"(SELECT string_agg(({vocab})[1 + floor(random() * {len(_WORDS)})::int], ' ') "
                "FROM generate_series(1, 420) i WHERE g > 0) "
                f"|| CASE WHEN g % 100 = 0 THEN ' {RARE}' ELSE '' END, "
                "'snippet ' || g, now() - make_interval(mins => g), CAST(:o AS uuid) "
                "FROM generate_series(1, :n) g"),
                {"a": box, "o": org, "n": n})
    with p.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("ANALYZE email_messages"))
    return {"ns": p, "boxes": boxes}


def test_the_migration_is_found_by_content() -> None:
    from tests.unit._tenant_ladder import ladder

    texts = {f: Path(f).read_text(encoding="utf-8") for f in ladder()}
    found = [f for f, body in texts.items() if "email_messages_fts_fill" in body]
    assert len(found) == 1, found
    body = texts[found[0]]
    # Expand only: a nullable column with no default, and no generated column.
    assert "ADD COLUMN IF NOT EXISTS fts tsvector;" in body
    assert "GENERATED ALWAYS" not in body


# ── (b) the trigger ─────────────────────────────────────────────────────────

def test_the_trigger_fills_on_insert_and_on_a_change_of_its_text(seeded, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    p = seeded["ns"]
    box = seeded["boxes"][p.org_a]

    def stored_matches(c: Any, mid: str) -> bool:
        return bool(c.execute(text(
            f"SELECT fts = {_EXPR} FROM email_messages WHERE id = CAST(:i AS uuid)"),
            {"i": mid}).scalar_one())

    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": p.org_a})
        mid = str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, from_address, "
            "to_addresses, subject, body_text, organization_id) VALUES (CAST(:a AS uuid), "
            "'kg3a-trigger', '{\"name\": \"Asha\", \"email\": \"asha@x.test\"}'::jsonb, "
            "'[]'::jsonb, 'pelican', 'heron', CAST(:o AS uuid)) RETURNING id"),
            {"a": box, "o": p.org_a}).scalar_one())
        assert stored_matches(c, mid)

        # A sentinel shows whether a later update recomputed the vector.
        c.execute(text("UPDATE email_messages SET fts = to_tsvector('english', 'sentinel') "
                       "WHERE id = CAST(:i AS uuid)"), {"i": mid})
        c.execute(text("UPDATE email_messages SET is_read = true, folder = 'archive' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": mid})
        kept = c.execute(text("SELECT fts::text FROM email_messages WHERE id = CAST(:i AS uuid)"),
                         {"i": mid}).scalar_one()
        assert kept == "'sentinel':1", "an update of another column recomputed the vector"

        for change in ("subject = 'cormorant'", "body_text = 'egret'",
                       "from_address = '{\"name\": \"Ravi\", \"email\": \"r@x.test\"}'::jsonb"):
            c.execute(text("UPDATE email_messages SET fts = to_tsvector('english', 'sentinel') "
                           "WHERE id = CAST(:i AS uuid)"), {"i": mid})
            c.execute(text(f"UPDATE email_messages SET {change} WHERE id = CAST(:i AS uuid)"),
                      {"i": mid})
            assert stored_matches(c, mid), f"the trigger missed: {change}"
        c.execute(text("DELETE FROM email_messages WHERE id = CAST(:i AS uuid)"), {"i": mid})


# ── (c) the same answer, filled or NULL ─────────────────────────────────────

async def test_the_email_app_gets_the_same_answer_filled_or_null(seeded, app_engine) -> None:  # noqa: F811
    from gateway.routes.email.transport import search as search_mod

    _assert_non_priv(app_engine)
    p = seeded["ns"]
    queries = [RARE, "nozzle calibration", '"spare cable"', "firmware OR kingfisher", "Sender 17"]

    async def answers() -> list[tuple[Any, list[tuple[str, float, str]]]]:
        out = []
        async with _as_member(p, p.org_a):
            for q in queries:
                got = await search_mod.search_messages(user=_member(p.org_a), q=q, **_SEARCH)
                out.append((got["total"], [(e["id"], round(e["rank"], 6), e["highlight"])
                                           for e in got["emails"]]))
        return out

    filled = await answers()
    assert _null_count(p.admin_engine, p.org_a) == 0
    _clear(p.admin_engine, p.org_a)
    try:
        assert _null_count(p.admin_engine, p.org_a) == ROWS
        unfilled = await answers()
    finally:
        async with _as_member(p, p.org_a):
            await search_mod.backfill_fts(p.org_a, pause_s=0)
    assert filled == unfilled
    # Non-vacuity: each query found mail, and the rare word found its 1 in 100.
    assert all(total and rows for total, rows in filled)
    assert filled[0][0] == ROWS // 100


# ── (d) the backfill ────────────────────────────────────────────────────────

async def test_the_backfill_is_idempotent_and_resumable(seeded, app_engine) -> None:  # noqa: F811
    from gateway.routes.email.transport.search import backfill_fts

    _assert_non_priv(app_engine)
    p = seeded["ns"]
    _clear(p.admin_engine, p.org_a)
    _clear(p.admin_engine, p.org_b)
    try:
        async with _as_member(p, p.org_a):
            # A run that stops after two batches keeps them.
            first = await backfill_fts(p.org_a, batch=1000, pause_s=0, max_batches=2)
            assert first == {"batches": 2, "filled": 2000}
            assert _null_count(p.admin_engine, p.org_a) == ROWS - 2000
            # The next run takes the rest, and a third run writes nothing.
            rest = await backfill_fts(p.org_a, batch=1000, pause_s=0)
            assert rest["filled"] == ROWS - 2000
            assert await backfill_fts(p.org_a, pause_s=0) == {"batches": 0, "filled": 0}
        assert _null_count(p.admin_engine, p.org_a) == 0
        # Bound to org A, the backfill cannot reach a row of org B.
        assert _null_count(p.admin_engine, p.org_b) == 50
        with p.admin_engine.connect() as c:
            same = c.execute(text(
                f"SELECT bool_and(fts = {_EXPR}) FROM email_messages "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a}).scalar_one()
        assert same is True, "the backfill wrote a vector that the trigger would not"
    finally:
        async with _as_member(p, p.org_b):
            await backfill_fts(p.org_b, pause_s=0)


# ── (a) the latency fence ───────────────────────────────────────────────────

async def test_the_plan_tests_the_stored_column_first(seeded, app_engine) -> None:  # noqa: F811
    """The statement that the route runs, EXPLAINed as the app role."""
    from gateway.retrieval.sources import REGISTRY

    _assert_non_priv(app_engine)
    p = seeded["ns"]
    seen: list[tuple[str, Any]] = []

    def grab(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any,
             executemany: bool) -> None:
        if "FROM email_messages em" in statement and "LIMIT" in statement:
            seen.append((statement, parameters))

    source = REGISTRY["email"]
    event.listen(Engine, "before_cursor_execute", grab)
    try:
        async with _as_member(p, p.org_a):
            hits = await source.find(_member(p.org_a), RARE, 5)
    finally:
        event.remove(Engine, "before_cursor_execute", grab)
    assert len(hits) == 5
    assert len(seen) == 1, "the email source ran no search, or ran it twice"
    statement, parameters = seen[0]
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": p.org_a})
        raw = c.connection.dbapi_connection
        # The driver sent `$n` with a tuple. Put each value back as a literal,
        # the highest number first, so `$1` never eats the head of `$10`.
        for n in range(len(parameters), 0, -1):
            statement = statement.replace(
                f"${n}", sql.Literal(parameters[n - 1]).as_string(raw))
        plan = "\n".join(r[0] for r in c.exec_driver_sql(
            "EXPLAIN (ANALYZE, COSTS OFF) " + statement).fetchall())
    filters = [line for line in plan.splitlines() if "Filter:" in line and "@@" in line]
    assert filters, plan
    assert all(re.search(r"COALESCE\((em\.)?fts,", line) for line in filters), plan


async def test_the_email_source_answers_inside_its_budget(seeded, app_engine) -> None:  # noqa: F811
    from gateway.retrieval.sources import REGISTRY

    _assert_non_priv(app_engine)
    p = seeded["ns"]
    assert _null_count(p.admin_engine, p.org_a) == 0
    source = REGISTRY["email"]
    async with _as_member(p, p.org_a):
        for q in (RARE, "nozzle calibration"):
            assert len(await source.find(_member(p.org_a), q, 5)) == 5  # warm
            times = []
            for _ in range(5):
                start = time.perf_counter()
                hits = await source.find(_member(p.org_a), q, 5)
                times.append(time.perf_counter() - start)
                assert len(hits) == 5
            median = statistics.median(times)
            assert median < BUDGET_S, f"{q!r}: median {median:.3f}s of {ROWS} rows"
