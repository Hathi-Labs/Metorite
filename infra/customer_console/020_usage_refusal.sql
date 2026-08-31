-- 020 — why we refused, recorded in the meter.
--
-- Spec: `project-docs/specs/ai_metering_and_analytics.md` §8.1 (slice 5, A5).
--
-- 🔴 **`usage_event` records a call that happened, and a refusal happens too.**
-- A customer at a wall writes no row today, so A5 ("is a customer hitting a
-- wall") has nothing to read and three refusal shapes leave no trace at all.
-- This column is the trace. NULL means the call served.
--
-- ⚠️ **The CHECK closes the vocabulary, and that is the point.** An open TEXT
-- column grows a second spelling of one wall within a month, and the two then
-- read as two different walls. Two of the three slugs are copied WORD FOR WORD
-- from the body the customer already reads (`credits.py` for the 402,
-- `main.py::_spend_refusal` for the 403). `tier_unknown` is minted by §8.1.
--
-- ⚠️ **Only a CUSTOMER wall gets a slug.** Our own failures — the two 503s and
-- the 502 — write no usage row at all. One table that mixes a customer wall
-- with a broken vendor answers neither question. The 401 CANNOT write a row:
-- `auth.py` refuses before the organization is known, and
-- `usage_event.organization_id` is NOT NULL (001:256).
--
-- R6: additive, nullable, no rename and no rewrite. Old code meets this schema
-- mid-deploy and keeps inserting; every existing row reads NULL, which is true
-- of them — they all served.
--
-- Fences (R7): `tests/unit/test_customer_console_sql.py` (the CHECK, the five
-- counting reads, `last_seen_by_org`, `run_spend`) and
-- `tests/unit/test_customer_console_router.py` (the HTTP path — a refusal
-- SURVIVES the raise).

ALTER TABLE usage_event
    ADD COLUMN IF NOT EXISTS refusal_reason TEXT;

-- ⚠️ A guarded `DO $$`, because `ALTER TABLE … ADD CONSTRAINT` has no
-- `IF NOT EXISTS` and this ladder is replayed on every start (013 makes the
-- same move for `served_rank`).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usage_event_refusal_reason_known'
    ) THEN
        ALTER TABLE usage_event ADD CONSTRAINT usage_event_refusal_reason_known
            CHECK (
                refusal_reason IS NULL
                OR refusal_reason IN (
                    'insufficient_credits',
                    'run_ceiling_exceeded',
                    'tier_unknown'
                )
            );
    END IF;
END $$;

COMMENT ON COLUMN usage_event.refusal_reason IS
    'Why we refused this call, from a closed vocabulary of three slugs. NULL '
    'means the call served, and every read that COUNTS calls excludes a row '
    'where this is set. A refusal bills 0 credits, so the credit sums need no '
    'filter. It does move last_seen_by_org on purpose: a customer at a wall '
    'is a customer who is trying, and filtering it there makes them read as '
    'silent.';
