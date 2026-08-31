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
from sqlalchemy.exc import IntegrityError

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
        # ⚠️ WIDER THAN `credits.RateCard.is_priced`, deliberately, and it
        # stopped being a mirror in CP-10 slice 2. `is_priced` now reads
        # `pricing_mode` alone (D61 G-4), so a fence that mirrored it would
        # ignore the NUMBERS — and a ladder shipping real rates under
        # `unpriced` would pass while carrying a price somebody meant.
        #
        # So this asserts BOTH halves: no non-zero rate in ANY column, and
        # no row declaring itself billable. `credits_per_unit` is in the
        # list because CP-10 slice 2 added it, and a per-MINUTE price is as
        # real as a per-1k one — omitting it would leave `transcribe`,
        # `speak` and `image` able to ship priced with nothing watching.
        priced = conn.execute(
            text("SELECT count(*) FROM model_rate_card "
                 "WHERE input_credits_per_1k <> 0 "
                 "   OR output_credits_per_1k <> 0 "
                 "   OR cached_input_credits_per_1k <> 0 "
                 "   OR credits_per_unit <> 0 "
                 "   OR pricing_mode <> 'unpriced'")
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

# ── CP-7 slice 1: the spend reads (D66) ─────────────────────────────────────
#
# R8 binds every test here. Each one asks something only a real server answers:
# a CITEXT grouping, a COALESCE fallback chain over NULLs, an interval window,
# and a NULL-safe optional filter. A hermetic fake would pass all four while
# the SQL was wrong — which is the exact history R8 was minted from.

class TestSpendReads:
    def _usage(self, conn, org, **fields):
        assert store.record_usage(
            conn, org_id=org, request_id=f"req-{uuid.uuid4().hex}",
            billed_credits=fields.pop("credits", Decimal("1")), **fields,
        ) is True

    def test_activity_prefers_the_agent_then_the_module(self, conn, org):
        self._usage(conn, org, agent="email-assistant", module_slug="email")
        self._usage(conn, org, agent=None, module_slug="crm")
        self._usage(conn, org, agent=None, module_slug=None)

        rows = {r["activity"]: r for r in
                store.usage_by_activity(conn, org_id=org)}

        # The agent WINS over the module when both are present — a row is not
        # counted twice and it is not filed under the coarser of the two.
        assert set(rows) == {"email-assistant", "crm",
                             store.UNATTRIBUTED_ACTIVITY}
        assert rows["email-assistant"]["calls"] == 1

    def test_an_unattributed_row_is_named_not_dropped(self, conn, org):
        self._usage(conn, org, credits=Decimal("7"))

        rows = store.usage_by_activity(conn, org_id=org)

        # Dropping it would make the parts silently fail to sum to the whole,
        # and the customer would have no way to see that they did not.
        assert [r["activity"] for r in rows] == [store.UNATTRIBUTED_ACTIVITY]
        assert rows[0]["credits"] == Decimal("7")

    def test_the_member_filter_is_null_safe(self, conn, org):
        """The `(:member IS NULL OR ...)` arm — the shape that silently returns
        nothing when a driver binds an absent parameter as SQL NULL."""
        self._usage(conn, org, user_email="alice@corp.com", agent="a")
        self._usage(conn, org, user_email="bob@corp.com", agent="b")

        everyone = store.usage_by_activity(conn, org_id=org)
        just_alice = store.usage_by_activity(
            conn, org_id=org, member="alice@corp.com")

        assert len(everyone) == 2
        assert [r["activity"] for r in just_alice] == ["a"]

    def test_a_member_is_matched_case_insensitively(self, conn, org):
        """`user_email` is CITEXT. Only a real server knows that.

        It matters because the header is typed by a human somewhere upstream,
        and `Alice@Corp.com` billing to a different row than `alice@corp.com`
        would split one person's spend across two lines of the admin's report.
        """
        self._usage(conn, org, user_email="Alice@Corp.com", agent="a")
        self._usage(conn, org, user_email="alice@corp.com", agent="a")

        rows = store.usage_by_member(conn, org_id=org)

        assert len(rows) == 1
        assert rows[0]["calls"] == 2

    def test_a_member_with_no_email_is_reported_not_hidden(self, conn, org):
        self._usage(conn, org, user_email=None, credits=Decimal("3"))

        rows = store.usage_by_member(conn, org_id=org)

        assert [r["member"] for r in rows] == [store.UNATTRIBUTED_ACTIVITY]

    def test_the_window_excludes_an_older_row(self, conn, org):
        self._usage(conn, org, agent="recent")
        self._usage(conn, org, agent="ancient")
        # Backdate one PAST the window. `record_usage` always stamps now(), so
        # this is the only way to test the interval against a real clock.
        conn.execute(
            text("UPDATE usage_event SET created_at = now() - interval '99 days' "
                 "WHERE organization_id = :o AND agent = 'ancient'"),
            {"o": org},
        )

        rows = store.usage_by_activity(conn, org_id=org)

        assert [r["activity"] for r in rows] == ["recent"]

    def test_one_organization_never_reads_another(self, conn, org):
        other = store.ensure_organization(
            conn, slug=f"other-{uuid.uuid4().hex[:8]}", name="Other Ltd",
            gstin="29ABCDE1234F1Z5", billing_state="KA",
        )
        self._usage(conn, org, agent="mine")
        self._usage(conn, other, agent="theirs")

        assert [r["activity"] for r in
                store.usage_by_activity(conn, org_id=org)] == ["mine"]

    def test_credits_come_back_as_Decimal_not_float(self, conn, org):
        """Money. A float total disagrees with the sum of its own rows."""
        self._usage(conn, org, agent="a", credits=Decimal("0.1"))
        self._usage(conn, org, agent="a", credits=Decimal("0.2"))

        rows = store.usage_by_activity(conn, org_id=org)

        assert isinstance(rows[0]["credits"], Decimal)
        assert rows[0]["credits"] == Decimal("0.3")


class TestOperatorSpendReads:
    """WS-31 §5 — the operator's cross-tenant reads (R8: real Postgres only).

    🔴 **These two are the only reads in `store.py` that cross tenants**, so the
    tests below check the two things that make that safe: they return an
    organization with no usage at all, and they never silently drop a day.
    """

    def test_an_org_with_no_usage_still_appears(self, conn, org):
        # ⚠️ The most actionable row on the page is "bought credits, used
        # none". An INNER JOIN hides exactly that customer, and the page then
        # looks healthy because only busy customers are on it.
        #
        # 🔴 **Asserted against THIS test's own organization, by slug.** The
        # first version scanned every returned row for a zero, which passed on
        # a fresh CI database and failed on a scratch one holding 563
        # organizations — because the page is capped and a zero-usage row sorts
        # LAST. A test that asserts on rows it did not create is testing the
        # database's contents.
        slug = conn.execute(
            text("SELECT slug FROM organization WHERE id = :i"), {"i": org}
        ).scalar_one()
        # 🔴 **FILTERED, and never paged wide** (HANDOFF H-83). The default
        # page is capped and rows sort by credits DESC, so a zero-usage
        # organization sorts LAST — asserting against the default page tests
        # the pagination. The first repair asked for a page of 10,000, which
        # is a bet on the size of the table: somebody sized that bound at 563
        # organizations, the scratch database reached 25,959, the zero-usage
        # block moved past the page, and this fence became a steady red that
        # read as volume noise. A filter cannot expire that way.
        page = store.usage_by_org(conn, days=30, slug=slug)
        mine = page["rows"]
        assert mine, (
            "usage_by_org must LEFT JOIN — an organization with no usage is "
            "the row an operator most needs to see"
        )
        assert mine[0]["credits"] == 0 and mine[0]["calls"] == 0

    def test_the_slug_filter_holds_a_page_of_ONE_against_richer_rows(
            self, conn, org):
        """🔴 **R7 — the fence for H-83.** Delete the filter and this goes red.

        Three neighbours with real spend outrank this fixture's zero-usage
        organization on every column the ORDER BY reads. A page of one then
        carries the top spender, and only the filter can put this row on it.

        ⚠️ **The volume is the point, and three rows are enough to prove it.**
        The shape this replaces asked for a page of 10,000, which passed on a
        small database and failed on a large one — so it measured how many
        organizations the box held rather than what the read returns. This
        fence gives the same answer at three rows and at 25,959.
        """
        slug = conn.execute(
            text("SELECT slug FROM organization WHERE id = :i"), {"i": org},
        ).scalar_one()
        for _ in range(3):
            rich = store.ensure_organization(
                conn, slug=f"zz-rich-{uuid.uuid4().hex[:8]}", name="Rich Ltd",
            )
            store.record_usage(
                conn, org_id=rich, request_id=f"req-{uuid.uuid4().hex}",
                billed_credits=Decimal("1000"),
            )

        page = store.usage_by_org(conn, days=30, limit=1, slug=slug)

        assert [r["slug"] for r in page["rows"]] == [slug], (
            "the page of one must carry the organization the caller named, "
            "and not the biggest spender on the box"
        )
        assert page["rows"][0]["credits"] == 0
        assert page["total"] == 1, (
            "a filtered page counts what it filtered — a total over the whole "
            "table would render as '1 of 25,959' and claim a truncation that "
            "never happened"
        )

    def test_a_quiet_customer_can_fall_OFF_the_default_page(self, conn, org):
        """🔴 The known defect, pinned so nobody rediscovers it (H-76).

        The LEFT JOIN exists to show "bought credits, used none". The ORDER BY
        sorts that row last. The LIMIT then removes it. Below the cap the two
        rules never meet, which is why dev, CI and production all agree this is
        fine — found on 2026-08-30 against a scratch database of 563.

        This test does not assert the bug happens. It asserts the page ADMITS
        its own truncation, which is the part that must never regress.
        """
        page = store.usage_by_org(conn, days=30, limit=1)
        assert page["shown"] == 1
        assert page["total"] >= 1
        if page["total"] > 1:
            assert page["shown"] < page["total"], (
                "a truncated page must report the total, or the table looks "
                "complete while hiding the most actionable customers"
            )

    def test_it_reports_how_many_rows_it_left_out(self, conn, org):
        # 🔴 The cap fights the LEFT JOIN: rows sort by credits DESC, so a
        # zero-usage organization sorts last and falls off the end first. That
        # is survivable only while the page SAYS it is truncated.
        page = store.usage_by_org(conn, days=30)
        assert page["shown"] == len(page["rows"])
        assert page["total"] >= page["shown"]
        assert page["shown"] <= store.SPEND_PAGE_SIZE

    def test_it_returns_one_row_per_organization(self, conn, org):
        rows = store.usage_by_org(conn, days=30)["rows"]
        slugs = [r["slug"] for r in rows]
        assert len(slugs) == len(set(slugs)), "an org must not appear twice"

    def test_credits_come_back_as_Decimal_not_float(self, conn, org):
        # Money never becomes a float. The API layer stringifies these, and a
        # float would already have lost precision before it got there.
        for r in store.usage_by_org(conn, days=30)["rows"]:
            assert isinstance(r["credits"], Decimal)
            assert isinstance(r["cost_usd"], Decimal)

    def test_the_daily_series_fills_every_gap(self, conn):
        # 🔴 The whole reason `usage_daily` uses generate_series. Grouping
        # `usage_event` alone returns only days that HAVE rows, and a chart
        # drawing a line between two points a week apart reads as steady use
        # across a week that had none.
        rows = store.usage_daily(conn, days=7)
        assert len(rows) == 7, f"expected 7 days, got {len(rows)}"
        days = [r["day"] for r in rows]
        assert days == sorted(days), "the series must be in date order"

    def test_the_daily_series_reports_zero_rather_than_null(self, conn, org):
        # A null renders as a hole in a chart. A zero renders as a quiet day,
        # which is what it is.
        #
        # ⚠️ **Scoped to a FRESH org, not the whole table.** The first version
        # of this test asserted `calls == 0` on the unfiltered series and CI
        # failed it with `assert 57 == 0` — the shared database carries rows
        # from every other suite in the module. A test that asserts on data it
        # does not create is testing the database's contents, not the function.
        for r in store.usage_daily(conn, days=3, org_id=org):
            assert r["calls"] == 0, "a fresh org has no usage"
            assert r["credits"] == Decimal(0)
            assert r["credits"] is not None

    def test_the_unfiltered_series_never_returns_a_null_cell(self, conn):
        # The shape half, over whatever the shared database happens to hold.
        # A null in either column is a hole in the chart regardless of volume.
        for r in store.usage_daily(conn, days=3):
            assert r["calls"] is not None
            assert r["credits"] is not None
            assert isinstance(r["credits"], Decimal)

    def test_the_org_filter_PREPAREs_in_both_directions(self, conn, org):
        # ⚠️ The CAST is load-bearing. A bare `:org IS NULL` gives Postgres no
        # type to infer and the statement fails to PREPARE with
        # AmbiguousParameter — on EVERY call, including the unfiltered one. So
        # both paths must be exercised, not just the filtered one.
        assert len(store.usage_daily(conn, days=3, org_id=None)) == 3
        assert len(store.usage_daily(conn, days=3, org_id=org)) == 3

    def test_a_window_of_one_day_returns_one_row(self, conn):
        # An off-by-one in `days - 1` inside generate_series is invisible on a
        # 30-day window and obvious on a 1-day one.
        assert len(store.usage_daily(conn, days=1)) == 1


# ── A5 slice 5: a refusal is recorded, and it is not a call ─────────────────
#
# Spec: `ai_metering_and_analytics.md` §8.1 · migration 020.
#
# 🔴 **The defect this slice can ship is SILENT.** A refusal row lands in
# `usage_event`, every read that counts rows starts counting it as a call, the
# call counts inflate and the credit sums stay right — because a refusal bills
# 0. So two columns on one page disagree and nothing says why. Each test below
# writes ONE refusal beside ONE served call and demands the answer 1.
#
# R8 binds all of it: the CHECK, the FILTER-versus-WHERE distinction and the
# LEFT JOIN survival are things only a real server can answer.

class TestARefusalIsNotACall:
    def _served(self, conn, org, **fields):
        assert store.record_usage(
            conn, org_id=org, request_id=f"req-{uuid.uuid4().hex}",
            billed_credits=fields.pop("credits", Decimal("1")), **fields,
        ) is True

    def _refused(self, conn, org, reason="tier_unknown", **fields):
        assert store.record_usage(
            conn, org_id=org, request_id=f"rtr-{uuid.uuid4().hex}",
            billed_credits=Decimal(0), refusal_reason=reason,
            quantity=0, unit="tokens", **fields,
        ) is True

    # ── The column and its closed vocabulary ────────────────────────────────

    def test_a_served_row_carries_NULL_and_that_is_what_NULL_MEANS(
            self, conn, org):
        self._served(conn, org, agent="a")
        assert conn.execute(
            text("SELECT count(*) FROM usage_event WHERE organization_id = :o "
                 "AND refusal_reason IS NULL"), {"o": org},
        ).scalar_one() == 1

    def test_the_CHECK_refuses_a_fourth_slug(self, conn, org):
        """An open TEXT column grows a second spelling of one wall inside a
        month, and the two then read as two different walls.

        ⚠️ Inside a SAVEPOINT: a failed statement aborts the surrounding
        transaction, and the fixture's rollback would then have nothing to roll
        back cleanly.
        """
        with pytest.raises(IntegrityError), conn.begin_nested():
            self._refused(conn, org, reason="out_of_cheese")

    def test_all_three_shipped_slugs_are_accepted(self, conn, org):
        for slug in ("insufficient_credits", "run_ceiling_exceeded",
                     "tier_unknown"):
            self._refused(conn, org, reason=slug)
        assert conn.execute(
            text("SELECT count(*) FROM usage_event WHERE organization_id = :o "
                 "AND refusal_reason IS NOT NULL"), {"o": org},
        ).scalar_one() == 3

    # ── The five counting reads ─────────────────────────────────────────────

    def test_usage_by_activity_counts_one(self, conn, org):
        self._served(conn, org, agent="a")
        self._refused(conn, org, agent="a")

        rows = store.usage_by_activity(conn, org_id=org)

        assert [(r["activity"], r["calls"]) for r in rows] == [("a", 1)]

    def test_usage_by_member_counts_one(self, conn, org):
        self._served(conn, org, user_email="alice@corp.com")
        self._refused(conn, org, user_email="alice@corp.com")

        rows = store.usage_by_member(conn, org_id=org)

        assert [(r["member"], r["calls"]) for r in rows] \
            == [("alice@corp.com", 1)]

    def test_usage_by_org_counts_one_call_and_one_member(self, conn, org):
        # ⚠️ Two DIFFERENT addresses. A member who only hit a wall made no
        # call, so the DISTINCT count must drop them — with one address the
        # unfiltered and filtered counts agree and the test proves nothing.
        self._served(conn, org, user_email="alice@corp.com")
        self._refused(conn, org, user_email="bob@corp.com")

        mine = self._row_for(conn, org)

        assert mine["calls"] == 1
        assert mine["members"] == 1

    def test_usage_daily_counts_one(self, conn, org):
        self._served(conn, org)
        self._refused(conn, org)

        rows = store.usage_daily(conn, days=3, org_id=org)

        assert sum(r["calls"] for r in rows) == 1

    # ── Why a FILTER clause, and never a WHERE clause ───────────────────────

    def test_an_org_with_ONLY_refusals_still_appears_in_usage_by_org(
            self, conn, org):
        """🔴 The regression a WHERE clause would cause.

        A WHERE on the right-hand table turns the LEFT JOIN into an inner
        join, and "this customer bought credits and used none" — the single
        most actionable row on the page — disappears. A customer stuck at a
        wall is exactly that customer.
        """
        self._refused(conn, org, reason="insufficient_credits")

        mine = self._row_for(conn, org)

        assert mine["calls"] == 0
        assert mine["credits"] == Decimal(0)

    def test_usage_daily_keeps_every_day_when_only_refusals_exist(
            self, conn, org):
        # The gap fill is the whole function. A WHERE on `u` drops the days
        # that hold only refusals, and the chart draws a straight line across
        # them as if usage were steady.
        self._refused(conn, org)

        rows = store.usage_daily(conn, days=3, org_id=org)

        assert len(rows) == 3
        assert all(r["calls"] == 0 for r in rows)

    def _row_for(self, conn, org) -> dict:
        """This organization's row from the operator page, BY SLUG.

        🔴 **Filtered, and never paged wide** (HANDOFF H-83). Six tests read
        through this helper. It asked for a page of 10,000 until 2026-08-31,
        and a limit is a bet on the size of the table: it was sized at 563
        organizations, the scratch database reached 25,959, and the zero-usage
        block moved past the page. All six then failed for a reason that had
        nothing to do with what they assert, which is how a real regression
        gets read as volume noise. A filter cannot expire that way.
        """
        slug = conn.execute(
            text("SELECT slug FROM organization WHERE id = :i"), {"i": org},
        ).scalar_one()
        page = store.usage_by_org(conn, days=30, slug=slug)
        mine = page["rows"]
        assert mine, "the LEFT JOIN must keep this organization on the page"
        return mine[0]

    # ── The three reads that must NOT change ────────────────────────────────

    def test_a_refusal_keeps_a_walled_customer_VISIBLE(self, conn, org):
        """🔴 `last_seen_by_org` takes no filter, and that is deliberate.

        A customer at a wall is a customer who is trying. Filtering the
        refusal out here makes them read as SILENT to A3, which is the exact
        defect H-76 closed.
        """
        slug = conn.execute(
            text("SELECT slug FROM organization WHERE id = :i"), {"i": org},
        ).scalar_one()
        assert store.last_seen_by_org(conn)[slug] is None

        self._refused(conn, org, reason="insufficient_credits")

        assert store.last_seen_by_org(conn)[slug] is not None

    def test_run_spend_reads_the_same_before_and_after_a_refusal(
            self, conn, org):
        # The breaker sums `billed_credits`, and a refusal adds 0 — so it
        # keeps its meaning with no edit at all.
        self._served(conn, org, run_id="run-x", credits=Decimal("7"))
        before = store.run_spend(conn, org_id=org, run_id="run-x")

        self._refused(conn, org, reason="run_ceiling_exceeded", run_id="run-x")

        assert store.run_spend(conn, org_id=org, run_id="run-x") == before \
            == Decimal("7")

    def test_the_failover_read_never_sees_a_refusal(self, conn, org):
        # It filters `served_rank > 1`. A refusal carries no served rank, so
        # it cannot reach that read — asserted rather than assumed, because
        # "cannot" is a claim about a NULL comparison.
        self._refused(conn, org)
        assert conn.execute(
            text("SELECT count(*) FROM usage_event WHERE organization_id = :o "
                 "AND served_rank > 1"), {"o": org},
        ).scalar_one() == 0

    # ── The operator can SEE the wall ───────────────────────────────────────

    def test_a_walled_org_reports_its_refusals_and_ZERO_calls(
            self, conn, org):
        """🔴 The signal, end to end. Without this the slice is write-only.

        A refusal moves `last_seen`, so a walled customer stops reading as
        `silent` to A3. If nothing else reported the wall, hitting one would
        make a customer HARDER to find than saying nothing did — the column
        would be written by the Router and read by nobody.
        """
        self._refused(conn, org, reason="insufficient_credits")
        self._refused(conn, org, reason="insufficient_credits")

        mine = self._row_for(conn, org)

        assert mine["refusals"] == 2
        assert mine["calls"] == 0

    def test_the_two_counts_are_measured_over_the_SAME_window(
            self, conn, org):
        # `calls` and `refusals` sit side by side on the operator's page, so a
        # refusal older than the window must leave both. Two windows on one
        # row is how a page starts disagreeing with itself.
        self._refused(conn, org)
        conn.execute(
            text("UPDATE usage_event SET created_at = now() - interval '99 days' "
                 "WHERE organization_id = :o"), {"o": org},
        )

        assert self._row_for(conn, org)["refusals"] == 0

    def test_a_served_call_is_never_counted_as_a_refusal(self, conn, org):
        # The inverted FILTER, checked in the direction that would silently
        # inflate a support queue rather than empty one.
        self._served(conn, org, agent="a")

        mine = self._row_for(conn, org)

        assert mine["calls"] == 1
        assert mine["refusals"] == 0

    def test_a_WALLED_but_funded_org_is_NOT_silent(self, conn, org):
        """🔴 The flag handoff, pinned on the side that produces it.

        `is_silent` is what the wall switches OFF, and `refusals` is what
        must switch on in its place. The operator console renders one chip or
        the other from exactly these two numbers, so a change that let both
        read false would hide the customer entirely.
        """
        from datetime import UTC, datetime

        from customer_console import analytics

        slug = conn.execute(
            text("SELECT slug FROM organization WHERE id = :i"), {"i": org},
        ).scalar_one()
        # FUNDED. `is_silent` returns False on a zero balance whatever the
        # timestamps say, so an unfunded org would pass this test vacuously.
        store.add_credit(conn, org_id=org, delta=Decimal("500"),
                         reason="purchase")
        self._refused(conn, org, reason="run_ceiling_exceeded")

        balance = balance_of(store.credit_deltas(conn, org_id=org))
        last_seen = store.last_seen_by_org(conn)[slug]

        assert balance > 0
        assert analytics.is_silent(
            balance, last_seen, datetime.now(UTC)) is False
        assert self._row_for(conn, org)["refusals"] == 1

    def test_the_writer_and_the_CHECK_name_the_same_three_slugs(self, conn):
        """R7 against DRIFT. Two lists say what a refusal may be called.

        `main._REFUSAL_REASONS` guards the write, and migration 020's CHECK
        guards the table. Adding a slug to one and not the other is silent in
        both directions: the writer drops a legal slug, or a new slug reaches
        the database and raises. Read from `pg_constraint` rather than from
        the migration text, so what is asserted is the APPLIED schema.
        """
        import re

        from customer_console.main import _REFUSAL_REASONS

        definition = conn.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                 "WHERE conname = 'usage_event_refusal_reason_known'"),
        ).scalar_one()

        in_the_database = set(re.findall(r"'([a-z_]+)'::text", definition))

        assert in_the_database == set(_REFUSAL_REASONS), (
            f"the writer says {sorted(_REFUSAL_REASONS)} and the database "
            f"says {sorted(in_the_database)} — one of them was edited alone"
        )
