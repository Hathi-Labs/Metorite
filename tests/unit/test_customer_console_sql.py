"""Customer Console SQL, against a REAL Postgres. R8 binds this file.

Spec: ``project-docs/specs/customer_console.md`` CP-1/CP-2 ·
``work_plan.md`` §1 R8.

Why real rather than hermetic: *"hermetic fakes agree with whatever SQL they are
handed, which is how five live bugs shipped green."* Every assertion here is
about something only a real database can answer — a partial unique index, an
``ON CONFLICT`` inference, a CITEXT collation, a ``COALESCE`` over an empty
aggregate. A fake would pass all of them while the SQL was wrong.

Run:
    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1/cc_platform
    uv run pytest tests/unit/test_customer_console_sql.py

Skips (loudly) when that variable is unset, so the suite stays runnable on a
laptop without Postgres — but a skipped R8 test proves nothing, and CI must set
it. The skip message says so.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

from tests.unit._customer_console_ladder import apply_ladder  # noqa: E402

from customer_console import store  # noqa: E402
from customer_console.credits import balance_of  # noqa: E402
from customer_console.keys import mint_key, verify_secret  # noqa: E402
from customer_console.seats import decide_assignment, seat_counts  # noqa: E402

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres for "
        "this suite. A skip here is not a pass; CI must set it."
    ),
)



@pytest.fixture(scope="module")
def engine():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    return eng


@pytest.fixture
def conn(engine):
    """A transaction per test, always rolled back — tests never share state."""
    with engine.connect() as connection:
        tx = connection.begin()
        try:
            yield connection
        finally:
            tx.rollback()


@pytest.fixture
def org(conn) -> str:
    return store.ensure_organization(
        conn, slug=f"acme-{uuid.uuid4().hex[:8]}", name="Acme Pumps Pvt Ltd",
        gstin="29ABCDE1234F1Z5", billing_state="KA",
    )


# ── The migrations themselves ───────────────────────────────────────────────

class TestSchema:
    def test_the_ladder_replays_cleanly(self, engine):
        # The fixture already applied it once; doing it again proves idempotence
        # against a real server rather than against our reading of the DDL.
        with engine.begin() as c:
            apply_ladder(c)

    def test_the_catalog_matches_the_pricing_ladder_of_record(self, conn):
        # D23/D24: 600 · 1,200 · 1,800 · 2,400 · 3,000. Spot-check the anchors
        # that customers actually see, so a stray edit to the seed is loud.
        prices = dict(
            conn.execute(text("SELECT slug, price_inr FROM plan_catalog")).all()
        )
        assert prices["core"] == Decimal("600.00")
        assert prices["sales"] == Decimal("600.00")       # app-bearing Center
        assert prices["operations"] == Decimal("300.00")  # slices-only Center
        assert prices["all_centers"] == Decimal("1800.00")
        assert prices["complete"] == Decimal("3000.00")
        assert prices["company"] == Decimal("0.00")       # leadership rollup

    def test_centers_not_yet_registered_are_seeded_INACTIVE(self, conn):
        # D22 put R&D and Support on the roster; lib/centers.ts still lists six.
        # The catalog may run ahead of the product, but nothing may SELL a
        # package whose Center does not exist.
        inactive = {
            r[0] for r in conn.execute(
                text("SELECT slug FROM plan_catalog WHERE NOT active")
            )
        }
        assert {"rnd", "support"} <= inactive

    def test_the_rate_card_ships_unpriced(self, conn):
        # Deliberate: CP-6 sets prices against measured burn, and rate_call()
        # raises UnpricedModel rather than billing a guess as free.
        assert conn.execute(
            text("SELECT count(*) FROM model_rate_card "
                 "WHERE input_credits_per_1k <> 0 OR output_credits_per_1k <> 0")
        ).scalar_one() == 0


# ── Registry ────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_creating_an_org_twice_yields_one_org(self, conn):
        slug = f"dup-{uuid.uuid4().hex[:8]}"
        first = store.ensure_organization(conn, slug=slug, name="X")
        second = store.ensure_organization(conn, slug=slug, name="X again")

        # Provisioning WILL be retried; it must not produce two companies.
        assert first == second

    def test_email_identity_is_case_insensitive(self, conn):
        # CITEXT. Without it "Ada@Corp.com" and "ada@corp.com" become two humans
        # holding two seats — which is the tenant plane's migration-162 bug.
        email = f"Ada.{uuid.uuid4().hex[:6]}@Corp.com"
        a = store.ensure_identity(conn, email=email)
        b = store.ensure_identity(conn, email=email.lower())

        assert a == b


# ── Seats ───────────────────────────────────────────────────────────────────

class TestSeats:
    def test_counts_come_out_of_sql_matching_the_pure_math(self, conn, org):
        store.grant_seats(conn, org_id=org, plan_slug="core", quantity=5)
        store.grant_seats(conn, org_id=org, plan_slug="core", quantity=-2)
        ident = store.ensure_identity(conn, email=f"a{uuid.uuid4().hex[:6]}@x.com")
        store.try_assign_seat(conn, org_id=org, plan_slug="core",
                              identity_id=ident, source="core")

        grants, assigned = store.seat_rows(conn, org_id=org, plan_slug="core")
        counts = seat_counts("core", grants, assigned)

        assert (counts.purchased, counts.assigned, counts.available) == (3, 1, 2)

    def test_an_org_with_no_rows_counts_zero_rather_than_erroring(self, conn, org):
        # COALESCE/empty-aggregate behaviour — a fake would happily return None
        # here and the caller would crash on the first real empty org.
        grants, assigned = store.seat_rows(conn, org_id=org, plan_slug="core")
        assert seat_counts("core", grants, assigned).purchased == 0

    def test_a_future_dated_grant_is_not_counted_yet(self, conn, org):
        store.grant_seats(conn, org_id=org, plan_slug="core", quantity=10)
        conn.execute(text(
            "UPDATE seat_grant SET effective_from = now() + interval '30 days' "
            "WHERE organization_id = :o"), {"o": org})

        grants, _ = store.seat_rows(conn, org_id=org, plan_slug="core")
        assert grants == []

    def test_double_assignment_is_impossible_not_merely_unlikely(self, conn, org):
        store.grant_seats(conn, org_id=org, plan_slug="core", quantity=5)
        ident = store.ensure_identity(conn, email=f"b{uuid.uuid4().hex[:6]}@x.com")

        assert store.try_assign_seat(conn, org_id=org, plan_slug="core",
                                     identity_id=ident) is True
        # The partial unique index rejects the second; DO NOTHING turns that
        # into False rather than an integrity error surfacing as a 500.
        assert store.try_assign_seat(conn, org_id=org, plan_slug="core",
                                     identity_id=ident) is False

        _, assigned = store.seat_rows(conn, org_id=org, plan_slug="core")
        assert assigned == 1

    def test_a_released_seat_frees_capacity_and_can_be_retaken(self, conn, org):
        store.grant_seats(conn, org_id=org, plan_slug="core", quantity=1)
        ident = store.ensure_identity(conn, email=f"c{uuid.uuid4().hex[:6]}@x.com")
        store.try_assign_seat(conn, org_id=org, plan_slug="core", identity_id=ident)

        assert store.release_seat(conn, org_id=org, plan_slug="core",
                                  identity_id=ident) is True
        _, assigned = store.seat_rows(conn, org_id=org, plan_slug="core")
        assert assigned == 0

        # The partial index is what allows this: a leaver who rejoins must be
        # able to get a seat again.
        assert store.try_assign_seat(conn, org_id=org, plan_slug="core",
                                     identity_id=ident) is True

    def test_releasing_a_seat_nobody_holds_is_false_not_an_error(self, conn, org):
        ident = store.ensure_identity(conn, email=f"d{uuid.uuid4().hex[:6]}@x.com")
        assert store.release_seat(conn, org_id=org, plan_slug="core",
                                  identity_id=ident) is False

    def test_the_cap_refuses_the_seat_that_would_exceed_it(self, conn, org):
        store.grant_seats(conn, org_id=org, plan_slug="core", quantity=1)
        first = store.ensure_identity(conn, email=f"e{uuid.uuid4().hex[:6]}@x.com")
        store.try_assign_seat(conn, org_id=org, plan_slug="core", identity_id=first)

        grants, assigned = store.seat_rows(conn, org_id=org, plan_slug="core")
        price = store.plan_price(conn, plan_slug="core")
        decision = decide_assignment(
            seat_counts("core", grants, assigned), price_inr=price
        )

        assert decision.allowed is False
        assert decision.status == 409
        assert decision.buy_more["price_inr"] == "600.00"


# ── Credits and usage ───────────────────────────────────────────────────────

class TestCreditsAndUsage:
    def test_balance_is_the_ledger_sum(self, conn, org):
        store.add_credit(conn, org_id=org, delta=Decimal("1000"), reason="purchase")
        store.add_credit(conn, org_id=org, delta=Decimal("-40.5"), reason="usage")

        assert balance_of(store.credit_deltas(conn, org_id=org)) == Decimal("959.5")

    def test_an_org_with_no_ledger_has_a_zero_balance(self, conn, org):
        assert balance_of(store.credit_deltas(conn, org_id=org)) == Decimal(0)

    def test_a_retried_request_bills_exactly_once(self, conn, org):
        rid = f"req-{uuid.uuid4().hex}"
        store.add_credit(conn, org_id=org, delta=Decimal("100"), reason="purchase")

        assert store.record_usage(conn, org_id=org, request_id=rid,
                                  billed_credits=Decimal("5"), model="m") is True
        # The retry. Stream reconnects and the usage-rebuild path make this
        # ordinary, not exotic.
        assert store.record_usage(conn, org_id=org, request_id=rid,
                                  billed_credits=Decimal("5"), model="m") is False

        assert conn.execute(
            text("SELECT count(*) FROM usage_event WHERE request_id = :r"),
            {"r": rid},
        ).scalar_one() == 1
        # And — the half that actually costs money — the ledger moved once.
        assert balance_of(store.credit_deltas(conn, org_id=org)) == Decimal("95")

    def test_a_zero_cost_call_records_usage_without_a_ledger_row(self, conn, org):
        rid = f"req-{uuid.uuid4().hex}"
        assert store.record_usage(conn, org_id=org, request_id=rid,
                                  billed_credits=Decimal("0"), model="m") is True

        assert store.credit_deltas(conn, org_id=org) == []


# ── Keys ────────────────────────────────────────────────────────────────────

class TestKeys:
    def _store_key(self, conn, org: str):
        k = mint_key()
        conn.execute(
            text("INSERT INTO llm_api_key (organization_id, prefix, key_hash) "
                 "VALUES (:o, :p, :h)"),
            {"o": org, "p": k.prefix, "h": k.key_hash},
        )
        return k

    def test_a_key_resolves_its_organization(self, conn, org):
        k = self._store_key(conn, org)

        resolved = store.resolve_key(conn, prefix=k.prefix)

        assert resolved is not None
        # Three-tuple since CP-3: the owning org's lifecycle status rides along
        # so a caller can refuse a cancelled customer without a second query.
        org_id, key_hash, org_status = resolved
        assert org_id == org
        assert org_status == "trial"
        assert verify_secret(k.token.split("_", 3)[3], key_hash) is True

    def test_a_revoked_key_resolves_to_nothing(self, conn, org):
        k = self._store_key(conn, org)
        conn.execute(
            text("UPDATE llm_api_key SET revoked_at = now() WHERE prefix = :p"),
            {"p": k.prefix},
        )

        assert store.resolve_key(conn, prefix=k.prefix) is None

    def test_an_unknown_prefix_resolves_to_nothing(self, conn):
        assert store.resolve_key(conn, prefix="cc_live_deadbeef") is None
