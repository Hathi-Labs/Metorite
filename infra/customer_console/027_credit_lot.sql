-- 027 — credits remember what they cost and when they expire.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 6 (slice 6).
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 022 learned
-- that the expensive way.
--
-- 🔴 **A balance is one number today, and it cannot answer three questions.**
-- `SUM(credit_ledger.delta)` says how many credits an organization holds. It
-- cannot say what they cost, when they expire, or whether they were bought or
-- granted. So a refund cannot be computed, an expiry cannot be applied, and
-- deferred revenue cannot be reported — an unredeemed credit is a liability
-- and not revenue until somebody consumes it.
--
-- 📌 **The lot EXPLAINS a balance and never replaces it.** `SUM(delta)` stays
-- the truth. A second authority for one number is how two answers to "what do
-- I have left" appear, and the one nobody reconciles is the one a customer
-- reads. `credit_lot.credits_used` is a projection of the ledger, kept for
-- ordering and reporting.
--
-- ⚠️ **Consumption order: soonest expiry first, then FREE before PAID.**
-- Free credits burn first so a customer never loses money they spent. Credits
-- near expiry burn first so we are not expiring value they would have used.
-- Both rules favour the customer, deliberately — the alternative is a support
-- conversation about credits that vanished.
--
-- ⚠️ **`price_paid_inr` is NULL for a grant and ZERO for a promotion, and the
-- two are different facts.** NULL means nobody paid. Zero means somebody was
-- given a paid-shaped lot at no charge. A refund calculation must be able to
-- tell them apart.
--
-- R6: `credit_lot` is new and `credit_ledger.lot_id` is additive and nullable.
-- Old code writes no lot and stays legal, and every existing ledger row reads
-- NULL — which is true of them, they predate lots.
--
-- ⚠️ **NO row-level security here, and that is not an omission.** This is the
-- CONTROL plane (`db.py`), which is cross-tenant by design and isolated by a
-- network and credential boundary rather than a row predicate. R5's tenant
-- gate reads `infra/postgres`, the tenant ladder, and not this one.
--
-- Fences (R7): `tests/unit/test_customer_console_credit_lots.py`.

CREATE TABLE IF NOT EXISTS credit_lot (
    id              BIGSERIAL PRIMARY KEY,
    organization_id UUID NOT NULL
                    REFERENCES organization(id) ON DELETE CASCADE,
    -- Where the credits came from. A closed vocabulary, because "how did they
    -- get these" is a refund question and a free-text answer cannot be summed.
    source          TEXT NOT NULL,
    credits         NUMERIC(14, 4) NOT NULL,
    -- A projection of the ledger, kept for ORDERING. Never the authority.
    credits_used    NUMERIC(14, 4) NOT NULL DEFAULT 0,
    -- ⚠️ NULL means nobody paid. ZERO means somebody paid nothing on purpose.
    price_paid_inr  NUMERIC(12, 2),
    -- NULL means these credits do not expire, which is the shipped policy
    -- until D74 is taken.
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT credit_lot_source_known CHECK (
        source IN ('purchase', 'trial', 'promo', 'refund', 'grant')
    ),
    CONSTRAINT credit_lot_amounts_sane CHECK (
        credits > 0
        AND credits_used >= 0
        AND credits_used <= credits
        AND (price_paid_inr IS NULL OR price_paid_inr >= 0)
    )
);

-- The consumption order, as an index. Soonest expiry first, and NULLS LAST
-- because a lot that never expires is the last one to burn.
CREATE INDEX IF NOT EXISTS credit_lot_draw_idx
    ON credit_lot (organization_id, expires_at NULLS LAST, id)
    WHERE credits_used < credits;

ALTER TABLE credit_ledger
    ADD COLUMN IF NOT EXISTS lot_id BIGINT REFERENCES credit_lot(id);

COMMENT ON TABLE credit_lot IS
    'One purchase or grant of credits, with what it cost and when it expires. '
    'EXPLAINS a balance and never replaces it: SUM(credit_ledger.delta) stays '
    'the truth, and credits_used here is a projection kept for ordering. A '
    'second authority for one number is how two answers to "what do I have '
    'left" appear, and the one nobody reconciles is the one a customer reads.';

COMMENT ON COLUMN credit_lot.price_paid_inr IS
    'NULL means nobody paid — a grant or a trial. ZERO means somebody was '
    'given a paid-shaped lot at no charge. A refund must tell them apart.';
