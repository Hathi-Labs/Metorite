-- ============================================================================
-- 334_crm_change_log.sql — the Change history of the CRM settings (WS-CRM
-- CRM-M1, Metorite #1431).
--
-- What: one new table, `crm_change_log`. Each row is one change of the CRM
--       set-up: who made it, when, through what (the UI, the chat, WhatsApp,
--       an AI proposal or the system), who approved it, the level of the
--       trust ladder, what changed, the value before and the value after,
--       the CRM mode at that time, and whether an Undo can reverse it.
--       An Undo is itself a row. It names the row that it undoes.
-- Why:  WS-CRM CRM-M1, spec project-docs/specs/crm_conversational.md §8 and
--       §4 (D164). No CRM settings change kept a history, and only the
--       mapping plan kept versions (277). Each later CRM change needs a safe
--       way back.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table, two indexes, one trigger function and one
-- trigger. No rename, no drop, and no change to an existing table. Old code
-- names no new object, so old code that meets this schema runs as before.
-- The one writer is `gateway/routes/crm/change_log.py::log_change`. It writes in
-- the transaction of the change itself, so a change that rolls back leaves
-- no row, and a row never names a change that did not happen.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE (UNIQUE) INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP
-- TRIGGER IF EXISTS then CREATE, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 277.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`, because
-- generated phase 3 adds that name.
-- One Undo for each row: the partial unique index `crm_change_log_one_undo`
-- on `(organization_id, undoes_id)`. A second Undo of one row fails its
-- INSERT, so two Undo requests at the same time cannot both write. The
-- foreign key of `undoes_id` is the pair `(organization_id, undoes_id)`, so
-- an Undo row cannot name a row of another org. `undone_by` is a copy of the
-- id of that Undo row, for the read. It has no foreign key.
-- A row never changes: the trigger `crm_change_log_guard` refuses each UPDATE
-- of each column but `undone_by`, and it sets `undone_by` once only. That
-- holds for each role, the table owner too. A DELETE stays legal, so the
-- cascade of an org works. The CRM start over keeps the rows
-- (`core.RESET_KEEPS`).
-- `area` has a shape CHECK and not a list, so CRM-M1 PR 2 adds an area with
-- no migration. The list of the areas lives in `change_log.AREAS`, and
-- tests/unit/test_crm_change_history.py holds it.
-- `private_to` names the one member who may read the row: a private saved
-- view or a business rule of one member. NULL means the readers of the
-- Change history (gateway/routes/crm/change_log.py).
-- The number was 328 at build time (R1), then 331. On 2026-11-02 origin/main
-- held 330 (330_reach_outbox_due_summary.sql) and 331
-- (331_calendar_entries.sql, WS-CAL CE-4), and no open PR held 332 or above.
-- So this file took 332. On 2026-11-03 origin/main held 332
-- (332_pm_decisions.sql, WS-ALN ALN-2a), and the branch of PZ-33b held 333
-- (333_chat_message_member_typed.sql). No open PR and no other branch held
-- 334, so this file took 334. Check it again at merge.
-- Pinned by tests/unit/test_crm_change_history.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_change_log (
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The member of the session, never a value of the request.
    actor_email      TEXT NOT NULL,
    via              TEXT NOT NULL,
    -- The member who approved the change. In CRM-M1 it is the actor.
    approved_by      TEXT NULL,
    ladder_level     TEXT NOT NULL,
    area             TEXT NOT NULL,
    action           TEXT NOT NULL,
    -- The id of the thing that changed. NULL for a setting of the org.
    entity_id        TEXT NULL,
    -- One line in plain words, made by the server.
    summary          TEXT NOT NULL,
    before           JSONB NULL,
    after            JSONB NULL,
    -- The CRM mode when the change ran. NULL when no mode was chosen.
    mode             TEXT NULL,
    reversible       BOOLEAN NOT NULL,
    private_to       TEXT NULL,
    undoes_id        UUID NULL,
    undone_by        UUID NULL,
    PRIMARY KEY (id),
    -- The target of the foreign key of `undoes_id`, so an Undo stays in its org.
    CONSTRAINT crm_change_log_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT crm_change_log_undoes_fk FOREIGN KEY (organization_id, undoes_id)
        REFERENCES crm_change_log (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_change_log_actor CHECK (
        actor_email <> '' AND actor_email = lower(actor_email)
        AND char_length(actor_email) <= 320),
    CONSTRAINT crm_change_log_approved_by CHECK (
        approved_by IS NULL OR (approved_by <> '' AND approved_by = lower(approved_by))),
    CONSTRAINT crm_change_log_private_to CHECK (
        private_to IS NULL OR (private_to <> '' AND private_to = lower(private_to))),
    CONSTRAINT crm_change_log_via CHECK (
        via IN ('ui', 'chat', 'whatsapp', 'ai_proposal', 'system')),
    CONSTRAINT crm_change_log_ladder CHECK (
        ladder_level IN ('own', 'team', 'company', 'always_ask')),
    CONSTRAINT crm_change_log_area CHECK (area ~ '^[a-z][a-z_]{0,39}$'),
    CONSTRAINT crm_change_log_action CHECK (action IN ('create', 'change', 'delete')),
    -- A create has no before, a delete has no after, and a change has both.
    CONSTRAINT crm_change_log_values CHECK (
        (action = 'create' AND before IS NULL AND after IS NOT NULL)
        OR (action = 'delete' AND before IS NOT NULL AND after IS NULL)
        OR (action = 'change' AND before IS NOT NULL AND after IS NOT NULL)),
    CONSTRAINT crm_change_log_before_object CHECK (
        before IS NULL OR (jsonb_typeof(before) = 'object'
                           AND octet_length(before::text) <= 65536)),
    CONSTRAINT crm_change_log_after_object CHECK (
        after IS NULL OR (jsonb_typeof(after) = 'object'
                          AND octet_length(after::text) <= 65536)),
    CONSTRAINT crm_change_log_summary CHECK (
        btrim(summary) <> '' AND char_length(summary) <= 500),
    CONSTRAINT crm_change_log_entity_id CHECK (
        entity_id IS NULL OR (entity_id <> '' AND char_length(entity_id) <= 200)),
    CONSTRAINT crm_change_log_mode CHECK (mode IS NULL OR mode IN ('native', 'mirror')),
    -- An Undo row cannot be undone, and no row undoes itself.
    CONSTRAINT crm_change_log_undo_final CHECK (undoes_id IS NULL OR NOT reversible),
    CONSTRAINT crm_change_log_not_self CHECK (
        undoes_id IS DISTINCT FROM id AND undone_by IS DISTINCT FROM id)
);

-- The list, newest first.
CREATE INDEX IF NOT EXISTS idx_crm_change_log_at
    ON crm_change_log (organization_id, at DESC, id DESC);

-- One Undo for each row.
CREATE UNIQUE INDEX IF NOT EXISTS crm_change_log_one_undo
    ON crm_change_log (organization_id, undoes_id)
    WHERE undoes_id IS NOT NULL;

-- A row never changes, and `undone_by` is set once. For each role, the table
-- owner too. Fence: tests/unit/test_crm_change_history.py.
CREATE OR REPLACE FUNCTION crm_change_log_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.at IS DISTINCT FROM OLD.at
       OR NEW.actor_email IS DISTINCT FROM OLD.actor_email
       OR NEW.via IS DISTINCT FROM OLD.via
       OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
       OR NEW.ladder_level IS DISTINCT FROM OLD.ladder_level
       OR NEW.area IS DISTINCT FROM OLD.area
       OR NEW.action IS DISTINCT FROM OLD.action
       OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
       OR NEW.summary IS DISTINCT FROM OLD.summary
       OR NEW.before IS DISTINCT FROM OLD.before
       OR NEW.after IS DISTINCT FROM OLD.after
       OR NEW.mode IS DISTINCT FROM OLD.mode
       OR NEW.reversible IS DISTINCT FROM OLD.reversible
       OR NEW.private_to IS DISTINCT FROM OLD.private_to
       OR NEW.undoes_id IS DISTINCT FROM OLD.undoes_id THEN
        RAISE EXCEPTION 'crm_change_log: a change row never changes'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.undone_by IS NOT NULL OR NEW.undone_by IS NULL THEN
        RAISE EXCEPTION 'crm_change_log: undone_by is set once'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS crm_change_log_guard ON crm_change_log;
CREATE TRIGGER crm_change_log_guard
    BEFORE UPDATE ON crm_change_log
    FOR EACH ROW EXECUTE FUNCTION crm_change_log_guard_update();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_change_log')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_change_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_change_log FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_change_log_tenant_isolation ON crm_change_log;
        CREATE POLICY crm_change_log_tenant_isolation ON crm_change_log
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_change_log IS
    'WS-CRM CRM-M1: one change of the CRM set-up, with before, after and Undo. '
    'Only gateway/routes/crm/change_log.py writes it. crm_conversational.md §8.';

COMMIT;
