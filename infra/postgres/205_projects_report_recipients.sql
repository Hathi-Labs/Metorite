-- 205_projects_report_recipients.sql — who a report goes to, and when.
--
-- What: pm_report_recipients, plus `schedule` and `enabled` on pm_reports.
-- Why:  spec `project_management_app.md` §9.12.8, WS-27bk wave 5, slice 3.
--       §9.12.8 orders it — *"render first, deliver second"*. Slice 1 renders
--       in the app (204), slice 2 builds the message body, and this is the
--       audience. The owner delegated the two open questions on 2026-09-17.
-- Depends on: 204_projects_reports.sql (pm_reports), 161_projects_tenancy.sql
--       (organization_id and the pm_organization_from_parent trigger),
--       203_projects_project_watchers.sql (the CHECKs this mirrors).
--
-- ⚠️ **A RECIPIENT IS AN ADDRESS WE ALREADY MAIL, NEVER FREE TEXT.** This is
-- the ruling the whole table exists to carry. A free-text field would turn an
-- internal analytics tool into an open mail relay: anybody who can save a
-- report could send company delivery figures to any address on the internet,
-- on a timer, from our one verified sender. That is an exfiltration path AND a
-- reputation risk to the only sending domain we have. The CHECKs below are the
-- same two `pm_project_watchers` carries (R10) — human, and folded — because
-- the audience of a report is the same kind of thing as the audience of a
-- notification, and a second grammar for "a person we mail" is how one of them
-- starts leaking.
--
-- ⚠️ **THE SEND RENDERS ONCE PER RECIPIENT, WITH THAT RECIPIENT'S OWN
-- VISIBILITY.** `render_report` already resolves visibility from the CALLER
-- rather than the author, so two people opening one report legitimately see
-- different numbers. An email cannot do that by itself — it is rendered once
-- and posted. So the job renders per address, and `sendReportEmail` takes one
-- recipient per call for this reason and not merely to protect the list.
--
-- **That is also why ANY member may add ANY member.** Adding somebody to a
-- report can never show them more than they could already see by opening the
-- app, because the numbers they receive are computed with their own grants. A
-- permission check on "may I add you" would be guarding a door that opens onto
-- the room the person is already standing in.
--
-- **`enabled` is FALSE and the schedule is NULL.** §9.12.8: *"the schedule is
-- owner-gated to arm. Build it dark, default off."* Nothing here sends. The
-- runtime flag `PROJECT_REPORT_EMAIL_ENABLED` is the second lock, and it is
-- also off — a row that says `enabled` on a deployment that is not armed still
-- sends nothing.
--
-- Idempotent per infra/postgres/README.md. Pinned by
-- tests/unit/test_projects_report_recipients.py.

BEGIN;

-- ── The audience ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_report_recipients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- CASCADE: a recipient of a deleted report is an audience for nothing.
    report_id       UUID NOT NULL REFERENCES pm_reports (id) ON DELETE CASCADE,
    -- Lowercased email. Never `agent:<name>` — see the header.
    recipient       TEXT NOT NULL,
    organization_id UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pm_report_recipients_is_human
        CHECK (recipient NOT LIKE 'agent:%' AND recipient <> ''),
    CONSTRAINT pm_report_recipients_lowercased
        CHECK (recipient = lower(recipient)),
    -- One row per person per report. Adding twice is adding, so the endpoint
    -- is idempotent without the route having to check first.
    UNIQUE (report_id, recipient)
);

CREATE INDEX IF NOT EXISTS idx_pm_report_recipients_report
    ON pm_report_recipients (report_id);

CREATE OR REPLACE TRIGGER pm_report_recipients_org_from_report
    BEFORE INSERT OR UPDATE ON pm_report_recipients
    FOR EACH ROW
    EXECUTE FUNCTION pm_organization_from_parent('pm_reports', 'report_id');

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'pm_report_recipients'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE pm_report_recipients ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE pm_report_recipients FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS pm_report_recipients_tenant_isolation '
                'ON pm_report_recipients';
        EXECUTE 'CREATE POLICY pm_report_recipients_tenant_isolation '
                'ON pm_report_recipients '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

-- ── The schedule, built dark ────────────────────────────────────────────────
--
-- Nullable and defaulted OFF (R6). A report that existed before this migration
-- keeps sending nothing, which is the only safe meaning for a column that did
-- not exist yesterday.

ALTER TABLE pm_reports
    ADD COLUMN IF NOT EXISTS schedule TEXT;

ALTER TABLE pm_reports
    ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT false;

-- ⚠️ `last_sent_at` is what makes the job IDEMPOTENT rather than merely
-- periodic. A timer that fires twice — a retry, a restart, two workers — must
-- not send one report twice, and the only way to know is to record that it
-- went. A send is not a transaction that can be rolled back.
ALTER TABLE pm_reports
    ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMPTZ;

DO $sched$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pm_reports_schedule_known'
    ) THEN
        -- NULL means "not scheduled", which is different from a schedule the
        -- code does not recognise. Only `weekly` exists today; a value the
        -- job cannot read must never reach the table.
        ALTER TABLE pm_reports
            ADD CONSTRAINT pm_reports_schedule_known
            CHECK (schedule IS NULL OR schedule IN ('weekly'));
    END IF;
END
$sched$;

COMMIT;
