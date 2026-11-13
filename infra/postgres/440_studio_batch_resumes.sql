-- ============================================================================
-- 440_studio_batch_resumes.sql — the resume count of a batch (WS-STU E8b,
-- task 3096).
--
-- What: one new column, `studio_batches.resumes`. It counts the new runs that
--       the batch scan started for the batch. The scan rebinds `run_id` from
--       the failed run to the new run with a compare-and-set, and the same
--       UPDATE adds 1 to `resumes` only while it is under the bound
--       (gateway/routes/workflows/batch.py MAX_RESUMES). After the bound, the
--       scan ends the item that blocks the batch, and the batch ends `failed`.
-- Why:  ai_studio.md §14 E8b, "a bound on the number of resumes". The run_id
--       of a batch changes at each resume, so no other row keeps the count.
-- Depends on: 438_studio_batches.sql.
--
-- Expand only (R6): one new column with a constant default, and one CHECK on
-- it. Old code never names the column, so an INSERT of old code gets 0.
-- Idempotent: ADD COLUMN IF NOT EXISTS, and the CHECK is added only when it is
-- missing. The table is already tenant-scoped with FORCE RLS (438), and a new
-- column changes no policy.
--
-- The number is taken at build time (R1). Check it again at merge.
-- ============================================================================

BEGIN;

ALTER TABLE studio_batches
    ADD COLUMN IF NOT EXISTS resumes INTEGER NOT NULL DEFAULT 0;

DO $chk$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'studio_batches_resumes'
           AND conrelid = 'studio_batches'::regclass
    ) THEN
        ALTER TABLE studio_batches
            ADD CONSTRAINT studio_batches_resumes CHECK (resumes BETWEEN 0 AND 100);
    END IF;
END
$chk$;

COMMENT ON COLUMN studio_batches.resumes IS
    'The new runs that the batch scan started for this batch (WS-STU E8b). '
    'The scan adds 1 in the compare-and-set of the run_id, under a bound.';

COMMIT;
