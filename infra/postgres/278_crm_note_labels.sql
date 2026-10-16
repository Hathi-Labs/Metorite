-- ============================================================================
-- 278_crm_note_labels.sql — the decide labels of one CRM note.
--
-- What: one new table, `crm_note_labels`. After a sync run, a background step
--       asks `decide` about each new or changed note, in `shadow` and in
--       `on`. Each note gets one row: the kind, the probability that it needs
--       action, the urgency, the confidence and the probabilities of each,
--       the md5 of the text that was labeled, the time and the request id.
--       The table holds NO tenant text: option keys, levels, numbers and ids
--       only. Only gateway/crm_sync/note_labels.py writes it, and no route
--       reads it yet.
-- Why:  WS-53 CRM-Q26, spec project-docs/specs/crm_platform.md §18.11.2.
--       `body_md5` is how the step finds a changed note, because
--       `crm_activities` has no `updated_at`. `labeled_at` is how it counts
--       the daily cap of each org.
-- Depends on: 130_org_access_control.sql (organization), 144_crm.sql
--       (crm_activities).
--
-- Expand only (R6): one new table. No rename, no drop, and no change to an
-- existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity. A
-- second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 262_my_day_judgments.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- The CHECKs keep each label column to our own keys, so a vendor string can
-- never land in a row.
-- The number is taken at build time (R1). On 2026-10-16 origin/main held 276
-- (#1095), the branch crm-q17a1 held 274, and crm-q18 took 277. Check it again
-- at merge.
-- Pinned by tests/unit/test_crm_note_labels_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_note_labels (
    organization_id        UUID NOT NULL
                           REFERENCES organization (id) ON DELETE CASCADE
                           DEFAULT current_setting('app.tenant_id', true)::uuid,
    activity_id            UUID NOT NULL
                           REFERENCES crm_activities (id) ON DELETE CASCADE,
    kind                   TEXT NULL,
    kind_confidence        REAL NULL,
    kind_probabilities     JSONB NULL,
    needs_action           REAL NULL,
    urgency                TEXT NULL,
    urgency_confidence     REAL NULL,
    urgency_probabilities  JSONB NULL,
    body_md5               TEXT NOT NULL,
    mode                   TEXT NOT NULL,
    labeled_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id             TEXT NULL,
    PRIMARY KEY (activity_id),
    CONSTRAINT crm_note_labels_kind_known CHECK (
        kind IS NULL OR kind IN ('payment', 'site_issue', 'commercial',
                                 'follow_up', 'internal', 'other')),
    CONSTRAINT crm_note_labels_urgency_known CHECK (
        urgency IS NULL OR urgency IN ('low', 'medium', 'high')),
    CONSTRAINT crm_note_labels_needs_action_range CHECK (
        needs_action IS NULL OR (needs_action >= 0 AND needs_action <= 1)),
    CONSTRAINT crm_note_labels_mode_known CHECK (mode IN ('shadow', 'on'))
);

-- The daily cap counts the rows of one org labeled in the last 24 hours.
CREATE INDEX IF NOT EXISTS crm_note_labels_org_labeled_idx
    ON crm_note_labels (organization_id, labeled_at);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_note_labels')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_note_labels ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_note_labels FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_note_labels_tenant_isolation ON crm_note_labels;
        CREATE POLICY crm_note_labels_tenant_isolation ON crm_note_labels
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_note_labels IS
    'WS-53 CRM-Q26: the decide labels of one CRM note, in shadow or on. Keys, '
    'levels and numbers only, never tenant text. Only gateway/crm_sync/'
    'note_labels.py writes it. crm_platform.md §18.11.2.';

COMMIT;
