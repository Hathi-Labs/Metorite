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

from tests.unit._customer_console_ladder import SECOND_PLAN, apply_ladder  # noqa: E402

from customer_console import store  # noqa: E402
from customer_console.credits import (  # noqa: E402
    TokenUsage,
    UnpricedModel,
    balance_of,
    rate_call,
)
from customer_console.keys import mint_key, verify_secret  # noqa: E402
from customer_console.router import resolve_rate_card
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

    def test_the_catalog_sells_exactly_one_thing_at_500(self, conn):
        """D49: one flat seat, ₹500/user/month, and nothing else purchasable.

        The pricing ladder of record moved. D23/D24's 600 · 1,200 · 1,800 ·
        2,400 · 3,000 is retired (`launch_surface.md` §4), and this is its
        replacement fence — deliberately an EXACT-set assertion rather than a
        spot-check, because the failure mode changed shape. Under a ladder, the
        risk was a wrong number on one rung; under a flat plan, it is a **second
        purchasable row**, which no per-slug spot-check would notice.

        The prices of retired rows are not asserted. They are frozen history now
        (008 deactivates, never deletes — those rows are the audit trail behind
        invoices already issued), and pinning a price nobody can be charged
        would be a fence guarding nothing.
        """
        # `SECOND_PLAN` is excluded by name, not swept under a filter: it is a
        # row the TEST HARNESS seeds (`_customer_console_ladder.ensure_second_plan`)
        # so suites that need two plans do not have to borrow a product, and it
        # is committed to the same database this fence reads. Naming it here is
        # the honest form — the claim is "the LADDER seeds exactly one sellable
        # plan", and a reader can see the one thing that is not the ladder's.
        active = dict(
            conn.execute(
                text("SELECT slug, price_inr FROM plan_catalog "
                     "WHERE active AND slug <> :qa"),
                {"qa": SECOND_PLAN},
            ).all()
        )
        assert active == {"core": Decimal("500.00")}, active

        # `core` is REPRICED, not replaced — membership IS the Core seat (D19.3)
        # and `seats.CORE_PLAN_SLUG` is the one slug the sign-in path allocates.
        # A `flat`/`standard` row appearing beside it would strand every
        # `seat_assignment` already written and raise "which seat does this
        # member really hold" (D49's rejected alternative (d)).
        assert conn.execute(
            text("SELECT kind FROM plan_catalog WHERE slug = 'core'")
        ).scalar_one() == "core"

    def test_the_retired_package_ladder_cannot_be_sold(self, conn):
        """Every Center package, add-on and bundle is inactive (D49).

        `store.priced_plan` filters on `active`, so this is what actually stops
        the checkout selling a retired object — not the absence of a UI for it.
        Asserted over the `kind` discriminator rather than a slug list for
        008's reason: a slug list silently misses a package seeded by a ladder
        file added later.

        This subsumes the old `test_centers_not_yet_registered_are_seeded_INACTIVE`
        (R&D and Support, seeded inactive because their Centers did not exist).
        That claim is now the weaker half of this one — EVERY Center package is
        inactive, registered or not — so it is folded in here rather than left
        as a second test asserting a subset of the same fact.
        """
        sellable_but_retired = [
            r[0] for r in conn.execute(
                text("SELECT slug FROM plan_catalog "
                     "WHERE active AND kind IN ('center', 'addon', 'bundle') "
                     "AND slug <> :qa ORDER BY slug"),
                {"qa": SECOND_PLAN},
            )
        ]
        assert sellable_but_retired == [], sellable_but_retired

        # ...and the rows are still THERE. 008 deactivates; it must never delete.
        present = {
            r[0] for r in conn.execute(text("SELECT slug FROM plan_catalog"))
        }
        assert {"sales", "all_centers", "complete", "rnd", "support"} <= present

    def test_the_rate_card_ships_unpriced(self, conn):
        # Deliberate: CP-6 sets prices against measured burn, and rate_call()
        # raises UnpricedModel rather than billing a guess as free.
        #
        # WHOLE TABLE, on purpose — every row the ladder applies, not just the
        # seed's effective_from. Two CP-6 money invariants are inert *only*
        # while every shipped card rates at zero, so this is their tripwire and
        # narrowing it to the seed date would let a later migration price a card
        # green. It coexists with the fixture-priced rows because nothing here
        # commits (the `conn` fixture rolls every test back) and the Router
        # suite's `priced_card` deletes its row in teardown.
        #
        # The predicate mirrors `credits.RateCard.is_priced` — cached input
        # included — so "priced" means the same thing to the fence as to the
        # code that bills.
        priced = conn.execute(
            text("SELECT count(*) FROM model_rate_card "
                 "WHERE input_credits_per_1k <> 0 "
                 "   OR output_credits_per_1k <> 0 "
                 "   OR cached_input_credits_per_1k <> 0")
        ).scalar_one()

        # ASCII on purpose: this string is read from a CI log and from a
        # Windows console, where cp1252 turns an em dash into mojibake
        # (CLAUDE.md section 6).
        assert priced == 0, (
            f"{priced} rate-card row(s) in the applied ladder carry a non-zero "
            "price. A non-zero rate card may not ship in the ladder until BOTH "
            "of these are closed: (1) BYOK zero-rating, customer_console.md "
            "sec 6 CP-6 deferred limit (a) - an org serving calls with its own "
            "provider_credential would be charged platform credits for tokens "
            "it has already paid the provider for (sec 3.4 / sec 4.4); and "
            "(2) the balance gate's pre-flight cost invariant (review P1 on "
            "WS-31 CP-6) - the pre-flight spends one CREDIT_QUANTUM rather "
            "than an estimate, so one large priced call can vault the "
            "overdraft floor in a single step. Both are inert ONLY while every "
            "card in the shipped ladder rates at zero, which is what this "
            "counts. Price via an owner act on a live system, never a seeded "
            "migration - customer_console.md sec 8 gate 4, D19.2."
        )


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
        # The upsell echoes the CATALOG's price, so the customer is quoted what
        # the next seat actually costs. Asserted against the price this test
        # just read rather than a transcribed number: the literal used to be
        # "600.00" and D49 repriced the seat to 500, which is exactly the kind
        # of edit a transcription turns into an unrelated red test.
        assert decision.buy_more["price_inr"] == str(price)
        # ...and the anchor for what that price IS today (D49, migration 008).
        assert price == Decimal("500.00")


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


# ── CP-6: the rate card in force, and the per-run breaker's input ───────────

class TestTheRateCardInForce:
    """The read the Router does at metering (``router.resolve_rate_card``).

    Same shape as ``resolve_tier`` — newest row whose ``effective_from`` has
    passed — so a re-price is an INSERT with a later date and a past invoice is
    never recomputed against today's card. Only a real server can answer the
    ordering and the ``<= now()`` predicate honestly.
    """

    def test_the_seeded_card_resolves_but_is_unpriced(self, conn):
        card = resolve_rate_card(conn, "deepseek/deepseek-v4-pro")

        assert card.is_priced is False
        # Not "bills zero" — raises. A model the card does not price is an
        # operational mistake, and billing it confidently as free looks like
        # revenue working while the margin leaks.
        with pytest.raises(UnpricedModel):
            rate_call(card, TokenUsage(prompt_tokens=1_000_000))

    def test_the_newest_card_in_effect_wins(self, conn):
        # ⚠️ `pricing_mode` is REQUIRED as of CP-10 slice 2 (D61, G-4).
        # Numbers alone no longer make a card billable: a zero cannot mean
        # "not yet priced", "absorbed into the seat price" (D19.2) and
        # "deliberately free" at the same time. A card carrying real rates
        # that nobody marked `priced` fails CLOSED, which is the safe way
        # for a draft price to fail.
        conn.execute(text(
            "INSERT INTO model_rate_card (model, input_credits_per_1k, "
            " output_credits_per_1k, cached_input_credits_per_1k, "
            " pricing_mode, effective_from) "
            "VALUES ('deepseek/deepseek-v4-pro', 2, 6, 0.5, 'priced', now())"))

        card = resolve_rate_card(conn, "deepseek/deepseek-v4-pro")

        assert card.is_priced is True
        # 1000 fresh input @2 + 500 output @6 = 5 credits.
        assert rate_call(
            card, TokenUsage(prompt_tokens=1000, completion_tokens=500)
        ) == Decimal("5.0")

    def test_a_future_dated_card_is_staged_not_live(self, conn):
        # A re-price can be staged for the first of the month without taking
        # effect the moment it is inserted.
        conn.execute(text(
            "INSERT INTO model_rate_card (model, input_credits_per_1k, "
            " output_credits_per_1k, effective_from) "
            "VALUES ('deepseek/deepseek-v4-pro', 99, 99, "
            "        now() + interval '7 days')"))

        assert resolve_rate_card(
            conn, "deepseek/deepseek-v4-pro").is_priced is False

    def test_a_model_with_no_card_row_raises_rather_than_billing_free(self, conn):
        with pytest.raises(UnpricedModel):
            resolve_rate_card(conn, "someprovider/never-priced")


class TestRunSpend:
    """``store.run_spend`` — what the per-run circuit breaker reads."""

    def test_a_run_with_no_usage_sums_to_zero_not_none(self, conn, org):
        # The empty-aggregate trap: SUM() over no rows is NULL, and a fake is
        # perfectly happy to hand back 0 instead. Without COALESCE the breaker
        # would raise on the FIRST call of every run.
        assert store.run_spend(conn, org_id=org, run_id="run-never-used") \
            == Decimal(0)

    def test_it_sums_only_that_run(self, conn, org):
        for run, cost in (("run-a", "3"), ("run-a", "4"), ("run-b", "10")):
            store.record_usage(
                conn, org_id=org, request_id=f"req-{uuid.uuid4().hex}",
                billed_credits=Decimal(cost), run_id=run, model="m")

        assert store.run_spend(conn, org_id=org, run_id="run-a") == Decimal("7")
        assert store.run_spend(conn, org_id=org, run_id="run-b") == Decimal("10")

    def test_it_never_crosses_organizations(self, conn, org):
        # Two customers whose agents happen to use the same run id must not
        # break each other's loop — the same class of defect migration 003 had
        # to fix for request_id.
        other = store.ensure_organization(
            conn, slug=f"other-{uuid.uuid4().hex[:8]}", name="Other")
        store.record_usage(
            conn, org_id=other, request_id=f"req-{uuid.uuid4().hex}",
            billed_credits=Decimal("500"), run_id="run-shared", model="m")

        assert store.run_spend(conn, org_id=org, run_id="run-shared") \
            == Decimal(0)
        assert store.run_spend(conn, org_id=other, run_id="run-shared") \
            == Decimal("500")


class TestBalanceIsTheLedgerSumAfterAMeteredCall:
    """CP-6 acceptance clause 1, the arithmetic half: *"balance equals
    SUM(credit_ledger.delta) in a fixture"*.

    The structural half — that no code path UPDATEs a balance column — is
    ``test_customer_console_credits.py::TestNoCodePathUpdatesABalanceColumn``,
    which needs no database and therefore cannot skip.
    """

    def test_the_draw_lands_and_the_balance_is_the_sum(self, conn, org):
        store.add_credit(conn, org_id=org, delta=Decimal("1000"),
                         reason="purchase")
        store.record_usage(
            conn, org_id=org, request_id=f"req-{uuid.uuid4().hex}",
            billed_credits=Decimal("1.29"), model="m")

        # Computed by the application...
        assert balance_of(store.credit_deltas(conn, org_id=org)) \
            == Decimal("998.71")
        # ...and by the database, independently. There is no third answer
        # stored anywhere, which is the property being pinned.
        assert conn.execute(
            text("SELECT SUM(delta) FROM credit_ledger "
                 "WHERE organization_id = :o"), {"o": org}
        ).scalar_one() == Decimal("998.71")


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
