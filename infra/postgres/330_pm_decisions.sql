-- ============================================================================
-- 330_pm_decisions.sql — one person decides (WS-ALN ALN-2a, Metorite task
-- 94ca4c39).
--
-- What: one new table, `pm_decisions`, and a wider `pm_notifications_kind_check`.
--       A decision is a question with two to four named options. It goes to
--       one named person (`decider`) about one task. The answer is on the
--       record: the table keeps the answer, and the service writes one
--       `pm_activities` row for it.
-- Why:  `pm_intake` has four fixed verbs, no named decider and no options. An
--       `agent_runs` elicitation needs a run. A decision needs neither.
--       The table runs no action: it records an answer and nothing more, so
--       it is not a third approval stack. `project-docs/specs/
--       project_alignment.md` §3.2. ALN-7 and AUTO-7 ask on it later.
-- Depends on: 130_org_access_control.sql (organization), 267_pm_agent_runs.sql
--       (the last definition of `pm_notifications_kind_check`).
--
-- Expand only (R6): one new table, one index, one trigger function, one
-- trigger, and one CHECK that widens from five kinds to six. No rename and
-- no drop of data. Old code never writes the kind `decision`, so a widened
-- CHECK cannot refuse its rows.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF
-- EXISTS then CREATE, DROP CONSTRAINT IF EXISTS then ADD, and an RLS block
-- guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 325.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
--
-- One answer. The trigger `pm_decisions_guard` refuses any UPDATE of a
-- decision that holds an answer, for each role. It also refuses a change of
-- the identity, the question or the options, and an answer that comes after
-- `expires_at`. The service answers with `UPDATE ... WHERE answered_at IS
-- NULL AND expires_at > now()`, so two concurrent answers end as one answer
-- and one refusal. The trigger is the second wall behind that guard.
-- Only the decider answers: the CHECK `pm_decisions_answered_by_decider`
-- holds it. The answer is one of the option keys: the CHECK
-- `pm_decisions_answer_is_an_option` holds it.
--
-- No score for any person (ALN-Q5). The only columns that name a person are
-- `decider` and `answered_by`. Both are for the record on the task. No index
-- starts on either one, and no query groups by either one. There is no
-- `raised_for` column and no counter. `tests/unit/test_decisions.py` scans
-- this file and the service for it.
--
-- Retention and the member purge. A decision is a record on a task, so the
-- member purge keeps it, as it keeps every `pm_` row (D63). An unanswered
-- decision of a purged decider ends at `expires_at` (7 days) and takes no
-- answer after it. No pruning yet: a later slice owns it.
--
-- The number. The scan of origin and every local branch on 2026-11-02 found
-- 326 to 329 taken (326_calendar_entries, 327 twice, 328 twice, and
-- 329_pm_project_authorities). So this file took 330. Check it again at
-- merge (R1). Pinned by tests/unit/test_decisions.py, which finds this file by
-- CONTENT, never by number.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_decisions (
    organization_id UUID NOT NULL
                    REFERENCES organization (id) ON DELETE CASCADE
                    DEFAULT current_setting('app.tenant_id', true)::uuid,
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    -- What the decision is about. Only a task is open in ALN-2a: the service
    -- refuses a page until the Knowledge seam can answer who opens it.
    subject_kind    TEXT NOT NULL DEFAULT 'task',
    -- No foreign key: the id is a task or a page, and one column cannot name both.
    subject_id      UUID NOT NULL,
    -- The one named person, as a lower-case email.
    decider         TEXT NOT NULL,
    -- Two to four objects {key, label}.
    options         JSONB NOT NULL,
    question        TEXT NOT NULL,
    -- Who asked, for the record: a member address or a system actor.
    asked_by        TEXT NOT NULL,
    -- Which feature asked: the alignment check or an unattended run.
    source          TEXT NOT NULL DEFAULT 'align',
    -- For SW-18 and Studio B2c. The service never reads it.
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + interval '7 days',
    -- The answer. All three are NULL until it comes, and then all three are set.
    answered_by     TEXT NULL,
    answer_key      TEXT NULL,
    answered_at     TIMESTAMPTZ NULL,
    answer_reason   TEXT NULL,
    PRIMARY KEY (id),
    CONSTRAINT pm_decisions_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT pm_decisions_subject_kind CHECK (subject_kind IN ('task', 'page')),
    CONSTRAINT pm_decisions_source CHECK (source IN ('align', 'auto')),
    CONSTRAINT pm_decisions_decider CHECK (
        decider <> '' AND decider = lower(decider)),
    CONSTRAINT pm_decisions_question CHECK (
        btrim(question) <> '' AND char_length(question) <= 140),
    CONSTRAINT pm_decisions_options CHECK (
        jsonb_typeof(options) = 'array'
        AND jsonb_array_length(options) BETWEEN 2 AND 4),
    CONSTRAINT pm_decisions_payload CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT pm_decisions_expiry CHECK (expires_at > created_at),
    CONSTRAINT pm_decisions_reason CHECK (
        answer_reason IS NULL OR char_length(answer_reason) <= 500),
    -- The answer is whole or absent.
    CONSTRAINT pm_decisions_answer_whole CHECK (
        (answered_at IS NULL) = (answered_by IS NULL)
        AND (answered_at IS NULL) = (answer_key IS NULL)
        AND (answered_at IS NOT NULL OR answer_reason IS NULL)),
    -- Only the named person answers.
    CONSTRAINT pm_decisions_answered_by_decider CHECK (
        answered_by IS NULL OR answered_by = decider),
    -- The answer is one of the option keys.
    CONSTRAINT pm_decisions_answer_is_an_option CHECK (
        answer_key IS NULL
        OR options @> jsonb_build_array(jsonb_build_object('key', answer_key)))
);

-- The decider reads their open decisions, and a task reads its decisions.
CREATE INDEX IF NOT EXISTS idx_pm_decisions_subject
    ON pm_decisions (organization_id, subject_id, created_at DESC);

-- One answer, and no change of what was asked. For each role, the table
-- owner too. Fence: tests/unit/test_decisions.py.
CREATE OR REPLACE FUNCTION pm_decisions_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF OLD.answered_at IS NOT NULL THEN
        RAISE EXCEPTION 'pm_decisions: a decision takes one answer'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.subject_kind IS DISTINCT FROM OLD.subject_kind
       OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
       OR NEW.decider IS DISTINCT FROM OLD.decider
       OR NEW.options IS DISTINCT FROM OLD.options
       OR NEW.question IS DISTINCT FROM OLD.question
       OR NEW.asked_by IS DISTINCT FROM OLD.asked_by
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
        RAISE EXCEPTION 'pm_decisions: what was asked cannot change'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.answered_at IS NOT NULL AND NEW.answered_at > OLD.expires_at THEN
        RAISE EXCEPTION 'pm_decisions: a decision takes no answer after it expires'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_decisions_guard ON pm_decisions;
CREATE TRIGGER pm_decisions_guard
    BEFORE UPDATE ON pm_decisions
    FOR EACH ROW EXECUTE FUNCTION pm_decisions_guard_update();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_decisions')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_decisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_decisions FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_decisions_tenant_isolation ON pm_decisions;
        CREATE POLICY pm_decisions_tenant_isolation ON pm_decisions
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_decisions IS
    'One question with two to four named options, for one named person, about one task '
    '(WS-ALN ALN-2a). It records an answer and runs no action. Only '
    'routes/projects/decisions.py writes it. project-docs/specs/project_alignment.md §3.2.';

-- The bell row of a decision. WIDENS the CHECK that 267 last defined.
-- tests/unit/test_projects_notifications.py reads the whole ladder and holds
-- the last definition to NOTIFICATION_KINDS.
ALTER TABLE pm_notifications DROP CONSTRAINT IF EXISTS pm_notifications_kind_check;

ALTER TABLE pm_notifications
    ADD CONSTRAINT pm_notifications_kind_check
    CHECK (kind IN ('assigned', 'mention', 'comment', 'nudge', 'agent_question', 'decision'));

COMMIT;
