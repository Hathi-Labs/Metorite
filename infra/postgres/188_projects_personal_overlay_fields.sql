-- 188_projects_personal_overlay_fields.sql — the last per-member fields the
-- Tasks lens needs before it can leave `gtd_items`.
--
-- Spec:  project-docs/specs/task_manager_app.md §13
--        project-docs/specs/project_management_app.md §12
-- Board: WS-39 slice S3a-server-2 · decision D53 (one task store)
-- Why:   H-33 named a prerequisite in terms — "fields with no `pm_*` home yet
--        ... decide those BEFORE writing the mapper, or they become silent data
--        loss at the cutover". This migration is that decision. Migration 187
--        gave the overlay the scheduled block; this gives it the remaining
--        NINE facts `GtdItem` carries and `pm_*` cannot yet hold.
--
-- ⚠️ `clarified_at` was the tenth and is DELIBERATELY ABSENT — it already
-- exists, from migration 147, and adding it again would have been a no-op that
-- left this file's header lying about what it does. Caught against a real
-- database (R8) rather than by reading the migration that created the table.
-- It is a WIRING gap, not a schema gap, and the distinction matters because the
-- two have different fixes: `set_personal` WRITES it (`personal.py:433`, on the
-- transition out of INBOX) but nothing projects it back — it is absent from
-- `_personal_to_dict` and from `_MY_TASKS_SQL`. So the column has been quietly
-- collecting a real value that no caller can read. The server half of this
-- slice projects it; no migration was needed.
--
-- Number 188 taken at build time against `ls infra/postgres/[0-9]*.sql` and
-- re-checked against all 137 remote branches (R1) — RE-CHECK AT MERGE.
--
-- ── Why every one of these is PER MEMBER ─────────────────────────────────────
--
-- Same argument as 147 (disposition) and 187 (the block), applied to what is
-- left. Each of these is a fact about how ONE PERSON is holding the task, not
-- about the work:
--
--   * the matrix flags are a member's own judgement of their own time;
--   * "I am waiting on Priya" is true for the delegator and false for the
--     doer of the very same row;
--   * `clarified_at` is when *I* processed it out of *my* inbox — a second
--     assignee's inbox is untouched by that;
--   * `sort_key` is my manual drag order in my own list.
--
-- Put any of them on `pm_tasks` and the second assignee to touch the task
-- silently overwrites the first, which is precisely the failure D53.7 was
-- recorded to prevent.
--
-- ── `important` is NOT `pm_tasks.importance`, and this is the trap ───────────
--
-- `pm_tasks.importance` already exists and is an INTEGER the Projects UI labels
-- **"Priority"** — a shared, per-task ranking everyone on the task sees.
-- `GtdItem.important` is the BOOLEAN axis of the Eisenhower matrix, paired with
-- an `urgent` that is *derived* from `due_at` and never stored.
--
-- Two different facts with confusingly similar names. Mapping one onto the
-- other — say, `important = importance >= 3` — would invent a threshold no
-- decision records, make one member's private triage visible to everyone on the
-- task, and quietly change what the Projects "Priority" column means. So the
-- boolean gets its own home here and `pm_tasks.importance` is left alone.
--
-- ── Waiting-For: four columns, not a second table ────────────────────────────
--
-- `gtd_waiting` is a child table allowing many rows per item with a `resolved`
-- flag — a delegation HISTORY. Measured 2026-08-25 before choosing this shape:
-- **every reader in the tree filters `resolved = false`**.
-- `routes/tasks/core.py:479` joins `AND w.resolved = false`, and the only other
-- consumer (`routes/tasks/ai.py:1645`, the stale-delegation count) does the
-- same. `GtdItem` correspondingly exposes exactly one of each field, not a
-- list. So the history has no reader, and collapsing the open record onto the
-- overlay loses nothing that is displayed.
--
-- Recorded so a later session does not read this as an oversight: resolved
-- delegation history is NOT carried across by this design. If someone later
-- wants "how many times have I chased this", that is a new feature with a new
-- home, not a restoration.
--
-- ── R6 expand/contract ───────────────────────────────────────────────────────
--
-- Every column NULLABLE with no default, exactly as 187. Two consequences, both
-- wanted: the ALTER is instant on a populated table (no rewrite, no default to
-- backfill), and NULL stays readable as "never stated". The four booleans are
-- `NOT NULL DEFAULT false` on `gtd_items`; they are deliberately NOT that here,
-- because a member who has never opened the matrix and a member who considered
-- the task and said "not important" are different states, and the one place
-- that difference is load-bearing is the nudge that asks people to triage.
-- Nothing is renamed, nothing is dropped.

ALTER TABLE pm_task_personal
    -- ── The prioritisation matrix (task_manager_app.md §4) ──────────────────
    -- The Eisenhower boolean. NOT `pm_tasks.importance` — see the header.
    ADD COLUMN IF NOT EXISTS important      BOOLEAN,

    -- "Does doing this multiply my other work?" A judgement about the member's
    -- own leverage, which is why two people can disagree about one task.
    ADD COLUMN IF NOT EXISTS leveraged      BOOLEAN,

    -- Needs an unbroken FLOW state. The planner protects a long peak-energy
    -- block for it and Focus Mode defaults to a longer timer — both of which
    -- act on MY calendar, so the flag belongs beside MY block (187).
    ADD COLUMN IF NOT EXISTS deep_work      BOOLEAN,

    -- The member dismissed the delegate/schedule suggestion ("this one is
    -- mine"). Suppressing a suggestion is per-person by definition: it must not
    -- stop a colleague being offered the same help on the same task.
    ADD COLUMN IF NOT EXISTS kept_mine      BOOLEAN,

    -- Manual drag rank within the member's own list. DOUBLE PRECISION, matching
    -- `gtd_items.sort_key`: inserting between two neighbours must not renumber
    -- the list, and a float lets a drop take the midpoint.
    ADD COLUMN IF NOT EXISTS sort_key       DOUBLE PRECISION,

    -- ── Waiting-For (task_manager_app.md §6) ────────────────────────────────
    -- {name, email} of whoever the work is with. Shape matches
    -- `gtd_waiting.waiting_on` so the S3b backfill is a copy, not a transform.
    ADD COLUMN IF NOT EXISTS waiting_on     JSONB,

    -- When it left my hands — the "since-when" of §1's who/what/since-when.
    ADD COLUMN IF NOT EXISTS delegated_at   TIMESTAMPTZ,

    -- The date I was PROMISED it by. Deliberately distinct from
    -- `pm_tasks.due_at`: my deadline and the date they gave me are two facts,
    -- and conflating them is how a chase goes out on the wrong day.
    ADD COLUMN IF NOT EXISTS expected_by    TIMESTAMPTZ,

    -- When a follow-up last went out. Reading it is what stops a double-chase;
    -- the nudge SENDING path is owner-gated and unbuilt, so this stays NULL
    -- until that ships. A column with no writer yet is not dead — it is the
    -- half of the feature that can land safely.
    ADD COLUMN IF NOT EXISTS last_nudged_at TIMESTAMPTZ;

-- ── One index, for the one query that needs it ───────────────────────────────
--
-- The Waiting-For list is "what am I waiting on, soonest promise first", and
-- the overdue badge is the same read with `expected_by < now()`. So the index
-- is (member_email, expected_by) and PARTIAL on `waiting_on IS NOT NULL`:
-- delegated tasks are a small minority of any member's overlay rows, and a full
-- index would carry every ordinary task to serve a list that can never contain
-- them.
--
-- Deliberately NOT added: anything on the four matrix booleans. They are filter
-- predicates over an already-small per-member row set, they have low
-- cardinality, and 187's note applies unchanged — an index nothing seeks on
-- still costs every write.
CREATE INDEX IF NOT EXISTS idx_pm_task_personal_waiting
    ON pm_task_personal (member_email, expected_by)
    WHERE waiting_on IS NOT NULL;

-- ── "Waiting on somebody since never" is not a state ─────────────────────────
--
-- On `gtd_waiting` both columns are NOT NULL together, and that pairing is
-- load-bearing rather than incidental: the Waiting-For view's whole job is
-- who / what / SINCE WHEN, and a row missing the since-when renders as a chase
-- with no age. Expressed as a CHECK rather than two NOT NULLs because the pair
-- is optional — most rows have neither.
--
-- Lands NOT VALID and is validated in a guarded block (R6; migrations 148 and
-- 187 are the reference). Every existing row satisfies it vacuously — both
-- columns are NULL on every row this migration just created — so validation is
-- expected to be instant. It is still guarded, because "expected to be instant"
-- is how a lock surprise reaches a deploy.
DO $mig188$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'pm_task_personal_waiting_since_check'
    ) THEN
        ALTER TABLE pm_task_personal
            ADD CONSTRAINT pm_task_personal_waiting_since_check
            CHECK (waiting_on IS NULL OR delegated_at IS NOT NULL) NOT VALID;
    END IF;
END $mig188$;

DO $mig188$
BEGIN
    BEGIN
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE pm_task_personal
            VALIDATE CONSTRAINT pm_task_personal_waiting_since_check;
    EXCEPTION WHEN lock_not_available OR insufficient_privilege THEN
        -- Still binds every INSERT and UPDATE from here on; only the backfill
        -- check is deferred. Re-run VALIDATE out of band rather than blocking a
        -- deploy on a lock.
        RAISE NOTICE 'pm_task_personal_waiting_since_check left NOT VALID; validate out of band';
    END;
END $mig188$;

COMMENT ON COLUMN pm_task_personal.important IS
    'Eisenhower IMPORTANT axis, per member (D53, WS-39 S3a). NOT the same as '
    'pm_tasks.importance, which is the shared per-task Priority integer.';
COMMENT ON COLUMN pm_task_personal.waiting_on IS
    'The OPEN Waiting-For record for this member, {name,email} (WS-39 S3a). '
    'Replaces the open row of gtd_waiting; resolved delegation history is '
    'deliberately not carried - no reader consumed it.';
COMMENT ON COLUMN pm_task_personal.sort_key IS
    'Manual drag rank in THIS member list. Float so an insert between two '
    'neighbours takes the midpoint instead of renumbering the list.';
