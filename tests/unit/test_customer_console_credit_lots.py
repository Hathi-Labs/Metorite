"""Credits remember what they cost and when they expire.

Spec: ``project-docs/specs/credit_pricing.md`` §6 (slice 6). Migration 028.

🔴 **A balance is one number and it cannot answer three questions.**
``SUM(credit_ledger.delta)`` says how many credits an organization holds. It
cannot say what they cost, when they expire, or whether they were bought or
granted — so a refund cannot be computed and deferred revenue cannot be
reported.

Three failure modes drive these tests:

  1. **Burning a PAID lot before a free one.** The free lot then expires
     unused and the customer paid for credits they never got to spend.
  2. **Burning a never-expiring lot first.** A dated lot then reaches its
     expiry unused, which is the same loss by another route. ``NULLS LAST`` is
     what prevents it, and Postgres orders NULLs FIRST by default.
  3. **Letting the lot table become a second balance.** ``SUM(delta)`` is the
     truth. Two authorities for one number means two answers to "what do I
     have left", and the one nobody reconciles is the one a customer reads.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _URL, reason="R8 requires a REAL Postgres; set CUSTOMER_CONSOLE_DATABASE_URL"
)


@pytest.fixture()
def db():
    from sqlalchemy import create_engine

    return create_engine(_URL, future=True)


@pytest.fixture()
def org(db):
    """A throwaway organization that cleans up after itself (H-91)."""
    from sqlalchemy import text

    slug = f"lot-{uuid.uuid4().hex[:8]}"
    with db.begin() as c:
        org_id = c.execute(
            text(
                "INSERT INTO organization (slug, name) VALUES (:s, :s) "
                "RETURNING id::text"
            ),
            {"s": slug},
        ).scalar_one()
    yield org_id
    with db.begin() as c:
        c.execute(
            text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
            {"o": org_id},
        )


def _soon(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


class TestTheConsumptionOrder:
    def test_the_soonest_EXPIRY_burns_first(self, db, org):
        from customer_console import store

        with db.begin() as c:
            far = store.add_credit_lot(
                c, org_id=org, source="purchase", credits=Decimal("100"),
                price_paid_inr=Decimal("999"), expires_at=_soon(90),
            )
            near = store.add_credit_lot(
                c, org_id=org, source="purchase", credits=Decimal("100"),
                price_paid_inr=Decimal("999"), expires_at=_soon(7),
            )
            drawn = store.draw_from_lots(c, org_id=org, credits=Decimal("60"))

        assert [d["lot_id"] for d in drawn] == [near]
        assert drawn[0]["credits"] == Decimal("60")
        assert far not in [d["lot_id"] for d in drawn]

    def test_FREE_burns_before_PAID_at_the_same_expiry(self, db, org):
        """🔴 Failure mode 1.

        Burning the paid lot first leaves the free one to expire unused, and
        the customer paid for credits they never got to spend.
        """
        from customer_console import store

        when = _soon(30)
        with db.begin() as c:
            paid = store.add_credit_lot(
                c, org_id=org, source="purchase", credits=Decimal("50"),
                price_paid_inr=Decimal("499"), expires_at=when,
            )
            free = store.add_credit_lot(
                c, org_id=org, source="trial", credits=Decimal("50"),
                expires_at=when,
            )
            drawn = store.draw_from_lots(c, org_id=org, credits=Decimal("30"))

        assert [d["lot_id"] for d in drawn] == [free]
        assert paid not in [d["lot_id"] for d in drawn]

    def test_a_NEVER_expiring_lot_burns_LAST(self, db, org):
        """🔴 Failure mode 2, and Postgres's default ordering is the trap.

        A bare `ORDER BY expires_at` puts NULL FIRST, which burns the lot that
        can wait and lets the dated one expire unused.
        """
        from customer_console import store

        with db.begin() as c:
            forever = store.add_credit_lot(
                c, org_id=org, source="purchase", credits=Decimal("100"),
                price_paid_inr=Decimal("999"),
            )
            dated = store.add_credit_lot(
                c, org_id=org, source="purchase", credits=Decimal("100"),
                price_paid_inr=Decimal("999"), expires_at=_soon(14),
            )
            drawn = store.draw_from_lots(c, org_id=org, credits=Decimal("40"))

        assert [d["lot_id"] for d in drawn] == [dated]
        assert forever not in [d["lot_id"] for d in drawn]

    def test_a_charge_SPANS_lots_in_order(self, db, org):
        from customer_console import store

        with db.begin() as c:
            first = store.add_credit_lot(
                c, org_id=org, source="trial", credits=Decimal("30"),
                expires_at=_soon(7),
            )
            second = store.add_credit_lot(
                c, org_id=org, source="purchase", credits=Decimal("100"),
                price_paid_inr=Decimal("999"), expires_at=_soon(60),
            )
            drawn = store.draw_from_lots(c, org_id=org, credits=Decimal("75"))

        assert [(d["lot_id"], d["credits"]) for d in drawn] == [
            (first, Decimal("30")),
            (second, Decimal("45")),
        ]

    def test_a_charge_larger_than_every_lot_draws_them_all_and_stops(self, db, org):
        """⚠️ It does not raise and it does not invent a lot.

        The hold in §5 is what refuses an unaffordable call. A second refusal
        here would fire on the ordinary case where lots have not been
        backfilled yet.
        """
        from customer_console import store

        with db.begin() as c:
            store.add_credit_lot(
                c, org_id=org, source="trial", credits=Decimal("10"),
            )
            drawn = store.draw_from_lots(c, org_id=org, credits=Decimal("500"))

        assert sum(d["credits"] for d in drawn) == Decimal("10")

    def test_an_exhausted_lot_is_not_offered_again(self, db, org):
        from customer_console import store

        with db.begin() as c:
            lot = store.add_credit_lot(
                c, org_id=org, source="trial", credits=Decimal("20"),
            )
            store.draw_from_lots(c, org_id=org, credits=Decimal("20"))
            remaining = store.open_lots(c, org_id=org)

        assert [r.id for r in remaining] == []
        assert lot is not None

    def test_a_zero_charge_draws_nothing(self, db, org):
        from customer_console import store

        with db.begin() as c:
            store.add_credit_lot(c, org_id=org, source="trial", credits=Decimal("10"))
            assert store.draw_from_lots(c, org_id=org, credits=Decimal(0)) == []


class TestTheLotExplainsAndNeverReplaces:
    def test_adding_a_lot_writes_NO_ledger_row(self, db, org):
        """🔴 Failure mode 3.

        A lot that wrote its own ledger row would let the two disagree — and
        the balance a customer reads is the ledger.
        """
        from sqlalchemy import text

        from customer_console import store

        with db.begin() as c:
            store.add_credit_lot(
                c, org_id=org, source="purchase", credits=Decimal("100"),
                price_paid_inr=Decimal("999"),
            )
            n = c.execute(
                text(
                    "SELECT count(*) FROM credit_ledger "
                    "WHERE organization_id = CAST(:o AS uuid)"
                ),
                {"o": org},
            ).scalar_one()
        assert n == 0, "add_credit_lot must not write the balance"

    def test_drawing_from_lots_does_not_move_the_BALANCE(self, db, org):
        """`credits_used` is a projection. `SUM(delta)` is the truth."""
        from sqlalchemy import text

        from customer_console import store

        with db.begin() as c:
            store.add_credit(
                c, org_id=org, delta=Decimal("100"), reason="grant", ref="lot-fund"
            )
            store.add_credit_lot(c, org_id=org, source="grant", credits=Decimal("100"))
            store.draw_from_lots(c, org_id=org, credits=Decimal("40"))
            balance = c.execute(
                text(
                    "SELECT COALESCE(SUM(delta), 0) FROM credit_ledger "
                    "WHERE organization_id = CAST(:o AS uuid)"
                ),
                {"o": org},
            ).scalar_one()
        assert Decimal(balance) == Decimal("100.0000")


class TestTheSchema:
    def test_a_lot_cannot_be_used_past_its_size(self, db, org):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        from customer_console import store

        with db.begin() as c:
            lot = store.add_credit_lot(
                c, org_id=org, source="trial", credits=Decimal("10")
            )
        with pytest.raises(IntegrityError), db.begin() as c:
            c.execute(
                text("UPDATE credit_lot SET credits_used = 11 WHERE id = :i"),
                {"i": lot},
            )

    def test_an_unknown_source_is_refused(self, db, org):
        from sqlalchemy.exc import IntegrityError

        from customer_console import store

        with pytest.raises(IntegrityError), db.begin() as c:
            store.add_credit_lot(
                c, org_id=org, source="mystery", credits=Decimal("10")
            )

    def test_NULL_price_and_ZERO_price_are_different_facts(self, db, org):
        """⚠️ NULL means nobody paid. ZERO means somebody paid nothing on
        purpose. A refund calculation must tell them apart."""
        from sqlalchemy import text

        from customer_console import store

        with db.begin() as c:
            granted = store.add_credit_lot(
                c, org_id=org, source="grant", credits=Decimal("10")
            )
            promoted = store.add_credit_lot(
                c, org_id=org, source="promo", credits=Decimal("10"),
                price_paid_inr=Decimal("0"),
            )
            prices = dict(
                c.execute(
                    text("SELECT id, price_paid_inr FROM credit_lot "
                         "WHERE id = ANY(:ids)"),
                    {"ids": [granted, promoted]},
                ).all()
            )
        assert prices[granted] is None
        assert prices[promoted] == Decimal("0.00")
