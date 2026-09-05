"""Reserve credits before a call, settle the real charge after it.

Spec: ``project-docs/specs/credit_pricing.md`` §5 (slice 4). Migration 026.

🔴 **Nothing reserved credits before this slice.** The meter ran AFTER the
provider answered, so two calls arriving together each read the same balance,
each passed, and the organization went negative by the size of the second —
under exactly the load where a spend gate is supposed to matter.

Four failure modes drive these tests:

  1. **Two concurrent calls both pass.** The balance is ``SUM(delta)`` and not
     a column, so there is nothing to contend on and nothing to lock unless
     somebody takes a lock deliberately.
  2. **A retry charges twice.** A customer billed twice is a credibility
     event, not a rounding error.
  3. **A crash strands credits.** The customer cannot spend them and nothing
     will reconcile them, because the request that reserved them is gone.
  4. **A hold sized on a cache HIT.** The reservation comes out too small, and
     the settle then takes the organization negative — the very thing the
     reserve exists to prevent.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from customer_console.credits import (
    LEDGER_REASON_HOLD,
    LEDGER_REASON_RELEASE,
    RateCard,
    estimate_hold,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "")

# ── The estimate: pure, so it needs no database ─────────────────────────────

PRICED = RateCard(
    model="m",
    input_per_1m=Decimal("4000"),
    output_per_1m=Decimal("20000"),
    cached_input_per_1m=Decimal("400"),
    pricing_mode="priced",
)


class TestTheEstimate:
    def test_the_worst_case_rates_at_the_UNCACHED_rate(self):
        """🔴 Failure mode 4.

        8000 prompt at 4000/1M is 32 credits. 4000 output at 20000/1M is 80.
        Assuming a cache hit would size this at a fraction of that, and the
        settle would then exceed what was reserved.
        """
        e = estimate_hold(PRICED, prompt_tokens=8000, max_output_tokens=4000)
        assert e.credits == Decimal("112.0000")
        assert e.reason == "worst_case"

    def test_a_deterministic_job_reserves_the_EXACT_charge(self):
        """Audio is easier than text here, not harder — the duration is a
        property of the file, so there is nothing to guess and nothing to
        release afterwards."""
        stt = RateCard(
            model="w", input_per_1m=Decimal(0), output_per_1m=Decimal(0),
            task="transcribe", unit="minutes",
            credits_per_unit=Decimal("90"), pricing_mode="priced",
        )
        e = estimate_hold(
            stt, prompt_tokens=0, max_output_tokens=0, quantity=Decimal("45")
        )
        assert e.credits == Decimal("4050.0000")
        assert e.is_exact is True

    def test_an_unpriced_card_reserves_NOTHING_and_does_not_raise(self):
        """⚠️ The card ships unpriced (H-42).

        A reserve that refused every call until somebody priced the slate
        would take the product down rather than protect it.
        """
        unpriced = RateCard("m", Decimal(0), Decimal(0))
        e = estimate_hold(unpriced, prompt_tokens=8000, max_output_tokens=4000)
        assert e.credits == 0
        assert e.reason == "unpriced"

    def test_a_per_unit_job_with_no_quantity_reserves_nothing(self):
        """`rate_call` refuses this at settle time and the meter downgrades it
        to "bill zero, loudly". Holding zero keeps the two answers consistent."""
        stt = RateCard(
            model="w", input_per_1m=Decimal(0), output_per_1m=Decimal(0),
            task="transcribe", unit="minutes",
            credits_per_unit=Decimal("90"), pricing_mode="priced",
        )
        e = estimate_hold(stt, prompt_tokens=0, max_output_tokens=0)
        assert e.credits == 0
        assert e.reason == "unmeasured"


# ── The ledger cycle: R8, against a real database ──────────────────────────

pytestmark = pytest.mark.skipif(
    not _URL, reason="R8 requires a REAL Postgres; set CUSTOMER_CONSOLE_DATABASE_URL"
)


@pytest.fixture()
def db():
    from sqlalchemy import create_engine

    return create_engine(_URL, future=True)


@pytest.fixture()
def funded(db):
    """A throwaway organization holding 1000 credits.

    ⚠️ Its own slug per test, and it cleans up after itself. H-91 records what
    a fixture that leaks an organization per run costs: the shared database
    reached 25,959 rows and suites began failing on volume alone.
    """
    from sqlalchemy import text

    from customer_console import store

    slug = f"hold-{uuid.uuid4().hex[:8]}"
    with db.begin() as c:
        org_id = c.execute(
            text(
                "INSERT INTO organization (slug, name) VALUES (:s, :s) "
                "RETURNING id::text"
            ),
            {"s": slug},
        ).scalar_one()
        store.add_credit(
            c, org_id=org_id, delta=Decimal("1000"), reason="grant", ref=f"{slug}-fund"
        )
    yield org_id
    with db.begin() as c:
        c.execute(
            text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
            {"o": org_id},
        )


def _balance(db, org_id: str) -> Decimal:
    from sqlalchemy import text

    with db.begin() as c:
        return Decimal(
            c.execute(
                text(
                    "SELECT COALESCE(SUM(delta), 0) FROM credit_ledger "
                    "WHERE organization_id = CAST(:o AS uuid)"
                ),
                {"o": org_id},
            ).scalar_one()
        )


class TestTheCycle:
    def test_hold_settle_release_nets_to_the_REAL_charge(self, db, funded):
        """The three rows of §5, and the only number that survives is 423."""
        from customer_console import store

        with db.begin() as c:
            store.place_hold(
                c, org_id=funded, request_id="r1", credits=Decimal("800")
            )
        assert _balance(db, funded) == Decimal("200.0000")

        with db.begin() as c:
            store.add_credit(
                c, org_id=funded, delta=Decimal("-423"), reason="usage", ref="r1"
            )
            store.release_hold(c, org_id=funded, request_id="r1")
        assert _balance(db, funded) == Decimal("577.0000")

    def test_a_release_is_idempotent(self, db, funded):
        """🔴 Failure mode 2. A retried request must give nothing back twice."""
        from customer_console import store

        with db.begin() as c:
            store.place_hold(
                c, org_id=funded, request_id="r2", credits=Decimal("300")
            )
        with db.begin() as c:
            store.release_hold(c, org_id=funded, request_id="r2")
        after = _balance(db, funded)
        with db.begin() as c:
            store.release_hold(c, org_id=funded, request_id="r2")
        assert _balance(db, funded) == after == Decimal("1000.0000")

    def test_a_repeated_hold_reserves_ONCE(self, db, funded):
        """`credit_ledger_reason_ref_unique` carries the idempotency, so a
        retried request re-reserves nothing."""
        from customer_console import store

        for _ in range(3):
            with db.begin() as c:
                store.place_hold(
                    c, org_id=funded, request_id="r3", credits=Decimal("100")
                )
        assert _balance(db, funded) == Decimal("900.0000")

    def test_a_hold_beyond_the_balance_is_REFUSED(self, db, funded):
        from customer_console import store

        with pytest.raises(store.HoldRefused), db.begin() as c:
            store.place_hold(
                c, org_id=funded, request_id="r4", credits=Decimal("1001")
            )
        assert _balance(db, funded) == Decimal("1000.0000")

    def test_a_zero_hold_writes_NO_row(self, db, funded):
        """The unpriced path — every path today. A zero-delta row is noise in
        the one table a customer reads during a dispute."""
        from sqlalchemy import text

        from customer_console import store

        with db.begin() as c:
            store.place_hold(c, org_id=funded, request_id="r5", credits=Decimal(0))
        with db.begin() as c:
            n = c.execute(
                text(
                    "SELECT count(*) FROM credit_ledger "
                    "WHERE organization_id = CAST(:o AS uuid) AND reason = :h"
                ),
                {"o": funded, "h": LEDGER_REASON_HOLD},
            ).scalar_one()
        assert n == 0


class TestConcurrency:
    def test_two_simultaneous_holds_cannot_BOTH_pass(self, db, funded):
        """🔴 Failure mode 1, and the reason `place_hold` takes a row lock.

        Two calls each needing 600 of a 1000 balance. Exactly one may hold.
        Without `SELECT ... FOR UPDATE` both read 1000, both pass, and the
        organization lands at -200.
        """
        from customer_console import store

        outcome: dict[str, str] = {}

        def attempt(name: str) -> None:
            try:
                with db.begin() as c:
                    store.place_hold(
                        c, org_id=funded, request_id=f"c-{name}",
                        credits=Decimal("600"),
                    )
                outcome[name] = "held"
            except store.HoldRefused:
                outcome[name] = "refused"

        threads = [threading.Thread(target=attempt, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(outcome.values()) == ["held", "refused"], outcome
        assert _balance(db, funded) == Decimal("400.0000")
        assert _balance(db, funded) >= 0, "the balance went negative"


class TestTheSweeper:
    def test_it_releases_a_hold_whose_call_never_closed_it(self, db, funded):
        """🔴 Failure mode 3 — a crash between the hold and the settle."""
        from sqlalchemy import text

        from customer_console import store

        with db.begin() as c:
            store.place_hold(
                c, org_id=funded, request_id="orphan", credits=Decimal("500")
            )
            # Age it past the sweeper's threshold.
            c.execute(
                text(
                    "UPDATE credit_ledger SET created_at = now() - interval '1 hour' "
                    "WHERE ref = 'orphan' AND organization_id = CAST(:o AS uuid)"
                ),
                {"o": funded},
            )
        assert _balance(db, funded) == Decimal("500.0000")

        with db.begin() as c:
            freed = store.sweep_orphan_holds(c, older_than_seconds=60)

        mine = [f for f in freed if f["ref"] == "orphan"]
        assert len(mine) == 1
        assert mine[0]["credits"] == Decimal("500.0000")
        assert _balance(db, funded) == Decimal("1000.0000")

    def test_it_leaves_a_CLOSED_hold_alone(self, db, funded):
        """A settled call already gave its reservation back. Sweeping it again
        would credit the customer twice for one call."""
        from customer_console import store

        with db.begin() as c:
            store.place_hold(
                c, org_id=funded, request_id="closed", credits=Decimal("400")
            )
            store.release_hold(c, org_id=funded, request_id="closed")
        before = _balance(db, funded)

        with db.begin() as c:
            freed = store.sweep_orphan_holds(c, older_than_seconds=0)

        assert [f for f in freed if f["ref"] == "closed"] == []
        assert _balance(db, funded) == before

    def test_it_leaves_a_YOUNG_hold_alone(self, db, funded):
        """⚠️ A hold placed a second ago belongs to a call still running.

        Sweeping it would release credits the settle is about to charge
        against, and the call would then be billed from an unreserved balance.
        """
        from customer_console import store

        with db.begin() as c:
            store.place_hold(
                c, org_id=funded, request_id="young", credits=Decimal("200")
            )
        with db.begin() as c:
            freed = store.sweep_orphan_holds(c, older_than_seconds=3600)

        assert [f for f in freed if f["ref"] == "young"] == []
        assert _balance(db, funded) == Decimal("800.0000")

    def test_what_it_frees_is_RETURNED_so_the_caller_can_shout(self, db):
        """⚠️ A swept hold is a DEFECT REPORT, never routine tidying.

        It means a request path died after reserving credits. A sweeper that
        returned a count would hide the thing it proves.
        """
        import inspect

        from customer_console import store

        sig = inspect.signature(store.sweep_orphan_holds)
        assert sig.return_annotation != "int"
        src = inspect.getsource(store.sweep_orphan_holds)
        for field in ("org_id", "ref", "credits", "held_since"):
            assert f'"{field}"' in src, f"the sweeper does not report {field}"


class TestTheLedgerVocabulary:
    def test_a_hold_is_negative_and_its_release_is_its_exact_inverse(
        self, db, funded
    ):
        """⚠️ `release_hold` reads the hold's OWN delta rather than being told
        the number. A caller that passed the amount could pass a different one,
        and the ledger would carry a release that does not match its hold."""
        from sqlalchemy import text

        from customer_console import store

        with db.begin() as c:
            store.place_hold(
                c, org_id=funded, request_id="pair", credits=Decimal("250")
            )
            store.release_hold(c, org_id=funded, request_id="pair")

        with db.begin() as c:
            rows = dict(
                c.execute(
                    text(
                        "SELECT reason, delta FROM credit_ledger "
                        "WHERE organization_id = CAST(:o AS uuid) AND ref = 'pair'"
                    ),
                    {"o": funded},
                ).all()
            )
        assert rows[LEDGER_REASON_HOLD] == Decimal("-250.0000")
        assert rows[LEDGER_REASON_RELEASE] == Decimal("250.0000")
        assert rows[LEDGER_REASON_HOLD] + rows[LEDGER_REASON_RELEASE] == 0
