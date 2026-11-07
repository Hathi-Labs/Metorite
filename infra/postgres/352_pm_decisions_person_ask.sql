-- ============================================================================
-- 352_pm_decisions_person_ask.sql — a member asks a member (WS-AP AP-8a,
-- Metorite task 1858).
--
-- What: four changes to `pm_decisions` (332).
--       * The CHECK `pm_decisions_source` takes `person` as well as `align`
--         and `auto`. A person ask is a decision that a member opened.
--       * Three nullable columns with no default: `due_at` (the date the
--         asker needs the answer by), `closed_at` and `closed_reason`.
--       * Two CHECKs: `closed_reason` is `withdrawn` or `task_closed`, and
--         the pair `closed_at` and `closed_reason` is whole or absent.
--       * The guard function `pm_decisions_guard_update` keeps every refusal
--         of 332 and adds these: `due_at` cannot change, a close happens
--         once, a closed ask takes no answer, and one UPDATE does not both
--         answer and close.
-- Why:  project-docs/specs/waiting_on_me.md §7.1 and §7.2. The ask route of
--       `routes/projects/decisions.py` writes the rows of source `person`.
-- Depends on: 332_pm_decisions.sql.
--
-- Expand only (R6). The CHECK of `source` widens: old code writes `align` or
-- `auto`, and both still pass. The three columns are nullable with no
-- default, so old code that names none of them inserts NULL, and each new
-- CHECK passes on NULL. The trigger refuses nothing that old code does: old
-- code never writes the three columns.
-- Idempotent per infra/postgres/README.md: DROP CONSTRAINT IF EXISTS then
-- ADD, ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE FUNCTION. VALIDATE of a
-- valid CHECK changes nothing on a second run.
-- Each CHECK is added NOT VALID and then validated, as 342 does. The ALTER
-- TABLE takes the ACCESS EXCLUSIVE lock and the file holds it until COMMIT,
-- so NOT VALID only splits the work in two. The table is small. The file sets
-- lock_timeout first, and the runner tries the whole file again on a lock
-- timeout.
--
-- Tenant-scoped (R5): no new table. The RLS and the policy of 332 cover the
-- new columns.
--
-- No score for any person (ALN-Q5, V6). No new index. The open list of one
-- person is a filter on a small table. The rate guard of the ask route reads
-- one row at an offset, and it counts nothing.
--
-- The number is taken at build time (R1). On 2026-11-07 origin/main held
-- 348, and open pull requests held 349, 350 and 351. Check it again at merge.
-- Pinned by tests/unit/test_person_asks_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- A member asks a member: the source `person`.
ALTER TABLE pm_decisions DROP CONSTRAINT IF EXISTS pm_decisions_source;
ALTER TABLE pm_decisions
    ADD CONSTRAINT pm_decisions_source
    CHECK (source IN ('align', 'auto', 'person')) NOT VALID;
ALTER TABLE pm_decisions VALIDATE CONSTRAINT pm_decisions_source;

-- The date of the asker, and the close of an ask.
ALTER TABLE pm_decisions ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ NULL;
ALTER TABLE pm_decisions ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ NULL;
ALTER TABLE pm_decisions ADD COLUMN IF NOT EXISTS closed_reason TEXT NULL;

ALTER TABLE pm_decisions DROP CONSTRAINT IF EXISTS pm_decisions_closed_reason;
ALTER TABLE pm_decisions
    ADD CONSTRAINT pm_decisions_closed_reason
    CHECK (closed_reason IS NULL OR closed_reason IN ('withdrawn', 'task_closed')) NOT VALID;
ALTER TABLE pm_decisions VALIDATE CONSTRAINT pm_decisions_closed_reason;

-- The close is whole or absent.
ALTER TABLE pm_decisions DROP CONSTRAINT IF EXISTS pm_decisions_close_whole;
ALTER TABLE pm_decisions
    ADD CONSTRAINT pm_decisions_close_whole
    CHECK ((closed_at IS NULL) = (closed_reason IS NULL)) NOT VALID;
ALTER TABLE pm_decisions VALIDATE CONSTRAINT pm_decisions_close_whole;

-- One answer, one close, and no change of what was asked. For each role, the
-- table owner too. The refusals of 332 stay as they were. Fence:
-- tests/unit/test_decisions.py and tests/unit/test_person_asks_r8.py.
CREATE OR REPLACE FUNCTION pm_decisions_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF OLD.answered_at IS NOT NULL THEN
        RAISE EXCEPTION 'pm_decisions: a decision takes one answer'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.subject_kind IS DISTINCT FROM OLD.subject_kind
       OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
       OR NEW.decider IS DISTINCT FROM OLD.decider
       OR NEW.options IS DISTINCT FROM OLD.options
       OR NEW.question IS DISTINCT FROM OLD.question
       OR NEW.asked_by IS DISTINCT FROM OLD.asked_by
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.due_at IS DISTINCT FROM OLD.due_at THEN
        RAISE EXCEPTION 'pm_decisions: what was asked cannot change'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.answered_at IS NOT NULL AND NEW.answered_at > OLD.expires_at THEN
        RAISE EXCEPTION 'pm_decisions: a decision takes no answer after it expires'
            USING ERRCODE = 'check_violation';
    END IF;
    -- AP-8a: a closed ask stays closed, and it takes no answer.
    IF OLD.closed_at IS NOT NULL THEN
        IF NEW.closed_at IS DISTINCT FROM OLD.closed_at
           OR NEW.closed_reason IS DISTINCT FROM OLD.closed_reason THEN
            RAISE EXCEPTION 'pm_decisions: an ask closes once'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.answered_at IS NOT NULL THEN
            RAISE EXCEPTION 'pm_decisions: a closed ask takes no answer'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.closed_at IS NOT NULL AND NEW.answered_at IS NOT NULL THEN
        RAISE EXCEPTION 'pm_decisions: an ask is answered or closed, not both'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

COMMIT;
