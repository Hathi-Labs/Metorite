-- 213_pm_activities_seq.sql — the activity spine gets a monotonic order (H-159)
--
-- What: `pm_activities.seq`, a BIGSERIAL, plus two indexes that read it.
-- Why:  two rows written in ONE transaction tie on `created_at`, and the tie
--       was then decided by a random UUID.
-- Depends on: 146_projects.sql (the table). Idempotent.
--
-- ## The tie is not a race, it is a guarantee
--
-- `created_at` defaults to `now()`, and in Postgres `now()` is the TRANSACTION
-- start time, not the statement's. Every activity written inside one
-- transaction therefore carries the identical timestamp. The spine then
-- ordered `created_at DESC, id DESC`, and `id` is `gen_random_uuid()`. So the
-- order of same-transaction rows was decided by a coin toss, every time.
--
-- ## What that cost, in two places
--
-- **`core.py::_coalescible_prior`** asks for "the latest row" and folds a new
-- field change into it when the actor and the field match. An intervening
-- event — a comment, somebody else's edit — is supposed to BREAK that run, so
-- the timeline still shows those things happened in that order. When the
-- intervening row ties with the prior field change, the UUID decides which one
-- comes back. Half the time the intervening row is invisible and two edits
-- that a third event separated get merged into one.
--
-- **`activities.py`'s timeline read** pages with LIMIT/OFFSET over the same
-- ORDER BY. An unstable sort under paging is the classic duplicate-or-skip
-- bug: page 1 and page 2 are two separate queries, and nothing makes them
-- agree about the order of a tied group.
--
-- ⚠️ **Measured 2026-09-23: production has 108 activity rows and ZERO tied
-- groups.** So neither defect has bitten real data yet. That is the argument
-- for adding the column now, while the table is 160 kB and the rewrite is
-- free, rather than after a busy day makes it both real and expensive.
--
-- ## Why a sequence, and not a better timestamp
--
-- `clock_timestamp()` would give each row its own value, and it would still
-- tie under a coarse clock and still let H-7's "now() can move backwards"
-- reorder history. A sequence is monotonic by construction and needs no clock
-- to be trustworthy. It is also what every later reader can page on.
--
-- ⚠️ `created_at` stays the PRIMARY sort key, and that is deliberate. Rows
-- imported with an explicit `created_at` must keep sorting by the time they
-- describe, not by the moment somebody imported them. `seq` only settles the
-- ties, which is the whole defect.

ALTER TABLE pm_activities ADD COLUMN IF NOT EXISTS seq BIGSERIAL;

-- Existing rows are filled by the ADD COLUMN itself, in physical order. That
-- is not insertion order in general, but every one of them predates this
-- migration and their `created_at` already separates them where it can. The
-- column's job starts with the next row written.

-- The two reads this exists for. `DESC` matches the ORDER BY so the index can
-- serve it without a sort, and each carries the target column first because
-- both reads filter on one target before they order.
CREATE INDEX IF NOT EXISTS idx_pm_activities_task_seq
    ON pm_activities (task_id, created_at DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_pm_activities_project_seq
    ON pm_activities (project_id, created_at DESC, seq DESC);

COMMENT ON COLUMN pm_activities.seq IS
    'Monotonic insertion order. Breaks the created_at tie that same-transaction '
    'rows always have, because now() is the transaction timestamp. Read it as '
    'the SECOND sort key, never the first — created_at still carries imported '
    'history. H-159.';
