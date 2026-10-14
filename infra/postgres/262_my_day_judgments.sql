-- ============================================================================
-- 262_my_day_judgments.sql — one row for each judged card and feature.
--
-- What: one new table, `my_day_judgments`. The My Day pass asks `decide`
--       about each new card, in `shadow` and later in `on`. Each comparison of
--       the judged value with the old-path value becomes one row. The measure
--       read joins it with `my_day_card_state` (MD-6a-3). The table holds NO
--       tenant text: option keys, levels, numbers and ids only. Only
--       gateway/routes/shell/feed_decide.py writes it.
-- Why:  WS-58 MD-6a-1, spec project-docs/specs/my_day_feed.md §10.6, §10.9
--       and §13.2. `old_rank` and `judged_rank` are the card's place on each
--       path. The pass writes them on the `my_day.triage` row only.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table. No rename, no drop, and no change to an
-- existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity. A
-- second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 250_crm_views.sql and 258_my_day_card_state.sql. `REFERENCES` comes before
-- `DEFAULT`, because the tenancy ratchet in tests/unit/test_tenancy_boundary.py
-- matches the two with no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- The member comes from the loop's active set, which only a session writes.
-- The number is taken at build time (R1). On 2026-10-14 main held 260, and an
-- open branch held 261. Check it again at merge.
-- Pinned by tests/unit/test_feed_decide_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS my_day_judgments (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    member_email     TEXT NOT NULL,
    signal_id        TEXT NOT NULL,
    fingerprint      TEXT NOT NULL,
    feature          TEXT NOT NULL,
    mode             TEXT NOT NULL,
    old_value        TEXT NULL,
    judged_value     TEXT NULL,
    confidence       REAL NULL,
    used             BOOLEAN NOT NULL,
    request_id       TEXT NULL,
    old_rank         SMALLINT NULL,
    judged_rank      SMALLINT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, member_email, signal_id, feature, created_at),
    CONSTRAINT my_day_judgments_mode_known CHECK (mode IN ('shadow', 'on'))
);

-- The sweep deletes rows older than 30 days, one org at a time (MD-6a-2).
CREATE INDEX IF NOT EXISTS my_day_judgments_org_created_idx
    ON my_day_judgments (organization_id, created_at);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('my_day_judgments')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE my_day_judgments ENABLE ROW LEVEL SECURITY;
        ALTER TABLE my_day_judgments FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS my_day_judgments_tenant_isolation ON my_day_judgments;
        CREATE POLICY my_day_judgments_tenant_isolation ON my_day_judgments
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE my_day_judgments IS
    'WS-58 MD-6a-1: one row for each card, feature and pass that decide judged '
    'in shadow or on. Keys, levels and numbers only, never tenant text. Only '
    'routes/shell/feed_decide.py writes it. my_day_feed.md §10.9, §13.2.';

COMMIT;
