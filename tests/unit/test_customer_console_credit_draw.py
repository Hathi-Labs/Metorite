"""Each charge records which lots paid for it. Migration 036.

Spec: ``project-docs/specs/operator_console_money.md`` §3.

🔴 **The operator asks what a customer PAID for, of what they spent.** A credit
drawn from a purchase lot is revenue. A credit drawn from a trial, promotion or
grant lot is not. ``credit_lot.credits_used`` is a lifetime total and cannot
answer the question for a window, so ``credit_draw`` records each draw.

Three failure modes drive these tests:

  1. **A charge that spans two lots records one.** The window then
     undercounts whichever lot came second.
  2. **An overdraft records nothing.** The window then counts fewer charged
     credits than ``usage_event`` did, and an overdraft reads like a charge
     that predates the table.
  3. **The value uses the CURRENT price, not the price the lot was sold at.**
     A customer who bought at ₹1.20 and spends after a price change to ₹1.00
     paid ₹1.20. Revenue is what they paid.
"""

from __future__ import annotations

import os
import uuid
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

    slug = f"draw-{uuid.uuid4().hex[:8]}"
    with db.begin() as c:
        org_id = c.execute(
            text("INSERT INTO organization (slug, name) VALUES (:s, :s) RETURNING id::text"),
            {"s": slug},
        ).scalar_one()
    yield {"id": org_id, "slug": slug}
    with db.begin() as c:
        c.execute(
            text("DELETE FROM credit_draw WHERE organization_id = CAST(:o AS uuid)"), {"o": org_id}
        )
        c.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"), {"o": org_id})


def _draws(c, org_id: str) -> list[tuple]:
    from sqlalchemy import text

    return [
        (r.lot_id, Decimal(r.credits))
        for r in c.execute(
            text(
                "SELECT lot_id, credits FROM credit_draw "
                "WHERE organization_id = CAST(:o AS uuid) ORDER BY id"
            ),
            {"o": org_id},
        ).all()
    ]


def _charge(c, org_id: str, credits: str, ref: str) -> None:
    from customer_console import store
    from customer_console.credits import LEDGER_REASON_USAGE

    store.add_credit(c, org_id=org_id, delta=-Decimal(credits), reason=LEDGER_REASON_USAGE, ref=ref)


class TestTheWritePath:
    def test_a_charge_across_TWO_lots_records_both(self, db, org):
        from customer_console import store

        with db.begin() as c:
            free = store.add_credit_lot(c, org_id=org["id"], source="trial", credits=Decimal("10"))
            paid = store.add_credit_lot(
                c,
                org_id=org["id"],
                source="purchase",
                credits=Decimal("100"),
                price_paid_inr=Decimal("120"),
            )
            _charge(c, org["id"], "15", "u-1")
            # Free burns first (migration 028), so 10 from the trial, 5 paid.
            assert _draws(c, org["id"]) == [(free, Decimal("10")), (paid, Decimal("5"))]

    def test_an_OVERDRAFT_records_the_uncovered_part_with_no_lot(self, db, org):
        from customer_console import store

        with db.begin() as c:
            paid = store.add_credit_lot(
                c,
                org_id=org["id"],
                source="purchase",
                credits=Decimal("4"),
                price_paid_inr=Decimal("4"),
            )
            _charge(c, org["id"], "7", "u-1")
            assert _draws(c, org["id"]) == [(paid, Decimal("4")), (None, Decimal("3"))]

    def test_a_GRANT_writes_no_draw(self, db, org):
        from customer_console import store
        from customer_console.credits import LEDGER_REASON_GRANT

        with db.begin() as c:
            store.add_credit(c, org_id=org["id"], delta=Decimal("50"), reason=LEDGER_REASON_GRANT)
            assert _draws(c, org["id"]) == []

    def test_a_failed_ledger_write_takes_its_draws_with_it(self, db, org):
        """The draws and the ledger row share ONE transaction.

        A retried charge with the same ref violates the ledger's unique key.
        Its draws must not survive the rollback, or a retry would count twice.
        """
        from customer_console import store
        from sqlalchemy.exc import IntegrityError

        with db.begin() as c:
            store.add_credit_lot(c, org_id=org["id"], source="purchase", credits=Decimal("50"))
            _charge(c, org["id"], "5", "same-ref")
        with pytest.raises(IntegrityError), db.begin() as c:
            _charge(c, org["id"], "5", "same-ref")
        with db.begin() as c:
            assert sum(x[1] for x in _draws(c, org["id"])) == Decimal("5")


class TestTheWindowRead:
    def test_paid_free_and_unbacked_are_split(self, db, org):
        from customer_console import store

        with db.begin() as c:
            store.add_credit_lot(
                c,
                org_id=org["id"],
                source="promo",
                credits=Decimal("10"),
                price_paid_inr=Decimal("0"),
            )
            store.add_credit_lot(
                c,
                org_id=org["id"],
                source="purchase",
                credits=Decimal("100"),
                price_paid_inr=Decimal("120"),
            )
            _charge(c, org["id"], "30", "u-1")  # 10 promo + 20 paid
            _charge(c, org["id"], "90", "u-2")  # 80 paid + 10 unbacked
            row = store.draws_by_org(c, days=30)["rows"][org["slug"]]

        assert row["free_credits"] == Decimal("10")
        assert row["paid_credits"] == Decimal("100")
        assert row["unbacked_credits"] == Decimal("10")
        assert row["unpriced_paid_credits"] == Decimal("0")

    def test_paid_value_uses_the_price_the_lot_was_SOLD_at(self, db, org):
        from customer_console import store

        with db.begin() as c:
            store.add_credit_lot(
                c,
                org_id=org["id"],
                source="purchase",
                credits=Decimal("100"),
                price_paid_inr=Decimal("120"),
            )
            _charge(c, org["id"], "25", "u-1")
            row = store.draws_by_org(c, days=30)["rows"][org["slug"]]

        # 25 credits of a lot sold at ₹1.20 each.
        assert row["paid_value_inr"] == Decimal("30")

    def test_a_purchase_lot_with_NO_price_is_counted_apart(self, db, org):
        """A manual grant typed without a rupee figure is paid, at a price
        nobody recorded. It must not read as ₹0 of revenue."""
        from customer_console import store

        with db.begin() as c:
            store.add_credit_lot(c, org_id=org["id"], source="purchase", credits=Decimal("40"))
            _charge(c, org["id"], "12", "u-1")
            row = store.draws_by_org(c, days=30)["rows"][org["slug"]]

        assert row["paid_credits"] == Decimal("12")
        assert row["unpriced_paid_credits"] == Decimal("12")
        assert row["paid_value_inr"] == Decimal("0")

    def test_the_lifetime_mix_comes_from_the_lots(self, db, org):
        from customer_console import store

        with db.begin() as c:
            store.add_credit_lot(c, org_id=org["id"], source="trial", credits=Decimal("5"))
            store.add_credit_lot(
                c,
                org_id=org["id"],
                source="purchase",
                credits=Decimal("10"),
                price_paid_inr=Decimal("20"),
            )
            _charge(c, org["id"], "8", "u-1")
            row = store.draws_by_org(c, days=30)["rows"][org["slug"]]

        assert row["life_free_used"] == Decimal("5")
        assert row["life_paid_used"] == Decimal("3")
        assert row["life_paid_value_inr"] == Decimal("6")

    def test_a_draw_OUTSIDE_the_window_is_not_counted(self, db, org):
        from customer_console import store
        from sqlalchemy import text

        with db.begin() as c:
            store.add_credit_lot(
                c,
                org_id=org["id"],
                source="purchase",
                credits=Decimal("10"),
                price_paid_inr=Decimal("10"),
            )
            _charge(c, org["id"], "4", "u-1")
            c.execute(
                text(
                    "UPDATE credit_draw SET created_at = now() - interval '40 days' "
                    "WHERE organization_id = CAST(:o AS uuid)"
                ),
                {"o": org["id"]},
            )
            got = store.draws_by_org(c, days=30)

        assert got["rows"][org["slug"]]["paid_credits"] == Decimal("0")
        assert got["since"] is not None


class TestTheRoute:
    def test_usage_orgs_carries_the_split(self, db, org):
        """The operator route returns the draw fields beside the org's row.

        ⚠️ The org needs a LARGE `usage_event` row, or it sorts last and falls
        off the capped page (H-76), and the assertion never runs (review of
        PR #779).
        """
        from customer_console import main, store
        from sqlalchemy import text

        with db.begin() as c:
            store.add_credit_lot(
                c,
                org_id=org["id"],
                source="purchase",
                credits=Decimal("10"),
                price_paid_inr=Decimal("15"),
            )
            _charge(c, org["id"], "2", "u-1")
            c.execute(
                text(
                    "INSERT INTO usage_event (organization_id, request_id, billed_credits) "
                    "VALUES (CAST(:o AS uuid), :r, 99999999)"
                ),
                {"o": org["id"], "r": f"draw-route-{org['slug']}"},
            )

        view = main.admin_usage_by_org(None, days=30)
        mine = [r for r in view.rows if r.slug == org["slug"]]
        assert len(mine) == 1
        assert Decimal(mine[0].paidCredits) == Decimal("2")
        assert Decimal(mine[0].paidValueInr) == Decimal("3")
        assert Decimal(mine[0].creditsLast7Days) == Decimal("99999999")
        assert view.drawsSince is not None


class TestRounding:
    def test_a_SUB_QUANTUM_charge_writes_no_draw_and_does_not_fail(self, db, org):
        """`/usage/record` passes credits unrounded. A draw that rounds to
        0.0000 must be dropped, never trip the CHECK and roll the charge back."""
        from customer_console import store

        with db.begin() as c:
            store.add_credit_lot(c, org_id=org["id"], source="purchase", credits=Decimal("1"))
            _charge(c, org["id"], "0.00003", "u-tiny")
            assert _draws(c, org["id"]) == []

    def test_a_sub_quantum_OVERDRAFT_remainder_is_dropped(self, db, org):
        from customer_console import store

        with db.begin() as c:
            lot = store.add_credit_lot(c, org_id=org["id"], source="purchase", credits=Decimal("1"))
            _charge(c, org["id"], "1.00003", "u-over")
            assert _draws(c, org["id"]) == [(lot, Decimal("1.0000"))]
