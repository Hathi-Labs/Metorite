-- ============================================================================
-- 416_workflow_runs_delivery_key.sql - a replayed webhook delivery starts one
-- run (H-328 part (c), H4 slice 6c, Metorite task 856)
-- ============================================================================
-- Spec: project-docs/specs/workflows_app.md (the H4 slice 6c note) · HANDOFF
-- H-328 · R1 · R5 · R6.
--
-- R1: on 2026-11-12 origin/main held 415 as its highest file. So this file is
--   416. Check the number again at merge (scripts/premerge.sh).
--
-- Why: a sender can deliver one webhook two times. Before this file the hook
--   door kept no delivery id, so a replay started a second run.
--
-- What: `workflow_runs.delivery_key`, TEXT, nullable, no default and no
--   backfill. The hook door (`gateway/routes/workflows/hooks.py`) sets it
--   from the `Idempotency-Key` header, else from `X-CC-Delivery`. NULL means
--   "no key", and a run with no key is never a duplicate.
--   The partial unique index `workflow_runs_delivery_key_uniq` holds one run
--   for each (workflow_id, delivery_key) where the key is set.
--   `service.start_run` writes `INSERT ... ON CONFLICT DO NOTHING`. On a
--   conflict it reads the id of the first run and starts no task.
--
-- Why the index has no `organization_id`: a workflow id is a UUID of one
--   workflow row, and that row has one org. So (workflow_id, delivery_key) is
--   the same key as (organization_id, workflow_id, delivery_key). The ladder
--   adds `workflow_runs.organization_id` only in the generated phases, which
--   run after the numbered files on a fresh database. An index that names
--   the column would fail there. This index works in both shapes.
--
-- Tenancy (R5): no new table, so `test_tenant_coverage.py` changes nothing.
--   `workflow_runs` keeps its FORCE RLS policy. The index is not a policy, so
--   it sees each row. The key names one workflow of one org, so two orgs
--   never share a key.
--
-- Expand only (R6): one nullable column with no default, and one index on
--   that column. The old gateway names each column of its INSERT and never
--   names this one, so it writes NULL, and the partial index skips NULL. The
--   index is new and holds no row at first, so the build is quick.
--   Idempotent: ADD COLUMN IF NOT EXISTS and CREATE UNIQUE INDEX IF NOT
--   EXISTS. A second run changes nothing.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE, and each run
--   writes this table. So the file sets `lock_timeout` first, as 401 does.
--   On a lock timeout the runner tries the whole file again.
--
-- Fence: tests/unit/test_workflows_triggers_r8.py (R8).
--
-- Depends on: 132_workflows.sql (the table).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS delivery_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS workflow_runs_delivery_key_uniq
    ON workflow_runs (workflow_id, delivery_key)
    WHERE delivery_key IS NOT NULL;

COMMENT ON COLUMN workflow_runs.delivery_key IS
    'H-328 (c): the delivery id of a webhook run. NULL = no key, no dedupe.';

COMMIT;
