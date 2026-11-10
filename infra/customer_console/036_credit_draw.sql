-- 036 — each charge records which lots paid for it.
--
-- Spec: `project-docs/specs/operator_console_money.md` section 3.
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 023 learned
-- that the expensive way.
--
-- 🔴 **Why this exists.** The operator asks one question per customer: what
-- did we charge them, and what did their AI cost us? A credit used from a
-- PAID lot is revenue. A credit used from a trial, promotion or grant lot is
-- not. `credit_lot.credits_used` is a LIFETIME total, and `credit_ledger.lot_id`
-- names only the FIRST lot a charge touched. Neither one can say how many
-- PAID credits a customer used in the last 30 days.
--
-- 📌 **One row per lot a charge drew from.** `store.add_credit` writes these
-- rows in the same transaction as the ledger row, from what `draw_from_lots`
-- returned. A charge that no open lot can cover writes one more row with a
-- NULL `lot_id`, for the credits the balance went below zero to pay.
--
-- ⚠️ **The ledger is still the authority.** `SUM(credit_ledger.delta)` is the
-- balance. This table EXPLAINS a charge, the way `credit_lot` explains a grant.
--
-- ⚠️ **Rows begin when this migration applies.** A charge before that has no
-- row, and the operator console labels that part of a window as an estimate.
-- No backfill: a replay of the old draws would be a guess shown as a fact.
--
-- ⚠️ **NO row-level security, for migration 028's reason.** This is the
-- CONTROL plane (`db.py`), cross-tenant by design.
--
-- ⚠️ **The purge KEEPS this table.** It is financial history and it holds no
-- personal data. `_ORG_PURGE_KEEPS_TABLES` in `main.py` names it.
--
-- R6: a new table, expand only, and idempotent. The ladder replays every file.
--
-- Fences (R7): `tests/unit/test_customer_console_credit_draw.py`.

CREATE TABLE IF NOT EXISTS credit_draw (
    id              BIGSERIAL PRIMARY KEY,
    organization_id UUID NOT NULL
                    REFERENCES organization(id) ON DELETE CASCADE,
    -- NULL means no lot covered these credits: the balance went below zero.
    lot_id          BIGINT REFERENCES credit_lot(id),
    credits         NUMERIC(14, 4) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT credit_draw_credits_positive CHECK (credits > 0)
);

-- The window read: one organization, the last N days.
CREATE INDEX IF NOT EXISTS credit_draw_org_time_idx
    ON credit_draw (organization_id, created_at);

COMMENT ON TABLE credit_draw IS
    'One row per lot a usage charge drew from. lot_id NULL = not covered by any lot. Migration 036.';
