-- 204_projects_reports.sql — a report is a SAVED DEFINITION, not a document.
--
-- What: pm_reports — scope, period and sections, named and kept.
-- Why:  spec `project_management_app.md` §9.12.8, WS-27bk wave 5. The owner
--       drew the line: *"analytics is what you look at, a report is what gets
--       delivered."* So this is built ON §9.12.7 and after it.
-- Depends on: 146_projects.sql (pm_projects, and pm_views which this mirrors),
--       161_projects_tenancy.sql (organization_id and the
--       pm_organization_from_parent trigger).
--
-- ⚠️ **IT STORES NO NUMBERS, and that is the point.** A report row holds the
-- QUESTION, never the answer. §9.12.8 says a report with no analytics behind
-- it "would mint a second set of numbers, and two sets of numbers disagree" —
-- so rendering re-runs §9.12.7's own SQL every time, and a report and the
-- dashboard beside it cannot drift apart. A `results` column here would be
-- that second set, cached and quietly ageing.
--
-- **`config` is JSONB, for pm_views' reason.** A saved view and a saved report
-- are the same shape of thing: a named set of choices whose vocabulary grows.
-- Columns for `weeks` and `sections` would need a migration per new option,
-- and §9.12.8 already lists options this slice does not build.
--
-- ⚠️ **`project_id` IS NULLABLE, and NULL means the PORTFOLIO.** Analytics
-- learned this the hard way: all four endpoints shipped requiring a node, and
-- the Analytics pane — which holds no node id — could call none of them
-- (PR #271). "Every space I can see" is the scope a weekly report most often
-- wants, and a nullable column is how it says so.
--
-- **The tenant key (D-MT-3, R5).** NOT NULL and carried on the row like every
-- pm_* table since 161. The pm_organization_from_parent trigger fills and
-- cross-checks it FROM THE PROJECT, so a scoped report cannot claim a tenant
-- its project does not have. ⚠️ A PORTFOLIO report has no parent to read, and
-- the trigger returns unchanged when the parent column is NULL — so the insert
-- site sets it, exactly as a root project already does.
--
-- **What this slice deliberately does NOT have: recipients and a schedule.**
-- §9.12.8 orders it — *"render first, deliver second"* — and slice 1 is the
-- render. Both are additive, nullable columns in a later migration (R6), and
-- adding them now would ship an armed delivery path with nothing rendering
-- through it yet.
--
-- Idempotent per infra/postgres/README.md: IF NOT EXISTS everywhere and
-- CREATE OR REPLACE TRIGGER. Pinned by tests/unit/test_projects_reports.py.

BEGIN;

CREATE TABLE IF NOT EXISTS pm_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- ⚠️ NULLABLE. NULL is the portfolio — see the header.
    -- CASCADE: a report scoped to a deleted project asks about nothing.
    project_id      UUID REFERENCES pm_projects (id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    -- The question: period, sections, and whether the subtree is included.
    -- Validated in the route, like pm_views.config — a CHECK here would need a
    -- migration every time the vocabulary grows.
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pm_reports_name_not_blank
        CHECK (btrim(name) <> '')
);

-- The list read: every report in one tenant, newest first. `organization_id`
-- leads because RLS filters on it before anything else runs.
CREATE INDEX IF NOT EXISTS idx_pm_reports_org_created
    ON pm_reports (organization_id, created_at DESC);

-- The scoped read: "reports about this node". Partial, because portfolio rows
-- carry NULL here and would otherwise take up most of the index.
CREATE INDEX IF NOT EXISTS idx_pm_reports_project
    ON pm_reports (project_id)
    WHERE project_id IS NOT NULL;

CREATE OR REPLACE TRIGGER pm_reports_org_from_project
    BEFORE INSERT OR UPDATE ON pm_reports
    FOR EACH ROW
    EXECUTE FUNCTION pm_organization_from_parent('pm_projects', 'project_id');

-- ── Tenant isolation (D-MT-3, R5) ───────────────────────────────────────────
--
-- FORCE, not merely ENABLE: the owner role bypasses a policy that is only
-- ENABLEd, and the gateway connects as the owner on some deployments. That is
-- the trap `metorite prod verification traps` records — a green tenancy test
-- against an owner connection proves nothing.
DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'pm_reports'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE pm_reports ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE pm_reports FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS pm_reports_tenant_isolation '
                'ON pm_reports';
        EXECUTE 'CREATE POLICY pm_reports_tenant_isolation '
                'ON pm_reports '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMIT;
