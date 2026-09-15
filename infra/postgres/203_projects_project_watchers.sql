-- 203_projects_project_watchers.sql — watch a PROJECT, not each of its tasks.
--
-- What: pm_project_watchers — one row per person following one project node.
-- Why:  spec `project_management_app.md` §9.12.2(b), WS-27bk wave 3. A member
--       wants *"tell me about this project"* without opening every task in it.
--       Today the only subscription is per task (165), so the only way to
--       follow a project is to watch each task and to keep doing it as tasks
--       arrive.
-- Depends on: 146_projects.sql (pm_projects), 161_projects_tenancy.sql (the
--       organization_id column and the pm_organization_from_parent trigger
--       function), 165_projects_watchers.sql (the task table this mirrors).
--
-- ⚠️ **WATCHING A PROJECT IS NOT WATCHING ITS TASKS, and that is the whole
-- design.** A subscription that expanded into per-task rows would be wrong
-- twice: it goes stale the moment somebody adds a task, and it writes
-- thousands of rows for one click. The project watch is its OWN row. The
-- notification audience becomes task watchers ∪ assignees ∪ watchers of the
-- task's project CHAIN, resolved at fan-out time, so a task created a second
-- ago is covered by a subscription taken a year ago.
--
-- ⚠️ **NO SEED, deliberately — and 165 seeded, so the difference is argued.**
-- Migration 165 had to seed task authors, because the audience it introduced
-- REPLACED a derived one and an unseeded author would have silently stopped
-- hearing. Nothing is replaced here: this audience is purely additive, so
-- nobody loses a notification they get today. Seeding every project's creator
-- or lead would instead subscribe people to a firehose they never asked for —
-- every task event in a subtree — which is the opposite of the ask.
--
-- **A watcher is a HUMAN address, stored folded (R10).** The same two CHECKs as
-- 165 and as pm_notifications' recipient, for the same reasons: an
-- `agent:<name>` row is an inbox nobody opens, and every read compares folded,
-- so a mixed-case row is a subscription that never fires.
--
-- **The tenant key (D-MT-3, R5).** Carried on the row like every pm_* table
-- since 161, and filled and cross-checked against the PROJECT's by the same
-- pm_organization_from_parent trigger — so no INSERT site has to remember it,
-- and a row cannot claim a tenant its project does not have.
--
-- Idempotent per infra/postgres/README.md: IF NOT EXISTS everywhere and
-- CREATE OR REPLACE TRIGGER. Pinned by tests/unit/test_project_watchers.py.

BEGIN;

CREATE TABLE IF NOT EXISTS pm_project_watchers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- CASCADE: a watcher row on a deleted project is a subscription to
    -- nothing. Same ruling as 165's task_id.
    project_id      UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    -- Lowercased email. Never `agent:<name>` — see the header.
    watcher         TEXT NOT NULL,
    organization_id UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pm_project_watchers_watcher_is_human
        CHECK (watcher NOT LIKE 'agent:%' AND watcher <> ''),
    CONSTRAINT pm_project_watchers_watcher_lowercased
        CHECK (watcher = lower(watcher)),
    -- One subscription per person per project. This is what makes the watch
    -- endpoint idempotent: watching twice is watching.
    UNIQUE (project_id, watcher)
);

-- ⚠️ This index is NOT the mirror of 165's "no second index" ruling, and the
-- difference is the point. 165 declined a watcher-leading index because
-- nothing asked "what do I watch"; WS-29b's rule is that an index earns its
-- place from a query that filters on it. The fan-out here walks UP from a
-- task's project through its ancestors and then asks for the watchers of that
-- SET, which is a project_id-leading read the UNIQUE already serves. But
-- `GET /projects/nodes/{id}/watchers` also answers "does the caller watch
-- this", and the chain query filters on watcher for the caller's own row, so
-- the reverse read is now real.
CREATE INDEX IF NOT EXISTS pm_project_watchers_watcher_idx
    ON pm_project_watchers (watcher);

-- Fill-and-verify the tenant from the project's, exactly as 161 attaches the
-- other pm_* tables and 165 attaches the task watchers. The function is 161's;
-- only the attachment is new.
CREATE OR REPLACE TRIGGER trg_pm_project_watchers_org_from_project
    BEFORE INSERT OR UPDATE ON pm_project_watchers
    FOR EACH ROW EXECUTE FUNCTION
    pm_organization_from_parent('pm_projects', 'project_id');

COMMENT ON TABLE pm_project_watchers IS
    'WS-27bk §9.12.2(b): one row per person following one project node. NOT an '
    'expansion into task watchers — the notification audience resolves the '
    'project CHAIN at fan-out time, so a task created after the subscription '
    'is covered by it. Additive to the task audience; nobody loses a '
    'notification they had before.';

-- ── RLS, but ONLY on a database that has already promoted it (H-104) ────────
--
-- ⚠️ **Without this, pm_project_watchers would be the ONE tenant table on
-- production with no isolation.** `generated/04_policies.sql` holds the
-- ENABLE/FORCE/POLICY for the other 140 tables, and that directory is NOT on
-- the ladder — `apply_migrations.sh` globs `[0-9][0-9]*_*.sql` in
-- `infra/postgres` only. So a table born after the promotion inherits nothing,
-- and every row in it would be readable across tenants. Measured 2026-09-15:
-- production has `relforcerowsecurity` on pm_projects, pm_tasks and
-- pm_task_watchers.
--
-- ⚠️ **Guarded, and the guard is the same shape migrations 200 and 201 use.**
-- Two schemas exist. A developer database and CI have no RLS on ANY pm_ table,
-- and forcing it here alone would make this the only table that needs
-- `app.tenant_id` bound — every fixture that writes it directly would break,
-- and for a fence nothing else on that database carries. So the probe asks
-- whether the PARENT table is already forced, and follows it.
--
-- The policy text is `gen_tenant_migration.py`'s, which now emits this table
-- too (141 in phase 4). This is the same statement, applied earlier, so a
-- promoted database and a freshly regenerated one agree.
DO $rls$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class
         WHERE relname = 'pm_projects'
           AND relkind = 'r'
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE pm_project_watchers ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE pm_project_watchers FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS pm_project_watchers_tenant_isolation '
                'ON pm_project_watchers';
        EXECUTE 'CREATE POLICY pm_project_watchers_tenant_isolation '
                'ON pm_project_watchers '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMIT;
