"""Migration 209 — one address may work for two customers (H-125).

Migration 148 made `lower(email)` unique across the WHOLE deployment. The
directory's whole reason for existing, per `people_center_app.md` §2, is the
person who works for you without being a member — and a contractor working
for two customers could only ever exist in the first one.

**And it failed silently.** `people_row_from_member` writes
`ON CONFLICT DO NOTHING`, so customer B's invite skipped the row and said
nothing. Row level security then hid A's row, so B's administrator saw a
member with no directory row, no error, and no way to find out why.

**Two organizations, on a real Postgres, or this proves nothing.** Every
assertion here needs a second tenant holding the same address. A
single-tenant fixture passes against the broken index — which is exactly how
this shipped.

⚠️ A sync connection behind a one-method async adapter, not an async engine:
psycopg refuses Windows' default `ProactorEventLoop` and this repo's primary
dev box is Windows (CLAUDE.md §6). An R8 suite that only runs for some of us
is not an R8 suite.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

SHARED = "contractor@twofirms.example"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


class _AsAsync:
    """A sync connection wearing the one async method the helpers call."""

    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql, params=None):
        return self._conn.execute(sql, params or {})


@contextmanager
def _bound(engine, org):
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :o, false)"),
                     {"o": str(org) if org else ""})
        yield conn, _AsAsync(conn)


def _org(conn, tag: str) -> uuid.UUID:
    oid = uuid.uuid4()
    conn.execute(text(
        "INSERT INTO organization (id, slug, display_name) "
        "VALUES (:id, :s, :n)"),
        {"id": oid, "s": f"{tag}-{oid.hex[:8]}", "n": tag})
    return oid


@pytest.fixture
def two_firms(eng):
    with eng.begin() as conn:
        a = _org(conn, "firm-a")
        b = _org(conn, "firm-b")
    yield a, b
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM people WHERE email = :e"),
                     {"e": SHARED})
        conn.execute(text("DELETE FROM app_user WHERE email = :e"),
                     {"e": SHARED})
        for org in (a, b):
            conn.execute(text("DELETE FROM organization WHERE id = :id"),
                         {"id": org})


# ══════════════════════════════════════════════════════════════════════════
# The bug
# ══════════════════════════════════════════════════════════════════════════

def test_two_customers_may_each_hold_the_same_contractor(eng, two_firms) -> None:
    """The defect, stated as the behaviour it broke.

    Against migration 148's global index the second INSERT raises a unique
    violation. Against 209's per-tenant one both rows exist, each in its
    own organization.
    """
    a, b = two_firms
    with eng.begin() as conn:
        for org, name in ((a, "Their name for her"), (b, "Our name for her")):
            conn.execute(text(
                "INSERT INTO people (id, name, email, status, skills, "
                "source, source_key, organization_id, updated_by, updated_at) "
                "VALUES (gen_random_uuid(), :n, :e, 'contractor', "
                "ARRAY[]::text[], 'manual', :k, :o, 'test', now())"),
                {"n": name, "e": SHARED, "k": f"manual:{org}", "o": org})
        rows = conn.execute(text(
            "SELECT organization_id FROM people WHERE email = :e"),
            {"e": SHARED}).scalars().all()
    assert sorted(map(str, rows)) == sorted([str(a), str(b)])


def test_the_same_address_is_still_unique_WITHIN_one_customer(eng, two_firms) -> None:
    """Per-tenant, not abandoned. Two rows for one address in ONE
    organization is the ambiguity migration 148 existed to remove, and
    `is_self` still rests on it."""
    from sqlalchemy.exc import IntegrityError

    a, _b = two_firms
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at) "
            "VALUES (gen_random_uuid(), 'First', :e, 'active', "
            "ARRAY[]::text[], 'manual', 'k1', :o, 'test', now())"),
            {"e": SHARED, "o": a})
    with pytest.raises(IntegrityError), eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO people (id, name, email, status, skills, "
            "source, source_key, organization_id, updated_by, updated_at) "
            "VALUES (gen_random_uuid(), 'Second', :e, 'active', "
            "ARRAY[]::text[], 'manual', 'k2', :o, 'test', now())"),
            {"e": SHARED, "o": a})


def test_the_member_trigger_writes_a_row_in_BOTH_organizations(eng, two_firms) -> None:
    """The path the defect actually travelled.

    An invite at B used to hit A's row on the global index, `DO NOTHING`
    swallowed it, and B's member had no profile. Both invites must now
    produce a row.
    """
    a, b = two_firms
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at) "
            "VALUES (gen_random_uuid(), 'At A', :e, 'contractor', "
            "ARRAY[]::text[], 'manual', 'ka', :o, 'test', now())"),
            {"e": SHARED, "o": a})
        # B invites the same human.
        conn.execute(text(
            "INSERT INTO app_user (email, display_name, organization_id, status) "
            "VALUES (:e, 'At B', :o, 'active')"), {"e": SHARED, "o": b})
        at_b = conn.execute(text(
            "SELECT count(*) FROM people "
            " WHERE email = :e AND organization_id = :o"),
            {"e": SHARED, "o": b}).scalar()
    assert at_b == 1, "the invite at B produced no directory row"


# ══════════════════════════════════════════════════════════════════════════
# The consequence — the self predicate can no longer lean on the index
# ══════════════════════════════════════════════════════════════════════════

def test_find_self_row_cannot_return_ANOTHER_customers_row(eng, two_firms) -> None:
    """🔴 The security consequence of widening the index, closed in the same
    change.

    `find_self_row` decides `is_self`, which authorises `PATCH /people/me`.
    Its guarantee used to be migration 148's GLOBAL index: one row per
    address anywhere, so `LIMIT 1` could only find the right one. Once two
    rows may share an address, an unscoped query can hand somebody another
    customer's row to edit.
    """
    from gateway.routes.people.core import find_self_row

    a, b = two_firms
    with eng.begin() as conn:
        for org, name in ((a, "Row at A"), (b, "Row at B")):
            conn.execute(text(
                "INSERT INTO people (id, name, email, status, skills, "
                "source, source_key, organization_id, updated_by, updated_at) "
                "VALUES (gen_random_uuid(), :n, :e, 'active', "
                "ARRAY[]::text[], 'manual', :k, :o, 'test', now())"),
                {"n": name, "e": SHARED, "k": f"m:{org}", "o": org})

    user = type("U", (), {"email": SHARED})()
    with _bound(eng, a) as (_c, db):
        assert run(find_self_row(db, user)).name == "Row at A"
    with _bound(eng, b) as (_c, db):
        assert run(find_self_row(db, user)).name == "Row at B"
    with _bound(eng, None) as (_c, db):
        assert run(find_self_row(db, user)) is None, "unbound must match nothing"


def test_the_duplicate_check_names_nobody_at_another_customer(eng, two_firms) -> None:
    """A 409 naming somebody at another customer is a refusal the admin
    cannot act on, and a disclosure of a name they should never see."""
    from gateway.routes.tasks.people import _email_taken_by

    a, b = two_firms
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at) "
            "VALUES (gen_random_uuid(), 'Held at A', :e, 'active', "
            "ARRAY[]::text[], 'manual', 'ka', :o, 'test', now())"),
            {"e": SHARED, "o": a})

    with _bound(eng, a) as (_c, db):
        assert run(_email_taken_by(db, SHARED)) == "Held at A"
    with _bound(eng, b) as (_c, db):
        assert run(_email_taken_by(db, SHARED)) is None


# ══════════════════════════════════════════════════════════════════════════
# The migration itself
# ══════════════════════════════════════════════════════════════════════════

def test_the_old_global_index_is_gone(eng) -> None:
    """Replaced, not added beside. Two unique indexes mean the stricter one
    decides, so leaving 148's in place would refuse the second customer
    exactly as before while the new one sat next to it looking like a fix."""
    with eng.begin() as conn:
        names = conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'people'"),
        ).scalars().all()
    assert "uq_gtd_people_org_email_lower" in names
    assert "uq_gtd_people_email_lower" not in names


def test_no_migration_pins_a_conflict_target_on_this_table(eng) -> None:
    """The replay hazard, fenced.

    206 named `ON CONFLICT (lower(email))`, which pins the statement to an
    index BY NAME — so 209 replacing that index made the trigger raise
    42P10 at plan time, which is how migration 162 took down every invite
    for `app_user`. A bare `DO NOTHING` survives the index changing.
    """
    import pathlib
    import re

    mig = pathlib.Path(__file__).resolve().parents[2] / "infra" / "postgres"
    offenders = []
    for f in sorted(mig.glob("[0-9]*_*.sql")):
        text_ = f.read_text(encoding="utf-8")
        if re.search(r"INSERT INTO people[\s\S]{0,900}?ON CONFLICT\s*\(", text_):
            offenders.append(f.name)
    assert not offenders, (
        "These pin a conflict target on `people`, so changing its unique "
        f"index breaks them at plan time: {sorted(set(offenders))}"
    )
