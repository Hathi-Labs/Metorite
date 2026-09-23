-- ============================================================================
-- 212_gtd_backfill_owns_statuses.sql — 189's backfill predates 196's CHECK
-- ============================================================================
-- WS-39 S3b · `project-docs/specs/my_tasks_cutover.md` §6 step 5 · HANDOFF
-- H-104 (the forward-only re-assertion this copies).
--
-- ⚠️ **The S3b move FAILED on production on 2026-09-23, and wrote nothing.**
--
--     SELECT * FROM gtd_backfill_to_pm(true);
--     ERROR:  new row for relation "pm_projects" violates check constraint
--             "pm_projects_root_owns_statuses"
--     PL/pgSQL function gtd_backfill_to_pm(boolean) line 71
--
-- Migration 189 defined the function on 2026-08-26. Migration 196 added
-- `pm_projects.owns_statuses` on 2026-09-06, and 197 added the CHECK that a
-- ROOT (`parent_project_id IS NULL`) owns its statuses. The function's root
-- insert names neither, so the column takes its default (`false`) and the
-- CHECK refuses the row. The function ran inside its own transaction, so the
-- failure was clean: no project, no task, no `migrated_task_id` was written.
-- The same suite reproduced the error on a scratch tenant, at the same line.
--
-- ⚠️ **Why a NEW number and not an edit to 189.** `apply_migrations.sh` skips a
-- file whose checksum matches the ledger and RE-RUNS one whose checksum
-- changed. Editing 189 would therefore re-apply the whole file on the next
-- deploy — and the ledger would still record only *that we ran it*, never
-- *what the function says now*. 202 met the same shape for `provision_
-- organization` (185's fix was in the ledger and not in the function) and
-- settled the practice: forward-only (R6), a new number, the full body
-- re-asserted. This file is that, for `gtd_backfill_to_pm`.
--
-- What changes against 189's body — four edits, nothing else:
--
--   0. The root LOOKUP gains `AND parent_project_id IS NULL`, on both the
--      "which root" query and the "is it in another tenant" query. 191 made a
--      member's categories carry `personal_owner` too and added that predicate
--      to `_load_personal_project` for exactly this reason. 189 did not gain
--      it, so on a RE-RUN (runbook step 8, after a LOCAL category exists) the
--      lookup could answer with the category, and a straggler would then be
--      rooted at a node that owns no statuses. `live_ws39_s3b.sql` §9 proves
--      the straggler lands under the root.
--   1. The ROOT insert (`'My Tasks'`) carries `owns_statuses = true`. That is
--      the shape of record: `ensure_personal_project` in
--      `routes/projects/personal.py` writes exactly this, and 197's CHECK is
--      the fence.
--   2. The CHILD insert (one per LOCAL `gtd_projects` row) carries
--      `owns_statuses = false`. `mint_personal_child` is the shape of record:
--      a child INHERITS the root's four lanes, seeds no status rows of its own,
--      and its tasks resolve `status_id` against the ROOT's statuses — which
--      189's item loop already did (`WHERE project_id = v_proj`).
--   3. The lane seed no longer writes `pm_task_statuses.is_default`, and the
--      fallback status lookup no longer orders by it. 196 retired the column
--      from every reader ("stops being READ by this release and is dropped in
--      a later one"), and `ensure_personal_project` stopped writing it on
--      2026-09-06. A function that still named it would break on the day the
--      column goes, from inside the one move that cannot be re-run after S3c.
--      The lanes stay byte-identical to the gateway's — Inbox, Next, Doing,
--      Done — and "Inbox" is first because position 10 is first.
--
-- `pm_projects.kind` (193) needs no value: it defaults to `'project'`, which
-- is what a personal root and a personal category both are, and the gateway
-- mints leave it unset too.
--
-- ⚠️ THIS MIGRATION MOVES NO DATA WHEN IT IS APPLIED. It re-defines the
-- function and never calls it, for the reason 189's header gives in full: the
-- move against a real database is OWNER-GATE (`work_plan.md` §6 (f)) and the
-- ladder runs unattended before services restart. The runbook in
-- `my_tasks_cutover.md` §6 runs the move by hand AFTER this file is in the
-- production ledger.
--
-- Idempotent (CREATE OR REPLACE). No schema change, a function redefinition
-- only. 189's header carries the mapping's reasoning (D-MT-1, the explicit
-- tenant predicates, what is refused) and it is not restated here.
--
-- Depends on: 189_gtd_backfill_to_pm.sql (the body this re-asserts, and the
--   view and column it reads), 191 (a child may carry personal_owner),
--   196 + 197 (owns_statuses and its CHECK).
-- Pinned by: tests/unit/test_gtd_backfill.py (the LAST definition on the
--   ladder must stamp the flag, and must never call the function) and
--   tests/live/live_ws39_s3b.sql (root and child paths, on real Postgres).
-- ============================================================================

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
        --
        -- ⚠️ `AND parent_project_id IS NULL` is new in 212 and it is the same
        -- predicate 191 added to `_load_personal_project`, for the same reason:
        -- since 191 a member's CATEGORIES carry `personal_owner` too, so on a
        -- re-run (step 8 of the runbook) this lookup could answer with a child.
        -- A child owns no statuses, and a task rooted at it would resolve none.
        SELECT id INTO v_proj
          FROM pm_projects
         WHERE lower(personal_owner) = v_owner.email
           AND organization_id = v_owner.org
           AND parent_project_id IS NULL;

        IF v_proj IS NULL THEN
            -- Before creating one: is this member's personal project sitting in
            -- a DIFFERENT tenant? Under D-MT-1 that cannot happen, and the
            -- partial unique index on lower(personal_owner) would reject the
            -- insert anyway — but it would reject it with a duplicate-key error
            -- naming an index, which tells an operator nothing about WHY. If
            -- the invariant this file rests on is ever false, say so in words.
            SELECT organization_id INTO v_other_org
              FROM pm_projects
             WHERE lower(personal_owner) = v_owner.email
               AND parent_project_id IS NULL;

            IF v_other_org IS NOT NULL THEN
                RAISE EXCEPTION
                    'S3b STOPPED: % has a personal project in organization %, but '
                    'their app_user row says organization %. D-MT-1(a) (one email = '
                    'one person = one organization) does not hold for this address, '
                    'and moving their tasks now would put them in the wrong tenant.',
                    v_owner.email, v_other_org, v_owner.org;
            END IF;

            IF p_apply THEN
                -- A personal project is a ROOT, so it owns its statuses (the
                -- four seeded below). 197's CHECK refuses a root that owns
                -- nothing, and this is the line 189 lacked.
                INSERT INTO pm_projects (name, description, personal_owner,
                                         created_by, source, organization_id,
                                         owns_statuses)
                VALUES ('My Tasks',
                        'Work only you can see. Tasks assigned to you from team '
                        'projects appear in your inbox without living here.',
                        v_owner.email, v_owner.email, 'manual', v_owner.org,
                        true)
                RETURNING id INTO v_proj;

                INSERT INTO pm_project_grants (project_id, subject, created_by,
                                               organization_id)
                VALUES (v_proj, v_owner.email, v_owner.email, v_owner.org)
                ON CONFLICT (project_id, subject) DO NOTHING;

                -- The same four lanes `ensure_personal_project` seeds. Kept
                -- byte-identical on purpose: a backfilled member and a member
                -- who captured their first task through the UI must land in the
                -- same board, or /projects shows two different personal projects
                -- depending on how the member arrived. No `is_default` since
                -- 196: a capture lands in the FIRST lane, and Inbox is first.
                INSERT INTO pm_task_statuses (project_id, name, category, position,
                                              organization_id)
                VALUES (v_proj, 'Inbox',  'backlog',     10, v_owner.org),
                       (v_proj, 'Next',   'todo',        20, v_owner.org),
                       (v_proj, 'Doing',  'in_progress', 30, v_owner.org),
                       (v_proj, 'Done',   'done',        40, v_owner.org);
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
                -- `owns_statuses = false`, as `mint_personal_child` writes it:
                -- a category INHERITS the root's four lanes and seeds none of
                -- its own, so a task moved between root and category keeps its
                -- status_id. The item loop below resolves every status against
                -- the ROOT (`WHERE project_id = v_proj`) for the same reason.
                INSERT INTO pm_projects (name, parent_project_id, created_by,
                                         source, organization_id, personal_owner,
                                         owns_statuses)
                VALUES (v_item.nm, v_proj, v_owner.email, 'manual', v_owner.org,
                        v_owner.email, false);

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
                 WHERE project_id = v_proj ORDER BY position LIMIT 1;
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
    'OWNER-GATE to run against a real database (work_plan.md §6 (f)). '
    'Body re-asserted by migration 212 (owns_statuses on the root and the '
    'child, per 196/197) — 189 is the original definition.';
