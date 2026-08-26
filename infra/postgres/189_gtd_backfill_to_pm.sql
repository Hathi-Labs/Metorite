-- ============================================================================
-- 189_gtd_backfill_to_pm.sql — WS-39 S3b. The `gtd_items` → `pm_*` data move.
--
-- What: D53.5 release (2). Every surviving `gtd_items` row becomes an ordinary
--   `pm_tasks` row in its owner's personal project, with the GTD overlay on
--   `pm_task_personal` where D53.7/D53.8 put it.
--
-- ⚠️ THIS MIGRATION MOVES NO DATA WHEN IT IS APPLIED. It only:
--     (A) adds `gtd_items.migrated_task_id`  — the audit pointer + idempotency key
--     (B) creates `gtd_retirement_arm`, EMPTY — S3c's safety catch
--     (C) defines `gtd_backfill_to_pm(p_apply boolean)` — the move itself
--     (D) defines `gtd_backfill_plan` — a read-only preview of the mapping
--   The function is DEFINED, never CALLED. That is deliberate and it is the
--   whole reason this file is safe to ship: running the move against a real
--   database is OWNER-GATE (`work_plan.md` §6 (f), registered by D53.5), and a
--   migration that called it would execute that gate from the deploy ladder —
--   the ladder applies migrations before restarting services (R6), unattended.
--   So the deploy makes the move POSSIBLE and a human makes it HAPPEN:
--
--       SELECT * FROM gtd_backfill_plan;            -- what would move, and where
--       SELECT * FROM gtd_backfill_to_pm(false);    -- dry run, writes nothing
--       SELECT * FROM gtd_backfill_to_pm(true);     -- apply
--
-- Why the mapping is a function and not a guess. `gtd_items.user_id` holds an
--   email (`routes/tasks/core.py::_uid` — `user.email or "anonymous"`), and
--   `app_user.email` is GLOBALLY unique (`app_user_email_lower_key`, D-MT-1(a)),
--   so an email resolves to exactly one person in exactly one organization.
--   That is the property this whole file rests on, and §12.8 names the failure
--   it prevents: a mis-mapped `member_email` does not lose a task, it PUBLISHES
--   one person's private task into somebody else's lens.
--
-- ⚠️ There is NO RLS safety net here. `pm_*` policies are bound at the
--   `get_db()` seam for the application; a migration runs as the database owner
--   and bypasses them. Every tenant predicate in this file is therefore written
--   out explicitly, and `organization_id` is always resolved from `app_user`,
--   never inferred from a project, a status or a caller.
--
-- What is REFUSED rather than guessed. A row whose `user_id` matches no
--   `app_user` — including the literal `'anonymous'` that `_uid` writes for an
--   unauthenticated capture — is NOT moved, NOT deleted, and NOT assigned to
--   anybody. It is reported by `gtd_backfill_plan` as `unmappable`. S3c then
--   refuses to drop the table while any such row exists, so the failure mode is
--   a blocked drop that someone must look at, never a silently discarded task.
--
-- Idempotent per infra/postgres/README.md, in both senses: applying the
--   migration twice is a no-op, and `gtd_backfill_to_pm(true)` may be re-run
--   safely — `migrated_task_id` makes an already-moved row invisible to it.
--   That matters operationally: the owner can move the bulk, flip the flags,
--   and re-run to sweep anything written in between.
--
-- Depends on: 48_task_manager_gtd.sql (gtd_items, gtd_waiting, gtd_projects),
--   146_projects.sql (pm_tasks, pm_task_counters), 147_projects_personal.sql
--   (personal_owner, pm_task_personal), 161_projects_tenancy.sql
--   (organization_id), 187 + 188 (the overlay columns this writes).
-- Pinned by: tests/unit/test_gtd_backfill.py (reads this file as TEXT) and
--   tests/live/live_ws39_s3b.sql (the two-org proof §12.8 demands).
-- ============================================================================


-- ── (A) The audit pointer ───────────────────────────────────────────────────
--
-- Nullable, no default, no FK cascade to worry about: it is a one-way pointer
-- from the retiring row to the row that replaced it. It is doing three jobs —
-- idempotency (the backfill skips what it already moved), auditability (an
-- operator can join old to new for as long as both exist), and S3c's
-- precondition (nothing is dropped until every row has one).

ALTER TABLE gtd_items
    ADD COLUMN IF NOT EXISTS migrated_task_id UUID;

COMMENT ON COLUMN gtd_items.migrated_task_id IS
    'WS-39 S3b: the pm_tasks row this became. NULL = not yet moved. '
    'S3c refuses to drop gtd_items while any row is NULL.';

CREATE INDEX IF NOT EXISTS idx_gtd_items_unmigrated
    ON gtd_items (user_id) WHERE migrated_task_id IS NULL;


-- ── (B) S3c's safety catch, created empty ───────────────────────────────────
--
-- The drop (migration 190) is in the ordinary ladder, so it runs on a deploy
-- like everything else — but it is inert until a human puts a row here. Two
-- independent conditions must hold before anything is dropped: every gtd row
-- accounted for (checked from the data) AND somebody armed it (checked from
-- this table). Neither alone is enough, because "the data looks migrated" is
-- exactly what a half-finished move also looks like.

CREATE TABLE IF NOT EXISTS gtd_retirement_arm (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    armed_by    TEXT NOT NULL,
    armed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    note        TEXT
);

COMMENT ON TABLE gtd_retirement_arm IS
    'WS-39 S3c: one row here arms the gtd_* drop. Inserted BY HAND by the '
    'owner after verifying the S3b backfill. Empty = migration 190 does '
    'nothing. See work_plan.md §6 (f).';


-- ── (C) The preview ─────────────────────────────────────────────────────────
--
-- Read this before running anything. One row per unmigrated `gtd_items` row,
-- carrying the owner it resolved to and the tenant that owner belongs to — or
-- `unmappable`, which is the answer that must be looked at rather than
-- overridden.

CREATE OR REPLACE VIEW gtd_backfill_plan AS
SELECT
    i.id                                        AS item_id,
    i.title,
    btrim(i.user_id)                            AS raw_user_id,
    lower(btrim(i.user_id))                     AS resolved_email,
    u.organization_id                           AS resolved_org,
    o.slug                                      AS resolved_org_slug,
    i.disposition,
    p.outcome                                   AS gtd_project_outcome,
    p.source                                    AS gtd_project_source,
    CASE
        WHEN u.id IS NULL              THEN 'unmappable: no app_user with this email'
        WHEN u.organization_id IS NULL THEN 'unmappable: app_user has no organization'
        ELSE 'mappable'
    END                                         AS verdict
FROM gtd_items i
LEFT JOIN app_user    u ON lower(u.email) = lower(btrim(i.user_id))
LEFT JOIN organization o ON o.id = u.organization_id
LEFT JOIN gtd_projects p ON p.id = i.project_id
WHERE i.migrated_task_id IS NULL;

COMMENT ON VIEW gtd_backfill_plan IS
    'WS-39 S3b preview: what gtd_backfill_to_pm() would move, and to whom. '
    'Rows reading `unmappable` are refused, not guessed.';


-- ── (D) The move ────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION gtd_backfill_to_pm(p_apply boolean DEFAULT false)
RETURNS TABLE (step text, detail text, n bigint)
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_owner     record;
    v_item      record;
    v_proj      uuid;
    v_other_org uuid;
    v_target    uuid;
    v_status    uuid;
    v_task      uuid;
    v_num       bigint;
    v_wait      record;
    v_owners    bigint := 0;
    v_projects  bigint := 0;
    v_subs      bigint := 0;
    v_moved     bigint := 0;
    v_unmapped  bigint := 0;
BEGIN
    -- Refusals first, so a dry run leads with what it will NOT do. Reporting
    -- these after the successes would bury them under a wall of green.
    SELECT count(*) INTO v_unmapped FROM gtd_backfill_plan WHERE verdict <> 'mappable';
    IF v_unmapped > 0 THEN
        step := 'REFUSED'; n := v_unmapped;
        detail := 'rows whose owner cannot be resolved — left in place, not moved '
               || '(SELECT * FROM gtd_backfill_plan WHERE verdict <> ''mappable'')';
        RETURN NEXT;
    END IF;

    FOR v_owner IN
        SELECT lower(btrim(i.user_id)) AS email,
               u.organization_id       AS org,
               count(*)                AS items
          FROM gtd_items i
          JOIN app_user u ON lower(u.email) = lower(btrim(i.user_id))
         WHERE i.migrated_task_id IS NULL
           AND u.organization_id IS NOT NULL
         GROUP BY 1, 2
         ORDER BY 1
    LOOP
        v_owners := v_owners + 1;

        -- The personal project, resolved WITH an explicit tenant predicate.
        -- `_load_personal_project` in the gateway deliberately omits one and
        -- says why (D-MT-1 makes email globally unique, and RLS scopes the
        -- read). Neither of those protects a migration, so it is written here.
        SELECT id INTO v_proj
          FROM pm_projects
         WHERE lower(personal_owner) = v_owner.email
           AND organization_id = v_owner.org;

        IF v_proj IS NULL THEN
            -- Before creating one: is this member's personal project sitting in
            -- a DIFFERENT tenant? Under D-MT-1 that cannot happen, and the
            -- partial unique index on lower(personal_owner) would reject the
            -- insert anyway — but it would reject it with a duplicate-key error
            -- naming an index, which tells an operator nothing about WHY. If
            -- the invariant this file rests on is ever false, say so in words.
            SELECT organization_id INTO v_other_org
              FROM pm_projects
             WHERE lower(personal_owner) = v_owner.email;

            IF v_other_org IS NOT NULL THEN
                RAISE EXCEPTION
                    'S3b STOPPED: % has a personal project in organization %, but '
                    'their app_user row says organization %. D-MT-1(a) (one email = '
                    'one person = one organization) does not hold for this address, '
                    'and moving their tasks now would put them in the wrong tenant.',
                    v_owner.email, v_other_org, v_owner.org;
            END IF;

            IF p_apply THEN
                INSERT INTO pm_projects (name, description, personal_owner,
                                         created_by, source, organization_id)
                VALUES ('My Tasks',
                        'Work only you can see. Tasks assigned to you from team '
                        'projects appear in your inbox without living here.',
                        v_owner.email, v_owner.email, 'manual', v_owner.org)
                RETURNING id INTO v_proj;

                INSERT INTO pm_project_grants (project_id, subject, created_by,
                                               organization_id)
                VALUES (v_proj, v_owner.email, v_owner.email, v_owner.org)
                ON CONFLICT (project_id, subject) DO NOTHING;

                -- The same four lanes `ensure_personal_project` seeds. Kept
                -- byte-identical on purpose: a backfilled member and a member
                -- who captured their first task through the UI must land in the
                -- same board, or /projects shows two different personal projects
                -- depending on how the member arrived.
                INSERT INTO pm_task_statuses (project_id, name, category, position,
                                              is_default, organization_id)
                VALUES (v_proj, 'Inbox',  'backlog',     10, true,  v_owner.org),
                       (v_proj, 'Next',   'todo',        20, false, v_owner.org),
                       (v_proj, 'Doing',  'in_progress', 30, false, v_owner.org),
                       (v_proj, 'Done',   'done',        40, false, v_owner.org);
            END IF;
            v_projects := v_projects + 1;
        END IF;

        step := 'owner'; n := v_owner.items;
        detail := v_owner.email || ' → org ' || v_owner.org
               || CASE WHEN v_proj IS NULL THEN ' (personal project would be created)'
                       ELSE ' (personal project ' || v_proj || ')' END;
        RETURN NEXT;

        CONTINUE WHEN NOT p_apply;

        -- ── Sub-projects for the LOCAL tree ─────────────────────────────────
        --
        -- Only `source = 'LOCAL'`. A SYNCED gtd_project was a mirror of a
        -- ClickUp list and D52 retired the connector, so re-creating one would
        -- resurrect a shape the product no longer has. Its ITEMS still move —
        -- they just land in the personal project root rather than under a
        -- folder named after a tool nobody can reach.
        --
        -- ⚠️ The child carries `personal_owner` TOO, which migration **191**
        -- is what allows: uniqueness moved onto the root
        -- (`parent_project_id IS NULL`), so the column now means "private to
        -- this person" at every depth rather than "this is the one personal
        -- project". That is the difference between a member's categories
        -- staying private and appearing on the company board — `tree.py:152`
        -- excludes exactly `personal_owner IS NULL`. An earlier draft of this
        -- file wrote NULL here because the old index left no choice, and the
        -- owner's 2026-08-26 directive ("these do not show up in the project
        -- management app but show up in the tasks app") is what settled it.
        FOR v_item IN
            SELECT DISTINCT p.id, coalesce(nullif(btrim(p.outcome), ''), 'Project') AS nm
              FROM gtd_items i
              JOIN gtd_projects p ON p.id = i.project_id
             WHERE i.migrated_task_id IS NULL
               AND lower(btrim(i.user_id)) = v_owner.email
               AND p.source = 'LOCAL'
        LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pm_projects
                 WHERE parent_project_id = v_proj
                   AND organization_id = v_owner.org
                   AND name = v_item.nm
            ) THEN
                INSERT INTO pm_projects (name, parent_project_id, created_by,
                                         source, organization_id, personal_owner)
                VALUES (v_item.nm, v_proj, v_owner.email, 'manual', v_owner.org,
                        v_owner.email);

                INSERT INTO pm_project_grants (project_id, subject, created_by,
                                               organization_id)
                SELECT id, v_owner.email, v_owner.email, v_owner.org
                  FROM pm_projects
                 WHERE parent_project_id = v_proj AND name = v_item.nm
                   AND organization_id = v_owner.org
                ON CONFLICT (project_id, subject) DO NOTHING;

                v_subs := v_subs + 1;
            END IF;
        END LOOP;

        -- ── The items ───────────────────────────────────────────────────────
        FOR v_item IN
            SELECT i.*, gp.source AS gp_source,
                   coalesce(nullif(btrim(gp.outcome), ''), 'Project') AS gp_name
              FROM gtd_items i
              LEFT JOIN gtd_projects gp ON gp.id = i.project_id
             WHERE i.migrated_task_id IS NULL
               AND lower(btrim(i.user_id)) = v_owner.email
             ORDER BY i.created_at
        LOOP
            -- Which project: the LOCAL sub-project if it has one, else the root.
            v_target := v_proj;
            IF v_item.gp_source = 'LOCAL' THEN
                SELECT id INTO v_target
                  FROM pm_projects
                 WHERE parent_project_id = v_proj
                   AND organization_id = v_owner.org
                   AND name = v_item.gp_name;
                v_target := coalesce(v_target, v_proj);
            END IF;

            -- Which status. The overlay carries `disposition` verbatim, so the
            -- lens is exact whatever lands here — but /projects and /calendar
            -- read the STATUS, and D53's whole claim is that the three lenses
            -- agree. A finished task that came back as 'Inbox' on the board
            -- would break that claim on day one.
            SELECT id INTO v_status FROM pm_task_statuses
             WHERE project_id = v_proj
               AND category = CASE
                     WHEN v_item.disposition = 'DONE'  THEN 'done'
                     WHEN v_item.disposition = 'TRASH' THEN 'cancelled'
                     WHEN v_item.disposition = 'SOMEDAY' THEN 'backlog'
                     ELSE 'backlog' END
             ORDER BY position LIMIT 1;

            IF v_status IS NULL THEN
                SELECT id INTO v_status FROM pm_task_statuses
                 WHERE project_id = v_proj ORDER BY is_default DESC, position LIMIT 1;
            END IF;

            -- The number comes from the ROOT project's counter, like every
            -- other task (`next_task_number` keys on root_project_id).
            INSERT INTO pm_task_counters (project_id, last_value, organization_id)
            VALUES (v_proj, 1, v_owner.org)
            ON CONFLICT (project_id) DO UPDATE
                SET last_value = pm_task_counters.last_value + 1
            RETURNING last_value INTO v_num;

            -- ⚠️ `deleted_at` → `archived_at`, and this is not cosmetic.
            -- `gtd_items.deleted_at` is a SOFT delete ("vanishes from every
            -- view", items.py:389) and it is undoable. Carrying it over as an
            -- ordinary task would RESURRECT every task the member had deleted,
            -- into the app they use most, on a migration that cannot be rolled
            -- back. Archiving instead preserves both halves of the fact: the
            -- row survives (so it does not block S3c and nothing is destroyed)
            -- and it stays out of sight — `MY_TASKS_FROM` filters on
            -- `t.archived_at IS NULL`, exactly as the old view filtered on
            -- `i.deleted_at IS NULL`.
            INSERT INTO pm_tasks (project_id, root_project_id, task_number,
                                  status_id, title, description, due_at,
                                  completed_at, created_by, source,
                                  organization_id, created_at, archived_at)
            VALUES (v_target, v_proj, v_num, v_status,
                    coalesce(nullif(btrim(v_item.title), ''), '(untitled)'),
                    v_item.description, v_item.due_at, v_item.completed_at,
                    v_owner.email, 'manual', v_owner.org,
                    coalesce(v_item.created_at, now()), v_item.deleted_at)
            RETURNING id INTO v_task;

            -- Assigned to its owner. Since migration 191 the lens would find
            -- these anyway — every node of the private tree carries
            -- `personal_owner`, so MY_TASKS_FROM's project arm matches — but
            -- the row is still written, and not as belt-and-braces: an
            -- assignee is what makes the task ANSWERABLE. `derive_disposition`
            -- reads NEXT from "assigned to me" and WAITING from "assigned to
            -- someone else", so a task with no assignee derives to INBOX
            -- forever. Migrating somebody's active next-actions back into
            -- their inbox is a quiet way to undo their triage.
            --
            -- ⚠️ This comment used to say `personal_owner` is on the parent and
            -- not the child, which was true for exactly one draft and is the
            -- kind of note that outlives its fact and then misleads.
            INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,
                                           organization_id)
            VALUES (v_task, v_owner.email, v_owner.email, v_owner.org)
            ON CONFLICT (task_id, assignee) DO NOTHING;

            -- The open Waiting-For, if there is one. Most recent wins; resolved
            -- rows are history and do not travel.
            SELECT * INTO v_wait FROM gtd_waiting
             WHERE item_id = v_item.id AND resolved = false
             ORDER BY delegated_at DESC LIMIT 1;

            INSERT INTO pm_task_personal (
                task_id, member_email, organization_id,
                disposition, next_action, context, energy, time_estimate_mins,
                is_two_minute, defer_until, clarified_at,
                is_hard_date,
                waiting_on, delegated_at, expected_by, last_nudged_at)
            VALUES (
                v_task, v_owner.email, v_owner.org,
                v_item.disposition, v_item.next_action, v_item.context,
                v_item.energy, v_item.time_estimate_mins,
                coalesce(v_item.is_two_minute, false),
                v_item.defer_until, v_item.clarified_at,
                v_item.is_hard_date,
                v_wait.waiting_on, v_wait.delegated_at, v_wait.expected_by,
                v_wait.last_nudged_at)
            ON CONFLICT (task_id, member_email) DO NOTHING;

            UPDATE gtd_items SET migrated_task_id = v_task WHERE id = v_item.id;
            v_moved := v_moved + 1;
        END LOOP;
    END LOOP;

    step := CASE WHEN p_apply THEN 'APPLIED' ELSE 'DRY RUN (nothing written)' END;
    detail := format('%s owner(s), %s personal project(s), %s sub-project(s), %s task(s)',
                     v_owners, v_projects, v_subs, v_moved);
    n := v_moved;
    RETURN NEXT;
END;
$fn$;

COMMENT ON FUNCTION gtd_backfill_to_pm(boolean) IS
    'WS-39 S3b. Moves gtd_items into their owners'' personal projects. '
    'Re-runnable: already-moved rows carry migrated_task_id and are skipped. '
    'OWNER-GATE to run against a real database (work_plan.md §6 (f)).';
