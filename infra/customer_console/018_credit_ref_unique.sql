-- 018_credit_ref_unique.sql — the duplicate-reference fence gets teeth.
--
-- `POST /credits/grant` refuses a second grant citing the same
-- (reason, ref) with a SELECT-then-INSERT. That check cannot hold under
-- concurrency: two grants both read "no prior row" and both insert — the
-- same READ COMMITTED race `store.lock_org_activation` documents for the
-- activation door. One bank transfer typed twice concurrently credited
-- twice, and the operator found out from the ledger.
--
-- This partial unique index makes the SECOND insert fail instead. The
-- grant route converts that IntegrityError into the same 409 the
-- sequential repeat has always answered.
--
-- Partial on `ref IS NOT NULL`: rows with no reference — metering draws,
-- corrections without one — stay unconstrained. The fence is about a
-- HUMAN-TYPED reference naming one bank transfer. It matches the route's
-- shipped semantics exactly: same (org, reason, ref) refused, the same
-- ref under a DIFFERENT reason (an `adjustment` correcting a `manual`
-- row) allowed.
--
-- ⚠️ If this CREATE fails on an existing database, duplicates already
-- exist — that is the double-credit defect materialised, and it must be
-- resolved by a human correction (an `adjustment` row), never by this
-- file deleting ledger rows. Production has no grants yet (Console not
-- deployed), so the ladder applies clean there.
--
-- R6: additive, no rename, no rewrite. Fence:
-- tests/unit/test_customer_console_manual_credits.py (the database-edge
-- tests added with this migration).

CREATE UNIQUE INDEX IF NOT EXISTS credit_ledger_reason_ref_unique
    ON credit_ledger (organization_id, reason, ref)
    WHERE ref IS NOT NULL;
