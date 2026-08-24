-- 187_projects_personal_timeboxing.sql — the scheduled block is PER MEMBER.
--
-- Spec:  project-docs/specs/calendar_focus_os.md §10 · §11
--        project-docs/specs/project_management_app.md §12
-- Board: WS-39 slice S3a · decisions D53 (one task store) + D54 (Calendar app)
-- Why:   D53 makes `pm_tasks` + `pm_task_personal` the one task store and gives
--        it three lenses — Projects, Tasks, Calendar. The Calendar lens cannot
--        move off `gtd_items` until this store can answer "when am I doing
--        this", and today it cannot: `pm_tasks` carries `start_date` (a DATE)
--        and `due_at` (a deadline), and neither is a scheduled block.
--
-- Number 187 taken at build time against `ls infra/postgres/[0-9]*.sql`
-- (R1) — RE-CHECK AT MERGE.
--
-- ── The decision this migration encodes ──────────────────────────────────────
--
-- **A scheduled block belongs to the MEMBER, not to the task**, so these
-- columns land on the overlay rather than on `pm_tasks`.
--
-- It is the same argument that made `disposition` an overlay in migration 147,
-- and it is not a stylistic echo — it is the same fact. Two people assigned one
-- task legitimately hold different dispositions (the doer says NEXT, the
-- delegator says WAITING), and for exactly the same reason they hold different
-- *calendars*: they each block their own Tuesday afternoon for their half of
-- the work. A `scheduled_start` on `pm_tasks` would let one person's calendar
-- overwrite another's, silently, with no way for either to notice — and the
-- second assignee's block would simply disappear the moment the first scheduled
-- theirs.
--
-- The deadline is the opposite case and stays where it is: `pm_tasks.due_at` is
-- a fact about the WORK, one per task, and everybody assigned to it shares it.
-- Two axes, two homes, no third.
--
-- ── R6 expand/contract ───────────────────────────────────────────────────────
--
-- Every column is added NULLABLE with no default and no constraint over
-- existing rows, so the deploy's migrate-then-restart window is safe: code
-- currently running has never heard of these columns, and code that arrives
-- later reads NULL as "not scheduled". Nothing is renamed and nothing is
-- dropped. `flexible` is deliberately NULLABLE rather than `NOT NULL DEFAULT
-- true` — see its comment.

ALTER TABLE pm_task_personal
    -- The block itself: when THIS member intends to do the work. Both NULL =
    -- unscheduled, which is the state every existing row is in and the reason
    -- the calendar's "Unscheduled" rail exists.
    ADD COLUMN IF NOT EXISTS scheduled_start  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS scheduled_end    TIMESTAMPTZ,

    -- Whether the packer may MOVE this block when it re-plans the day.
    --
    -- ⚠️ Nullable on purpose, and NULL is not the same as `true`. The reader
    -- resolves NULL to "flexible" — matching migration 79's default on the
    -- store this replaces — but storing that default here would erase the
    -- difference between "the member pinned this as movable" and "nobody has
    -- said". The packer does not care today; the Ideal Week work (WS-21) does,
    -- because "never stated" is what it is allowed to fill in.
    ADD COLUMN IF NOT EXISTS flexible         BOOLEAN,

    -- A deadline the member has marked immovable *for themselves*. Distinct
    -- from `pm_tasks.due_at`, which is the project's date: this says "I am
    -- treating my copy of this as hard", which is a scheduling stance, not a
    -- change to the work.
    ADD COLUMN IF NOT EXISTS is_hard_date     BOOLEAN,

    -- What actually happened, as opposed to what was planned. The Shutdown
    -- ritual's leverage ratio is computed from these, and keeping them beside
    -- the plan is what lets "planned 90m, took 150m" be a fact rather than a
    -- recollection.
    ADD COLUMN IF NOT EXISTS actual_start     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS actual_end       TIMESTAMPTZ;

-- ── The one index, and why only one ──────────────────────────────────────────
--
-- The calendar's every read is "MY blocks between these two instants", so the
-- index is (member_email, scheduled_start) and it is PARTIAL: unscheduled rows
-- are the overwhelming majority of this table and none of them can ever match a
-- range query. A full index would carry every triaged-but-unscheduled task for
-- nothing.
--
-- Deliberately NOT added: an index on `scheduled_end`. No read filters on it
-- alone — a range scan seeks on start and filters end from the heap — and an
-- index nothing uses still costs every write.
CREATE INDEX IF NOT EXISTS idx_pm_task_personal_scheduled
    ON pm_task_personal (member_email, scheduled_start)
    WHERE scheduled_start IS NOT NULL;

-- ── Ordering invariant, NOT VALID ────────────────────────────────────────────
--
-- A block that ends before it starts is not a bad row, it is an impossible one,
-- and the calendar's layout maths divides by the duration. But R6 forbids
-- validating over existing data inside the deploy window, so this lands
-- NOT VALID (migration 148 is the reference) and is validated in a guarded
-- block. Every existing row satisfies it vacuously — both columns are NULL and
-- a CHECK is not violated by NULL — so the validation is expected to be
-- instant; it is still guarded, because "expected to be instant" is how a lock
-- surprise gets shipped.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'pm_task_personal_block_order_check'
    ) THEN
        ALTER TABLE pm_task_personal
            ADD CONSTRAINT pm_task_personal_block_order_check
            CHECK (
                scheduled_end IS NULL
                OR scheduled_start IS NULL
                OR scheduled_end > scheduled_start
            ) NOT VALID;
    END IF;
END $$;

DO $$
BEGIN
    BEGIN
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE pm_task_personal
            VALIDATE CONSTRAINT pm_task_personal_block_order_check;
    EXCEPTION WHEN lock_not_available OR insufficient_privilege THEN
        -- Leaving it NOT VALID is correct and safe: it still binds every INSERT
        -- and UPDATE from this point on, and only the backfill check is
        -- deferred. Re-run the VALIDATE out of band rather than blocking a
        -- deploy on a lock.
        RAISE NOTICE 'pm_task_personal_block_order_check left NOT VALID; validate out of band';
    END;
END $$;

COMMENT ON COLUMN pm_task_personal.scheduled_start IS
    'When THIS member intends to do the work (D53/D54, WS-39 S3a). Per-member '
    'by design: two assignees block their own time. The deadline is shared and '
    'lives on pm_tasks.due_at.';
COMMENT ON COLUMN pm_task_personal.flexible IS
    'May the packer move this block? NULL = never stated (resolved as flexible '
    'by the reader), which is deliberately distinct from an explicit true.';
