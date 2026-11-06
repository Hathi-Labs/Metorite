-- ============================================================================
-- 343_pm_agent_guidance_rules.sql — the rules of a project on the one
-- guidance store (WS-ALN ALN-5a, Metorite task 1379, D160).
--
-- What: no new table. `pm_agent_guidance` (migration 340) holds a rule as a
--       row of `kind = 'rule'`. This file adds the CHECKs that a rule row
--       needs, a guard that refuses an UPDATE of a rule row too, and one
--       partial index for the reads of the rules of a project.
-- Why:  project_alignment.md §4.2. A lead writes the rules of a project, and
--       a rule may quote a published Knowledge page. The route
--       `gateway/routes/project_rules.py` is the one writer of rule rows.
-- Depends on: 340_pm_agent_guidance.sql.
--
-- ⚠️ Append only, for a rule too. The header of 340 says that a rule row may
-- change `state`. ALN-5a chooses the other way: an accept, an edit, a retire
-- and a change of level each INSERT a new version row of the same
-- `rule_key`. So the history of a rule is its rows, as for guidance, and the
-- guard below refuses an UPDATE of each row, for each role. A DELETE stays
-- legal, so the cascade of a project or an org works.
--
-- Three states: `proposed`, `active` and `retired`. ALN-11 adds
-- `needs_review`, and a later file can widen the CHECK, as 338 does.
--
-- The CHECKs:
--   * `pm_agent_guidance_level`: `level` is NULL, `soft` or `hard`.
--   * `pm_agent_guidance_state`: `state` is NULL, `proposed`, `active` or
--     `retired`.
--   * `pm_agent_guidance_rule_shape`: a rule row has `rule_key`, `level`,
--     `state` and `proposed_by`.
--   * `pm_agent_guidance_rule_body`: the sentence of a rule holds 1 to 300
--     characters. A block of guidance keeps its cap of 8,000 in the route.
--   * `pm_agent_guidance_quote`: a quote holds 1 to 400 characters.
--   * `pm_agent_guidance_quote_source`: a quote, its page and its version
--     come together or not at all.
-- Each guidance row of SW-31a passes each CHECK, because SW-31a writes NULL
-- in each rule column.
--
-- Expand only (R6): CHECKs that the rows of old code pass, a replaced
-- trigger function and one new index. No new table, no new column, no
-- rename, no drop of a column. Old code writes guidance rows only, and the
-- old guard refused an UPDATE of each of them already.
-- Idempotent per infra/postgres/README.md: each CHECK is added only while
-- `pg_constraint` lacks its name, CREATE OR REPLACE FUNCTION, and CREATE
-- INDEX IF NOT EXISTS. A second run changes nothing. A replay of 340 puts
-- back the old body of the guard function, and this file then runs after it
-- and puts back the new body.
-- The ALTER TABLE takes the ACCESS EXCLUSIVE lock of `pm_agent_guidance`,
-- and each CHECK reads each row under it. The table is new and small, so the
-- file sets lock_timeout first, as 338 does, and the runner tries the whole
-- file again on a lock timeout.
--
-- Tenant-scoped (R5): no new table. The RLS and the policy of 340 cover each
-- rule row. The generated phases carry the table already.
--
-- No score for a person (ALN-Q5): no column and no index of this file groups
-- by `written_by` or `proposed_by`.
--
-- The number (R1): taken at build time on 2026-11-04. origin/main held 340.
-- The branch of CRM change history held 341, and the branch of MCP-P1b1
-- held 342. Check it again at merge.
-- Pinned by tests/unit/test_project_rules_static.py and, on a real database,
-- tests/unit/test_project_rules.py. Both find this file by CONTENT, never by
-- number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_agent_guidance_level'
           AND conrelid = 'pm_agent_guidance'::regclass
    ) THEN
        ALTER TABLE pm_agent_guidance ADD CONSTRAINT pm_agent_guidance_level
            CHECK (level IS NULL OR level IN ('soft', 'hard'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_agent_guidance_state'
           AND conrelid = 'pm_agent_guidance'::regclass
    ) THEN
        ALTER TABLE pm_agent_guidance ADD CONSTRAINT pm_agent_guidance_state
            CHECK (state IS NULL OR state IN ('proposed', 'active', 'retired'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_agent_guidance_rule_shape'
           AND conrelid = 'pm_agent_guidance'::regclass
    ) THEN
        ALTER TABLE pm_agent_guidance ADD CONSTRAINT pm_agent_guidance_rule_shape
            CHECK (kind <> 'rule' OR (rule_key IS NOT NULL AND level IS NOT NULL
                                      AND state IS NOT NULL AND proposed_by IS NOT NULL));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_agent_guidance_rule_body'
           AND conrelid = 'pm_agent_guidance'::regclass
    ) THEN
        ALTER TABLE pm_agent_guidance ADD CONSTRAINT pm_agent_guidance_rule_body
            CHECK (kind <> 'rule' OR char_length(body) BETWEEN 1 AND 300);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_agent_guidance_quote'
           AND conrelid = 'pm_agent_guidance'::regclass
    ) THEN
        ALTER TABLE pm_agent_guidance ADD CONSTRAINT pm_agent_guidance_quote
            CHECK (quote IS NULL OR char_length(quote) BETWEEN 1 AND 400);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_agent_guidance_quote_source'
           AND conrelid = 'pm_agent_guidance'::regclass
    ) THEN
        ALTER TABLE pm_agent_guidance ADD CONSTRAINT pm_agent_guidance_quote_source
            CHECK ((quote IS NULL) = (source_page_id IS NULL)
                   AND (quote IS NULL) = (source_page_version IS NULL));
    END IF;
END
$checks$;

-- A saved row never changes: the next save, accept, edit, retire or change
-- of level is a new row. 340 refused an UPDATE of a `guidance` row only. This
-- body refuses an UPDATE of a `rule` row too. The trigger of 340 calls this
-- function, so it needs no change. Fences: tests/unit/test_agent_guidance_r8.py
-- and tests/unit/test_project_rules.py.
CREATE OR REPLACE FUNCTION pm_agent_guidance_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF OLD.kind = 'guidance' THEN
        RAISE EXCEPTION 'pm_agent_guidance: a saved block of guidance cannot change, save a new version'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.kind = 'rule' THEN
        RAISE EXCEPTION 'pm_agent_guidance: a saved version of a rule cannot change, save a new version'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

-- The newest version of each rule of a project, and the history of a rule.
CREATE INDEX IF NOT EXISTS pm_agent_guidance_rule
    ON pm_agent_guidance (organization_id, project_id, rule_key, version DESC)
    WHERE kind = 'rule';

COMMIT;
