-- ============================================================================
-- 003_metering_integrity.sql — two integrity holes found by CP-3 verification
-- ============================================================================
-- Spec: customer_console.md §3.4 · found by independent verification of
-- CP-3 (findings F1 and F3), both MEASURED against a live database rather than
-- reasoned about.
--
-- (F3) `request_id` was UNIQUE **globally**. With per-organization key auth on
--      the metering path that is a cross-tenant defect, demonstrated:
--
--          org A posts request_id "req-shared" with 0 credits
--          org B posts the SAME request_id with 500 credits
--          -> B gets {recorded: false}; only A's row exists, at 0 credits
--
--      Org B's charge is silently dropped, and `recorded:false` becomes an
--      existence oracle for another tenant's request ids. Idempotency is a
--      property WITHIN one organization — a request id from customer A says
--      nothing about customer B — so the key must be (organization_id,
--      request_id).
--
-- (F1) `billed_credits` had no floor. A negative value became a POSITIVE
--      ledger delta (store.record_usage negates it), i.e. a caller could mint
--      credits. Measured: billed_credits=-100000 moved a balance from 989 to
--      100989. The application now rejects it at the schema boundary; this
--      CHECK is the structural fence behind that, per R7's preference for
--      fences that hold over validation that can be bypassed by the next
--      call site.
--
-- Idempotent, and safe to replay.
-- ============================================================================

-- ── F3: idempotency is per organization, not global ─────────────────────────

-- Expand first: the new index exists before the old constraint goes, so a
-- replay that stops between the two statements still has uniqueness enforced.
CREATE UNIQUE INDEX IF NOT EXISTS usage_event_org_request_uniq
    ON usage_event (organization_id, request_id);

-- Then contract. Named lookup rather than a bare DROP: the constraint is
-- auto-named by Postgres and differs if the table was created by an older
-- revision of 001, so resolve it from the catalog instead of guessing.
DO $$
DECLARE
    conname TEXT;
BEGIN
    SELECT c.conname INTO conname
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE t.relname = 'usage_event'
      AND c.contype = 'u'
      AND (SELECT count(*) FROM unnest(c.conkey)) = 1
      AND EXISTS (
          SELECT 1 FROM pg_attribute a
          WHERE a.attrelid = t.oid
            AND a.attnum = c.conkey[1]
            AND a.attname = 'request_id'
      );
    IF conname IS NOT NULL THEN
        -- `quote_ident` rather than a format() identifier placeholder: psycopg
        -- parses per-cent placeholders in the statement text it is handed, even
        -- inside a DO block, and rejects the identifier form. psql accepts it,
        -- so that version passes by hand and fails from the application — the
        -- same trap as a per-cent sign in a comment (see 001). Migrations here
        -- must run under BOTH, so this file contains no per-cent character at
        -- all, including in prose.
        EXECUTE 'ALTER TABLE usage_event DROP CONSTRAINT '
                || quote_ident(conname);
    END IF;
END $$;


-- ── F1: a metered call can never be worth negative money ────────────────────

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usage_event_billed_credits_nonneg'
    ) THEN
        -- NOT VALID + a separate VALIDATE, per R6: the check is enforced for
        -- new rows immediately without taking a lock long enough to matter on
        -- a populated table. There is no existing data to violate it, but the
        -- pattern is the one migration 148 established and is worth keeping
        -- uniform.
        ALTER TABLE usage_event
            ADD CONSTRAINT usage_event_billed_credits_nonneg
            CHECK (billed_credits >= 0) NOT VALID;
        ALTER TABLE usage_event
            VALIDATE CONSTRAINT usage_event_billed_credits_nonneg;
    END IF;
END $$;

-- Token counters likewise: a negative count is meaningless and would corrupt
-- any rollup that sums them.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'usage_event_tokens_nonneg'
    ) THEN
        ALTER TABLE usage_event
            ADD CONSTRAINT usage_event_tokens_nonneg
            CHECK (prompt_tokens >= 0 AND completion_tokens >= 0
                   AND cached_tokens >= 0) NOT VALID;
        ALTER TABLE usage_event
            VALIDATE CONSTRAINT usage_event_tokens_nonneg;
    END IF;
END $$;


-- ── F5: run attribution was captured and thrown away ────────────────────────
--
-- `X-CC-Run` was read into the caller context and then dropped on the floor:
-- there was nowhere to put it. D1's attribution four-tuple is (run, member,
-- agent, instance), and without the run id a completion cannot be rolled up per
-- agent run — which is the unit an operator actually debugs.
--
-- Nullable with no default, per R6: old code that never sends it keeps working.
ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS run_id TEXT;

CREATE INDEX IF NOT EXISTS usage_event_run_idx
    ON usage_event (organization_id, run_id) WHERE run_id IS NOT NULL;
